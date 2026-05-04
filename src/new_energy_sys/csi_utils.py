"""
Shared utilities for CSI (Clear-Sky Index) target experiments.

Used by: train_csi_target.py, train_quantile_pv.py, train_gating_experiment.py

Provides:
- Leakage-free clear-sky PV power computation (solar geometry only)
- CSI target construction with numerical protection
- E1 feature pipeline (valid-time solar + ramp)
- Shared constants for the Golden, CO PVDAQ site
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib
from pvlib.location import Location

from new_energy_sys.feature_enhancements import (
    add_valid_time_solar_features,
    add_ramp_features,
)

# ---------------------------------------------------------------------------
# Site constants — PVDAQ Golden, CO (NSRDB grid cell centre)
# ---------------------------------------------------------------------------

SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}
CAPACITY_KW: float = SITE["capacity_kw"]
EFFICIENCY: float = 0.75  # residential DC-AC derating factor

SOLAR_ELEV_THRESHOLD: float = 5.0   # degrees above horizon for daytime
CSI_CLIP_LOW: float = 0.0           # minimum physically-plausible CSI
CSI_CLIP_HIGH: float = 1.2          # maximum (allows cloud enhancement)
CSP_THRESHOLD: float = 0.05         # min clear-sky power as fraction of capacity
PREDICTION_UPPER: float = CAPACITY_KW * 1.05  # 1.176 kW

HORIZONS: list[int] = [1, 6, 24]

ALL_TARGET_COLS: list[str] = [
    "target_pv_power_t_plus_1h",
    "target_pv_power_t_plus_6h",
    "target_pv_power_t_plus_24h",
]

# LightGBM hyperparameters (Stage5/E1 tuned for t+24h)
DEFAULT_HP: dict[str, int | float] = {
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

QUANTILE_HP: dict[str, int | float] = {
    "learning_rate": 0.02,
    "max_depth": 10,
    "num_leaves": 45,
    "n_estimators": 1500,
    "reg_lambda": 0.8,
    "min_child_samples": 35,
    "subsample": 0.85,
    "colsample_bytree": 0.8,
}


# ---------------------------------------------------------------------------
# Clear-sky power computation (LEAKAGE-FREE)
# ---------------------------------------------------------------------------

def compute_clear_sky_power(
    timestamps: pd.DatetimeIndex,
    latitude: float = SITE["latitude"],
    longitude: float = SITE["longitude"],
    altitude: float = SITE["altitude"],
    capacity_kw: float = CAPACITY_KW,
    efficiency: float = EFFICIENCY,
) -> pd.Series:
    """Compute clear-sky PV power from solar geometry only.

    CRITICAL: Uses ONLY site static parameters + time + solar geometry.
    No measured power, irradiance, temperature, or cloud cover enters the
    calculation — guaranteed no data leakage.

    Formula:  P_clear = capacity_kw * GHI_clear_sky / 1000 * efficiency

    Parameters
    ----------
    timestamps : pd.DatetimeIndex
        UTC timestamps for which to compute clear-sky power.
    latitude, longitude, altitude : float
        Site coordinates.
    capacity_kw : float
        Nameplate DC capacity in kW.
    efficiency : float
        DC-AC derating factor (0.75 for residential).

    Returns
    -------
    pd.Series
        Clear-sky PV power in kW, indexed by input timestamps.
    """
    loc = Location(latitude=latitude, longitude=longitude, altitude=altitude)
    solar_pos = loc.get_solarposition(times=timestamps)
    clearsky = loc.get_clearsky(times=timestamps, solar_position=solar_pos)
    power = capacity_kw * clearsky["ghi"].values / 1000.0 * efficiency
    return pd.Series(power, index=timestamps)


def precompute_clear_sky_lookup(df: pd.DataFrame) -> pd.Series:
    """Build a timestamp-indexed Series of clear-sky power for all needed times.

    Extends beyond dataset range by one day to cover t+24h valid times.

    Parameters
    ----------
    df : pd.DataFrame
        Must have a ``'timestamp'`` column (UTC datetime64).

    Returns
    -------
    pd.Series
        Hourly clear-sky power with DatetimeIndex, covering the dataset
        range extended by -1h / +25h.
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
    """Compute CSI target and auxiliary columns for the given horizon.

    CSI = PV_power_at_valid / clear_sky_power_at_valid

    IMPORTANT: Filtering uses clear_sky_power and solar elevation ONLY.
    Using actual PV power as a filter would introduce selection bias.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``'timestamp'`` and the target PV column for the horizon.
    horizon_hours : int
        Forecast horizon (1, 6, or 24).
    csp_lookup : pd.Series
        Timestamp-indexed clear-sky power from precompute_clear_sky_lookup().
    csi_clip_low, csi_clip_high : float
        CSI clipping bounds (default 0.0, 1.2).
    csp_threshold : float
        Minimum clear-sky power as fraction of CAPACITY_KW for validity.

    Returns
    -------
    pd.DataFrame
        Copy of ``df`` with additional columns:
        - ``clear_sky_power_valid_{h}h`` — clear-sky power at valid time (kW)
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

    # Solar elevation at valid time (for daytime gating)
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(times=valid_time)
    elev_valid = solar_pos["elevation"].values
    result[f"solar_elevation_valid_{horizon_hours}h"] = elev_valid

    # Filtering: daytime + sufficient clear-sky power (NO actual PV used)
    min_csp = csp_threshold * CAPACITY_KW
    valid_mask = (elev_valid > SOLAR_ELEV_THRESHOLD) & (csp_valid > min_csp)

    # Raw CSI = PV / clear_sky_power
    pv_actual = result[target_col].values
    csi_raw = np.where(
        valid_mask,
        pv_actual / np.maximum(csp_valid, 1e-6),
        np.nan,
    )
    csi_clipped = np.clip(csi_raw, csi_clip_low, csi_clip_high)

    col_name = f"csi_target_{horizon_hours}h"
    result[col_name] = np.where(valid_mask, csi_clipped, np.nan)

    # Report filtering statistics
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
# E1 feature pipeline
# ---------------------------------------------------------------------------

def add_e1_features(df: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    """Add E1 feature set: valid-time solar geometry + ramp rates.

    Matches the pipeline used by the E1 model (best baseline).
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


def build_e1_feature_list(
    df: pd.DataFrame,
    horizon_hours: int,
    extra_exclude: set[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Add E1 features and return (enhanced_df, feature_column_names).

    Parameters
    ----------
    df : pd.DataFrame
        Base dataset with timestamp and target columns.
    horizon_hours : int
        Forecast horizon.
    extra_exclude : set[str] | None
        Additional column names to exclude from the feature set.

    Returns
    -------
    tuple[pd.DataFrame, list[str]]
        (E1-enhanced DataFrame, list of numeric feature column names)
    """
    result = add_e1_features(df, horizon_hours)

    exclude = {"timestamp", "pv_power_kw"}
    for h in HORIZONS:
        exclude.add(f"target_pv_power_t_plus_{h}h")
        exclude.add(f"csi_target_{h}h")
        exclude.add(f"clear_sky_power_valid_{h}h")
        exclude.add(f"solar_elevation_valid_{h}h")
    if extra_exclude:
        exclude.update(extra_exclude)

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
# Weather scenario classification
# ---------------------------------------------------------------------------

def classify_scenario(
    solar_elevation_deg: pd.Series,
    csi: pd.Series,
) -> pd.Series:
    """Classify rows into weather scenarios based on clearsky index.

    Rules:
      - night:    solar_elevation <= 5
      - clear:    solar_elevation > 5 AND CSI >= 0.7
      - mixed:    solar_elevation > 5 AND 0.3 <= CSI < 0.7
      - overcast: solar_elevation > 5 AND CSI < 0.3

    Parameters
    ----------
    solar_elevation_deg : pd.Series
        Solar elevation in degrees.
    csi : pd.Series
        Clearsky index (GHI / clearsky GHI) at the timestamp of interest.

    Returns
    -------
    pd.Series
        Scenario labels: 'night', 'clear', 'mixed', or 'overcast'.
    """
    result = pd.Series("night", index=solar_elevation_deg.index, dtype=str)
    daytime = solar_elevation_deg > SOLAR_ELEV_THRESHOLD
    # Only classify where CSI is finite
    valid = daytime & csi.notna()
    result.loc[valid & (csi >= 0.7)] = "clear"
    result.loc[valid & (csi >= 0.3) & (csi < 0.7)] = "mixed"
    result.loc[valid & (csi < 0.3)] = "overcast"
    return result


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def evaluate_pv_predictions(
    predictions: np.ndarray,
    actual: np.ndarray,
    solar_elevation: np.ndarray,
    csi_actual: np.ndarray,
    valid_mask: np.ndarray | None = None,
    capacity_kw: float = CAPACITY_KW,
) -> dict:
    """Compute comprehensive PV prediction metrics with weather stratification.

    Parameters
    ----------
    predictions : np.ndarray
        Predicted PV power (kW).
    actual : np.ndarray
        Actual PV power (kW) at valid time.
    solar_elevation : np.ndarray
        Solar elevation at valid time (degrees).
    csi_actual : np.ndarray
        Actual clearsky index at valid time (for weather stratification).
    valid_mask : np.ndarray | None
        Pre-computed daytime mask. Computed from solar_elevation if None.
    capacity_kw : float
        Nameplate capacity for nRMSE/capacity calculation.

    Returns
    -------
    dict
        Flat dictionary with all_*, day_*, clear_*, mixed_*, overcast_* metrics.
    """
    if valid_mask is None:
        valid_mask = solar_elevation > SOLAR_ELEV_THRESHOLD

    error = predictions - actual
    abs_error = np.abs(error)

    # All-sample metrics
    all_rmse = float(np.sqrt(np.nanmean(error ** 2)))
    all_mean_actual = float(np.nanmean(actual))
    all_nrmse = all_rmse / all_mean_actual if all_mean_actual > 0 else float("nan")

    # Daytime metrics
    day_mask = valid_mask & np.isfinite(actual) & np.isfinite(predictions)
    day_err = error[day_mask]
    day_actual = actual[day_mask]
    n_day = int(day_mask.sum())

    if n_day == 0:
        return {"day_n": 0, "day_nrmse": float("nan"), "all_nrmse": all_nrmse}

    day_rmse = float(np.sqrt(np.nanmean(day_err ** 2)))
    day_mean = float(np.nanmean(day_actual))
    day_nrmse = day_rmse / day_mean if day_mean > 0 else float("nan")

    metrics = {
        "all_rmse_kw": all_rmse,
        "all_nrmse": all_nrmse,
        "all_mae_kw": float(np.nanmean(abs_error)),
        "all_bias_kw": float(np.nanmean(error)),
        "day_n": n_day,
        "day_rmse_kw": day_rmse,
        "day_nrmse": day_nrmse,
        "day_mae_kw": float(np.nanmean(np.abs(day_err))),
        "day_bias_kw": float(np.nanmean(day_err)),
        "day_capacity_nrmse": day_rmse / capacity_kw,
        "all_capacity_nrmse": all_rmse / capacity_kw,
    }

    # Weather-stratified
    for scenario, (lo, hi) in [
        ("clear", (0.7, float("inf"))),
        ("mixed", (0.3, 0.7)),
        ("overcast", (0.0, 0.3)),
    ]:
        sc_mask = day_mask & (csi_actual >= lo) & (csi_actual < hi)
        n = int(sc_mask.sum())
        metrics[f"{scenario}_n"] = n
        if n == 0:
            for suffix in ("rmse_kw", "nrmse", "mae_kw", "bias_kw"):
                metrics[f"{scenario}_{suffix}"] = float("nan")
            continue
        sc_err = error[sc_mask]
        sc_actual = actual[sc_mask]
        sc_rmse = float(np.sqrt(np.nanmean(sc_err ** 2)))
        sc_mean = float(np.nanmean(sc_actual))
        metrics[f"{scenario}_rmse_kw"] = sc_rmse
        metrics[f"{scenario}_nrmse"] = (
            sc_rmse / sc_mean if sc_mean > 0 else float("nan")
        )
        metrics[f"{scenario}_mae_kw"] = float(np.nanmean(np.abs(sc_err)))
        metrics[f"{scenario}_bias_kw"] = float(np.nanmean(sc_err))

    return metrics
