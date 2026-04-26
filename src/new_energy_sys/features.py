from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FeatureEngineeringResult:
    """第三阶段特征工程结果容器。

    dataset 保存模型可直接读取的完整特征表；report 保存构造逻辑、字段规模、
    质量门禁和潜在风险，避免后续建模阶段只能依赖人工记忆判断数据是否可用。
    """

    dataset: pd.DataFrame
    report: dict[str, Any]


def _require_columns(frame: pd.DataFrame, required: set[str]) -> None:
    """校验输入数据的最小字段集合。

    特征工程依赖阶段二的清洗结果。这里显式失败，而不是静默生成半成品特征，
    可以防止后续训练阶段把缺字段问题误判为模型效果问题。
    """

    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"阶段三输入缺少必要字段: {', '.join(missing)}")


def _cyclic_encode(values: pd.Series, period: float) -> tuple[pd.Series, pd.Series]:
    """把周期型整数编码为 sin/cos。

    小时、星期、月份、年内日这类字段存在首尾相邻关系，例如 23 点和 0 点相邻。
    直接把它们作为整数会制造虚假的距离；sin/cos 编码能保留周期结构。
    """

    radians = 2.0 * np.pi * values.astype(float) / period
    return np.sin(radians), np.cos(radians)


def _safe_divide(numerator: pd.Series, denominator: float | pd.Series) -> pd.Series:
    """执行带零值保护的除法。

    生产数据中容量、预测功率或价格可能出现零值。统一用 NaN 承接非法除法，
    再由后续质量检查决定是否填充或剔除，避免 inf 混入训练矩阵。
    """

    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def _add_time_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """构造时间特征。

    设计原则：
    - 保留原始 hour/day_of_week/month，便于规则模型或可解释性分析；
    - 新增周期编码，供线性模型、树模型和神经网络稳定捕捉日周期/周周期/季节性；
    - 新增工作日、周末、季度、年内日，辅助负荷、电价和太阳高度季节模式建模。
    """

    enriched = frame.copy()
    timestamp = enriched["timestamp"]

    enriched["hour"] = timestamp.dt.hour
    enriched["day_of_week"] = timestamp.dt.dayofweek
    enriched["month"] = timestamp.dt.month
    enriched["day_of_year"] = timestamp.dt.dayofyear
    enriched["quarter"] = timestamp.dt.quarter
    enriched["is_weekend"] = (enriched["day_of_week"] >= 5).astype("int8")
    enriched["is_business_hour"] = enriched["hour"].between(8, 18).astype("int8")

    enriched["hour_sin"], enriched["hour_cos"] = _cyclic_encode(enriched["hour"], 24.0)
    enriched["day_of_week_sin"], enriched["day_of_week_cos"] = _cyclic_encode(enriched["day_of_week"], 7.0)
    enriched["month_sin"], enriched["month_cos"] = _cyclic_encode(enriched["month"] - 1, 12.0)
    enriched["day_of_year_sin"], enriched["day_of_year_cos"] = _cyclic_encode(enriched["day_of_year"] - 1, 365.0)

    columns = [
        "day_of_year",
        "quarter",
        "is_weekend",
        "is_business_hour",
        "hour_sin",
        "hour_cos",
        "day_of_week_sin",
        "day_of_week_cos",
        "month_sin",
        "month_cos",
        "day_of_year_sin",
        "day_of_year_cos",
    ]
    return enriched, columns


def _add_weather_features(frame: pd.DataFrame, capacity_kw: float) -> tuple[pd.DataFrame, list[str], str]:
    """构造天气相关特征。

    当前主链路使用 NREL 太阳能集成数据。该数据没有独立气象字段进入阶段二，
    但包含 DA/HA4 光伏预测功率。预测功率本质上已经吸收了辐照、云量等天气预报
    信息，因此这里把它们作为“天气代理特征”。如果后续接入 GHI/DNI/温度/风速，
    本函数会自动把真实气象字段纳入派生特征。
    """

    enriched = frame.copy()
    columns: list[str] = []
    weather_mode = "forecast_proxy"

    forecast_columns = [column for column in ["pv_forecast_da_kw", "pv_forecast_ha4_kw"] if column in enriched.columns]
    for column in forecast_columns:
        ratio_column = f"{column}_capacity_ratio"
        ramp_column = f"{column}_ramp_1h"
        daylight_column = f"{column}_daylight_flag"

        enriched[ratio_column] = _safe_divide(enriched[column], capacity_kw).clip(lower=0.0, upper=1.2)
        enriched[ramp_column] = enriched[column].diff()
        enriched[daylight_column] = (enriched[column] > capacity_kw * 0.01).astype("int8")
        columns.extend([ratio_column, ramp_column, daylight_column])

    if {"pv_forecast_da_kw", "pv_forecast_ha4_kw"}.issubset(enriched.columns):
        enriched["forecast_spread_kw"] = enriched["pv_forecast_ha4_kw"] - enriched["pv_forecast_da_kw"]
        enriched["forecast_spread_abs_kw"] = enriched["forecast_spread_kw"].abs()
        enriched["forecast_mean_kw"] = enriched[["pv_forecast_da_kw", "pv_forecast_ha4_kw"]].mean(axis=1)
        enriched["forecast_mean_capacity_ratio"] = _safe_divide(enriched["forecast_mean_kw"], capacity_kw).clip(
            lower=0.0,
            upper=1.2,
        )
        columns.extend(
            [
                "forecast_spread_kw",
                "forecast_spread_abs_kw",
                "forecast_mean_kw",
                "forecast_mean_capacity_ratio",
            ]
        )

    real_weather_candidates = [
        "ghi_wm2",
        "dhi_wm2",
        "dni_wm2",
        "clearsky_ghi_wm2",
        "clearsky_dhi_wm2",
        "clearsky_dni_wm2",
        "toa_radiation_wm2",
        "temperature_c",
        "dew_point_c",
        "relative_humidity_pct",
        "wind_speed_ms",
        "wind_gusts_ms",
        "wind_direction_deg",
        "cloud_cover_pct",
        "cloud_cover_low_pct",
        "cloud_cover_mid_pct",
        "cloud_cover_high_pct",
        "pressure_hpa",
        "surface_pressure_hpa",
        "precipitation_mm",
        "solar_zenith_angle_deg",
        "surface_albedo",
        "precipitable_water_cm",
        "weather_forecast_lead_time_hour",
    ]
    real_weather_columns = [column for column in real_weather_candidates if column in enriched.columns]
    if real_weather_columns:
        weather_mode = (
            "forecast_weather_plus_forecast_proxy"
            if "weather_forecast_lead_time_hour" in real_weather_columns
            else "observed_weather_plus_forecast_proxy"
        )
        for column in real_weather_columns:
            normalized_column = f"{column}_normalized"
            rolling_column = f"{column}_roll_24h_mean"
            enriched[normalized_column] = _safe_divide(enriched[column], enriched[column].max())
            enriched[rolling_column] = enriched[column].shift(1).rolling(window=24, min_periods=6).mean()
            columns.extend([normalized_column, rolling_column])

        if {"ghi_wm2", "clearsky_ghi_wm2"}.issubset(enriched.columns):
            # Clear-sky index is physically meaningful only when the clear-sky
            # irradiance denominator is positive. NSRDB correctly reports both
            # GHI and clear-sky GHI as 0 at night; treating 0/0 as missing would
            # delete most night samples during the final supervised-row filter.
            # Set non-solar periods to 0 and keep daytime ratios bounded.
            clearsky_denominator = enriched["clearsky_ghi_wm2"].where(enriched["clearsky_ghi_wm2"] > 0.0)
            enriched["clearsky_index_ghi"] = (
                _safe_divide(enriched["ghi_wm2"], clearsky_denominator)
                .clip(lower=0.0, upper=2.0)
                .fillna(0.0)
            )
            columns.append("clearsky_index_ghi")
        if {"temperature_c", "dew_point_c"}.issubset(enriched.columns):
            enriched["temperature_dew_point_spread_c"] = enriched["temperature_c"] - enriched["dew_point_c"]
            columns.append("temperature_dew_point_spread_c")
        if {"ghi_wm2", "toa_radiation_wm2"}.issubset(enriched.columns):
            # 夜间大气顶辐射为 0，GHI 也通常为 0。此时透过率没有物理意义，
            # 但不能产生 NaN 进而删除夜间样本；统一置 0，表示无有效太阳入射。
            daylight_denominator = enriched["toa_radiation_wm2"].where(enriched["toa_radiation_wm2"] > 0.0)
            enriched["atmospheric_transmittance_proxy"] = (
                _safe_divide(enriched["ghi_wm2"], daylight_denominator)
                .clip(lower=0.0, upper=2.0)
                .fillna(0.0)
            )
            columns.append("atmospheric_transmittance_proxy")

    return enriched, columns, weather_mode


def _add_historical_power_features(frame: pd.DataFrame, capacity_kw: float) -> tuple[pd.DataFrame, list[str]]:
    """构造历史功率特征。

    所有历史功率特征都必须 shift 后再 rolling，保证 t 时刻特征只使用 t 之前的
    已知真实功率。这里严禁直接 rolling 当前行，否则会把标签泄漏给模型。
    """

    enriched = frame.copy()
    columns: list[str] = []

    previous_power = enriched["pv_power_kw"].shift(1)
    for lag in [1, 2, 3, 6, 12, 24, 48, 168]:
        column = f"pv_power_lag_{lag}h"
        enriched[column] = enriched["pv_power_kw"].shift(lag)
        columns.append(column)

    for window in [3, 6, 12, 24, 48, 168]:
        mean_column = f"pv_power_roll_{window}h_mean"
        std_column = f"pv_power_roll_{window}h_std"
        max_column = f"pv_power_roll_{window}h_max"
        min_column = f"pv_power_roll_{window}h_min"

        rolling = previous_power.rolling(window=window, min_periods=max(2, window // 3))
        enriched[mean_column] = rolling.mean()
        enriched[std_column] = rolling.std(ddof=0)
        enriched[max_column] = rolling.max()
        enriched[min_column] = rolling.min()
        columns.extend([mean_column, std_column, max_column, min_column])

    enriched["pv_power_capacity_ratio_lag_1h"] = _safe_divide(enriched["pv_power_lag_1h"], capacity_kw).clip(
        lower=0.0,
        upper=1.2,
    )
    enriched["pv_power_ramp_lag_1h"] = enriched["pv_power_kw"].shift(1) - enriched["pv_power_kw"].shift(2)
    enriched["pv_power_ramp_abs_lag_1h"] = enriched["pv_power_ramp_lag_1h"].abs()
    columns.extend(["pv_power_capacity_ratio_lag_1h", "pv_power_ramp_lag_1h", "pv_power_ramp_abs_lag_1h"])

    if "pv_forecast_da_kw" in enriched.columns:
        enriched["da_forecast_error_lag_1h"] = (enriched["pv_power_kw"] - enriched["pv_forecast_da_kw"]).shift(1)
        enriched["da_forecast_error_lag_24h"] = (enriched["pv_power_kw"] - enriched["pv_forecast_da_kw"]).shift(24)
        columns.extend(["da_forecast_error_lag_1h", "da_forecast_error_lag_24h"])

    if "pv_forecast_ha4_kw" in enriched.columns:
        enriched["ha4_forecast_error_lag_1h"] = (enriched["pv_power_kw"] - enriched["pv_forecast_ha4_kw"]).shift(1)
        enriched["ha4_forecast_error_lag_24h"] = (enriched["pv_power_kw"] - enriched["pv_forecast_ha4_kw"]).shift(24)
        columns.extend(["ha4_forecast_error_lag_1h", "ha4_forecast_error_lag_24h"])

    return enriched, columns


def _add_target_aligned_weather_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Build future-valid-time weather features for medium-horizon PV targets.

    These columns align exogenous weather and solar-geometry signals with the
    prediction target timestamp. For example, at row `t`,
    `target_plus_24h_ghi_wm2` contains the GHI valid at `t+24h`, matching
    `target_pv_power_t_plus_24h`.
    Production use requires a real weather forecast issued no later than `t`.
    With NSRDB historical weather this is an offline upper-bound experiment,
    not a deployable online feature by itself.
    """

    enriched = frame.copy()
    columns: list[str] = []
    weather_columns = [
        "ghi_wm2",
        "dhi_wm2",
        "dni_wm2",
        "clearsky_ghi_wm2",
        "clearsky_dhi_wm2",
        "clearsky_dni_wm2",
        "temperature_c",
        "dew_point_c",
        "relative_humidity_pct",
        "wind_speed_ms",
        "wind_direction_deg",
        "pressure_hpa",
        "solar_zenith_angle_deg",
        "surface_albedo",
        "precipitable_water_cm",
        "clearsky_index_ghi",
        "temperature_dew_point_spread_c",
    ]
    available_weather_columns = [column for column in weather_columns if column in enriched.columns]

    for horizon in [6, 24]:
        target_timestamp = enriched["timestamp"] + pd.to_timedelta(horizon, unit="h")
        enriched[f"target_plus_{horizon}h_hour_sin"], enriched[f"target_plus_{horizon}h_hour_cos"] = _cyclic_encode(
            target_timestamp.dt.hour,
            24.0,
        )
        enriched[f"target_plus_{horizon}h_day_of_year_sin"], enriched[f"target_plus_{horizon}h_day_of_year_cos"] = _cyclic_encode(
            target_timestamp.dt.dayofyear - 1,
            365.0,
        )
        columns.extend(
            [
                f"target_plus_{horizon}h_hour_sin",
                f"target_plus_{horizon}h_hour_cos",
                f"target_plus_{horizon}h_day_of_year_sin",
                f"target_plus_{horizon}h_day_of_year_cos",
            ]
        )

        for column in available_weather_columns:
            aligned_column = f"target_plus_{horizon}h_{column}"
            enriched[aligned_column] = enriched[column].shift(-horizon)
            columns.append(aligned_column)

    return enriched, columns


def _add_dispatch_features(frame: pd.DataFrame, storage_config: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    """构造储能调度特征。

    这些字段把规则调度结果转换为模型可学习的状态量：SOC、净出力、可充/可放空间、
    电价阈值距离和历史调度滚动统计。后续若替换为优化调度器，字段语义仍可保持稳定。
    """

    enriched = frame.copy()
    columns: list[str] = []

    capacity_kwh = float(storage_config["capacity_kwh"])
    max_charge_kw = float(storage_config["max_charge_kw"])
    max_discharge_kw = float(storage_config["max_discharge_kw"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    charge_threshold = float(storage_config["charge_price_threshold"])
    discharge_threshold = float(storage_config["discharge_price_threshold"])

    if {"storage_charge_kw", "storage_discharge_kw"}.issubset(enriched.columns):
        enriched["storage_net_discharge_kw"] = enriched["storage_discharge_kw"] - enriched["storage_charge_kw"]
        enriched["storage_charge_ratio"] = _safe_divide(enriched["storage_charge_kw"], max_charge_kw).clip(0.0, 1.0)
        enriched["storage_discharge_ratio"] = _safe_divide(enriched["storage_discharge_kw"], max_discharge_kw).clip(0.0, 1.0)
        enriched["storage_dispatch_mode"] = np.select(
            [
                enriched["storage_charge_kw"] > 0.0,
                enriched["storage_discharge_kw"] > 0.0,
            ],
            [-1, 1],
            default=0,
        ).astype("int8")
        columns.extend(
            [
                "storage_net_discharge_kw",
                "storage_charge_ratio",
                "storage_discharge_ratio",
                "storage_dispatch_mode",
            ]
        )

    if "storage_soc" in enriched.columns:
        enriched["storage_energy_kwh"] = enriched["storage_soc"] * capacity_kwh
        enriched["storage_headroom_charge_kwh"] = (soc_max - enriched["storage_soc"]).clip(lower=0.0) * capacity_kwh
        enriched["storage_headroom_discharge_kwh"] = (enriched["storage_soc"] - soc_min).clip(lower=0.0) * capacity_kwh
        enriched["storage_soc_lag_1h"] = enriched["storage_soc"].shift(1)
        enriched["storage_soc_roll_24h_mean"] = enriched["storage_soc"].shift(1).rolling(window=24, min_periods=6).mean()
        columns.extend(
            [
                "storage_energy_kwh",
                "storage_headroom_charge_kwh",
                "storage_headroom_discharge_kwh",
                "storage_soc_lag_1h",
                "storage_soc_roll_24h_mean",
            ]
        )

    if "price_eur_mwh" in enriched.columns:
        enriched["price_charge_margin"] = charge_threshold - enriched["price_eur_mwh"]
        enriched["price_discharge_margin"] = enriched["price_eur_mwh"] - discharge_threshold
        enriched["price_roll_24h_mean"] = enriched["price_eur_mwh"].shift(1).rolling(window=24, min_periods=6).mean()
        enriched["price_roll_24h_std"] = enriched["price_eur_mwh"].shift(1).rolling(window=24, min_periods=6).std(ddof=0)
        columns.extend(["price_charge_margin", "price_discharge_margin", "price_roll_24h_mean", "price_roll_24h_std"])

    if "load_mw" in enriched.columns:
        enriched["load_roll_24h_mean"] = enriched["load_mw"].shift(1).rolling(window=24, min_periods=6).mean()
        enriched["load_ramp_lag_1h"] = enriched["load_mw"].shift(1) - enriched["load_mw"].shift(2)
        columns.extend(["load_roll_24h_mean", "load_ramp_lag_1h"])

    return enriched, columns


def _add_target_horizons(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """构造多步预测标签。

    第三阶段的主目标仍是特征工程，但提前生成 1h/6h/24h 标签可以让第四阶段
    直接比较短期、日内和日前预测任务。标签使用负 shift，只在建模切分后作为 y 使用。
    """

    enriched = frame.copy()
    columns: list[str] = []
    for horizon in [1, 6, 24]:
        column = f"target_pv_power_t_plus_{horizon}h"
        enriched[column] = enriched["pv_power_kw"].shift(-horizon)
        columns.append(column)
    return enriched, columns


def _fill_engineered_missing_values(frame: pd.DataFrame, feature_columns: list[str], target_columns: list[str]) -> tuple[pd.DataFrame, dict[str, int]]:
    """处理特征工程引入的空值。

    lag/rolling 会在序列开头产生空值，未来标签会在序列尾部产生空值。这些行无法
    形成完整监督样本，直接剔除比插值更稳健，因为插值会污染时间因果关系。
    """

    required_columns = ["timestamp", "pv_power_kw", *feature_columns, *target_columns]
    missing_before = frame[required_columns].isna().sum()
    cleaned = frame.dropna(subset=feature_columns + target_columns).copy()
    missing_after = cleaned[required_columns].isna().sum()

    return cleaned.reset_index(drop=True), {
        "rows_removed_by_lag_or_future_target": int(len(frame) - len(cleaned)),
        "missing_cells_before_drop": int(missing_before.sum()),
        "missing_cells_after_drop": int(missing_after.sum()),
    }


def _chronological_split(frame: pd.DataFrame) -> dict[str, Any]:
    """生成严格按时间排序的训练/验证/测试切分说明。

    本函数只输出索引和时间范围，不物理拆分文件。第四阶段训练时必须复用该顺序，
    禁止随机切分，避免未来信息进入训练集。
    """

    row_count = len(frame)
    train_end = int(row_count * 0.70)
    valid_end = int(row_count * 0.85)

    splits = {
        "train": frame.iloc[:train_end],
        "validation": frame.iloc[train_end:valid_end],
        "test": frame.iloc[valid_end:],
    }

    return {
        name: {
            "rows": int(len(split)),
            "start": str(split["timestamp"].min()) if not split.empty else None,
            "end": str(split["timestamp"].max()) if not split.empty else None,
        }
        for name, split in splits.items()
    }


def build_stage_three_features(frame: pd.DataFrame, config: dict[str, Any]) -> FeatureEngineeringResult:
    """执行第三阶段：时间、天气代理、历史功率和调度特征构造。"""

    required = {"timestamp", "pv_power_kw", "storage_soc", "storage_charge_kw", "storage_discharge_kw"}
    _require_columns(frame, required)

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"], keep="first")
    working = working.sort_values("timestamp").reset_index(drop=True)

    initial_columns = set(working.columns)
    feature_groups: dict[str, list[str]] = {}

    working, feature_groups["time_features"] = _add_time_features(working)
    working, feature_groups["weather_features"], weather_mode = _add_weather_features(working, capacity_kw)
    working, feature_groups["target_aligned_weather_features"] = _add_target_aligned_weather_features(working)
    working, feature_groups["historical_power_features"] = _add_historical_power_features(working, capacity_kw)
    working, feature_groups["dispatch_features"] = _add_dispatch_features(working, config["storage"])
    working, target_columns = _add_target_horizons(working)

    engineered_feature_columns = [
        column
        for group_columns in feature_groups.values()
        for column in group_columns
        if column in working.columns and column not in initial_columns
    ]

    feature_dataset, missing_report = _fill_engineered_missing_values(working, engineered_feature_columns, target_columns)

    duplicate_columns = feature_dataset.columns[feature_dataset.columns.duplicated()].tolist()
    numeric_features = feature_dataset[engineered_feature_columns].select_dtypes(include=[np.number])

    pitfall = (
        "当前已接入外部气象补充数据，但其来源与NREL功率数据不是同一原始生成链路。"
        "论文中应表述为按坐标和UTC时间对齐的气象补充特征，不能写成电站实测气象。"
        if weather_mode == "observed_weather_plus_forecast_proxy"
        else (
            "当前没有独立实测天气字段，天气特征主要由 DA/HA4 预测功率代理。"
            "后续论文表述必须写成天气代理特征，不能声称已接入真实气象观测。"
        )
    )

    if weather_mode == "forecast_weather_plus_forecast_proxy":
        pitfall = (
            "当前已接入预报型天气字段，并按 UTC valid time 与 PVDAQ 功率对齐；"
            "但 Open-Meteo 简化接口的 forecast issue time 为显式 lead-time 假设，"
            "后续若写成严格数值天气预报循环实验，应改用 HRRR 等带原生 cycle/lead_time 的数据。"
        )
    if feature_groups["target_aligned_weather_features"]:
        pitfall = (
            "已生成 target_plus_* 目标时刻天气/太阳几何特征。当前 NSRDB 是历史再分析/观测对齐数据，"
            "只能用于评估“若目标时刻天气预报可用”的离线上限；生产推理必须改接真实预报 issue time/lead time。"
        )

    report = {
        "stage": "stage_3_feature_engineering",
        "input_rows": int(len(frame)),
        "output_rows": int(len(feature_dataset)),
        "input_columns": int(len(frame.columns)),
        "output_columns": int(len(feature_dataset.columns)),
        "engineered_feature_count": int(len(engineered_feature_columns)),
        "target_columns": target_columns,
        "feature_groups": {group: sorted(columns) for group, columns in feature_groups.items()},
        "weather_feature_mode": weather_mode,
        "missing_value_handling": missing_report,
        "chronological_split": _chronological_split(feature_dataset),
        "quality_gates": {
            "no_duplicate_columns": bool(not duplicate_columns),
            "timestamp_monotonic": bool(feature_dataset["timestamp"].is_monotonic_increasing),
            "no_missing_engineered_features": bool(feature_dataset[engineered_feature_columns].isna().sum().sum() == 0),
            "no_infinite_numeric_features": bool(np.isfinite(numeric_features.to_numpy()).all()),
            "minimum_rows_for_baseline_modeling": bool(len(feature_dataset) >= 8000),
        },
        "duplicate_columns": duplicate_columns,
        "pitfall": pitfall,
    }
    return FeatureEngineeringResult(dataset=feature_dataset, report=report)


def write_feature_report(report: dict[str, Any], path: Path) -> None:
    """写出第三阶段 Markdown 报告。"""

    gates = report["quality_gates"]
    splits = report["chronological_split"]

    lines = [
        "# 第三阶段特征工程报告",
        "",
        "## 范围",
        "",
        "- Stage: `特征工程`",
        f"- 输入行数: `{report['input_rows']}`",
        f"- 输出行数: `{report['output_rows']}`",
        f"- 输入列数: `{report['input_columns']}`",
        f"- 输出列数: `{report['output_columns']}`",
        f"- 派生特征数: `{report['engineered_feature_count']}`",
        f"- 天气特征模式: `{report['weather_feature_mode']}`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["第二阶段清洗后小时数据"] --> B["时间周期特征"]',
        '    B --> C["天气与天气代理特征"]',
        '    C --> D["目标时刻天气对齐特征"]',
        '    D --> E["历史光伏滞后与滚动特征"]',
        '    E --> F["储能调度状态特征"]',
        '    F --> G["未来预测目标"]',
        '    G --> H["第三阶段建模数据集"]',
        "```",
        "",
        "## 特征组",
        "",
    ]

    for group, columns in report["feature_groups"].items():
        lines.append(f"### {group}")
        lines.append("")
        for column in columns:
            lines.append(f"- `{column}`")
        lines.append("")

    lines.extend(["## 目标列", ""])
    for column in report["target_columns"]:
        lines.append(f"- `{column}`")

    lines.extend(["", "## 缺失值处理", ""])
    for key, value in report["missing_value_handling"].items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(["", "## 时间切分", ""])
    for name, split in splits.items():
        lines.append(f"- {name}: `{split['rows']}` rows, `{split['start']}` to `{split['end']}`")

    lines.extend(["", "## 质量门禁", ""])
    for gate, passed in gates.items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## 潜在坑点", "", report["pitfall"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
