"""
Phase 2: LightGBM Quantile Regression for PV prediction.

Dual-track design:
  Q1 — PV power directly: P10/P50/P90 on pv_power_kw
  Q2 — CSI -> PV: P10/P50/P90 on CSI target, restore to PV space

Usage:
    python -m new_energy_sys.cli.train_quantile_pv

Output (quantile_models/):
    lightgbm_Q{N}_{horizon}_a{alpha}.pkl    Trained quantile models
    quantile_metrics.csv                     Full metrics table
    quantile_results.json                    Machine-readable results
"""

from __future__ import annotations

import json
import pickle
import sys
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from pvlib.location import Location

from new_energy_sys.feature_enhancements import (
    add_valid_time_solar_features,
    add_ramp_features,
)
from new_energy_sys.modeling import _chronological_split

warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet"
)
OUTPUT_DIR = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/quantile_models"
)

# PVDAQ site: Golden, CO (same as Phase 1)
SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}
CAPACITY_KW: float = SITE["capacity_kw"]
EFFICIENCY: float = 0.75  # DC-AC derating factor

# Daytime / CSI filtering thresholds (identical to Phase 1)
SOLAR_ELEV_THRESHOLD: float = 5.0
CSI_CLIP_LOW: float = 0.0
CSI_CLIP_HIGH: float = 1.2
CSP_THRESHOLD: float = 0.05  # min clear-sky power as fraction of capacity
PREDICTION_UPPER: float = CAPACITY_KW * 1.05

# Target columns in Stage3
ALL_TARGET_COLS: list[str] = [
    "target_pv_power_t_plus_1h",
    "target_pv_power_t_plus_6h",
    "target_pv_power_t_plus_24h",
]

# Quantile regression hyperparams (Stage5/E1 tuned, adapted for quantile obj)
HP: dict[str, int | float] = {
    "learning_rate": 0.02,
    "max_depth": 10,
    "num_leaves": 45,
    "n_estimators": 1500,
    "reg_lambda": 0.8,
    "min_child_samples": 35,
    "subsample": 0.85,
    "colsample_bytree": 0.8,
}

HORIZONS: list[int] = [1, 6, 24]
ALPHAS: list[float] = [0.1, 0.5, 0.9]


def _target_col(horizon: int) -> str:
    """Return the PV power target column for a given horizon."""
    return f"target_pv_power_t_plus_{horizon}h"


# ---------------------------------------------------------------------------
# Clear-sky power computation (leakage-free, identical to Phase 1)
# ---------------------------------------------------------------------------


def compute_clear_sky_power(timestamps: pd.DatetimeIndex) -> pd.Series:
    """Compute clear-sky PV power from solar geometry only.

    Uses ONLY site parameters + time + solar geometry.
    No measured power, irradiance, or cloud cover — guaranteed no leakage.

    Formula: P_clear = capacity_kw * GHI_clear_sky / 1000 * efficiency
    """
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(times=timestamps)
    clearsky = loc.get_clearsky(times=timestamps, solar_position=solar_pos)
    power = CAPACITY_KW * clearsky["ghi"].values / 1000.0 * EFFICIENCY
    return pd.Series(power, index=timestamps)


def precompute_clear_sky_lookup(df: pd.DataFrame) -> pd.Series:
    """Build timestamp-indexed clear-sky power lookup.

    Extends beyond the dataset range by one day to cover t+24h valid times.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True)
    full_range = pd.date_range(
        start=ts.min() - pd.Timedelta(hours=1),
        end=ts.max() + pd.Timedelta(hours=25),
        freq="h",
        tz="UTC",
    )
    return compute_clear_sky_power(full_range)


# ---------------------------------------------------------------------------
# CSI target construction (identical to Phase 1)
# ---------------------------------------------------------------------------


def build_csi_target(
    df: pd.DataFrame,
    horizon_hours: int,
    csp_lookup: pd.Series,
) -> pd.DataFrame:
    """Compute CSI target column for the given horizon.

    CSI = PV_power_at_valid / clear_sky_power_at_valid

    Filtering uses clear_sky_power and solar elevation ONLY
    (no actual PV to avoid selection bias).
    """
    result = df.copy()
    target_col = _target_col(horizon_hours)
    valid_time = pd.DatetimeIndex(
        result["timestamp"] + pd.Timedelta(hours=horizon_hours)
    )

    # Look up clear-sky power at valid time
    csp_valid = csp_lookup.reindex(valid_time).values
    result[f"clear_sky_power_valid_{horizon_hours}h"] = csp_valid

    # Solar elevation at valid time
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(times=valid_time)
    elev_valid = solar_pos["elevation"].values
    result[f"solar_elevation_valid_{horizon_hours}h"] = elev_valid

    # Filter: daytime + sufficient clear-sky power
    min_csp = CSP_THRESHOLD * CAPACITY_KW
    valid_mask = (elev_valid > SOLAR_ELEV_THRESHOLD) & (csp_valid > min_csp)

    # Raw CSI = PV / clear_sky_power, clamped to [0, 1.2]
    pv_actual = result[target_col].values
    csi_raw = np.where(
        valid_mask,
        pv_actual / np.maximum(csp_valid, 1e-6),
        np.nan,
    )
    csi_clipped = np.clip(csi_raw, CSI_CLIP_LOW, CSI_CLIP_HIGH)

    col_name = f"csi_target_{horizon_hours}h"
    result[col_name] = np.where(valid_mask, csi_clipped, np.nan)

    n_total = len(result)
    n_valid = int(valid_mask.sum())
    print(
        f"    CSI valid samples: {n_valid}/{n_total}"
        f" ({n_valid / n_total * 100:.1f}%)"
    )

    return result


# ---------------------------------------------------------------------------
# E1 feature pipeline (consistent with Phase 1)
# ---------------------------------------------------------------------------


def _add_e1_features(df: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    """Add E1 feature set: valid-time solar geometry + ramp rates.

    Matches the pipeline used by Phase 1 C0 (best baseline).
    """
    result = add_valid_time_solar_features(
        df,
        horizon_hours,
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    result = add_ramp_features(result)
    return result


def _build_e1_features(df: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    """Return (enhanced_df, feature_column_list) for E1 experiment.

    Adds valid-time solar features and ramp features, then selects all
    numeric columns excluding labels, timestamps, and CSI helpers.
    """
    result = _add_e1_features(df, horizon_hours)

    exclude = {"timestamp", "pv_power_kw"}
    for h in HORIZONS:
        exclude.add(_target_col(h))
        exclude.add(f"csi_target_{h}h")
        exclude.add(f"clear_sky_power_valid_{h}h")
        exclude.add(f"solar_elevation_valid_{h}h")

    allowed_dtypes = (
        np.float64, np.float32, np.int64, np.int32,
        np.int8, np.int16, np.uint8,
    )
    features = [
        c for c in result.columns
        if c not in exclude and result[c].dtype in allowed_dtypes
    ]
    return result, features


# ---------------------------------------------------------------------------
# Quantile loss and evaluation metrics
# ---------------------------------------------------------------------------


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, alpha: float) -> float:
    """Compute pinball (quantile) loss.

    Standard asymmetric piecewise linear loss:
      L = mean( max(alpha * (y-t), (alpha-1) * (y-t)) )
    """
    errors = y_true - y_pred
    return float(np.mean(np.maximum(alpha * errors, (alpha - 1) * errors)))


def evaluate_quantile(
    y_true: np.ndarray,
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
    capacity: float,
) -> dict:
    """Compute full set of quantile regression metrics.

    Returns
    -------
    dict with keys: picp, mpiw_kw, mpiw_capacity_ratio, crossing_rate_raw,
                    pinball_p10, pinball_p50, pinball_p90, pinball_mean,
                    p50_rmse_kw, p50_capacity_nrmse
    """
    # Crossing rate BEFORE monotonicity fix
    crossing_raw = float(np.mean((p10 > p50) | (p50 > p90)))

    # Enforce monotonicity: sort each triplet per-sample
    sorted_preds = np.sort([p10, p50, p90], axis=0)
    p10_fixed, p50_fixed, p90_fixed = (
        sorted_preds[0], sorted_preds[1], sorted_preds[2]
    )

    # Prediction Interval Coverage Probability
    picp = float(np.mean((y_true >= p10_fixed) & (y_true <= p90_fixed)))

    # Mean Prediction Interval Width
    mpiw_kw = float(np.mean(p90_fixed - p10_fixed))

    # Pinball loss per quantile
    pb_p10 = pinball_loss(y_true, p10_fixed, 0.1)
    pb_p50 = pinball_loss(y_true, p50_fixed, 0.5)
    pb_p90 = pinball_loss(y_true, p90_fixed, 0.9)
    pb_mean = float(np.mean([pb_p10, pb_p50, pb_p90]))

    # P50 RMSE
    p50_rmse = float(np.sqrt(np.mean((p50_fixed - y_true) ** 2)))

    return {
        "crossing_rate_raw": crossing_raw,
        "picp": picp,
        "mpiw_kw": mpiw_kw,
        "mpiw_capacity_ratio": mpiw_kw / capacity,
        "pinball_p10": pb_p10,
        "pinball_p50": pb_p50,
        "pinball_p90": pb_p90,
        "pinball_mean": pb_mean,
        "p50_rmse_kw": p50_rmse,
        "p50_capacity_nrmse": p50_rmse / capacity,
    }


def _scenario_stratified_metrics(
    y_true: np.ndarray,
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
    csi_valid: np.ndarray,
    day_mask: np.ndarray,
) -> dict:
    """Compute PICP / MPIW / P50 RMSE stratified by weather scenario."""
    metrics: dict = {}
    for scenario, (csi_lo, csi_hi) in [
        ("clear", (0.7, float("inf"))),
        ("mixed", (0.3, 0.7)),
        ("overcast", (0.0, 0.3)),
    ]:
        sc_mask = day_mask & (csi_valid >= csi_lo) & (csi_valid < csi_hi)
        n = int(sc_mask.sum())
        metrics[f"{scenario}_n"] = n
        if n < 10:
            metrics[f"{scenario}_picp"] = float("nan")
            metrics[f"{scenario}_mpiw_kw"] = float("nan")
            metrics[f"{scenario}_p50_rmse_kw"] = float("nan")
        else:
            metrics[f"{scenario}_picp"] = float(
                np.mean((y_true[sc_mask] >= p10[sc_mask])
                       & (y_true[sc_mask] <= p90[sc_mask]))
            )
            metrics[f"{scenario}_mpiw_kw"] = float(
                np.mean(p90[sc_mask] - p10[sc_mask])
            )
            metrics[f"{scenario}_p50_rmse_kw"] = float(
                np.sqrt(np.mean((p50[sc_mask] - y_true[sc_mask]) ** 2))
            )
    return metrics


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_one_quantile(
    df: pd.DataFrame,
    features: list[str],
    target_col: str,
    horizon: int,
    alpha: float,
    experiment_id: str,
    output_dir: Path,
) -> lgb.LGBMRegressor | None:
    """Train a single LightGBM quantile model.

    Drops NaN rows from the target, does chronological split,
    trains with early stopping, saves to output_dir.

    Returns the trained model, or None on failure.
    """
    # Drop NaN targets (e.g. CSI target for nighttime rows)
    clean = df.dropna(subset=[target_col]).copy()
    if len(clean) < 200:
        print(f"    SKIP (n={len(clean)} < 200)")
        return None

    splits = _chronological_split(clean)

    model = lgb.LGBMRegressor(
        objective="quantile",
        alpha=alpha,
        n_estimators=HP["n_estimators"],
        learning_rate=HP["learning_rate"],
        max_depth=HP["max_depth"],
        num_leaves=HP["num_leaves"],
        reg_lambda=HP["reg_lambda"],
        min_child_samples=HP["min_child_samples"],
        subsample=HP["subsample"],
        subsample_freq=1,
        colsample_bytree=HP["colsample_bytree"],
        random_state=42 + int(alpha * 100),
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        splits["train"][features],
        splits["train"][target_col],
        eval_set=[(splits["validation"][features], splits["validation"][target_col])],
        callbacks=[lgb.early_stopping(80, verbose=False), lgb.log_evaluation(period=0)],
    )

    # Save
    model_path = (
        output_dir / f"lightgbm_{experiment_id}_t{horizon}h_a{alpha:.1f}.pkl"
    )
    with open(model_path, "wb") as f:
        pickle.dump(
            {
                "model": model,
                "features": features,
                "target": target_col,
                "alpha": alpha,
                "horizon_hours": horizon,
                "experiment": experiment_id,
                "capacity_kw": CAPACITY_KW,
            },
            f,
        )

    best_iter = int(model.best_iteration_ or model.n_estimators)
    print(f"    Alpha={alpha:.1f}  best_iter={best_iter}", end="")
    return model


# ---------------------------------------------------------------------------
# Experiment runners
# ---------------------------------------------------------------------------


def run_q1(
    df_e1: pd.DataFrame,
    features: list[str],
    horizon: int,
    csp_lookup: pd.Series,
    output_dir: Path,
) -> dict | None:
    """Q1: Train P10/P50/P90 on PV power target directly.

    Returns summary metrics dict for the experiment, or None on failure.
    """
    target = _target_col(horizon)
    print(f"\n  --- Q1 / t+{horizon}h ---")
    print(f"    Features: {len(features)}, Target: {target}")

    # Train 3 models
    models: dict[float, lgb.LGBMRegressor] = {}
    for alpha in ALPHAS:
        m = train_one_quantile(df_e1, features, target, horizon, alpha, "Q1", output_dir)
        if m is not None:
            models[alpha] = m

    if len(models) < 3:
        print("    FAILED: not all quantile models trained")
        return None

    # ---- Predict on test split ----
    full = df_e1.dropna(subset=[target]).copy()
    splits = _chronological_split(full)
    test = splits["test"].copy()
    X_te = test[features].values

    preds = {a: models[a].predict(X_te) for a in ALPHAS}

    # Valid-time alignment to fetch actual clearsky index
    test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(hours=horizon)

    # Fetch actual PV and clearsky index at valid time from original data
    df_base = pd.read_parquet(DATA_PATH)
    df_base["timestamp"] = pd.to_datetime(df_base["timestamp"], utc=True)
    full_actual = df_base[["timestamp", "pv_power_kw", "clearsky_index_ghi"]].copy()
    full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
    test = test.merge(
        full_actual, on="valid_time", how="left", suffixes=("", "_valid")
    )

    y_true = test["pv_power_kw_valid"].values
    csi_valid = test["clearsky_index_ghi_valid"].values
    solar_elev = test.get(f"solar_elevation_valid_{horizon}h", test.get(f"valid_h{horizon}h_solar_elevation", 90)).values

    # Physical clip predictions
    p10_pred = np.clip(preds[0.1], 0.0, PREDICTION_UPPER)
    p50_pred = np.clip(preds[0.5], 0.0, PREDICTION_UPPER)
    p90_pred = np.clip(preds[0.9], 0.0, PREDICTION_UPPER)

    # Overall evaluation
    day_mask = (solar_elev > SOLAR_ELEV_THRESHOLD) & np.isfinite(y_true) & (y_true >= 0)
    valid_mask = day_mask  # daytime only for meaningful metrics
    y_day = y_true[valid_mask]

    if len(y_day) < 50:
        print(f"    FAILED: insufficient daytime samples ({len(y_day)})")
        return None

    eval_metrics = evaluate_quantile(y_day, p10_pred[valid_mask], p50_pred[valid_mask], p90_pred[valid_mask], CAPACITY_KW)
    scenario_metrics = _scenario_stratified_metrics(
        y_day, p10_pred[valid_mask], p50_pred[valid_mask], p90_pred[valid_mask],
        csi_valid[valid_mask], np.ones(len(y_day), dtype=bool),  # all are daytime
    )

    results: dict = {
        "experiment": "Q1",
        "target": "pv_power_kw",
        "horizon_hours": horizon,
        "n_test": int(valid_mask.sum()),
        "note": "PV direct quantile",
        **eval_metrics,
        **scenario_metrics,
    }
    return results


def run_q2(
    df_base: pd.DataFrame,
    features: list[str],
    horizon: int,
    csp_lookup: pd.Series,
    output_dir: Path,
) -> dict | None:
    """Q2: Train P10/P50/P90 on CSI target, restore to PV space.

    Returns summary metrics dict for the experiment, or None on failure.
    """
    print(f"\n  --- Q2 / t+{horizon}h ---")

    # Build CSI target
    df = build_csi_target(df_base, horizon, csp_lookup)
    csi_target_col = f"csi_target_{horizon}h"
    csp_col = f"clear_sky_power_valid_{horizon}h"
    elev_col = f"solar_elevation_valid_{horizon}h"

    print(f"    Features: {len(features)}, Target: {csi_target_col}")

    # Train 3 models on CSI target
    models: dict[float, lgb.LGBMRegressor] = {}
    for alpha in ALPHAS:
        m = train_one_quantile(df, features, csi_target_col, horizon, alpha, "Q2", output_dir)
        if m is not None:
            models[alpha] = m

    if len(models) < 3:
        print("    FAILED: not all quantile models trained")
        return None

    # ---- Predict on test split ----
    full = df.dropna(subset=[csi_target_col]).copy()
    splits = _chronological_split(full)
    test = splits["test"].copy()
    X_te = test[features].values

    # Get CSI-space predictions
    preds_csi = {a: models[a].predict(X_te) for a in ALPHAS}

    # Valid-time alignment for clearsky index
    test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(hours=horizon)

    df_base_orig = pd.read_parquet(DATA_PATH)
    df_base_orig["timestamp"] = pd.to_datetime(df_base_orig["timestamp"], utc=True)
    full_actual = df_base_orig[["timestamp", "pv_power_kw", "clearsky_index_ghi"]].copy()
    full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
    test = test.merge(
        full_actual, on="valid_time", how="left", suffixes=("", "_valid")
    )

    y_pv_true = test["pv_power_kw_valid"].values
    csi_valid = test["clearsky_index_ghi_valid"].values
    csp_test = test[csp_col].values
    solar_elev = test[elev_col].values

    # Create common valid mask (daytime + finite PV)
    day_mask = (solar_elev > SOLAR_ELEV_THRESHOLD) & np.isfinite(y_pv_true) & (y_pv_true >= 0) & (csp_test > CSP_THRESHOLD * CAPACITY_KW)

    if day_mask.sum() < 50:
        print(f"    FAILED: insufficient daytime samples ({day_mask.sum()})")
        return None

    # Clip CSI predictions
    for a in ALPHAS:
        preds_csi[a] = np.clip(preds_csi[a], CSI_CLIP_LOW, CSI_CLIP_HIGH)

    # Restore CSI -> PV: PV_pred = CSI_pred * clear_sky_power
    p10_csi = preds_csi[0.1]
    p50_csi = preds_csi[0.5]
    p90_csi = preds_csi[0.9]

    # Sort to fix crossing (in CSI space)
    sorted_csi = np.sort([p10_csi, p50_csi, p90_csi], axis=0)

    # Restore to PV
    p10_pv = np.clip(sorted_csi[0] * csp_test, 0.0, PREDICTION_UPPER)
    p50_pv = np.clip(sorted_csi[1] * csp_test, 0.0, PREDICTION_UPPER)
    p90_pv = np.clip(sorted_csi[2] * csp_test, 0.0, PREDICTION_UPPER)

    y_pv = y_pv_true[day_mask]
    p10_d = p10_pv[day_mask]
    p50_d = p50_pv[day_mask]
    p90_d = p90_pv[day_mask]
    csi_d = csi_valid[day_mask]

    # Crossing rate in CSI space (raw, before sort)
    crossing_raw = float(np.mean((preds_csi[0.1] > preds_csi[0.5]) | (preds_csi[0.5] > preds_csi[0.9])))

    eval_metrics = evaluate_quantile(y_pv, p10_d, p50_d, p90_d, CAPACITY_KW)
    eval_metrics["crossing_rate_raw"] = crossing_raw  # report CSI-space crossing

    scenario_metrics = _scenario_stratified_metrics(
        y_pv, p10_d, p50_d, p90_d,
        csi_d, np.ones(len(y_pv), dtype=bool),
    )

    results: dict = {
        "experiment": "Q2",
        "target": "csi -> pv_power",
        "horizon_hours": horizon,
        "n_test": int(day_mask.sum()),
        "note": "CSI quantile restored to PV",
        **eval_metrics,
        **scenario_metrics,
    }
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(val: float) -> str:
    """Format a float value for table display."""
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return "  N/A    "
    return f"{val:<8.4f}"


def print_comparison_table(results: list[dict]) -> None:
    """Print formatted comparison table for Q1 and Q2 results."""
    print("\n" + "=" * 110)
    print("QUANTILE REGRESSION RESULTS")
    print("=" * 110)
    header = (
        f"{'Exp':<5} {'Horizon':<8} {'PICP':<8} {'MPIW':<8} {'nRMSE':<8}"
        f" {'Pinball':<10} {'Clear_PICP':<11} {'Mixed_PICP':<11}"
        f" {'Over_PICP':<11} {'Crossing':<9}"
    )
    print(header)
    print("-" * 110)

    ordered = sorted(results, key=lambda r: (r.get("horizon_hours", 0), r.get("experiment", "")))
    for r in ordered:
        exp = r.get("experiment", "?")
        h = f"t+{r.get('horizon_hours', '?')}h"
        picp = r.get("picp", float("nan"))
        mpiw = r.get("mpiw_capacity_ratio", float("nan"))
        nrmse = r.get("p50_capacity_nrmse", float("nan"))
        pinball = r.get("pinball_mean", float("nan"))
        c_picp = r.get("clear_picp", float("nan"))
        m_picp = r.get("mixed_picp", float("nan"))
        o_picp = r.get("overcast_picp", float("nan"))
        cross = r.get("crossing_rate_raw", float("nan"))
        print(
            f"{exp:<5} {h:<8} {_fmt(picp)} {_fmt(mpiw)} {_fmt(nrmse)}"
            f" {_fmt(pinball)} {_fmt(c_picp)} {_fmt(m_picp)}"
            f" {_fmt(o_picp)} {_fmt(cross)}"
        )

    print("=" * 110)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: train and evaluate quantile regression models."""
    print("=" * 70)
    print("PHASE 2: LightGBM Quantile Regression for PV Prediction")
    print("=" * 70)

    # Resolve paths
    root_dir = Path(__file__).resolve().parents[3]
    data_path = root_dir / DATA_PATH
    output_dir = root_dir / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Data:     {data_path}")
    print(f"Output:   {output_dir}")
    print(f"Capacity: {CAPACITY_KW} kW")

    # ---- 1. Load Stage3 dataset ----
    print("\n[1/5] Loading Stage3 dataset ...")
    if not data_path.exists():
        print(f"FATAL: {data_path} not found", file=sys.stderr)
        sys.exit(1)

    df = pd.read_parquet(data_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"  {len(df):,} rows, {len(df.columns)} columns "
          f"({df['timestamp'].dt.year.min()}-{df['timestamp'].dt.year.max()})")

    # ---- 2. Precompute clear-sky power lookup ----
    print("\n[2/5] Precomputing clear-sky power (leakage-free) ...")
    csp_lookup = precompute_clear_sky_lookup(df)
    print(f"  {len(csp_lookup)} hourly values: {csp_lookup.index.min()} to {csp_lookup.index.max()}")

    # ---- 3. Main experiments ----
    print("\n[3/5] Running quantile regression experiments ...")

    all_results: list[dict] = []

    for horizon in HORIZONS:
        # Build E1 features once per horizon, shared by Q1 and Q2
        print(f"\n{'=' * 60}")
        print(f"  Horizon: t+{horizon}h")
        print(f"{'=' * 60}")

        df_e1, features = _build_e1_features(df, horizon)
        print(f"  E1 features: {len(features)}")

        # Q1: PV power direct
        q1_result = run_q1(df_e1.copy(), features, horizon, csp_lookup, output_dir)
        if q1_result is not None:
            all_results.append(q1_result)

        # Q2: CSI -> PV (use same E1-enhanced DataFrame so features align)
        q2_result = run_q2(df_e1.copy(), features, horizon, csp_lookup, output_dir)
        if q2_result is not None:
            all_results.append(q2_result)

    # ---- 4. Save results ----
    print("\n[4/5] Saving results ...")

    metrics_df = pd.DataFrame(all_results)
    csv_path = output_dir / "quantile_metrics.csv"
    metrics_df.to_csv(csv_path, index=False, float_format="%.6f")
    print(f"  Metrics CSV: {csv_path}")

    json_path = output_dir / "quantile_results.json"
    json_path.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"  JSON:       {json_path}")

    # ---- 5. Print comparison table ----
    print_comparison_table(all_results)

    # ---- Summary ----
    print("\n[5/5] Summary")
    print(f"  Experiments completed: {len(all_results)}")
    print(f"  Models in:             {output_dir}")

    # Success criteria check
    print("\n  Success Criteria Check:")
    for r in all_results:
        exp = r.get("experiment", "?")
        h = r.get("horizon_hours", "?")
        picp = r.get("picp", float("nan"))
        mpiw = r.get("mpiw_capacity_ratio", float("nan"))
        clear_picp = r.get("clear_picp", float("nan"))
        mixed_picp = r.get("mixed_picp", float("nan"))
        overcast_picp = r.get("overcast_picp", float("nan"))
        clear_mpiw = r.get("clear_mpiw_kw", float("nan"))
        mixed_mpiw = r.get("mixed_mpiw_kw", float("nan"))
        crossing = r.get("crossing_rate_raw", float("nan"))
        nrmse = r.get("p50_capacity_nrmse", float("nan"))

        print(f"\n    {exp} t+{h}h:")
        print(f"      PICP={picp:.3f}  (target: 75-85%)")
        print(f"      Clear PICP={clear_picp:.3f}, Mixed PICP={mixed_picp:.3f}, Overcast PICP={overcast_picp:.3f}")
        print(f"      Clear MPIW={clear_mpiw:.4f}, Mixed MPIW={mixed_mpiw:.4f}  (expect Clear < Mixed)")
        print(f"      Crossing rate={crossing:.4f}  (target: < 5%)")
        print(f"      P50 nRMSE={nrmse:.4f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
