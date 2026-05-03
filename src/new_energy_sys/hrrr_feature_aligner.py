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
