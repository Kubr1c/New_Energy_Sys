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
    """Stage 7 forecast-weather validation artifacts."""

    forecast_weather: pd.DataFrame
    feature_dataset: pd.DataFrame
    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


FORECAST_SOURCE_COMPARISON = [
    {
        "source": "NOAA HRRR f24",
        "issue_time": "native forecast cycle",
        "valid_time": "native GRIB valid time",
        "lead_time": "native forecast hour f24",
        "status": "preferred production-grade route; local full-year extraction not yet available",
        "pitfall": "GRIB extraction is heavy; partial-cycle coverage will bias metrics if missing hours are silently filled.",
    },
    {
        "source": "Open-Meteo Historical Forecast f24",
        "issue_time": "valid_time - 24h, stored as explicit assumption",
        "valid_time": "hourly timestamp",
        "lead_time": "assumed 24h",
        "status": "selected executable Stage7 route because local 2022 site-matched data already exists",
        "pitfall": "Issue time is assumed by the simplified API export; it is weaker than native HRRR cycle metadata.",
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
    """Load a forecast-valid-time weather table with stable Stage3 names.

    Open-Meteo raw CSV and normalized parquet are both accepted. The returned
    table is one row per valid time, carrying the audit fields that prove the
    model only sees weather issued no later than the PV prediction timestamp.
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
    """Return every target-valid-time feature column currently present."""

    return [column for column in frame.columns if column.startswith("target_plus_")]


def _replace_target_plus_with_forecast(
    stage3: pd.DataFrame,
    forecast_weather: pd.DataFrame,
    *,
    horizons: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Replace NSRDB target-plus weather with forecast-issued weather.

    The input Stage3 table already contains labels, historical PV lags, dispatch
    fields, and current-time weather features. Only `target_plus_*` columns are
    rebuilt here. For a row at prediction timestamp `t`, each forecast feature
    is joined from weather valid at `t + horizon`. The leakage gate requires
    `weather_forecast_issue_time <= t`.
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
    """Compute the hard Stage7 gates used for the production-value decision."""

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
    """Write the Chinese Stage7 decision report."""

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
    """Execute Stage7 end-to-end with forecast-weather target-plus features."""

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
