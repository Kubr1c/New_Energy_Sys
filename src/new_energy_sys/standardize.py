"""数据标准化模块。

本模块负责将不同来源的原始数据文件统一为项目内部的标准 schema，
是数据采集（data_sources.py）与下游特征工程/建模之间的桥梁。

模块设计原则：
  - schema 稳定：所有输出 DataFrame 使用统一列名（timestamp、pv_power_kw 等），
    下游特征工程和建模代码不依赖任何特定数据源的原始列名。
  - 配置驱动：列名候选列表和单位换算规则来自 JSON 配置，而非硬编码。
  - 物理护栏：光伏功率裁剪到 [0, 装机容量×1.2] 区间，防止异常值污染训练集。
  - 丢行透明：所有 dropna 操作均在标准化阶段完成，而非在下游静默丢弃。

本模块对应项目 Stage 2 的数据标准化功能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
import zipfile

import numpy as np
import pandas as pd


def _first_existing_column(columns: Iterable[str], candidates: list[str], label: str) -> str:
    """在源表列名中查找第一个匹配的候选列名。

    不同数据源的列名各不相同，配置中声明一组候选名称，
    此函数返回第一个在源表中出现的候选列。

    Args:
        columns: 源表的所有列名。
        candidates: 按优先级排列的候选列名列表。
        label: 列用途的中文标签，仅用于错误提示。

    Returns:
        第一个在源表中存在的候选列名。

    Raises:
        ValueError: 所有候选列均未找到时抛出。
    """

    column_set = set(columns)
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    raise ValueError(f"无法识别{label}字段，候选字段: {', '.join(candidates)}")


def _read_csv(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    """读取 CSV 文件，可选限制行数以应对大型公开数据集。

    Args:
        path: CSV 文件路径。
        max_rows: 最大读取行数，None 表示不限制。

    Returns:
        读取得到的 DataFrame。
    """

    return pd.read_csv(path, nrows=max_rows, low_memory=False)


def normalize_pv_power(path: Path, source_config: dict, capacity_kw: float) -> pd.DataFrame:
    """将光伏功率数据标准化为 timestamp + pv_power_kw 的统一 schema。

    公开光伏数据集的 schema 不稳定：时间戳和功率列名因系统而异。
    因此配置中声明候选列名，此函数取第一个可用的列。
    功率单位根据配置自动换算为 kW，并裁剪到物理合理范围。

    Args:
        path: 原始 CSV 文件路径。
        source_config: 该数据源的配置字典，须包含
            timestamp_candidates、power_candidates，可选 power_unit、max_rows。
        capacity_kw: 装机容量（kW），用于物理护栏的上界裁剪。

    Returns:
        包含 timestamp（UTC）和 pv_power_kw 两列的 DataFrame。
    """

    frame = _read_csv(path, source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        source_config["timestamp_candidates"],
        "光伏时间戳",
    )
    power_col = _first_existing_column(
        frame.columns,
        source_config["power_candidates"],
        "光伏功率",
    )

    power = pd.to_numeric(frame[power_col], errors="coerce")
    power_unit = source_config.get("power_unit", "kW").lower()
    if power_unit in {"w", "watt", "watts"}:
        power = power / 1000.0
    elif power_unit not in {"kw", "kilowatt", "kilowatts"}:
        raise ValueError(f"Unsupported PV power unit: {source_config.get('power_unit')}")

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
            "pv_power_kw": power,
        }
    )
    output = output.dropna(subset=["timestamp", "pv_power_kw"])

    # 物理护栏：光伏出力不可为负，且不应超过装机容量的合理上界。
    # 1.2 倍因子容许早期探索阶段元数据暂不匹配时保留有用样本。
    output["pv_power_kw"] = output["pv_power_kw"].clip(lower=0, upper=capacity_kw * 1.2)
    return output.sort_values("timestamp")


def normalize_nrel_solar_zip(path: Path, source_config: dict) -> pd.DataFrame:
    """标准化 NREL Solar Integration 实际出力/预测 ZIP 文件。

    NREL 州级 ZIP 包含对齐的文件：
      - Actual_*_5_Min.csv：实测/模拟实际光伏出力；
      - DA_*_60_Min.csv：日前（Day-Ahead）预测光伏出力；
      - HA4_*_60_Min.csv：4 小时前预测光伏出力。

    实际 5 分钟出力重采样为小时均值后，与 DA 和 HA4 预测按时间戳
    左连接合并，产出连续的小时级表格供主预测实验使用。

    Args:
        path: ZIP 文件路径。
        source_config: 该数据源的配置字典，须包含 actual_member、capacity_mw；
            可选 day_ahead_member、hour_ahead_member、timezone。

    Returns:
        包含 timestamp、pv_power_kw 及可选预测列的小时级 DataFrame。
    """

    actual_member = source_config["actual_member"]
    da_member = source_config.get("day_ahead_member")
    ha4_member = source_config.get("hour_ahead_member")
    timezone = source_config.get("timezone", "UTC")

    def read_member(member: str, output_column: str) -> pd.DataFrame:
        with zipfile.ZipFile(path) as archive:
            with archive.open(member) as handle:
                frame = pd.read_csv(handle)
        timestamp = pd.to_datetime(frame["LocalTime"], format="%m/%d/%y %H:%M", errors="coerce")
        timestamp = timestamp.dt.tz_localize(timezone, nonexistent="shift_forward", ambiguous="NaT").dt.tz_convert("UTC")
        return pd.DataFrame(
            {
                "timestamp": timestamp,
                output_column: pd.to_numeric(frame["Power(MW)"], errors="coerce") * 1000.0,
            }
        ).dropna(subset=["timestamp", output_column])

    actual = read_member(actual_member, "pv_power_kw")
    # 实际出力从 5 分钟重采样为小时均值
    actual_hourly = actual.set_index("timestamp").resample("1h").mean(numeric_only=True).reset_index()

    merged = actual_hourly
    if da_member:
        da = read_member(da_member, "pv_forecast_da_kw")
        merged = merged.merge(da, on="timestamp", how="left")
    if ha4_member:
        ha4 = read_member(ha4_member, "pv_forecast_ha4_kw")
        merged = merged.merge(ha4, on="timestamp", how="left")

    # 按配置中装机容量裁剪所有功率列，1.05 倍容许测量误差
    capacity_kw = float(source_config["capacity_mw"]) * 1000.0
    power_columns = [column for column in merged.columns if column.endswith("_kw")]
    for column in power_columns:
        merged[column] = merged[column].clip(lower=0, upper=capacity_kw * 1.05)

    return merged.sort_values("timestamp").reset_index(drop=True)


def normalize_weather(path: Path) -> pd.DataFrame:
    """将天气字段标准化为模型友好的统一列名。

    同时支持 Open-Meteo/ERA5 CSV 文件和 NSRDB PSM CSV 文件。
    NSRDB 文件在真实列头之前有两行元数据；Open-Meteo 文件为常规单行头 CSV。
    输出 schema 刻意保持稳定，使下游清洗和特征工程不依赖数据提供方的命名惯例。

    Args:
        path: 天气 CSV 文件路径（Open-Meteo 或 NSRDB 格式）。

    Returns:
        标准化列名的天气 DataFrame，timestamp 列为 UTC 时区。
    """

    # 通过首行内容判断文件格式：NSRDB 首行含 "Source" 和 "Location"
    first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    if "Source" in first_line and "Location" in first_line:
        # NSRDB PSM 格式：跳过前 2 行元数据
        frame = pd.read_csv(path, skiprows=2, low_memory=False)
        # NSRDB 的 Year/Month/Day/Hour/Minute 列拼接为 UTC 时间戳
        base_date = pd.to_datetime(
            {
                "year": pd.to_numeric(frame["Year"], errors="coerce"),
                "month": pd.to_numeric(frame["Month"], errors="coerce"),
                "day": pd.to_numeric(frame["Day"], errors="coerce"),
            },
            errors="coerce",
            utc=True,
        )
        hour = pd.to_numeric(frame["Hour"], errors="coerce").fillna(0)
        minute = pd.to_numeric(frame["Minute"], errors="coerce").fillna(0)
        frame["timestamp"] = base_date + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m")
        # NSRDB 原始列名映射为标准列名
        output = frame.rename(
            columns={
                "GHI": "ghi_wm2",
                "DNI": "dni_wm2",
                "DHI": "dhi_wm2",
                "Clearsky GHI": "clearsky_ghi_wm2",
                "Clearsky DNI": "clearsky_dni_wm2",
                "Clearsky DHI": "clearsky_dhi_wm2",
                "Temperature": "temperature_c",
                "Air Temperature": "temperature_c",
                "Dew Point": "dew_point_c",
                "Relative Humidity": "relative_humidity_pct",
                "Pressure": "pressure_hpa",
                "Surface Pressure": "pressure_hpa",
                "Wind Speed": "wind_speed_ms",
                "Wind Direction": "wind_direction_deg",
                "Solar Zenith Angle": "solar_zenith_angle_deg",
                "Surface Albedo": "surface_albedo",
                "Cloud Type": "cloud_type",
                "Fill Flag": "weather_fill_flag",
                "Precipitable Water": "precipitable_water_cm",
            }
        )
        # 只保留标准列名中实际存在的列
        keep = [
            "timestamp",
            "ghi_wm2",
            "dni_wm2",
            "dhi_wm2",
            "clearsky_ghi_wm2",
            "clearsky_dni_wm2",
            "clearsky_dhi_wm2",
            "temperature_c",
            "dew_point_c",
            "relative_humidity_pct",
            "pressure_hpa",
            "wind_speed_ms",
            "wind_direction_deg",
            "solar_zenith_angle_deg",
            "surface_albedo",
            "cloud_type",
            "weather_fill_flag",
            "precipitable_water_cm",
        ]
        existing = [column for column in keep if column in output.columns]
        # 将非时间戳列转为数值类型
        for column in existing:
            if column != "timestamp":
                output[column] = pd.to_numeric(output[column], errors="coerce")
        return output[existing].dropna(subset=["timestamp"]).sort_values("timestamp")

    # Open-Meteo / ERA5 常规格式：单行头 CSV
    frame = _read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    # Open-Meteo 原始列名映射为标准列名
    output = frame.rename(
        columns={
            "temperature_2m": "temperature_c",
            "relative_humidity_2m": "relative_humidity_pct",
            "dew_point_2m": "dew_point_c",
            "surface_pressure": "surface_pressure_hpa",
            "pressure_msl": "pressure_hpa",
            "precipitation": "precipitation_mm",
            "wind_speed_10m": "wind_speed_ms",
            "wind_direction_10m": "wind_direction_deg",
            "wind_gusts_10m": "wind_gusts_ms",
            "shortwave_radiation": "ghi_wm2",
            "direct_radiation": "dni_wm2",
            "direct_normal_irradiance": "dni_wm2",
            "diffuse_radiation": "dhi_wm2",
            "terrestrial_radiation": "toa_radiation_wm2",
            "cloud_cover": "cloud_cover_pct",
            "cloud_cover_low": "cloud_cover_low_pct",
            "cloud_cover_mid": "cloud_cover_mid_pct",
            "cloud_cover_high": "cloud_cover_high_pct",
            "weather_forecast_lead_time_hour": "weather_forecast_lead_time_hour",
        }
    )
    # 去除重复列（direct_radiation 和 direct_normal_irradiance 均映射为 dni_wm2）
    if output.columns.duplicated().any():
        output = output.loc[:, ~output.columns.duplicated()]
    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def derive_weather_from_pv(path: Path, pv_source_config: dict, weather_config: dict) -> pd.DataFrame:
    """从 PVDAQ/DuraMAT 光伏源文件中提取类天气字段。

    部分 PVDAQ 公开子集已包含辐照度、环境温度和风速。对于首个项目里程碑，
    复用这些列比单独关联一个时间范围可能不重叠的天气源更稳健。

    Args:
        path: 光伏原始 CSV 文件路径。
        pv_source_config: 光伏数据源配置字典，须包含 timestamp_candidates；
            可选 max_rows。
        weather_config: 天气字段映射配置字典，须包含 field_map，
            key 为源列名、value 为标准列名。

    Returns:
        标准化列名的天气 DataFrame。
    """

    frame = _read_csv(path, pv_source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        pv_source_config["timestamp_candidates"],
        "天气时间戳",
    )

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
        }
    )
    # 按 field_map 逐列映射源列到标准列名，同一目标列取首次非空值
    for source_col, target_col in weather_config.get("field_map", {}).items():
        if source_col in frame.columns:
            values = pd.to_numeric(frame[source_col], errors="coerce")
            if target_col in output.columns:
                output[target_col] = output[target_col].fillna(values)
            else:
                output[target_col] = values

    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def normalize_opsd(path: Path, source_config: dict) -> pd.DataFrame:
    """标准化 OPSD（Open Power System Data）负荷和电价字段。

    OPSD 列可用性因发布版本和国家而异。项目从德国/卢森堡字段起步，
    因为它们在公开数据包中最常见，足以驱动经济调度演示。

    Args:
        path: OPSD 原始 CSV 文件路径。
        source_config: 该数据源的配置字典，须包含 timestamp_candidates、
            load_candidates、price_candidates；可选 max_rows。

    Returns:
        包含 timestamp、load_mw 和 price_eur_mwh 的 DataFrame。
    """

    frame = _read_csv(path, source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        source_config["timestamp_candidates"],
        "OPSD时间戳",
    )
    load_col = _first_existing_column(frame.columns, source_config["load_candidates"], "负荷")

    # 电价列可能不存在，逐候选查找
    price_col = None
    for candidate in source_config["price_candidates"]:
        if candidate in frame.columns:
            price_col = candidate
            break

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
            "load_mw": pd.to_numeric(frame[load_col], errors="coerce"),
        }
    )
    if price_col:
        output["price_eur_mwh"] = pd.to_numeric(frame[price_col], errors="coerce")
    else:
        # 兜底电价曲线：保证调度模块在 OPSD 缺少日前电价字段时仍可运行。
        # 高峰时段（17-21 时）120 €/MWh，其余时段 50 €/MWh。
        hour = output["timestamp"].dt.hour
        output["price_eur_mwh"] = 50 + ((hour >= 17) & (hour <= 21)).astype(int) * 60

    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def map_opsd_profile_to_target_timeline(target: pd.DataFrame, opsd: pd.DataFrame) -> pd.DataFrame:
    """将 OPSD 负荷/电价典型曲线映射到目标光伏时间轴。

    NREL Solar Integration 光伏数据为 2006 年，而 OPSD 负荷/电价数据
    通常始于 2015 年。为使调度模块使用真实 OPSD 曲线形状而不假设时间戳
    重叠，此函数从 OPSD 构建星期×小时平均曲线，再投影到 NREL 时间戳上。

    Args:
        target: 目标时间轴 DataFrame，须包含 timestamp 列。
        opsd: 标准化后的 OPSD DataFrame，须包含 timestamp、load_mw、
            price_eur_mwh 列。

    Returns:
        与 target 时间轴对齐的负荷/电价 DataFrame。
    """

    profile_source = opsd.dropna(subset=["load_mw", "price_eur_mwh"]).copy()
    profile_source["day_of_week"] = profile_source["timestamp"].dt.dayofweek
    profile_source["hour"] = profile_source["timestamp"].dt.hour
    # 按 (星期, 小时) 分组计算 OPSD 负荷和电价均值
    profile = (
        profile_source.groupby(["day_of_week", "hour"], as_index=False)[["load_mw", "price_eur_mwh"]]
        .mean()
    )

    mapped = pd.DataFrame({"timestamp": target["timestamp"].dropna().drop_duplicates()})
    mapped["day_of_week"] = mapped["timestamp"].dt.dayofweek
    mapped["hour"] = mapped["timestamp"].dt.hour
    # 将典型曲线按 (星期, 小时) 匹配到目标时间轴
    mapped = mapped.merge(profile, on=["day_of_week", "hour"], how="left")

    # 少数时段可能匹配不到（如 OPSD 数据缺失），用中位数填充
    for column in ["load_mw", "price_eur_mwh"]:
        if mapped[column].isna().any():
            mapped[column] = mapped[column].fillna(profile_source[column].median())

    return mapped[["timestamp", "load_mw", "price_eur_mwh"]].sort_values("timestamp")


def build_synthetic_market(index_source: pd.DataFrame, source_config: dict) -> pd.DataFrame:
    """生成与光伏时间轴对齐的确定性负荷和电价曲线。

    首个实现需要经济上有意义的调度信号，而非完美的市场模型。
    此函数提供可复现的典型曲线：夜间低谷电价、晚间高峰电价，
    负荷跟随平滑的日周期变化。

    Args:
        index_source: 提供时间轴的 DataFrame，须包含 timestamp 列。
        source_config: 合成市场配置字典，须包含 base_load_mw、
            daily_load_amplitude_mw、base_price_eur_mwh、
            peak_price_eur_mwh、valley_price_eur_mwh。

    Returns:
        包含 timestamp、load_mw、price_eur_mwh 的 DataFrame。
    """

    timestamps = pd.DataFrame({"timestamp": index_source["timestamp"].dropna().drop_duplicates()})
    timestamps = timestamps.sort_values("timestamp").reset_index(drop=True)
    hour = timestamps["timestamp"].dt.hour

    base_load = float(source_config["base_load_mw"])
    amplitude = float(source_config["daily_load_amplitude_mw"])
    base_price = float(source_config["base_price_eur_mwh"])
    peak_price = float(source_config["peak_price_eur_mwh"])
    valley_price = float(source_config["valley_price_eur_mwh"])

    # 负荷用余弦函数模拟日周期，峰值在 18 时
    daily_phase = (hour - 18) / 24 * 2 * np.pi
    timestamps["load_mw"] = base_load + amplitude * (1 + np.cos(daily_phase))
    # 电价：0-6 时为低谷价，17-21 时为高峰价，其余为基础价
    timestamps["price_eur_mwh"] = base_price
    timestamps.loc[(hour >= 0) & (hour <= 6), "price_eur_mwh"] = valley_price
    timestamps.loc[(hour >= 17) & (hour <= 21), "price_eur_mwh"] = peak_price
    return timestamps


def build_hourly_training_table(
    *,
    pv_power: pd.DataFrame,
    weather: pd.DataFrame,
    opsd: pd.DataFrame,
) -> pd.DataFrame:
    """将所有数据源对齐为一张小时级 UTC 训练表。

    光伏、天气、市场三张表分别重采样为小时均值后，按 timestamp
    左连接合并。光伏功率列是监督学习标签，缺失该列的行将被移除；
    天气/市场特征的短间隙通过前后向有限步填充和中位数填充处理。

    Args:
        pv_power: 标准化后的光伏功率 DataFrame（须含 timestamp、pv_power_kw）。
        weather: 标准化后的天气 DataFrame（须含 timestamp 及气象列）。
        opsd: 标准化后的负荷/电价 DataFrame（须含 timestamp、load_mw、
            price_eur_mwh）。

    Returns:
        合并后的小时级训练表，包含时间特征列 hour、day_of_week、month。
    """

    pv_hourly = (
        pv_power.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )
    weather_hourly = (
        weather.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )
    opsd_hourly = (
        opsd.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )

    merged = pv_hourly.merge(weather_hourly, on="timestamp", how="left")
    merged = merged.merge(opsd_hourly, on="timestamp", how="left")

    # 光伏功率是监督学习标签，缺失标签的行不是有效训练样本，必须移除
    merged = merged.dropna(subset=["pv_power_kw"]).copy()

    # 天气和市场字段在重采样/连接后可能有短间隙。
    # 限制填充步数为 3，使长时间缺失在质量检查中仍然可见。
    feature_columns = [column for column in merged.columns if column not in {"timestamp", "pv_power_kw"}]
    merged[feature_columns] = merged[feature_columns].ffill(limit=3).bfill(limit=3)
    # 有限步前后向填充仍无法覆盖的缺失值，用中位数兜底
    for column in feature_columns:
        if pd.api.types.is_numeric_dtype(merged[column]) and merged[column].isna().any():
            merged[column] = merged[column].fillna(merged[column].median())

    # 时间特征是确定性的、无泄漏风险的，对树基线和序列模型均有用
    merged["hour"] = merged["timestamp"].dt.hour
    merged["day_of_week"] = merged["timestamp"].dt.dayofweek
    merged["month"] = merged["timestamp"].dt.month

    return merged.sort_values("timestamp").reset_index(drop=True)
