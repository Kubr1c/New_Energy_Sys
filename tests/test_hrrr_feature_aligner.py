"""P0/P1 data leak prevention tests for HRRR feature aligner.

P0 强制规则（先验证特征严格无泄露，再谈模型精度）:
  1. valid_time == T
  2. 24h <= lead_time_hour <= 48h
  3. issue_time <= target_time - 24h
  4. Each target_time must have exactly one feature row

TDD pattern: the module ``new_energy_sys.hrrr_feature_aligner`` does not
exist yet.  All tests are expected to fail with ImportError — that is the
"red" state of Red-Green-Refactor.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Data paths  — resolve from test file location so they work regardless of
# the working directory from which pytest is launched.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent

HRRR_PATH = str(
    _PROJECT_ROOT
    / "data"
    / "processed"
    / "pvdaq_nsrdb_2020_2022"
    / "stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
)

PV_PATH = str(
    _PROJECT_ROOT
    / "data"
    / "processed"
    / "pvdaq_nsrdb_2020_2022"
    / "stage2_cleaned_hourly_dataset.parquet"
)

# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

# Suffix naming convention for rolling features (expected)
_ROLLING_MEAN_SUFFIX = "_rolling_mean_24h"
_ROLLING_STD_SUFFIX = "_rolling_std_24h"

# ---------------------------------------------------------------------------
# Fixtures  — load real data once per module to avoid repeated I/O.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_hrrr() -> pd.DataFrame:
    """Full decomposed HRRR forecast parquet (2021-2022, F24-F44)."""
    return pd.read_parquet(HRRR_PATH)


@pytest.fixture(scope="module")
def real_targets() -> pd.DataFrame:
    """Cleaned hourly PV dataset (2020-2022) providing target timestamps."""
    return pd.read_parquet(PV_PATH)


# ===================================================================
# P0: 数据泄露强制检测
# ===================================================================


def test_no_forecast_with_lead_time_less_than_24h(
    real_hrrr: pd.DataFrame, real_targets: pd.DataFrame
) -> None:
    """P0: After aggregation, all non-missing rows must have lead_time_min >= 24.

    日前预测只能使用 T-24h 或更早时刻已发布的信息。若存在 lead_time < 24h
    的预报被混入，说明使用了"未来"信息（实际还未发布的预报），构成泄露。
    """
    # Lazy import — will raise ImportError until the module is implemented
    from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times

    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)

    # 只检查 weather_missing=False 的行（有实际预报覆盖的时间戳）
    non_missing = features.loc[~features["weather_missing"]]
    assert len(non_missing) > 0, "All rows are weather_missing -- cannot test lead time."

    min_lead = non_missing["lead_time_min"]
    assert (min_lead >= 24.0).all(), (
        f"Found {int((min_lead < 24.0).sum())} row(s) with lead_time_min < 24h"
        f" (min={min_lead.min():.2f}h)"
    )


def test_no_forecast_issued_after_target_minus_24h(
    real_hrrr: pd.DataFrame, real_targets: pd.DataFrame
) -> None:
    """P0: issue_time_max <= target_time - 24h for every non-missing row.

    issue_time_max 记录该 target_time 所用预报中最晚的发起时刻。
    若 issue_time_max > target_time - 24h，意味着使用了 T-24h 之后
    才发布的预报，在日前调度中不可用，构成数据泄露。
    """
    from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times

    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)
    non_missing = features.loc[~features["weather_missing"]]

    assert len(non_missing) > 0, "All rows are weather_missing -- cannot test."

    cutoff = non_missing["target_time"] - pd.Timedelta(hours=24)
    violations = non_missing["issue_time_max"] > cutoff
    assert not violations.any(), (
        f"Found {int(violations.sum())} row(s) where "
        f"issue_time_max > target_time - 24h."
    )


def test_one_feature_row_per_target_time(
    real_hrrr: pd.DataFrame, real_targets: pd.DataFrame
) -> None:
    """P0: Each target_time must have exactly one feature row.

    多时次聚合输出应保证 target_time 唯一。重复意味着下游模型会在同一
    时间戳收到多条特征行（训练/测试期间的时间泄露）。
    """
    from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times

    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)

    dup_mask = features.duplicated(subset=["target_time"], keep=False)
    assert not dup_mask.any(), (
        f"Found {int(dup_mask.sum())} duplicate target_time row(s): "
        f"{features.loc[dup_mask, 'target_time'].unique()}"
    )


# ===================================================================
# P1: 审计列完整性
# ===================================================================


def test_audit_columns_are_present(
    real_hrrr: pd.DataFrame, real_targets: pd.DataFrame
) -> None:
    """P1: Feature table must include all required audit columns.

    审计列确保特征可追溯，便于排查泄露或数据质量问题。这些列不入模，
    但必须保留以便调试和生产监控。
    """
    from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times

    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)

    expected = {
        "target_time",
        "issue_time_min",
        "issue_time_max",
        "lead_time_min",
        "lead_time_max",
        "n_forecasts_used",
        "weather_missing",
    }
    missing = sorted(expected - set(features.columns))
    assert not missing, f"Missing audit column(s): {missing}"

    # n_forecasts_used should be integer and non-null for all rows
    assert features["n_forecasts_used"].notna().all(), (
        "n_forecasts_used contains NaN"
    )

    # weather_missing must be boolean for downstream masking
    assert features["weather_missing"].dtype == bool, (
        f"weather_missing dtype should be bool, got "
        f"{features['weather_missing'].dtype}"
    )


# ===================================================================
# P1: 派生特征边界安全
# ===================================================================


def test_dhi_ghi_ratio_is_safe() -> None:
    """P1: dhi_ghi_ratio contains no inf and all finite values in [0, 1.5].

    GHI <= 20 W/m2 时应为 NaN; 否则 clip [0, 1.5]。
    """
    from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table

    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)
    ratio = df["dhi_ghi_ratio"]

    assert not np.isinf(ratio).any(), "dhi_ghi_ratio contains inf values"

    finite = ratio.dropna()
    if len(finite) > 0:
        assert finite.min() >= 0.0, (
            f"dhi_ghi_ratio min={finite.min():.4f} < 0"
        )
        assert finite.max() <= 1.5, (
            f"dhi_ghi_ratio max={finite.max():.4f} > 1.5"
        )


def test_clearsky_index_range() -> None:
    """P1: clearsky_index contains no inf and all finite values in [0, 2].

    GHI <= 0 或 cos(zenith) <= 0 时应为 NaN; 否则 clip [0, 2]。
    """
    from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table

    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)
    csi = df["clearsky_index"]

    assert not np.isinf(csi).any(), "clearsky_index contains inf values"

    finite = csi.dropna()
    if len(finite) > 0:
        assert finite.min() >= 0.0, (
            f"clearsky_index min={finite.min():.4f} < 0"
        )
        assert finite.max() <= 2.0, (
            f"clearsky_index max={finite.max():.4f} > 2.0"
        )


def test_kt_range() -> None:
    """P1: kt (clearness index) contains no inf and all finite values in [0, 1.2].

    同 clearsky_index 保护规则; clip [0, 1.2]。
    """
    from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table

    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)
    kt = df["kt"]

    assert not np.isinf(kt).any(), "kt contains inf values"

    finite = kt.dropna()
    if len(finite) > 0:
        assert finite.min() >= 0.0, f"kt min={finite.min():.4f} < 0"
        assert finite.max() <= 1.2, f"kt max={finite.max():.4f} > 1.2"


def test_rolling_features_are_causal() -> None:
    """P1: Rolling stats must use valid_time <= T only (no future look-ahead).

    ``rolling_mean_24h(T)`` = ``mean(weather[T-23h : T])`` — 仅使用 <= T
    的历史数据，而非 centered window。验证方式：对每个 rolling 特征列，
    手动从聚合中间结果计算期望值并与特征表比对。
    """
    from new_energy_sys.hrrr_feature_aligner import (
        aggregate_hrrr_to_target_times,
        build_complete_step1_feature_table,
    )

    # -- 加载中间结果（逐 target_time 聚合后、rolling 之前的天气值） --
    hrrr_raw = pd.read_parquet(HRRR_PATH)
    targets_raw = pd.read_parquet(PV_PATH)
    agg = aggregate_hrrr_to_target_times(hrrr_raw, targets_raw)

    # -- 加载最终特征表（含 rolling 特征） --
    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)

    # 找出 rolling 特征列
    rolling_cols = [
        c
        for c in df.columns
        if c.endswith(_ROLLING_MEAN_SUFFIX) or c.endswith(_ROLLING_STD_SUFFIX)
    ]
    assert len(rolling_cols) > 0, "No rolling feature columns found in feature table"

    # 按 target_time 排序并设为 index 以便对齐
    agg_sorted = agg.sort_values("target_time").set_index("target_time")
    df_sorted = df.sort_values("target_time").set_index("target_time")

    for col in rolling_cols:
        # 推断基础变量名（去掉后缀）
        if col.endswith(_ROLLING_MEAN_SUFFIX):
            base_var = col[: -len(_ROLLING_MEAN_SUFFIX)]
            is_mean = True
        else:
            base_var = col[: -len(_ROLLING_STD_SUFFIX)]
            is_mean = False

        if base_var not in agg_sorted.columns:
            continue

        # 选一个有足够历史窗口的 target_time
        candidate_idx = df_sorted.index[
            df_sorted[col].notna()
            & ~df_sorted["weather_missing"]
        ]
        if len(candidate_idx) < 25:
            continue  # 数据不足，跳过该列

        t_check = candidate_idx[24]
        window_start = t_check - pd.Timedelta(hours=23)

        # 取 [T-23h, T] 窗口值（boolean mask 避免 KeyError）
        mask_win = (agg_sorted.index >= window_start) & (
            agg_sorted.index <= t_check
        )
        window_vals = agg_sorted.loc[mask_win, base_var].dropna()
        if len(window_vals) < 2:
            continue

        expected = window_vals.mean() if is_mean else window_vals.std(ddof=0)
        actual = df_sorted.loc[t_check, col]

        np.testing.assert_allclose(
            actual,
            expected,
            rtol=1e-3,
            err_msg=(
                f"{col} @ {t_check}: "
                f"expected {'mean' if is_mean else 'std'}"
                f"={expected:.4f}, got {actual:.4f} "
                f"(window n={len(window_vals)})"
            ),
        )


def test_all_outputs_nonnegative() -> None:
    """P1: DHI and DNI must never be negative in the final feature table."""
    from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table

    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)

    for col in ("dhi_wm2", "dni_wm2"):
        if col not in df.columns:
            continue
        negatives = df[col] < -1e-9
        assert not negatives.any(), (
            f"{col} has {int(negatives.sum())} negative value(s): "
            f"min={df[col].min():.4f}"
        )
