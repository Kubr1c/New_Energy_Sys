"""
Phase 1: CSI target reformulation for PV prediction.

Hypothesis
----------
CSI target (k = PV / clear_sky_power) isolates weather attenuation from the
deterministic diurnal cycle, allowing HRRR weather skill to transfer into PV
predictions that a direct PV-power target cannot capture (corr(HRRR_error,
PV_error) = 0.02).

Experiment Matrix (main: t+24h)
--------------------------------
| ID | Target      | Features                          | Purpose                          |
|----|-------------|-----------------------------------|----------------------------------|
| C0 | PV power    | E1 features (time+solar+ramp)     | Baseline                         |
| C1 | CSI         | Same as C0 (no HRRR)               | Target reformulation alone       |
| C2 | CSI         | C1 + HRRR (ghi, cloud, temp)       | HRRR skill via CSI               |
| C3 | Physical    | PV = clear_sky x HRRR_CSI          | Direct HRRR scaling (no ML)      |
| C4 | CSI         | C2 + all HRRR extra features       | Upper bound exploration          |

Sanity checks: t+6h  (C0 vs C1)
Ablations (t+24h): A1 (CSI clip) + A2 (clear_sky_power threshold)

Usage
-----
    python -m new_energy_sys.cli.train_csi_target

Output
------
    data/processed/pvdaq_nsrdb_2020_2022/csi_models/
        lightgbm_C{N}_{target}.pkl          Model files
        csi_ablation.csv                    Metrics table
    Updated inspection_predictions.parquet  Appended C0-C4 predictions

Reference
---------
- E1 best model: t+24h day_nRMSE = 0.335 (PV power target)
- HRRR GHI has MSE_Skill = 0.82 vs persistence
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pvlib
from pvlib.location import Location

from new_energy_sys.feature_enhancements import (
    add_valid_time_solar_features,
    add_ramp_features,
)
from new_energy_sys.modeling import _chronological_split

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet"
)
HRRR_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022"
    "/stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
)
INSPECTION_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.parquet"
)
OUTPUT_DIR = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/csi_models"
)

# PVDAQ site: Golden, CO (NSRDB grid cell centre)
SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}
CAPACITY_KW: float = SITE["capacity_kw"]
EFFICIENCY: float = 0.75  # residential system DC-AC derating factor

# Solar elevation threshold for daytime filtering (degrees above horizon)
SOLAR_ELEV_THRESHOLD: float = 5.0

# Default CSI clipping range
CSI_CLIP_LOW: float = 0.0
CSI_CLIP_HIGH: float = 1.2

# Default clear-sky power threshold (fraction of capacity)
# Only rows with clear_sky_power > CAPACITY_KW * CSP_THRESHOLD are valid
CSP_THRESHOLD: float = 0.05

# Prediction upper bound in PV space
PREDICTION_UPPER: float = CAPACITY_KW * 1.05

# Target columns in Stage3
ALL_TARGET_COLS: list[str] = [
    "target_pv_power_t_plus_1h",
    "target_pv_power_t_plus_6h",
    "target_pv_power_t_plus_24h",
]

# Hyperparameters (Stage5/E1 tuned for t+24h)
HP: dict[str, int | float] = {
    "learning_rate": 0.02,
    "max_depth": 10,
    "num_leaves": 45,
    "n_estimators": 1800,
    "colsample_bytree": 0.8,
    "min_child_samples": 35,
    "reg_alpha": 0.1,
    "reg_lambda": 0.8,
    "subsample": 0.85,
}


def _exclude_columns() -> set[str]:
    """Columns never used as model features (labels, timestamps, current PV)."""
    return {"timestamp", *ALL_TARGET_COLS, "pv_power_kw"}


# ---------------------------------------------------------------------------
# Clear-sky power computation (LEAKAGE-FREE)
# ---------------------------------------------------------------------------


def compute_clear_sky_power(timestamps: pd.DatetimeIndex) -> pd.Series:
    """Compute clear-sky PV power from solar geometry only.

    CRITICAL: This uses ONLY site static parameters + time + solar geometry.
    NO measured power, irradiance, temperature, or cloud cover enters the
    calculation.  This guarantees no data leakage.

    Formula:  P_clear = capacity_kw * GHI_clear_sky / 1000 * efficiency

    Parameters
    ----------
    timestamps : pd.DatetimeIndex
        UTC timestamps for which to compute clear-sky power.

    Returns
    -------
    pd.Series
        Clear-sky PV power in kW, indexed by input timestamps.
    """
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    # Solar position from pure geometry (pvlib uses NREL SPA algorithm)
    solar_pos = loc.get_solarposition(times=timestamps)
    # Ineichen clear-sky GHI — depends on Linke turbidity (site-dependent
    # climatological value from pvlib, NOT real-time measurements)
    clearsky = loc.get_clearsky(times=timestamps, solar_position=solar_pos)
    # Convert GHI to DC power with fixed efficiency derating
    power = CAPACITY_KW * clearsky["ghi"].values / 1000.0 * EFFICIENCY
    return pd.Series(power, index=timestamps)


def _precompute_clear_sky_lookup(df: pd.DataFrame) -> pd.Series:
    """Build a timestamp-indexed Series of clear-sky power for all needed times.

    Extends beyond dataset range by one day to cover t+24h valid times.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True)
    full_range = pd.date_range(
        start=ts.min() - pd.Timedelta(hours=1),
        end=ts.max() + pd.Timedelta(hours=25),
        freq="h",
        tz="UTC",
    )
    csp = compute_clear_sky_power(full_range)
    return csp  # pd.Series with DatetimeIndex -> float kW


# ---------------------------------------------------------------------------
# CSI target construction
# ---------------------------------------------------------------------------


def build_csi_target(
    df: pd.DataFrame,
    horizon_hours: int,
    csp_lookup: pd.Series,
    csi_clip_low: float = CSI_CLIP_LOW,
    csi_clip_high: float = CSI_CLIP_HIGH,
    csp_threshold: float = CSP_THRESHOLD,
) -> pd.DataFrame:
    """Compute CSI target and add it as a column for the given horizon.

    CSI = PV_power_at_valid / clear_sky_power_at_valid

    IMPORTANT: Filtering uses clear_sky_power and solar elevation ONLY.
    Using actual PV power as a filter would introduce selection bias and
    produce optimistically biased evaluation.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``'timestamp'`` and the target PV column for the horizon.
    horizon_hours : int
        Forecast horizon (1, 6, or 24).
    csp_lookup : pd.Series
        Timestamp-indexed clear-sky power lookup from _precompute_clear_sky_lookup.
    csi_clip_low : float
        Lower bound for CSI clipping.
    csi_clip_high : float
        Upper bound for CSI clipping.
    csp_threshold : float
        Minimum clear-sky power as fraction of capacity for a valid sample.

    Returns
    -------
    pd.DataFrame
        Copy of ``df`` with additional columns:
        - ``clear_sky_power_valid_{h}h`` — clear-sky power at valid time
        - ``solar_elevation_valid_{h}h`` — solar elevation at valid time (deg)
        - ``csi_target_{h}h`` — clipped CSI target (NaN for invalid samples)
    """
    result = df.copy()

    target_col = f"target_pv_power_t_plus_{horizon_hours}h"
    valid_time = pd.DatetimeIndex(
        result["timestamp"] + pd.Timedelta(hours=horizon_hours)
    )

    # Look up clear-sky power at valid time
    csp_valid = csp_lookup.reindex(valid_time).values
    result[f"clear_sky_power_valid_{horizon_hours}h"] = csp_valid

    # Compute solar elevation at valid time (for daytime gating)
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(times=valid_time)
    elev_valid = solar_pos["elevation"].values
    result[f"solar_elevation_valid_{horizon_hours}h"] = elev_valid

    # --- Filtering criteria (ONLY clear_sky_power + solar elevation) ---
    min_csp = csp_threshold * CAPACITY_KW
    valid_mask = (elev_valid > SOLAR_ELEV_THRESHOLD) & (csp_valid > min_csp)

    # Raw CSI = PV / clear_sky_power
    pv_actual = result[target_col].values
    csi_raw = np.where(
        valid_mask,
        pv_actual / np.maximum(csp_valid, 1e-6),
        np.nan,
    )

    # Clamp to physically plausible range
    csi_clipped = np.clip(csi_raw, csi_clip_low, csi_clip_high)
    col_name = f"csi_target_{horizon_hours}h"
    result[col_name] = np.where(valid_mask, csi_clipped, np.nan)

    # Report filtering statistics (only first call — avoid spam)
    n_total = len(result)
    n_valid = int(valid_mask.sum())
    print(
        f"    CSI valid samples: {n_valid}/{n_total}"
        f" ({n_valid / n_total * 100:.1f}%)"
    )
    print(f"    CSI clipped at 0:  {(csi_raw < 0).sum()}")
    n_gt_1 = int((csi_raw > 1.0).sum())
    n_gt_12 = int((csi_raw > 1.2).sum())
    print(f"    CSI > 1.0:          {n_gt_1}")
    print(f"    CSI > 1.2 (clipped):{n_gt_12}")

    return result


# ---------------------------------------------------------------------------
# HRRR data loading & merging
# ---------------------------------------------------------------------------


def _load_and_filter_hrrr(root_dir: Path) -> pd.DataFrame:
    """Load decomposed HRRR data and filter to standard init cycles.

    Only keeps rows where:
    - Lead time is 24-44 hours (matches t+24h forecast)
    - Issue time is 00/06/12/18 UTC (standard NCEP cycles)
    """
    hrrr_path = root_dir / HRRR_PATH
    if not hrrr_path.exists():
        print(f"WARNING: HRRR data not found at {hrrr_path}", file=sys.stderr)
        return pd.DataFrame()

    hrrr = pd.read_parquet(hrrr_path)
    hrrr["timestamp"] = pd.to_datetime(hrrr["timestamp"], utc=True)
    hrrr["weather_forecast_issue_time"] = pd.to_datetime(
        hrrr["weather_forecast_issue_time"], utc=True
    )

    # Apply standard init filter
    valid = (
        (hrrr["weather_forecast_lead_time_hour"] >= 24)
        & (hrrr["weather_forecast_lead_time_hour"] <= 44)
        & (hrrr["weather_forecast_issue_time"].dt.hour.isin([0, 6, 12, 18]))
    )
    hrrr = hrrr[valid].copy()
    print(f"    HRRR filtered: {len(hrrr)} rows from standard init cycles")
    return hrrr


def _merge_hrrr_features(
    df: pd.DataFrame,
    hrrr: pd.DataFrame,
    horizon_hours: int,
) -> pd.DataFrame:
    """Merge HRRR weather features at valid time.

    CRITICAL: Renames ALL HRRR columns to ``hrrr_*`` BEFORE the merge so
    that origin-time Stage3 columns (``ghi_wm2``, ``temperature_c``, etc.)
    are never overwritten or renamed — only the valid-time HRRR values get
    the ``hrrr_*`` prefix.

    Adds:
    - ``hrrr_ghi_wm2``, ``hrrr_cloud_cover_pct``, ``hrrr_temperature_c``
    - ``hrrr_csi_valid`` = HRRR_ghi / clearsky_ghi
    - ``hrrr_*`` (all other HRRR weather columns)

    Parameters
    ----------
    df : pd.DataFrame
        Must have ``'timestamp'`` column and ``clear_sky_power_valid_{h}h``.
    hrrr : pd.DataFrame
        Filtered HRRR data with ``'timestamp'`` (=valid_time) as merge key.
    horizon_hours : int
        Forecast horizon used to compute valid_time.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with HRRR columns merged (all prefixed ``hrrr_``).
    """
    result = df.copy()
    result["_valid_time"] = pd.to_datetime(
        result["timestamp"]
    ) + pd.Timedelta(hours=horizon_hours)

    # ---- Prepare HRRR: prefix ALL weather columns BEFORE merge ----
    # Selecting only the columns we want, then renaming them so the merge
    # keys are _valid_time and all other columns have the hrrr_ prefix.
    weather_cols = [
        "ghi_wm2",
        "cloud_cover_pct",
        "temperature_c",
        "relative_humidity_pct",
        "wind_speed_ms",
        "wind_direction_deg",
        "pressure_hpa",
        "dew_point_c",
        "precipitation_mm",
    ]

    hrrr_merge = hrrr[["timestamp"] + weather_cols].copy()
    hrrr_merge = hrrr_merge.rename(
        columns={"timestamp": "_valid_time"}
    )
    # Prefix all weather columns before merge so they never collide
    col_rename = {c: f"hrrr_{c}" for c in weather_cols if c in hrrr_merge.columns}
    hrrr_merge = hrrr_merge.rename(columns=col_rename)

    result = result.merge(hrrr_merge, on="_valid_time", how="left")
    result.drop(columns=["_valid_time"], inplace=True)

    # --- Compute HRRR CSI validity: hrrr_csi = HRRR_ghi / clearsky_ghi ---
    csp_col = f"clear_sky_power_valid_{horizon_hours}h"
    # Recover clearsky_ghi from clear_sky_power:
    #   csp = CAPACITY * clearsky_ghi / 1000 * EFF
    #   => clearsky_ghi = csp * 1000 / (CAPACITY * EFF)
    csp_vals = result[csp_col].values
    clearsky_ghi_recovered = csp_vals * 1000.0 / (CAPACITY_KW * EFFICIENCY)
    hrrr_ghi = result["hrrr_ghi_wm2"].fillna(0.0).values
    result["hrrr_csi_valid"] = np.where(
        clearsky_ghi_recovered > 1.0,
        hrrr_ghi / np.maximum(clearsky_ghi_recovered, 1e-6),
        np.nan,
    )
    result["hrrr_csi_valid"] = result["hrrr_csi_valid"].clip(0.0, 2.0)

    n_hrrr = int(result["hrrr_ghi_wm2"].notna().sum())
    print(f"    HRRR merged: {n_hrrr}/{len(result)} rows have HRRR data")
    return result


# ---------------------------------------------------------------------------
# Feature filtering
# ---------------------------------------------------------------------------


def _build_feature_list(
    df: pd.DataFrame,
    experiment: str,
    horizon_hours: int,
) -> list[str]:
    """Return valid numeric feature columns for the given experiment.

    Excludes label columns, timestamp, internal CSI helper columns,
    and experiment-specific HRRR columns.
    """
    exclude = _exclude_columns()

    # CSI helper columns are NEVER features (they are the target or
    # internal scaffolding for evaluation).  Including them would cause
    # catastrophic data leakage.
    for h in (1, 6, 24):
        exclude.add(f"csi_target_{h}h")
        exclude.add(f"clear_sky_power_valid_{h}h")
        exclude.add(f"solar_elevation_valid_{h}h")

    # For non-HRRR experiments, exclude ALL HRRR columns
    if experiment in ("C0", "C1"):
        exclude.update(
            {
                "hrrr_ghi_wm2",
                "hrrr_cloud_cover_pct",
                "hrrr_temperature_c",
                "hrrr_csi_valid",
                "hrrr_relative_humidity_pct",
                "hrrr_wind_speed_ms",
                "hrrr_wind_direction_deg",
                "hrrr_pressure_hpa",
                "hrrr_dew_point_c",
                "hrrr_precipitation_mm",
            }
        )

    # C2: only use basic HRRR features (ghi, cloud, temp, csi),
    # exclude the extended weather columns
    if experiment == "C2":
        exclude.update(
            {
                "hrrr_relative_humidity_pct",
                "hrrr_wind_speed_ms",
                "hrrr_wind_direction_deg",
                "hrrr_pressure_hpa",
                "hrrr_dew_point_c",
                "hrrr_precipitation_mm",
            }
        )

    allowed_dtypes = (
        np.float64, np.float32, np.int64, np.int32,
        np.int8, np.int16, np.uint8,
    )
    return [
        c
        for c in df.columns
        if c not in exclude and df[c].dtype in allowed_dtypes
    ]


# ---------------------------------------------------------------------------
# Experiment feature preparation (E1-style enhancements)
# ---------------------------------------------------------------------------


def _add_e1_features(
    df: pd.DataFrame,
    horizon_hours: int,
) -> pd.DataFrame:
    """Add E1 feature set: valid-time solar geometry + ramp rates.

    This is the same feature pipeline used by the E1 model (best baseline).
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


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _evaluate_predictions(
    predictions: np.ndarray,
    actual: np.ndarray,
    solar_elevation: np.ndarray,
    csi_actual: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> dict:
    """Compute comprehensive evaluation metrics in PV space.

    Parameters
    ----------
    predictions : np.ndarray
        Predicted PV power (kW), already restored from CSI if applicable.
    actual : np.ndarray
        Actual PV power (kW) at the valid time.
    solar_elevation : np.ndarray
        Solar elevation at valid time (degrees).
    csi_actual : np.ndarray
        Actual clearsky index at valid time (for weather stratification).
    valid_mask : np.ndarray | None
        Boolean mask for valid samples (e.g., daytime only). If None, uses
        solar_elevation > 5 degrees.

    Returns
    -------
    dict
        Flat dictionary of metrics.
    """
    if valid_mask is None:
        valid_mask = solar_elevation > SOLAR_ELEV_THRESHOLD

    error = predictions - actual
    abs_error = np.abs(error)

    # All-samples metrics
    all_rmse = float(np.sqrt(np.nanmean(error**2)))
    all_mae = float(np.nanmean(abs_error))
    all_bias = float(np.nanmean(error))
    all_mean_actual = float(np.nanmean(actual))

    # Daytime metrics (solar_elevation > 5 deg)
    day_mask = valid_mask & np.isfinite(actual) & np.isfinite(predictions)
    day_err = error[day_mask]
    day_actual = actual[day_mask]
    day_rmse = float(np.sqrt(np.nanmean(day_err**2))) if len(day_err) > 0 else float("nan")
    day_mae = float(np.nanmean(np.abs(day_err))) if len(day_err) > 0 else float("nan")
    day_bias = float(np.nanmean(day_err)) if len(day_err) > 0 else float("nan")
    day_mean_actual = float(np.nanmean(day_actual)) if len(day_actual) > 0 else float("nan")
    day_nrmse = day_rmse / day_mean_actual if day_mean_actual > 0 else float("nan")
    all_nrmse = all_rmse / all_mean_actual if all_mean_actual > 0 else float("nan")

    # Weather-stratified metrics (using clear-sky index at valid time)
    metrics = {
        "all_rmse_kw": all_rmse,
        "all_nrmse": all_nrmse,
        "all_mae_kw": all_mae,
        "all_bias_kw": all_bias,
        "day_n": int(day_mask.sum()),
        "day_rmse_kw": day_rmse,
        "day_nrmse": day_nrmse,
        "day_mae_kw": day_mae,
        "day_bias_kw": day_bias,
    }

    for scenario, csi_range in [
        ("clear", (0.7, float("inf"))),
        ("mixed", (0.3, 0.7)),
        ("overcast", (0.0, 0.3)),
    ]:
        lo, hi = csi_range
        sc_mask = day_mask & (csi_actual >= lo) & (csi_actual < hi)
        n = int(sc_mask.sum())
        if n == 0:
            metrics.update({
                f"{scenario}_n": 0,
                f"{scenario}_rmse_kw": float("nan"),
                f"{scenario}_nrmse": float("nan"),
                f"{scenario}_mae_kw": float("nan"),
                f"{scenario}_bias_kw": float("nan"),
            })
            continue
        sc_err = error[sc_mask]
        sc_actual = actual[sc_mask]
        sc_rmse = float(np.sqrt(np.nanmean(sc_err**2)))
        sc_mean = float(np.nanmean(sc_actual))
        metrics.update({
            f"{scenario}_n": n,
            f"{scenario}_rmse_kw": sc_rmse,
            f"{scenario}_nrmse": sc_rmse / sc_mean if sc_mean > 0 else float("nan"),
            f"{scenario}_mae_kw": float(np.nanmean(np.abs(sc_err))),
            f"{scenario}_bias_kw": float(np.nanmean(sc_err)),
        })

    return metrics


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


def _clip_predictions(pred: np.ndarray) -> np.ndarray:
    """Physically clip predictions to [0, 1.05 * capacity] kW."""
    return np.clip(pred, 0.0, PREDICTION_UPPER)


def run_experiment(
    df_base: pd.DataFrame,
    hrrr: pd.DataFrame,
    csp_lookup: pd.Series,
    horizon_hours: int,
    experiment: str,
    output_dir: Path,
    csi_clip_low: float = CSI_CLIP_LOW,
    csi_clip_high: float = CSI_CLIP_HIGH,
    csp_threshold: float = CSP_THRESHOLD,
) -> tuple[pd.DataFrame, dict]:
    """Run one CSI experiment, returning predictions DataFrame and metrics dict.

    Parameters
    ----------
    df_base : pd.DataFrame
        Base Stage3 dataset (no E1 features added yet).
    hrrr : pd.DataFrame
        Filtered HRRR data (empty DataFrame if not available).
    csp_lookup : pd.Series
        Clear-sky power lookup for CSI computation.
    horizon_hours : int
        Forecast horizon for training label.
    experiment : str
        Experiment ID: C0, C1, C2, C4.
    output_dir : Path
        Directory for model persistence.
    csi_clip_low, csi_clip_high, csp_threshold : float
        CSI construction parameters (used for ablation).

    Returns
    -------
    tuple[pd.DataFrame, dict]
        (test_predictions_df, metrics_dict)
    """
    target_label = f"target_pv_power_t_plus_{horizon_hours}h"
    print(f"\n  --- {experiment} / {target_label} ---")

    # ---- Step 1: Add E1 features (all experiments need these) ----------
    df = _add_e1_features(df_base, horizon_hours)

    # ---- Step 2: Compute clear-sky power and CSI target ----------------
    # (even C0 computes it for evaluation — the clear-sky power at valid time
    #  is used for weather stratification during scoring)
    df = build_csi_target(
        df, horizon_hours, csp_lookup,
        csi_clip_low=csi_clip_low,
        csi_clip_high=csi_clip_high,
        csp_threshold=csp_threshold,
    )
    csp_col = f"clear_sky_power_valid_{horizon_hours}h"
    csi_target_col = f"csi_target_{horizon_hours}h"
    elev_col = f"solar_elevation_valid_{horizon_hours}h"

    # ---- Step 3: Merge HRRR features (for C2, C4) ---------------------
    if experiment in ("C2", "C4") and len(hrrr) > 0:
        df = _merge_hrrr_features(df, hrrr, horizon_hours)

    # ---- Step 4: Determine features and target -------------------------
    features = _build_feature_list(df, experiment, horizon_hours)

    if experiment == "C0":
        target_col = target_label
    else:
        # C1, C2, C4: CSI target
        target_col = csi_target_col

    print(f"    Features: {len(features)}")
    print(f"    Target:   {target_col}")

    # ---- Step 5: Chronological split -----------------------------------
    # Drop rows with NaN target before splitting (NaN CSI = nighttime/invalid)
    train_df = df.dropna(subset=[target_col]).copy()
    if len(train_df) < len(df):
        print(
            f"    Dropped {len(df) - len(train_df)} rows with NaN target"
            f" ({100 * (1 - len(train_df) / len(df)):.1f}%)"
        )

    splits = _chronological_split(train_df)

    # ---- Step 6: Train LightGBM ----------------------------------------
    model = lgb.LGBMRegressor(
        objective="regression",
        boosting_type="gbdt",
        n_estimators=HP["n_estimators"],
        learning_rate=HP["learning_rate"],
        num_leaves=HP["num_leaves"],
        max_depth=HP["max_depth"],
        min_child_samples=HP["min_child_samples"],
        subsample=HP["subsample"],
        subsample_freq=1,
        colsample_bytree=HP["colsample_bytree"],
        reg_alpha=HP["reg_alpha"],
        reg_lambda=HP["reg_lambda"],
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        splits["train"][features],
        splits["train"][target_col],
        eval_set=[(splits["validation"][features], splits["validation"][target_col])],
        eval_metric="rmse",
        callbacks=[
            lgb.early_stopping(stopping_rounds=80, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    )
    best_iter = int(model.best_iteration_ or model.n_estimators)
    print(f"    Best iteration: {best_iter}")
    if "training" in model.best_score_:
        train_rmse = model.best_score_["training"]["rmse"]
        print(f"    Train RMSE:   {train_rmse:.4f}")
    val_rmse = model.best_score_["valid_0"]["rmse"]
    print(f"    Val RMSE:     {val_rmse:.4f}")

    # ---- Step 7: Predict on test set -----------------------------------
    test = splits["test"].copy()
    raw_pred = model.predict(test[features], num_iteration=model.best_iteration_)

    # ---- Step 8: Restore to PV space if CSI target ---------------------
    if experiment == "C0":
        pred_kw = _clip_predictions(raw_pred)
    else:
        # CSI -> PV: PV_pred = CSI_pred * clear_sky_power_at_valid
        csp_test = test[csp_col].values
        pred_kw = _clip_predictions(raw_pred * csp_test)

    test["prediction_kw"] = pred_kw

    # ---- Step 9: Valid-time alignment ---------------------------------
    test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(
        hours=horizon_hours
    )
    test["origin_time"] = pd.to_datetime(test["timestamp"])

    # Fetch actual PV and clearsky index at valid time from the full dataset
    full_actual = df_base[
        ["timestamp", "pv_power_kw", "clearsky_index_ghi"]
    ].copy()
    full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
    test = test.merge(
        full_actual, on="valid_time", how="left", suffixes=("", "_valid")
    )

    actual_kw = test["pv_power_kw_valid"].values
    csi_valid = test["clearsky_index_ghi_valid"].values
    solar_elev_test = test[elev_col].values

    # Overall valid-time mask (daytime + finite actual)
    valid_test_mask = (
        (solar_elev_test > SOLAR_ELEV_THRESHOLD)
        & np.isfinite(actual_kw)
        & (actual_kw >= 0)
    )

    metrics = _evaluate_predictions(
        pred_kw, actual_kw, solar_elev_test, csi_valid, valid_test_mask,
    )
    metrics["experiment"] = experiment
    metrics["target"] = target_label
    metrics["horizon_hours"] = horizon_hours
    metrics["feature_count"] = len(features)
    metrics["best_iteration"] = best_iter
    # nRMSE/capacity (normalised by nameplate capacity, not mean)
    metrics["all_capacity_nrmse"] = (
        metrics["all_rmse_kw"] / CAPACITY_KW
        if metrics.get("all_rmse_kw") is not None and np.isfinite(metrics["all_rmse_kw"])
        else float("nan")
    )
    metrics["day_capacity_nrmse"] = (
        metrics["day_rmse_kw"] / CAPACITY_KW
        if metrics.get("day_rmse_kw") is not None and np.isfinite(metrics["day_rmse_kw"])
        else float("nan")
    )

    # ---- Step 10: Save model -------------------------------------------
    target_short = target_label.replace("target_pv_power_t_plus_", "t")
    model_name = f"lightgbm_{experiment}_{target_short}.pkl"
    model_path = output_dir / model_name
    with open(model_path, "wb") as f:
        pickle.dump(
            {
                "model": model,
                "features": features,
                "target": target_col,
                "experiment": experiment,
                "horizon_hours": horizon_hours,
                "capacity_kw": CAPACITY_KW,
                "prediction_lower_bound_kw": 0.0,
                "prediction_upper_bound_kw": PREDICTION_UPPER,
                "csi_clip": (csi_clip_low, csi_clip_high),
                "csp_threshold": csp_threshold,
                "best_iteration": best_iter,
            },
            f,
        )
    print(f"    Model saved: {model_path.name}")

    # ---- Build prediction output DataFrame -----------------------------
    pred_out = pd.DataFrame({
        "origin_time": test["origin_time"],
        "valid_time": test["valid_time"],
        "horizon_hours": horizon_hours,
        "experiment": experiment.lower(),
        "model_name": "lightgbm",
        "model_version": "csi_phase1",
        "feature_set": f"{experiment}_{len(features)}feat",
        "target_type": "csi" if experiment != "C0" else "pv_power",
        "raw_prediction_kw": raw_pred,
        "prediction_kw": pred_kw,
        "actual_kw": actual_kw,
        "error_kw": pred_kw - actual_kw,
        "abs_error_kw": np.abs(pred_kw - actual_kw),
        "ghi_wm2": test.get("ghi_wm2", np.nan),
        "clearsky_ghi_wm2": test.get("clearsky_ghi_wm2", np.nan),
        "solar_elevation_deg": solar_elev_test,
        "cloud_cover_pct": test.get("cloud_cover_pct", np.nan),
        "csi_valid": csi_valid,
        "split": "test",
    })

    # Add scenario classification for the prediction output
    pred_out["scenario"] = "night"
    day_idx = pred_out["solar_elevation_deg"] > SOLAR_ELEV_THRESHOLD
    pred_out.loc[day_idx & (pred_out["csi_valid"] >= 0.7), "scenario"] = "clear"
    pred_out.loc[
        day_idx & (pred_out["csi_valid"] >= 0.3) & (pred_out["csi_valid"] < 0.7),
        "scenario",
    ] = "mixed"
    pred_out.loc[day_idx & (pred_out["csi_valid"] < 0.3), "scenario"] = "overcast"

    print(f"    Test predictions: {len(pred_out)} rows")
    _print_metrics_row(experiment, target_short, metrics)

    return pred_out, metrics


# ---------------------------------------------------------------------------
# C3: Physical baseline (no ML)
# ---------------------------------------------------------------------------


def run_c3_physical(
    df_base: pd.DataFrame,
    hrrr: pd.DataFrame,
    csp_lookup: pd.Series,
    horizon_hours: int,
) -> tuple[pd.DataFrame, dict]:
    """C3: Physical baseline — PV_pred = clear_sky_power * HRRR_CSI.

    This is a pure physics-based model with NO machine learning. It directly
    scales the clear-sky power by the HRRR GHI / clearsky GHI ratio.

    Restricted to rows where HRRR data is available (2021-2022).
    """
    target_label = f"target_pv_power_t_plus_{horizon_hours}h"
    print(f"\n  --- C3 / {target_label} (Physical baseline) ---")

    df = _add_e1_features(df_base, horizon_hours)
    df = build_csi_target(df, horizon_hours, csp_lookup)
    csp_col = f"clear_sky_power_valid_{horizon_hours}h"
    elev_col = f"solar_elevation_valid_{horizon_hours}h"

    # Merge HRRR
    df = _merge_hrrr_features(df, hrrr, horizon_hours)

    # Chronological split (full dataset, since C3 doesn't train)
    splits = _chronological_split(df)
    test = splits["test"].copy()

    # C3 prediction: PV = clear_sky_power * hrrr_csi_valid
    csp_test = test[csp_col].values
    hrrr_csi = test["hrrr_csi_valid"].values
    # Fallback: if HRRR CSI is NaN, use 1.0 (clear sky assumption)
    hrrr_csi = np.where(np.isfinite(hrrr_csi), hrrr_csi, 1.0)
    pred_kw = _clip_predictions(csp_test * hrrr_csi)

    test["prediction_kw"] = pred_kw
    test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(
        hours=horizon_hours
    )
    test["origin_time"] = pd.to_datetime(test["timestamp"])

    # Valid-time alignment
    full_actual = df_base[
        ["timestamp", "pv_power_kw", "clearsky_index_ghi"]
    ].copy()
    full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
    test = test.merge(
        full_actual, on="valid_time", how="left", suffixes=("", "_valid")
    )

    actual_kw = test["pv_power_kw_valid"].values
    csi_valid = test["clearsky_index_ghi_valid"].values
    solar_elev_test = test[elev_col].values

    valid_test_mask = (
        (solar_elev_test > SOLAR_ELEV_THRESHOLD)
        & np.isfinite(actual_kw)
        & (actual_kw >= 0)
    )

    metrics = _evaluate_predictions(
        pred_kw, actual_kw, solar_elev_test, csi_valid, valid_test_mask,
    )
    metrics["experiment"] = "C3"
    metrics["target"] = target_label
    metrics["horizon_hours"] = horizon_hours
    metrics["feature_count"] = 0  # physical baseline

    _print_metrics_row("C3", f"t_{horizon_hours}h", metrics)

    # Add capacity-normalised metrics
    metrics["all_capacity_nrmse"] = (
        metrics["all_rmse_kw"] / CAPACITY_KW
        if np.isfinite(metrics.get("all_rmse_kw", float("nan")))
        else float("nan")
    )
    metrics["day_capacity_nrmse"] = (
        metrics["day_rmse_kw"] / CAPACITY_KW
        if np.isfinite(metrics.get("day_rmse_kw", float("nan")))
        else float("nan")
    )

    # Build prediction output
    pred_out = pd.DataFrame({
        "origin_time": test["origin_time"],
        "valid_time": test["valid_time"],
        "horizon_hours": horizon_hours,
        "experiment": "c3",
        "model_name": "physical",
        "model_version": "hrrr_csi_scaling",
        "feature_set": "physical_0feat",
        "target_type": "pv_power",
        "raw_prediction_kw": pred_kw,
        "prediction_kw": pred_kw,
        "actual_kw": actual_kw,
        "error_kw": pred_kw - actual_kw,
        "abs_error_kw": np.abs(pred_kw - actual_kw),
        "ghi_wm2": test.get("ghi_wm2", np.nan),
        "clearsky_ghi_wm2": test.get("clearsky_ghi_wm2", np.nan),
        "solar_elevation_deg": solar_elev_test,
        "cloud_cover_pct": test.get("cloud_cover_pct", np.nan),
        "csi_valid": csi_valid,
        "split": "test",
        "scenario": "night",
    })
    day_idx = pred_out["solar_elevation_deg"] > SOLAR_ELEV_THRESHOLD
    pred_out.loc[day_idx & (pred_out["csi_valid"] >= 0.7), "scenario"] = "clear"
    pred_out.loc[
        day_idx & (pred_out["csi_valid"] >= 0.3) & (pred_out["csi_valid"] < 0.7),
        "scenario",
    ] = "mixed"
    pred_out.loc[day_idx & (pred_out["csi_valid"] < 0.3), "scenario"] = "overcast"

    print(f"    Test predictions: {len(pred_out)} rows")
    return pred_out, metrics


# ---------------------------------------------------------------------------
# Ablation experiments
# ---------------------------------------------------------------------------


def run_ablations(
    df_base: pd.DataFrame,
    hrrr: pd.DataFrame,
    csp_lookup: pd.Series,
    horizon_hours: int,
    output_dir: Path,
) -> list[dict]:
    """Run CSI ablation experiments A1 (clip) and A2 (threshold).

    All ablations use C1-like setup (CSI target, E1 features, no HRRR).
    """
    all_ablation_metrics: list[dict] = []
    target_label = f"target_pv_power_t_plus_{horizon_hours}h"
    target_short = target_label.replace("target_pv_power_t_plus_", "t")

    # ---- A1: CSI clip range ------------------------------------------------
    print("\n" + "=" * 60)
    print("ABLATION A1: CSI clip boundaries")
    print("=" * 60)

    for clip_high, label in [(1.0, "clip10"), (1.2, "clip12"), (99.0, "noclip")]:
        clip_low = 0.0
        # Build a unique experiment name for model file
        exp_name = f"A1_{label}"
        print(f"\n  --- {exp_name} (clip [{clip_low}, {clip_high}]) ---")

        df = _add_e1_features(df_base, horizon_hours)
        df = build_csi_target(
            df, horizon_hours, csp_lookup,
            csi_clip_low=clip_low,
            csi_clip_high=clip_high,
        )
        target_col = f"csi_target_{horizon_hours}h"
        features = _build_feature_list(df, "C1", horizon_hours)
        train_df = df.dropna(subset=[target_col]).copy()
        splits = _chronological_split(train_df)

        model = lgb.LGBMRegressor(
            objective="regression",
            boosting_type="gbdt",
            n_estimators=HP["n_estimators"],
            learning_rate=HP["learning_rate"],
            num_leaves=HP["num_leaves"],
            max_depth=HP["max_depth"],
            min_child_samples=HP["min_child_samples"],
            subsample=HP["subsample"],
            subsample_freq=1,
            colsample_bytree=HP["colsample_bytree"],
            reg_alpha=HP["reg_alpha"],
            reg_lambda=HP["reg_lambda"],
            random_state=42,
            n_jobs=-1,
            verbosity=-1,
        )
        model.fit(
            splits["train"][features],
            splits["train"][target_col],
            eval_set=[(splits["validation"][features], splits["validation"][target_col])],
            eval_metric="rmse",
            callbacks=[
                lgb.early_stopping(stopping_rounds=80, verbose=False),
                lgb.log_evaluation(period=0),
            ],
        )
        best_iter = int(model.best_iteration_ or model.n_estimators)
        test = splits["test"].copy()
        raw_pred = model.predict(test[features], num_iteration=model.best_iteration_)
        csp_col = f"clear_sky_power_valid_{horizon_hours}h"
        pred_kw = _clip_predictions(raw_pred * test[csp_col].values)

        test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(hours=horizon_hours)
        full_actual = df_base[["timestamp", "pv_power_kw", "clearsky_index_ghi"]].copy()
        full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
        test = test.merge(full_actual, on="valid_time", how="left", suffixes=("", "_valid"))

        elev_col = f"solar_elevation_valid_{horizon_hours}h"
        solar_elev_test = test[elev_col].values
        actual_kw = test["pv_power_kw_valid"].values
        csi_valid = test["clearsky_index_ghi_valid"].values
        valid_test_mask = (
            (solar_elev_test > SOLAR_ELEV_THRESHOLD)
            & np.isfinite(actual_kw) & (actual_kw >= 0)
        )
        m = _evaluate_predictions(
            pred_kw, actual_kw, solar_elev_test, csi_valid, valid_test_mask,
        )
        m["experiment"] = exp_name
        m["target"] = target_label
        m["horizon_hours"] = horizon_hours
        m["feature_count"] = len(features)
        m["best_iteration"] = best_iter
        _print_metrics_row(exp_name, target_short, m)
        all_ablation_metrics.append(m)

    # ---- A2: clear-sky power threshold -------------------------------------
    print("\n" + "=" * 60)
    print("ABLATION A2: clear_sky_power threshold")
    print("=" * 60)

    for thresh, label in [(0.03, "thr003"), (0.05, "thr005"), (0.10, "thr010")]:
        exp_name = f"A2_{label}"
        print(f"\n  --- {exp_name} (threshold {thresh}) ---")

        df = _add_e1_features(df_base, horizon_hours)
        df = build_csi_target(
            df, horizon_hours, csp_lookup,
            csp_threshold=thresh,
        )
        target_col = f"csi_target_{horizon_hours}h"
        features = _build_feature_list(df, "C1", horizon_hours)
        train_df = df.dropna(subset=[target_col]).copy()
        splits = _chronological_split(train_df)

        model = lgb.LGBMRegressor(
            objective="regression",
            boosting_type="gbdt",
            n_estimators=HP["n_estimators"],
            learning_rate=HP["learning_rate"],
            num_leaves=HP["num_leaves"],
            max_depth=HP["max_depth"],
            min_child_samples=HP["min_child_samples"],
            subsample=HP["subsample"],
            subsample_freq=1,
            colsample_bytree=HP["colsample_bytree"],
            reg_alpha=HP["reg_alpha"],
            reg_lambda=HP["reg_lambda"],
            random_state=42,
            n_jobs=-1,
            verbosity=-1,
        )
        model.fit(
            splits["train"][features],
            splits["train"][target_col],
            eval_set=[(splits["validation"][features], splits["validation"][target_col])],
            eval_metric="rmse",
            callbacks=[
                lgb.early_stopping(stopping_rounds=80, verbose=False),
                lgb.log_evaluation(period=0),
            ],
        )
        best_iter = int(model.best_iteration_ or model.n_estimators)
        test = splits["test"].copy()
        raw_pred = model.predict(test[features], num_iteration=model.best_iteration_)
        csp_col = f"clear_sky_power_valid_{horizon_hours}h"
        pred_kw = _clip_predictions(raw_pred * test[csp_col].values)

        test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(hours=horizon_hours)
        full_actual = df_base[["timestamp", "pv_power_kw", "clearsky_index_ghi"]].copy()
        full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
        test = test.merge(full_actual, on="valid_time", how="left", suffixes=("", "_valid"))

        elev_col = f"solar_elevation_valid_{horizon_hours}h"
        solar_elev_test = test[elev_col].values
        actual_kw = test["pv_power_kw_valid"].values
        csi_valid = test["clearsky_index_ghi_valid"].values
        valid_test_mask = (
            (solar_elev_test > SOLAR_ELEV_THRESHOLD)
            & np.isfinite(actual_kw) & (actual_kw >= 0)
        )
        m = _evaluate_predictions(
            pred_kw, actual_kw, solar_elev_test, csi_valid, valid_test_mask,
        )
        m["experiment"] = exp_name
        m["target"] = target_label
        m["horizon_hours"] = horizon_hours
        m["feature_count"] = len(features)
        m["best_iteration"] = best_iter
        _print_metrics_row(exp_name, target_short, m)
        all_ablation_metrics.append(m)

    return all_ablation_metrics


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _print_metrics_row(
    experiment: str, target_short: str, metrics: dict,
) -> None:
    """Print a single metrics summary line."""
    print(
        f"      nRMSE_day={metrics.get('day_nrmse', float('nan')):.4f}"
        f" | nRMSE_all={metrics.get('all_nrmse', float('nan')):.4f}"
        f" | clear={metrics.get('clear_nrmse', float('nan')):.4f}"
        f" | mixed={metrics.get('mixed_nrmse', float('nan')):.4f}"
        f" | overcast={metrics.get('overcast_nrmse', float('nan')):.4f}"
        f" | bias={metrics.get('day_bias_kw', float('nan')):+.4f}"
    )


def _print_report_table(all_metrics: list[dict]) -> None:
    """Print formatted comparison table for main experiments."""
    print("\n" + "=" * 120)
    print("CSI EXPERIMENT COMPARISON (t+24h)")
    print("=" * 120)
    header = (
        f"{'Exp':<6} {'Target':<10} {'All_nRMSE':<10} {'Day_nRMSE':<11}"
        f" {'Cap_nRMSE':<11} {'Clear_nRMSE':<12} {'Mixed_nRMSE':<12}"
        f" {'Overcast_nRMSE':<14} {'DayBias':<10} {'Feat':<6}"
    )
    print(header)
    print("-" * 120)

    for m in all_metrics:
        target_display = m["target"].replace("target_pv_power_t_plus_", "t")
        day_cap = m.get("day_capacity_nrmse", m.get("day_nrmse", float("nan")))
        print(
            f"{m['experiment']:<6} {target_display:<10} "
            f"{_fmt(m.get('all_nrmse', float('nan'))):<10} "
            f"{_fmt(m.get('day_nrmse', float('nan'))):<11} "
            f"{_fmt(day_cap):<11} "
            f"{_fmt(m.get('clear_nrmse', float('nan'))):<12} "
            f"{_fmt(m.get('mixed_nrmse', float('nan'))):<12} "
            f"{_fmt(m.get('overcast_nrmse', float('nan'))):<14} "
            f"{m.get('day_bias_kw', float('nan')):<+10.4f} "
            f"{m.get('feature_count', 0):<6}"
        )

    print("=" * 120)


def _print_ablation_table(all_metrics: list[dict]) -> None:
    """Print formatted comparison table for ablation experiments."""
    print("\n" + "=" * 110)
    print("CSI ABLATION RESULTS (t+24h)")
    print("=" * 110)
    header = (
        f"{'Exp':<14} {'Day_nRMSE':<11} {'Clear_nRMSE':<12}"
        f" {'Mixed_nRMSE':<12} {'Overcast_nRMSE':<14} {'DayBias':<10}"
        f" {'Feat':<6}"
    )
    print(header)
    print("-" * 110)

    for m in all_metrics:
        print(
            f"{m['experiment']:<14} "
            f"{_fmt(m.get('day_nrmse', float('nan'))):<11} "
            f"{_fmt(m.get('clear_nrmse', float('nan'))):<12} "
            f"{_fmt(m.get('mixed_nrmse', float('nan'))):<12} "
            f"{_fmt(m.get('overcast_nrmse', float('nan'))):<14} "
            f"{m.get('day_bias_kw', float('nan')):<+10.4f} "
            f"{m.get('feature_count', 0):<6}"
        )

    print("=" * 110)


def _fmt(val: float) -> str:
    """Format float for table, handling NaN."""
    if np.isnan(val):
        return "  N/A     "
    return f"{val:<10.4f}"


# ---------------------------------------------------------------------------
# Inspection predictions update
# ---------------------------------------------------------------------------


def _update_inspection_predictions(
    all_preds: list[pd.DataFrame],
    inspection_path: Path,
) -> None:
    """Append new experiment predictions to the existing inspection file."""
    if not all_preds:
        print("No predictions to save, skipping inspection update.")
        return

    new_preds = pd.concat(all_preds, ignore_index=True)

    if inspection_path.exists():
        existing = pd.read_parquet(inspection_path)
        print(f"Existing inspection predictions: {len(existing)} rows")
        merged = pd.concat([existing, new_preds], ignore_index=True)
    else:
        merged = new_preds

    merged.to_parquet(inspection_path, index=False)
    print(f"Updated inspection predictions: {len(merged)} rows (+{len(new_preds)} new)")
    print(f"  File: {inspection_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: run all CSI experiments, ablations, sanity checks."""
    print("=" * 70)
    print("PHASE 1: CSI Target Reformulation for PV Prediction")
    print("=" * 70)

    # Resolve project root
    root_dir = Path(__file__).resolve().parents[3]
    data_path = root_dir / DATA_PATH
    output_dir = root_dir / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    inspection_path = root_dir / INSPECTION_PATH

    print(f"Data:     {data_path}")
    print(f"Output:   {output_dir}")
    print(f"Capacity: {CAPACITY_KW} kW, Efficiency: {EFFICIENCY}")

    # ---- 1. Load Stage3 dataset --------------------------------------------
    print("\n[1/5] Loading Stage3 dataset ...")
    if not data_path.exists():
        print(f"ERROR: Data not found at {data_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_parquet(data_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"  {len(df):,} rows, {len(df.columns)} columns ({df['timestamp'].dt.year.min()}-{df['timestamp'].dt.year.max()})")

    # ---- 2. Precompute leakage-free clear-sky power ------------------------
    print("\n[2/5] Precomputing clear-sky power (leakage-free, solar geometry only) ...")
    csp_lookup = _precompute_clear_sky_lookup(df)
    print(f"  Computed {len(csp_lookup)} hourly values from {csp_lookup.index.min()} to {csp_lookup.index.max()}")

    # ---- 3. Load HRRR data -------------------------------------------------
    print("\n[3/5] Loading HRRR weather data ...")
    hrrr = _load_and_filter_hrrr(root_dir)
    has_hrrr = len(hrrr) > 0
    print(f"  HRRR available: {has_hrrr} ({len(hrrr)} rows)")

    # ---- 4. Run main experiments (t+24h) -----------------------------------
    print("\n[4/5] Running main experiments (t+24h) ...")

    horizon_main = 24
    all_metrics_main: list[dict] = []
    all_preds: list[pd.DataFrame] = []

    # C0: PV power baseline
    pred, met = run_experiment(df, hrrr, csp_lookup, horizon_main, "C0", output_dir)
    all_preds.append(pred)
    all_metrics_main.append(met)

    # C1: CSI target (E1 features, no HRRR)
    pred, met = run_experiment(df, hrrr, csp_lookup, horizon_main, "C1", output_dir)
    all_preds.append(pred)
    all_metrics_main.append(met)

    # C2: CSI + HRRR (ghi, cloud, temp)
    if has_hrrr:
        pred, met = run_experiment(df, hrrr, csp_lookup, horizon_main, "C2", output_dir)
        all_preds.append(pred)
        all_metrics_main.append(met)
    else:
        print("  Skipping C2 (no HRRR data)")

    # C3: Physical baseline
    if has_hrrr:
        pred, met = run_c3_physical(df, hrrr, csp_lookup, horizon_main)
        all_preds.append(pred)
        all_metrics_main.append(met)
    else:
        print("  Skipping C3 (no HRRR data)")

    # C4: CSI + all HRRR features
    if has_hrrr:
        pred, met = run_experiment(df, hrrr, csp_lookup, horizon_main, "C4", output_dir)
        all_preds.append(pred)
        all_metrics_main.append(met)
    else:
        print("  Skipping C4 (no HRRR data)")

    # Print main comparison table
    _print_report_table(all_metrics_main)

    # ---- 5a. Sanity check: t+6h (C0 vs C1) --------------------------------
    print("\n" + "=" * 70)
    print("SANITY CHECK: t+6h (C0 vs C1)")
    print("=" * 70)

    for horizon in (6, 1):
        for exp in ("C0", "C1"):
            pred, met = run_experiment(df, hrrr, csp_lookup, horizon, exp, output_dir)
            all_preds.append(pred)
            all_metrics_main.append(met)

    # ---- 5b. Ablations (A1, A2 on t+24h C1) -------------------------------
    print("\n" + "=" * 70)
    print("ABLATIONS: A1 (clip) + A2 (threshold)")
    print("=" * 70)

    ablation_metrics = run_ablations(df, hrrr, csp_lookup, horizon_main, output_dir)

    # ---- 6. Save metrics ---------------------------------------------------
    print("\n[5/5] Saving results ...")

    # Main metrics
    main_metrics_df = pd.DataFrame(all_metrics_main)
    main_csv = output_dir / "csi_ablation.csv"
    main_metrics_df.to_csv(main_csv, index=False, float_format="%.6f")
    print(f"Main metrics CSV: {main_csv}")

    # Ablation metrics
    if ablation_metrics:
        ablation_metrics_df = pd.DataFrame(ablation_metrics)
        ablation_csv = output_dir / "csi_ablations_detail.csv"
        ablation_metrics_df.to_csv(ablation_csv, index=False, float_format="%.6f")
        print(f"Ablation metrics CSV: {ablation_csv}")

    # JSON (all metrics combined)
    combined_metrics = all_metrics_main + ablation_metrics
    json_path = output_dir / "csi_experiments.json"
    json_path.write_text(
        json.dumps(combined_metrics, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"JSON: {json_path}")

    # Print ablation table
    if ablation_metrics:
        _print_ablation_table(ablation_metrics)

    # ---- 7. Update inspection predictions ----------------------------------
    _update_inspection_predictions(all_preds, inspection_path)

    # ---- Summary -----------------------------------------------------------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Experiments completed: {len(all_metrics_main)}")
    print(f"Ablations completed:   {len(ablation_metrics)}")
    print(f"Total predictions:     {sum(len(p) for p in all_preds)}")
    best = min(
        (m for m in all_metrics_main if m.get("day_nrmse", float("nan")) is not None and not np.isnan(m.get("day_nrmse", float("nan")))),
        key=lambda m: m["day_nrmse"],
        default=None,
    )
    if best:
        print(f"Best model: {best['experiment']} day_nRMSE={best['day_nrmse']:.4f}")
    print(f"\nModel directory: {output_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
