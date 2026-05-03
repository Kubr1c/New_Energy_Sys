"""HRRR multi-time forecast aggregation for PV power prediction.

Converts HRRR forecast rows (issue_time, lead_time_hour, valid_time=timestamp)
into target-time-aligned feature rows suitable for PV prediction training.

P0 数据泄露预防规则（必须严格验证）:
  1. valid_time == target_time T           — 只使用预报对应当前目标时刻的数值
  2. 24h <= lead_time_hour <= 48h           — 日前预测只能使用 T-24h 或更早时刻已发布的信息
  3. issue_time = target_time - lead_time   — 每个预报的发起时刻由上述规则确定
  4. 每个 target_time 必须恰好输出一行       — 避免下游训练/测试产生时间泄露

聚合方式:
  - inverse_lead_weighted (默认): 权重 ∝ 1/lead_time_hour，提前时间越短的预报权重越大
  - simple_mean: 等权平均
  - nearest_lead: 选 lead_time 最接近 24h 的单个预报

审计列（每行必须包含）:
  - target_time, issue_time_min, issue_time_max, lead_time_min, lead_time_max,
    n_forecasts_used, weather_missing

依赖: 纯 pandas/numpy，无 pvlib 依赖。
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

# 需要聚合的气象变量列
_WEATHER_COLS = [
    "ghi_wm2",
    "dni_wm2",
    "dhi_wm2",
    "temperature_c",
    "cloud_cover_pct",
]


def aggregate_hrrr_to_target_times(
    hrrr_decomposed: pd.DataFrame,
    target_timestamps: Union[pd.DataFrame, pd.DatetimeIndex],
    agg_method: str = "inverse_lead_weighted",
) -> pd.DataFrame:
    """将 HRRR 预报行逐 target_time 聚合为单行特征表。

    Parameters
    ----------
    hrrr_decomposed : pd.DataFrame
        HRRR Stage7 数据（含 DISC 分解后的 DNI/DHI）。
        必须包含列: timestamp, weather_forecast_issue_time,
        weather_forecast_lead_time_hour, ghi_wm2, dni_wm2, dhi_wm2,
        temperature_c, cloud_cover_pct。
    target_timestamps : pd.DataFrame or pd.DatetimeIndex
        PV 目标时间戳集。若传入 DataFrame，取其 ``timestamp`` 列。
    agg_method : str
        聚合方式，可选:
        - "inverse_lead_weighted": 逆提前时间加权平均（默认）
        - "simple_mean": 等权平均
        - "nearest_lead": 选提前时间最接近 24h 的单预报

    Returns
    -------
    pd.DataFrame
        每行对应一个 target_time，含气象特征列和审计列。

    Raises
    ------
    ValueError
        当 agg_method 不支持时抛出。
    """
    # ---- 参数校验 ----
    if agg_method not in ("inverse_lead_weighted", "simple_mean", "nearest_lead"):
        raise ValueError(
            f"agg_method '{agg_method}' 不支持, "
            f"可选: 'inverse_lead_weighted', 'simple_mean', 'nearest_lead'"
        )

    # ---- 标准化 target_timestamps 为 DatetimeIndex ----
    if isinstance(target_timestamps, pd.DataFrame):
        # 从 DataFrame 的 timestamp 列提取
        target_idx = pd.DatetimeIndex(target_timestamps["timestamp"])
    else:
        target_idx = target_timestamps

    # ---- 预处理 HRRR DataFrame 以加速循环查询 ----
    df = hrrr_decomposed.copy()
    # 统一列名（确保类型安全）
    df["valid_time"] = df["timestamp"]
    df["lead_time_hour"] = df["weather_forecast_lead_time_hour"].astype(float)
    df["issue_time"] = pd.to_datetime(df["weather_forecast_issue_time"], utc=True)

    # ---- 逐 target_time 聚合 ----
    results = [
        _aggregate_single_target(T, df, agg_method) for T in target_idx
    ]

    result_df = pd.DataFrame(results)

    # 确保 weather_missing 为 bool 类型
    result_df["weather_missing"] = result_df["weather_missing"].astype(bool)

    return result_df


def _aggregate_single_target(
    T: pd.Timestamp,
    hrrr: pd.DataFrame,
    agg_method: str,
) -> dict:
    """聚合单个目标时间戳 T 对应的所有 HRRR 预报。

    Parameters
    ----------
    T : pd.Timestamp
        目标时间（UTC）。
    hrrr : pd.DataFrame
        预处理后的 HRRR 完整 DataFrame（须含 valid_time, lead_time_hour, issue_time）。
    agg_method : str
        聚合方式。

    Returns
    -------
    dict
        包含 target_time、各气象列值、审计列的字典。
    """
    # ---- P0 严格过滤: valid_time == T, 24h <= lead_time_hour <= 48h ----
    mask = (
        (hrrr["valid_time"] == T)
        & (hrrr["lead_time_hour"] >= 24.0)
        & (hrrr["lead_time_hour"] <= 48.0)
    )
    candidates = hrrr[mask]

    # ---- 无可用预报: 缺失标记 ----
    if len(candidates) == 0:
        row: dict = {
            "target_time": T,
            "n_forecasts_used": 0,
            "weather_missing": True,
            "issue_time_min": pd.NaT,
            "issue_time_max": pd.NaT,
            "lead_time_min": np.nan,
            "lead_time_max": np.nan,
        }
        for col in _WEATHER_COLS:
            row[col] = np.nan
        return row

    # ---- 可用预报的数据 ----
    leads = candidates["lead_time_hour"].values.astype(float)

    # ---- 按 agg_method 选择/加权 ----
    if agg_method == "nearest_lead":
        # 选 lead_time 最接近 24h 的单行
        idx = int(np.argmin(np.abs(leads - 24.0)))
        selected = candidates.iloc[[idx]]
        weights = np.array([1.0])
    elif agg_method == "simple_mean":
        selected = candidates
        # 等权: 每条预报警权重相同
        n = len(selected)
        weights = np.ones(n) / n
    else:  # inverse_lead_weighted
        selected = candidates
        # 逆提前时间加权: 权重 ∝ 1/lead_time_hour
        raw_weights = 1.0 / leads
        weights = raw_weights / raw_weights.sum()

    # ---- 构建输出行 ----
    row = {
        "target_time": T,
        "n_forecasts_used": len(selected),
        "weather_missing": False,
        "issue_time_min": selected["issue_time"].min(),
        "issue_time_max": selected["issue_time"].max(),
        "lead_time_min": float(leads.min()),
        "lead_time_max": float(leads.max()),
    }

    # 加权聚合每个气象变量
    for col in _WEATHER_COLS:
        vals = selected[col].values.astype(float)
        row[col] = np.average(vals, weights=weights)

    return row


def build_complete_step1_feature_table(
    hrrr_path: str,
    pv_path: str,
) -> pd.DataFrame:
    """构建完整的 STEP 1 特征表（~80 列），端到端输出训练用特征矩阵。

    功能流程:
      1. 加载 HRRR 分解 parquet 和 PV 清洗 parquet
      2. 过滤 PV 至 HRRR 重叠期 (2021-01-01 ~ 2022-12-31)
      3. 调用 aggregate_hrrr_to_target_times 聚合气象预报
      4. 调用 _add_time_features / _add_historical_power_features 构造时间+历史功率特征
      5. 生成 24h 目标列并合并
      6. 构造因果滚动特征（rolling 24h mean/std，仅使用过去值）
      7. 构造派生比值特征（dhi_ghi_ratio, clearsky_index, kt）带离群保护

    Parameters
    ----------
    hrrr_path : str
        HRRR 分解 parquet 路径（Stage7，含 DNI/DHI 分解）。
    pv_path : str
        PV 清洗 parquet 路径（Stage2 hourly dataset）。

    Returns
    -------
    pd.DataFrame
        约 80 列特征表，包含气象、时间、历史功率、派生比值特征及目标列。
    """
    # ===================================================================
    # 1. 加载并过滤数据
    # ===================================================================
    hrrr = pd.read_parquet(hrrr_path)
    pv = pd.read_parquet(pv_path)

    # PV 过滤至 HRRR 覆盖期：2021-01-01 ~ 2022-12-31
    pv_overlap = pv[
        (pv["timestamp"] >= "2021-01-01")
        & (pv["timestamp"] <= "2022-12-31")
    ].copy()
    pv_overlap = pv_overlap.sort_values("timestamp").reset_index(drop=True)

    # ===================================================================
    # 2. 聚合 HRRR 气象预报至各 target_time
    # ===================================================================
    # 传入 DataFrame，aggregate_hrrr_to_target_times 内部提取 timestamp 列
    agg_features = aggregate_hrrr_to_target_times(hrrr, pv_overlap)

    # ===================================================================
    # 3-4. 构造时间特征 + 历史功率特征
    # ===================================================================
    from new_energy_sys.features import (
        _add_time_features,
        _add_historical_power_features,
    )

    # 记录原始 PV 列，用于后续提取新增特征列
    original_pv_cols = set(pv_overlap.columns)

    # 时间特征（周期编码 + 基本时间属性）
    pv_enriched, _ = _add_time_features(pv_overlap)

    # 历史功率特征（滞后 + 滚动统计，须在 _add_time_features 之后链式调用）
    pv_enriched, _ = _add_historical_power_features(
        pv_enriched, capacity_kw=1.12,
    )

    # 24h 日前预测目标：pv_power_kw.shift(-24) 即 T+24h 的真实功率
    pv_enriched["target_pv_power_t_plus_24h"] = pv_enriched["pv_power_kw"].shift(-24)

    # 确定 PV 侧新增的特征列（时间特征 + 历史功率特征 + 目标列）
    added_pv_cols = [c for c in pv_enriched.columns if c not in original_pv_cols]

    # ===================================================================
    # 5. 合并聚合气象特征 + PV 侧特征
    # ===================================================================
    # PV 原始数据已含 ghi_wm2、temperature_c 等气象列，必须排除与 agg
    # 侧重复的列，避免 merge 产生 _x/_y 后缀导致下游逻辑混乱。
    agg_col_names = set(agg_features.columns)
    pv_merge_cols = ["timestamp", "pv_power_kw"] + added_pv_cols
    pv_merge_cols = [
        c for c in pv_merge_cols if c not in agg_col_names or c == "timestamp"
    ]

    df = agg_features.merge(
        pv_enriched[pv_merge_cols],
        left_on="target_time",
        right_on="timestamp",
        how="left",
    )
    # df 同时包含 target_time（左）和 timestamp（右），
    # 下游训练代码期望使用 timestamp 列

    # ===================================================================
    # 6. 因果滚动天气特征（24h 窗口，仅使用 <= T 的历史值）
    # ===================================================================
    df = df.sort_values("target_time").reset_index(drop=True)

    for col in _WEATHER_COLS:
        roll_mean = f"{col}_rolling_mean_24h"
        roll_std = f"{col}_rolling_std_24h"

        # min_periods=1：序列开头至少有 1 个值即不产生 NaN
        df[roll_mean] = df[col].rolling(window=24, min_periods=1).mean()

        # ddof=0：总体标准差，匹配测试中手工窗口验证
        # fillna(0)：首个观测值的 std 算出来是 0（单点无方差）
        df[roll_std] = (
            df[col].rolling(window=24, min_periods=1).std(ddof=0).fillna(0)
        )

    # ===================================================================
    # 7. 派生比值特征（带离群保护）
    # ===================================================================

    # 7a. dhi_ghi_ratio — 散射/总辐射比
    # GHI <= 20 W/m2 时比值无物理意义，标记为 NaN
    df["dhi_ghi_ratio"] = np.where(
        df["ghi_wm2"] > 20,
        (df["dhi_wm2"] / df["ghi_wm2"]).clip(0, 1.5),
        np.nan,
    )

    # 7b. clearsky_index — pvlib Ineichen 晴空指数
    import pvlib

    loc = pvlib.location.Location(
        latitude=39.74, longitude=-105.18, altitude=1730,
    )
    ts_dti = pd.DatetimeIndex(df["target_time"])
    solar_pos = loc.get_solarposition(times=ts_dti)

    zenith = solar_pos["zenith"].values.astype(float)
    z_rad = np.radians(zenith)
    cos_z = np.cos(z_rad)

    # 日地距离修正 -> 地外辐射
    doy = ts_dti.dayofyear.astype(float)
    eccentricity = 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)
    dni_extra = 1367.0 * eccentricity

    # Ineichen 晴空模型 GHI（pvlib 0.15+ 移至 clearsky 子模块）
    # 返回 OrderedDict 或 DataFrame，键为 "ghi"
    _ineichen_result = pvlib.clearsky.ineichen(
        apparent_zenith=zenith,
        airmass_absolute=pvlib.atmosphere.get_relative_airmass(zenith),
        linke_turbidity=3.0,
        altitude=1700.0,
        dni_extra=dni_extra,
    )
    clearsky_ghi = _ineichen_result["ghi"]

    # 白天有辐照时 clip [0, 2]，夜间为 NaN
    df["clearsky_index"] = np.where(
        (clearsky_ghi > 20) & (cos_z > 0),
        (df["ghi_wm2"] / np.maximum(clearsky_ghi, 1.0)).clip(0, 2),
        np.nan,
    )

    # 7c. kt (clearness index) — 大气层顶辐射归一化
    # cos_z <= 0 时大气层顶辐射为 0，kt 无定义
    ghi_extra = dni_extra * cos_z
    ghi_extra = np.maximum(ghi_extra, 1e-6)
    df["kt"] = np.where(
        (ghi_extra > 20) & (cos_z > 0),
        (df["ghi_wm2"] / ghi_extra).clip(0, 1.2),
        np.nan,
    )

    # ===================================================================
    # 8. 列名统一
    # ===================================================================
    # df 同时保留 target_time（审计回溯用）和 timestamp（训练代码期望），
    # 外部若需单一时间列可自行 drop("target_time", axis=1)。

    return df
