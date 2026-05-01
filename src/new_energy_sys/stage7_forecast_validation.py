"""真实预报天气可用性验证模块。

模块设计原则：
- 仅替换 target_plus_* 天气列为预报发布天气，保持历史特征和标签不变
- 泄漏门禁：要求 weather_forecast_issue_time <= 预测时间戳
- 缺失预报行直接删除而非插补，保证训练集无隐式信息泄漏
- 日历特征（hour_sin 等）从确定性时间戳计算，不依赖天气供应商
- 验收门槛硬性要求：nRMSE、日间 nRMSE、泄漏、质量门禁、物理边界全部通过

本模块对应项目 Stage7 的真实预报天气替代与可用性验证功能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.modeling import TARGET_COLUMNS
from new_energy_sys.sequence_modeling import run_tcn_experiments
from new_energy_sys.standardize import normalize_weather


@dataclass(frozen=True)
class Stage7Result:
    """Stage7 预报天气验证产物容器。

    Attributes:
        forecast_weather: 预报有效时间天气表
        feature_dataset: 替换 target_plus 后的 Stage7 特征数据集
        metrics: TCN 实验指标 DataFrame
        predictions: TCN 实验预测值 DataFrame
        report: 包含质量门禁、验收结论和产物路径的报告字典
    """

    forecast_weather: pd.DataFrame
    feature_dataset: pd.DataFrame
    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


FORECAST_SOURCE_COMPARISON = [
    {
        "source": "NOAA HRRR f24",
        "issue_time": "原生预报周期",
        "valid_time": "原生 GRIB 有效时间",
        "lead_time": "原生预报小时 f24",
        "status": "首选生产级路径；本地全年提取尚未完成",
        "pitfall": "GRIB 提取开销大；缺测小时若静默填充将引入指标偏差",
    },
    {
        "source": "Open-Meteo Historical Forecast f24",
        "issue_time": "valid_time - 24h，作为显式假设存储",
        "valid_time": "逐小时时间戳",
        "lead_time": "假设 24h",
        "status": "选定的可执行 Stage7 路径，因为本地 2022 站点匹配数据已存在",
        "pitfall": "Issue time 是简化 API 导出的假设；弱于原生 HRRR 周期元数据",
    },
]


TARGET_PLUS_WEATHER_BASE_COLUMNS = [
    "ghi_wm2",
    "dhi_wm2",
    "dni_wm2",
    "temperature_c",
    "dew_point_c",
    "relative_humidity_pct",
    "wind_speed_ms",
    "wind_direction_deg",
    "pressure_hpa",
    "surface_pressure_hpa",
    "wind_gusts_ms",
    "cloud_cover_pct",
    "cloud_cover_low_pct",
    "cloud_cover_mid_pct",
    "cloud_cover_high_pct",
    "precipitation_mm",
]


def _load_forecast_weather(path: Path) -> pd.DataFrame:
    """加载预报有效时间天气表（使用稳定的 Stage3 列名）。

    接受 Open-Meteo 原始 CSV 和标准化 parquet 两种格式。返回的表
    每行一个有效时间，携带审计字段以证明模型仅看到不晚于
    光伏预测时间戳发布的天气。

    Args:
        path: 天气数据文件路径（.parquet 或 .csv）

    Returns:
        按 timestamp 排序、去重且删除关键空值的天气 DataFrame
    """

    if path.suffix.lower() == ".parquet":
        weather = pd.read_parquet(path)
    else:
        weather = normalize_weather(path)

    weather = weather.copy()
    weather["timestamp"] = pd.to_datetime(weather["timestamp"], errors="coerce", utc=True)
    weather["weather_forecast_issue_time"] = pd.to_datetime(
        weather["weather_forecast_issue_time"],
        errors="coerce",
        utc=True,
    )
    weather["weather_forecast_lead_time_hour"] = pd.to_numeric(
        weather["weather_forecast_lead_time_hour"],
        errors="coerce",
    )
    return (
        weather.dropna(subset=["timestamp", "weather_forecast_issue_time", "weather_forecast_lead_time_hour"])
        .drop_duplicates(subset=["timestamp"], keep="first")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _target_plus_columns(frame: pd.DataFrame) -> list[str]:
    """返回当前存在的所有目标有效时间特征列。

    Args:
        frame: 输入数据帧

    Returns:
        以 target_plus_ 开头的列名列表
    """

    return [column for column in frame.columns if column.startswith("target_plus_")]


def _replace_target_plus_with_forecast(
    stage3: pd.DataFrame,
    forecast_weather: pd.DataFrame,
    *,
    horizons: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """用预报发布天气替换 NSRDB target_plus 天气。

    输入 Stage3 表已包含标签、历史光伏滞后、调度字段和当前时间天气特征。
    仅 target_plus_* 列在此重建。对于预测时间戳 t 所在的行，
    每个预报特征从有效时间 t + horizon 的天气中拼接。
    泄漏门禁要求 weather_forecast_issue_time <= t。

    Args:
        stage3: Stage3 特征数据帧
        forecast_weather: 预报有效时间天气 DataFrame
        horizons: 预报提前时间列表（如 [6, 24]）

    Returns:
        元组 (替换后的特征数据集, 特征映射表, 审计字典)
        审计字典包含 audit DataFrame 和 summary 摘要
    """

    working = stage3.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    original_target_plus = _target_plus_columns(working)
    working = working.drop(columns=original_target_plus)

    forecast_fields = [column for column in TARGET_PLUS_WEATHER_BASE_COLUMNS if column in forecast_weather.columns]
    mapping_rows: list[dict[str, Any]] = []
    audit_frames: list[pd.DataFrame] = []

    for horizon in horizons:
        target_time = working["timestamp"] + pd.to_timedelta(horizon, unit="h")
        working[f"target_plus_{horizon}h_hour_sin"] = np.sin(2.0 * np.pi * target_time.dt.hour.astype(float) / 24.0)
        working[f"target_plus_{horizon}h_hour_cos"] = np.cos(2.0 * np.pi * target_time.dt.hour.astype(float) / 24.0)
        working[f"target_plus_{horizon}h_day_of_year_sin"] = np.sin(
            2.0 * np.pi * (target_time.dt.dayofyear.astype(float) - 1.0) / 365.0
        )
        working[f"target_plus_{horizon}h_day_of_year_cos"] = np.cos(
            2.0 * np.pi * (target_time.dt.dayofyear.astype(float) - 1.0) / 365.0
        )
        for field in [
            "hour_sin",
            "hour_cos",
            "day_of_year_sin",
            "day_of_year_cos",
        ]:
            mapping_rows.append(
                {
                    "stage7_feature": f"target_plus_{horizon}h_{field}",
                    "source": "deterministic target valid-time calendar",
                    "required_from_forecast": False,
                    "horizon_hour": horizon,
                    "availability_rule": "computed from timestamp + horizon; no weather provider required",
                }
            )

        join = pd.DataFrame(
            {
                "timestamp": working["timestamp"],
                "forecast_valid_time": target_time,
            }
        )
        weather_slice = forecast_weather[
            ["timestamp", "weather_forecast_issue_time", "weather_forecast_lead_time_hour", *forecast_fields]
        ].rename(columns={"timestamp": "forecast_valid_time"})
        joined = join.merge(weather_slice, on="forecast_valid_time", how="left")

        for field in forecast_fields:
            stage7_column = f"target_plus_{horizon}h_{field}"
            working[stage7_column] = joined[field].to_numpy()
            mapping_rows.append(
                {
                    "stage7_feature": stage7_column,
                    "source": "Open-Meteo Historical Forecast f24",
                    "required_from_forecast": True,
                    "horizon_hour": horizon,
                    "availability_rule": f"forecast_valid_time = timestamp + {horizon}h; issue_time <= timestamp",
                }
            )

        issue_column = f"target_plus_{horizon}h_weather_forecast_issue_time"
        lead_column = f"target_plus_{horizon}h_weather_forecast_lead_time_hour"
        working[issue_column] = joined["weather_forecast_issue_time"].to_numpy()
        working[lead_column] = joined["weather_forecast_lead_time_hour"].to_numpy()
        mapping_rows.extend(
            [
                {
                    "stage7_feature": issue_column,
                    "source": "forecast audit metadata",
                    "required_from_forecast": True,
                    "horizon_hour": horizon,
                    "availability_rule": "must be <= prediction timestamp",
                },
                {
                    "stage7_feature": lead_column,
                    "source": "forecast audit metadata",
                    "required_from_forecast": True,
                    "horizon_hour": horizon,
                    "availability_rule": "must be numeric and positive",
                },
            ]
        )

        audit_frames.append(
            joined.assign(
                horizon_hour=horizon,
                issue_time_lte_prediction_time=joined["weather_forecast_issue_time"] <= joined["timestamp"],
            )[
                [
                    "timestamp",
                    "forecast_valid_time",
                    "weather_forecast_issue_time",
                    "weather_forecast_lead_time_hour",
                    "horizon_hour",
                    "issue_time_lte_prediction_time",
                ]
            ]
        )

    rebuilt_target_plus = _target_plus_columns(working)
    required_columns = [*TARGET_COLUMNS, *rebuilt_target_plus]
    before_drop = len(working)
    working = working.dropna(subset=required_columns).reset_index(drop=True)
    audit = pd.concat(audit_frames, ignore_index=True)
    audit = audit[audit["timestamp"].isin(set(working["timestamp"]))].reset_index(drop=True)

    mapping = pd.DataFrame(mapping_rows)
    summary = {
        "original_target_plus_columns_removed": sorted(original_target_plus),
        "stage7_target_plus_columns": sorted(rebuilt_target_plus),
        "forecast_required_columns": sorted(
            mapping[mapping["required_from_forecast"]]["stage7_feature"].tolist()
        ),
        "rows_before_forecast_drop": int(before_drop),
        "rows_after_forecast_drop": int(len(working)),
        "rows_removed_without_complete_forecast": int(before_drop - len(working)),
    }
    return working, mapping, {"audit": audit, "summary": summary}


def _quality_gates(feature_dataset: pd.DataFrame, forecast_audit: pd.DataFrame, predictions: pd.DataFrame, capacity_kw: float) -> dict[str, bool]:
    """计算 Stage7 用于生产可用性判定的硬门禁。

    Args:
        feature_dataset: Stage7 特征数据集
        forecast_audit: 预报可用性审计 DataFrame
        predictions: TCN 预测值 DataFrame
        capacity_kw: 电站装机容量 (kW)

    Returns:
        门禁名称到是否通过的布尔映射
    """

    numeric = feature_dataset.select_dtypes(include=[np.number])
    test_predictions = predictions[predictions["split"] == "test"]
    return {
        "forecast_weather_non_empty": bool(len(forecast_audit) > 0),
        "forecast_issue_time_lte_prediction_time": bool(forecast_audit["issue_time_lte_prediction_time"].all()),
        "forecast_lead_time_present": bool(forecast_audit["weather_forecast_lead_time_hour"].notna().all()),
        "no_nsrdb_target_plus_weather_columns": bool(
            not any(
                column.startswith("target_plus_")
                and any(marker in column for marker in ["clearsky", "solar_zenith", "surface_albedo", "precipitable_water"])
                for column in feature_dataset.columns
            )
        ),
        "timestamp_monotonic": bool(feature_dataset["timestamp"].is_monotonic_increasing),
        "no_missing_numeric_values": bool(numeric.isna().sum().sum() == 0),
        "no_infinite_numeric_values": bool(np.isfinite(numeric.to_numpy()).all()),
        "test_predictions_within_physical_bound": bool(
            test_predictions["prediction_kw"].between(0.0, capacity_kw * 1.05).all()
        ),
    }


def _stage7_report_markdown(report: dict[str, Any], path: Path) -> None:
    """写出 Stage7 中文 Markdown 决策报告。

    Args:
        report: Stage7 报告字典
        path: 输出文件路径
    """

    gates = report["quality_gates"]
    metrics = report["comparison"]["stage7_tcn"]
    lines = [
        "# Stage7 真实预报天气可用性验证报告",
        "",
        "## 结论",
        "",
        f"- 主判定: `{report['decision']['recommendation']}`",
        f"- 原因: {report['decision']['reason']}",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage3 原始特征"] --> B["删除 NSRDB target_plus_*"]',
        '    C["Open-Meteo f24 forecast-valid-time"] --> D["按 timestamp+horizon 拼接"]',
        '    D --> E{"issue_time <= timestamp"}',
        '    E -->|通过| F["Stage7 特征集"]',
        '    F --> G["TCN regularized 168h"]',
        '    G --> H["三方指标对比"]',
        "```",
        "",
        "## 数据源方案",
        "",
        "| 方案 | issue time | valid time | lead time | 状态 |",
        "|---|---|---|---|---|",
    ]
    for row in report["forecast_source_comparison"]:
        lines.append(
            f"| `{row['source']}` | {row['issue_time']} | {row['valid_time']} | {row['lead_time']} | {row['status']} |"
        )

    lines.extend(
        [
            "",
            "## 三方对比",
            "",
            "| 模型/实验 | nRMSE | 日间 nRMSE | 说明 |",
            "|---|---:|---:|---|",
            (
                f"| Stage5 tuned LightGBM | {report['comparison']['stage5_lightgbm']['nrmse_capacity']:.4f} | "
                f"{report['comparison']['stage5_lightgbm']['daytime_nrmse_capacity']:.4f} | 当前生产基线 |"
            ),
            (
                f"| Stage6 TCN 上限实验 | {report['comparison']['stage6_tcn_upper_bound']['nrmse_capacity']:.4f} | "
                f"{report['comparison']['stage6_tcn_upper_bound']['daytime_nrmse_capacity']:.4f} | 使用 NSRDB target_plus 上限 |"
            ),
            (
                f"| Stage7 TCN 真实预报替代 | {metrics['nrmse_capacity']:.4f} | "
                f"{metrics['daytime_nrmse_capacity']:.4f} | Open-Meteo f24 替代 target_plus |"
            ),
            "",
            "## 验收门槛",
            "",
            f"- t+24h TCN nRMSE <= `0.1225`: `{report['acceptance']['nrmse_pass']}`",
            f"- t+24h 日间 nRMSE <= `0.1689`: `{report['acceptance']['daytime_nrmse_pass']}`",
            f"- 质量门禁 100%: `{report['acceptance']['quality_gates_pass']}`",
            f"- 数据泄漏检查: `{report['acceptance']['leakage_check_pass']}`",
            f"- 预测值物理边界 [0, capacity_kw * 1.05]: `{report['acceptance']['physical_bound_pass']}`",
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in gates.items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(
        [
            "",
            "## 产物",
            "",
            f"- Forecast-valid-time 数据集: `{report['artifacts']['forecast_weather_dataset']}`",
            f"- Stage7 特征集: `{report['artifacts']['feature_dataset']}`",
            f"- 特征映射表: `{report['artifacts']['feature_mapping']}`",
            f"- TCN 指标: `{report['artifacts']['metrics']}`",
            f"- TCN 预测: `{report['artifacts']['predictions']}`",
            "",
            "## Pitfall",
            "",
            "Open-Meteo 简化导出的 issue time 是显式 lead-time 假设；生产落地前仍需切换到 HRRR 原生 cycle/lead_time 或供应商原生预报归档。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_stage7_forecast_validation(
    *,
    config: dict[str, Any],
    stage3_path: Path,
    forecast_weather_path: Path,
    output_dir: Path,
) -> Stage7Result:
    """端到端执行 Stage7 预报天气 target_plus 特征替代与验证。

    Args:
        config: 全局配置字典，须包含 site.capacity_kw
        stage3_path: Stage3 parquet 数据集路径
        forecast_weather_path: 预报天气数据路径（.parquet 或 .csv）
        output_dir: 输出目录路径

    Returns:
        Stage7Result 包含 forecast_weather、feature_dataset、metrics、predictions、report
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    capacity_kw = float(config["site"]["capacity_kw"])
    stage3 = pd.read_parquet(stage3_path)
    forecast_weather = _load_forecast_weather(forecast_weather_path)

    feature_dataset, mapping, audit_bundle = _replace_target_plus_with_forecast(
        stage3,
        forecast_weather,
        horizons=[6, 24],
    )

    forecast_weather_dataset_path = output_dir / "stage7_forecast_weather_dataset.parquet"
    feature_dataset_path = output_dir / "stage7_feature_dataset.parquet"
    mapping_path = output_dir / "stage7_target_plus_feature_mapping.csv"
    audit_path = output_dir / "stage7_forecast_availability_audit.csv"

    forecast_weather.to_parquet(forecast_weather_dataset_path, index=False)
    feature_dataset.to_parquet(feature_dataset_path, index=False)
    mapping.to_csv(mapping_path, index=False)
    audit_bundle["audit"].to_csv(audit_path, index=False)

    tcn = run_tcn_experiments(
        feature_dataset,
        config,
        output_dir=output_dir,
        model_subdir="stage7_tcn_models",
        window_sizes=[168],
        targets=["24h"],
        feature_set="weather_history_target_aligned",
        tcn_config_names=["regularized"],
        max_epochs=20,
        patience=4,
        batch_size=256,
    )

    metrics_path = output_dir / "stage7_tcn_metrics.csv"
    predictions_path = output_dir / "stage7_tcn_predictions.csv"
    tcn.metrics.to_csv(metrics_path, index=False)
    tcn.predictions.to_csv(predictions_path, index=False)

    stage5 = pd.read_csv(output_dir / "stage5_tuned_metrics.csv")
    stage6 = pd.read_csv(output_dir / "stage6_tcn_metrics.csv")
    stage7_best = (
        tcn.metrics[
            (tcn.metrics["target"] == "target_pv_power_t_plus_24h")
            & (tcn.metrics["split"] == "test")
        ]
        .sort_values(["nrmse_capacity", "daytime_nrmse_capacity"])
        .iloc[0]
    )
    stage5_best = (
        stage5[
            (stage5["target"] == "target_pv_power_t_plus_24h")
            & (stage5["split"] == "test")
        ]
        .sort_values(["nrmse_capacity", "daytime_nrmse_capacity"])
        .iloc[0]
    )
    stage6_best = (
        stage6[
            (stage6["target"] == "target_pv_power_t_plus_24h")
            & (stage6["split"] == "test")
        ]
        .sort_values(["nrmse_capacity", "daytime_nrmse_capacity"])
        .iloc[0]
    )

    gates = _quality_gates(feature_dataset, audit_bundle["audit"], tcn.predictions, capacity_kw)
    acceptance = {
        "nrmse_pass": bool(stage7_best["nrmse_capacity"] <= 0.1225),
        "daytime_nrmse_pass": bool(stage7_best["daytime_nrmse_capacity"] <= 0.1689),
        "quality_gates_pass": bool(all(gates.values())),
        "leakage_check_pass": bool(gates["forecast_issue_time_lte_prediction_time"]),
        "physical_bound_pass": bool(gates["test_predictions_within_physical_bound"]),
    }
    production_value = bool(all(acceptance.values()))
    report = {
        "stage": "stage7_forecast_weather_validation",
        "forecast_source_comparison": FORECAST_SOURCE_COMPARISON,
        "feature_mapping_summary": audit_bundle["summary"],
        "input_rows": int(len(stage3)),
        "forecast_weather_rows": int(len(forecast_weather)),
        "feature_dataset_rows": int(len(feature_dataset)),
        "quality_gates": gates,
        "acceptance": acceptance,
        "comparison": {
            "stage5_lightgbm": {
                "nrmse_capacity": float(stage5_best["nrmse_capacity"]),
                "daytime_nrmse_capacity": float(stage5_best["daytime_nrmse_capacity"]),
            },
            "stage6_tcn_upper_bound": {
                "nrmse_capacity": float(stage6_best["nrmse_capacity"]),
                "daytime_nrmse_capacity": float(stage6_best["daytime_nrmse_capacity"]),
            },
            "stage7_tcn": {
                "nrmse_capacity": float(stage7_best["nrmse_capacity"]),
                "daytime_nrmse_capacity": float(stage7_best["daytime_nrmse_capacity"]),
                "model_path": str(stage7_best["model_path"]),
            },
        },
        "decision": {
            "recommendation": "推进 TCN 生产建模" if production_value else "暂不推进 TCN 生产建模",
            "reason": (
                "真实预报替代后同时通过总体误差、日间误差、泄漏、质量和物理边界门槛。"
                if production_value
                else "至少一个硬门槛未通过；TCN 上限收益尚不能直接外推到真实预报上线场景。"
            ),
        },
        "artifacts": {
            "forecast_weather_dataset": str(forecast_weather_dataset_path),
            "feature_dataset": str(feature_dataset_path),
            "feature_mapping": str(mapping_path),
            "availability_audit": str(audit_path),
            "metrics": str(metrics_path),
            "predictions": str(predictions_path),
        },
    }

    report_json_path = output_dir / "stage7_forecast_validation_report.json"
    report_md_path = output_dir / "stage7_forecast_validation_report.md"
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _stage7_report_markdown(report, report_md_path)

    return Stage7Result(
        forecast_weather=forecast_weather,
        feature_dataset=feature_dataset,
        metrics=tcn.metrics,
        predictions=tcn.predictions,
        report=report,
    )
