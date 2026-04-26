from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CleaningResult:
    """第二阶段清洗结果。

    cleaned 保存物理约束、缺失值、异常值和时间范围处理后的数据；
    standardized 保存面向建模的标准化特征表；
    report 保存清洗前后质量指标，用于阶段验收和论文复现实验记录。
    """

    cleaned: pd.DataFrame
    standardized: pd.DataFrame
    report: dict[str, Any]


def _to_utc_timestamp(value: str, *, end_of_day: bool = False) -> pd.Timestamp:
    """把配置日期转成 UTC 时间戳。

    配置里一般只写日期，例如 2010-03-07。结束日期按闭区间理解，
    因此需要扩展到当天 23:59:59.999999，避免误删当天样本。
    """

    timestamp = pd.Timestamp(value, tz="UTC")
    if end_of_day and timestamp.time() == pd.Timestamp("00:00").time():
        timestamp = timestamp + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return timestamp


def _count_out_of_range(series: pd.Series, *, lower: float | None, upper: float | None) -> int:
    """统计超出物理边界的样本数。"""

    numeric = pd.to_numeric(series, errors="coerce")
    mask = pd.Series(False, index=numeric.index)
    if lower is not None:
        mask = mask | (numeric < lower)
    if upper is not None:
        mask = mask | (numeric > upper)
    return int(mask.sum())


def _apply_physical_bounds(frame: pd.DataFrame, capacity_kw: float) -> tuple[pd.DataFrame, dict[str, int]]:
    """按物理边界处理异常值。

    处理原则：
    - 功率和辐照度这类天然非负字段直接裁剪到合理范围；
    - 温度、风速、负荷、电价设置宽边界，避免误删真实极端样本；
    - 异常数量进入报告，保证清洗动作可追溯。
    """

    cleaned = frame.copy()
    bounds = {
        "pv_power_kw": (0.0, capacity_kw * 1.05),
        "pv_forecast_da_kw": (0.0, capacity_kw * 1.05),
        "pv_forecast_ha4_kw": (0.0, capacity_kw * 1.05),
        "ghi_wm2": (0.0, 1400.0),
        "dhi_wm2": (0.0, 1400.0),
        "dni_wm2": (0.0, 1400.0),
        "clearsky_ghi_wm2": (0.0, 1400.0),
        "clearsky_dhi_wm2": (0.0, 1400.0),
        "clearsky_dni_wm2": (0.0, 1400.0),
        "toa_radiation_wm2": (0.0, 1500.0),
        "temperature_c": (-50.0, 60.0),
        "dew_point_c": (-80.0, 40.0),
        "relative_humidity_pct": (0.0, 100.0),
        "wind_speed_ms": (0.0, 60.0),
        "wind_gusts_ms": (0.0, 80.0),
        "wind_direction_deg": (0.0, 360.0),
        "cloud_cover_pct": (0.0, 100.0),
        "cloud_cover_low_pct": (0.0, 100.0),
        "cloud_cover_mid_pct": (0.0, 100.0),
        "cloud_cover_high_pct": (0.0, 100.0),
        "pressure_hpa": (800.0, 1100.0),
        "surface_pressure_hpa": (800.0, 1100.0),
        "precipitation_mm": (0.0, 500.0),
        "solar_zenith_angle_deg": (0.0, 180.0),
        "surface_albedo": (0.0, 1.0),
        "precipitable_water_cm": (0.0, 15.0),
        "load_mw": (0.0, None),
        "price_eur_mwh": (-500.0, 5000.0),
        "storage_soc": (0.0, 1.0),
        "storage_charge_kw": (0.0, None),
        "storage_discharge_kw": (0.0, None),
    }

    anomaly_counts: dict[str, int] = {}
    for column, (lower, upper) in bounds.items():
        if column not in cleaned.columns:
            continue
        anomaly_counts[column] = _count_out_of_range(cleaned[column], lower=lower, upper=upper)
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce").clip(lower=lower, upper=upper)

    return cleaned, anomaly_counts


def _known_deviations(config: dict) -> list[str]:
    """根据当前配置生成真实的数据方案偏离说明。"""

    deviations: list[str] = []
    sources = config["sources"]
    pv_kind = sources.get("pv_power", {}).get("kind")
    weather_kind = sources.get("weather", {}).get("kind")
    opsd = sources.get("opsd", {})

    if pv_kind == "nrel_solar_zip":
        deviations.append("主数据源已切换为 NREL Solar Power Data for Integration Studies；该数据连续性强，但属于模拟PV场站数据，不是实测SCADA。")
    if pv_kind in {"pvdaq_s3_year", "pvdaq_s3_years"}:
        if weather_kind == "nsrdb":
            deviations.append("主数据源已切换为 OEDI/PVDAQ 站点级实测功率；天气字段来自 NSRDB PSM，按站点坐标和 UTC 时间对齐。")
        elif weather_kind == "open_meteo_historical_forecast":
            deviations.append("主数据源已切换为 OEDI/PVDAQ 站点级实测功率；天气字段来自 Open-Meteo Historical Forecast，按坐标和 UTC 时间对齐。")
        else:
            deviations.append("主数据源已切换为 OEDI/PVDAQ 站点级实测功率；天气字段来自外部坐标气象源，按 UTC 时间对齐。")
    if weather_kind == "disabled":
        deviations.append("当前NREL主链路未使用独立天气字段，使用DA/HA4功率预测作为预测特征。")
    if weather_kind == "nsrdb_then_open_meteo":
        deviations.append("当前使用NSRDB优先、Open-Meteo/ERA5兜底的外部气象补充数据；天气字段按站点坐标和UTC时间对齐。")
    if weather_kind == "nsrdb":
        deviations.append("当前使用NSRDB-only外部太阳辐照与气象数据；该链路不再使用Open-Meteo兜底。")
    if opsd.get("align") == "profile_to_pv_timeline":
        deviations.append("OPSD负荷/电价与NREL 2006时间轴年份不重叠，当前使用OPSD星期-小时画像映射到PV时间轴。")
    if opsd.get("kind") == "synthetic_market":
        deviations.append("当前未接入OPSD真实负荷/电价，使用合成市场曲线。")
    if weather_kind == "from_pv_power":
        deviations.append("当前未接入独立NSRDB/Open-Meteo天气，复用PV数据内置天气字段。")

    return deviations


def _stage_pitfall(config: dict, coverage: float) -> str:
    """生成当前数据清洗阶段最主要风险。"""

    opsd = config["sources"].get("opsd", {})
    if config["sources"].get("pv_power", {}).get("kind") in {"pvdaq_s3_year", "pvdaq_s3_years"} and opsd.get("align") == "profile_to_pv_timeline":
        return "OPSD 负荷/电价是画像映射，不是 PVDAQ 站点所在市场的真实同刻数据；经济性结论必须按调度仿真特征表述。"
    if coverage < 0.95:
        return "当前配置日期范围内目标小时覆盖率偏低，正式训练前应扩大连续窗口或更换连续站点。"
    if opsd.get("align") == "profile_to_pv_timeline":
        return "OPSD负荷/电价是画像映射，不是与2006年PV数据严格同年同刻的真实市场数据；经济性结论必须按仿真结果表述。"
    return "当前清洗质量满足建模入口要求，但后续训练/测试仍必须严格按时间顺序切分，禁止随机切分造成时间泄漏。"


def _fill_feature_missing_values(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """处理特征缺失值。

    目标字段 pv_power_kw 不填补，因为它是监督学习标签。特征先做最多 3
    个小时的前后填充，再用列中位数兜底。这样既保留短缺口连续性，又
    避免长时间缺口被无限传播。
    """

    cleaned = frame.copy()
    feature_columns = [column for column in cleaned.columns if column not in {"timestamp", "pv_power_kw"}]
    before = cleaned[feature_columns].isna().sum()

    cleaned[feature_columns] = cleaned[feature_columns].ffill(limit=3).bfill(limit=3)

    after_limited_fill = cleaned[feature_columns].isna().sum()
    for column in feature_columns:
        if pd.api.types.is_numeric_dtype(cleaned[column]) and cleaned[column].isna().any():
            median = cleaned[column].median()
            cleaned[column] = cleaned[column].fillna(0.0 if pd.isna(median) else median)

    after = cleaned[feature_columns].isna().sum()
    missing_report: dict[str, dict[str, int]] = {}
    for column in feature_columns:
        missing_report[column] = {
            "before": int(before[column]),
            "after_limited_fill": int(after_limited_fill[column]),
            "after_final_fill": int(after[column]),
        }
    return cleaned, missing_report


def _standardize_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    """生成 z-score 标准化特征列。

    原始业务字段全部保留，新增 *_z 列供模型直接使用。均值和标准差写入
    报告，后续推理阶段必须复用同一组参数，不能用测试集重新拟合。
    """

    standardized = frame.copy()
    exclude = {
        "timestamp",
        "pv_power_kw",
        "hour",
        "day_of_week",
        "month",
        "storage_revenue_eur",
    }
    scaler: dict[str, dict[str, float]] = {}
    for column in standardized.select_dtypes(include=[np.number]).columns:
        if column in exclude:
            continue
        mean = float(standardized[column].mean())
        std = float(standardized[column].std(ddof=0))
        if std == 0.0 or np.isnan(std):
            standardized[f"{column}_z"] = 0.0
        else:
            standardized[f"{column}_z"] = (standardized[column] - mean) / std
        scaler[column] = {"mean": mean, "std": std}
    return standardized, scaler


def clean_stage_two_dataset(frame: pd.DataFrame, config: dict) -> CleaningResult:
    """执行第二阶段：缺失值、异常值、时间对齐、重采样后清洗和标准化。"""

    site = config["site"]
    date_range = config["date_range"]
    capacity_kw = float(site["capacity_kw"])
    start = _to_utc_timestamp(date_range["start"])
    end = _to_utc_timestamp(date_range["end"], end_of_day=True)

    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)

    initial_rows = len(working)
    invalid_timestamp_rows = int(working["timestamp"].isna().sum())
    duplicate_rows = int(working.duplicated(subset=["timestamp"]).sum())

    working = working.dropna(subset=["timestamp"])
    working = working.drop_duplicates(subset=["timestamp"], keep="first")
    working = working[(working["timestamp"] >= start) & (working["timestamp"] <= end)].copy()
    after_date_filter_rows = len(working)

    # 当前输入已由第一阶段重采样为小时级。这里再次 floor 到小时并去重，
    # 防止后续更换数据源时出现分钟/秒级时间戳进入第二阶段。
    working["timestamp"] = working["timestamp"].dt.floor("h")
    working = working.drop_duplicates(subset=["timestamp"], keep="first").sort_values("timestamp")

    expected_hours = int(((end.floor("h") - start.floor("h")) / pd.Timedelta(hours=1)) + 1)
    observed_hours = int(working["timestamp"].nunique())

    missing_target_rows = int(working["pv_power_kw"].isna().sum())
    working = working.dropna(subset=["pv_power_kw"]).copy()

    bounded, anomaly_counts = _apply_physical_bounds(working, capacity_kw)
    filled, missing_report = _fill_feature_missing_values(bounded)

    filled["hour"] = filled["timestamp"].dt.hour
    filled["day_of_week"] = filled["timestamp"].dt.dayofweek
    filled["month"] = filled["timestamp"].dt.month
    filled = filled.sort_values("timestamp").reset_index(drop=True)

    standardized, scaler = _standardize_features(filled)

    target_hour_coverage = round(observed_hours / expected_hours, 6) if expected_hours else 0.0
    known_deviations = _known_deviations(config)
    if config["sources"].get("pv_power", {}).get("kind") in {"pvdaq_s3_year", "pvdaq_s3_years"}:
        known_deviations = [
            deviation
            for deviation in known_deviations
            if "NREL 2006" not in deviation
        ]
    report = {
        "stage": "stage_2_data_cleaning",
        "date_range": {"start": str(start), "end": str(end)},
        "rows": {
            "initial": initial_rows,
            "invalid_timestamp": invalid_timestamp_rows,
            "duplicates": duplicate_rows,
            "after_date_filter": after_date_filter_rows,
            "missing_target_removed": missing_target_rows,
            "final_cleaned": len(filled),
        },
        "time_alignment": {
            "frequency": "1h",
            "expected_hours_in_config_range": expected_hours,
            "observed_target_hours": observed_hours,
            "target_hour_coverage": target_hour_coverage,
            "min_timestamp": str(filled["timestamp"].min()) if not filled.empty else None,
            "max_timestamp": str(filled["timestamp"].max()) if not filled.empty else None,
        },
        "missing_values": missing_report,
        "anomalies_before_clipping": anomaly_counts,
        "standardization": scaler,
        "quality_gates": {
            "no_missing_values": bool(filled.isna().sum().sum() == 0),
            "pv_power_within_capacity_bound": bool((filled["pv_power_kw"].between(0, capacity_kw * 1.05)).all()),
            "storage_soc_within_physical_bound": bool((filled["storage_soc"].between(0, 1)).all())
            if "storage_soc" in filled.columns
            else None,
            "monotonic_timestamp": bool(filled["timestamp"].is_monotonic_increasing),
        },
        "known_deviation_from_original_plan": known_deviations,
        "pitfall": _stage_pitfall(config, target_hour_coverage),
    }
    return CleaningResult(cleaned=filled, standardized=standardized, report=report)


def write_quality_report(report: dict[str, Any], path: Path) -> None:
    """写出 Markdown 质量报告，便于直接放入阶段验收材料。"""

    gates = report["quality_gates"]
    rows = report["rows"]
    time = report["time_alignment"]
    anomalies = report["anomalies_before_clipping"]

    lines = [
        "# Stage 2 Data Cleaning Quality Report",
        "",
        "## Scope",
        "",
        f"- Date range: `{report['date_range']['start']}` to `{report['date_range']['end']}`",
        "- Frequency: `1h`",
        "",
        "## Row Quality",
        "",
        f"- Initial rows: `{rows['initial']}`",
        f"- Invalid timestamp rows: `{rows['invalid_timestamp']}`",
        f"- Duplicate timestamp rows: `{rows['duplicates']}`",
        f"- Rows after date filter: `{rows['after_date_filter']}`",
        f"- Missing target rows removed: `{rows['missing_target_removed']}`",
        f"- Final cleaned rows: `{rows['final_cleaned']}`",
        "",
        "## Time Alignment",
        "",
        f"- Expected hours in configured range: `{time['expected_hours_in_config_range']}`",
        f"- Observed target hours: `{time['observed_target_hours']}`",
        f"- Target hour coverage: `{time['target_hour_coverage']}`",
        f"- Min timestamp: `{time['min_timestamp']}`",
        f"- Max timestamp: `{time['max_timestamp']}`",
        "",
        "## Quality Gates",
        "",
    ]
    for gate, passed in gates.items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## Anomalies Before Clipping", ""])
    for column, count in anomalies.items():
        lines.append(f"- {column}: `{count}`")

    lines.extend(["", "## Known Deviations", ""])
    for deviation in report["known_deviation_from_original_plan"]:
        lines.append(f"- {deviation}")

    lines.extend(
        [
            "",
            "## Pitfall",
            "",
            report["pitfall"],
        ]
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
