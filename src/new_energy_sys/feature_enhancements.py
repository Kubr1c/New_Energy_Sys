"""Feature enhancements for PV power prediction.

All features are classified by data leakage risk:
  Class A: origin-time known info — safe for model training
  Class B: valid-time solar geometry — deterministic, no leakage
  Class C: valid-time actual weather/PV — evaluation labels ONLY, never features

This module adds three groups of features:
  1. add_valid_time_solar_features   — Class B: solar elevation/zenith/attenuation
  2. add_ramp_features               — Class A: origin-time ramp rates via timestamp merge
  3. add_origin_cloud_scenario       — Class A: clear/overcast/mixed one-hot encoding
"""

from __future__ import annotations

import pvlib
import numpy as np
import pandas as pd

from pvlib.location import Location


# ---------------------------------------------------------------------------
# Valid-time solar geometry (Class B)
# ---------------------------------------------------------------------------


def add_valid_time_solar_features(
    df: pd.DataFrame,
    horizon_hours: int,
    latitude: float = 39.74,
    longitude: float = -105.18,
    altitude: float = 1730.0,
) -> pd.DataFrame:
    """Generate valid-time solar geometry features for a given prediction horizon.

    These are **Class B** features — deterministic astronomical calculations,
    no data leakage concern.  They help the model reason about conditions at
    the target time, especially resolving evening/night ambiguity where
    low-elevation sun angles produce near-zero irradiance.

    Parameters
    ----------
    df : DataFrame
        Must contain a ``'timestamp'`` column (datetime64, UTC).
    horizon_hours : int
        Prediction horizon in hours (e.g. 1, 6, 24).  Features are prefixed
        with ``valid_h{N}h_`` where ``N`` is this value.
    latitude : float
        Site latitude in degrees (default: Golden, CO).
    longitude : float
        Site longitude in degrees (default: Golden, CO).
    altitude : float
        Site elevation in metres (default: Golden, CO).

    Returns
    -------
    pd.DataFrame
        Input DataFrame with new columns added:
        - ``valid_h{N}h_solar_elevation`` — solar elevation (degrees)
        - ``valid_h{N}h_solar_zenith`` — solar zenith (degrees)
        - ``valid_h{N}h_sunset_attenuation`` — nonlinear attenuation [0, 1]
        - ``valid_h{N}h_cos_zenith`` — cos(solar_zenith)
        - ``valid_h{N}h_hour_sin`` — cyclic sin encoding of valid-time hour
        - ``valid_h{N}h_hour_cos`` — cyclic cos encoding of valid-time hour
    """
    result = df.copy()

    # ---- valid time = origin time + horizon
    valid_time = pd.DatetimeIndex(
        result["timestamp"] + pd.Timedelta(hours=horizon_hours)
    )

    # ---- compute solar position via pvlib
    loc = Location(latitude=latitude, longitude=longitude, altitude=altitude)
    solar_pos = loc.get_solarposition(times=valid_time)
    # solar_pos columns: apparent_zenith, zenith, apparent_elevation, elevation,
    #                    azimuth, equation_of_time

    elev = solar_pos["elevation"]       # degrees above horizon
    zenith = solar_pos["apparent_zenith"]  # degrees from vertical

    prefix = f"valid_h{horizon_hours}h_"

    result[f"{prefix}solar_elevation"] = elev.values
    result[f"{prefix}solar_zenith"] = zenith.values

    # ---- sunset attenuation: nonlinear fall-off for low sun
    # When elevation > 0: sin(elev_rad) ** 1.5, clamped to [0, 1]
    # When elevation <= 0: 0.0 — model learns "expect near-zero PV"
    elev_rad = np.deg2rad(elev)
    atten = np.where(elev.values > 0, np.sin(elev_rad) ** 1.5, 0.0)
    atten = np.clip(atten, 0.0, 1.0)
    result[f"{prefix}sunset_attenuation"] = atten

    # ---- cos(zenith): smooth daytime irradiance proxy
    zenith_rad = np.deg2rad(zenith)
    result[f"{prefix}cos_zenith"] = np.cos(zenith_rad).values

    # ---- cyclic encoding of valid-time hour
    valid_hour = valid_time.hour
    hour_rad = 2.0 * np.pi * valid_hour.astype(float) / 24.0
    result[f"{prefix}hour_sin"] = np.sin(hour_rad)
    result[f"{prefix}hour_cos"] = np.cos(hour_rad)

    return result


# ---------------------------------------------------------------------------
# Origin-time ramp features (Class A)
# ---------------------------------------------------------------------------


def add_ramp_features(df: pd.DataFrame) -> pd.DataFrame:
    """PV power ramp rate features using timestamp-merge for safe alignment.

    These are **Class A** features — only origin-time known information,
    safe for model training.

    Uses timestamp merge (NOT simple .shift()) to handle potential gaps or
    missing hours correctly: each row's lag value comes from the row whose
    timestamp is exactly ``N`` hours earlier, regardless of whether the
    DataFrame index is contiguous.

    For each lag window {1, 3, 6} hours:
    - ``pv_ramp_{N}h_kw_per_h`` = (pv_power_kw - pv_power_{N}h_ago) / N
      Positive = power increasing (ramping up); negative = ramping down.

    Parameters
    ----------
    df : DataFrame
        Must contain ``'timestamp'`` and ``'pv_power_kw'`` columns.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with new ``pv_ramp_{N}h_kw_per_h`` columns added.
    """
    result = df.copy()

    # LAG_WINDOWS: which past horizons (hours) to compute ramp rates for.
    # Using 1, 3, 6 hours captures short-term variability, medium-term trends,
    # and roughly matches the solar inertia window (typical ramp events last
    # 15-60 min, but 3-6h captures cloud-passage regime changes).
    for lag_hours in (1, 3, 6):
        col_ramp = f"pv_ramp_{lag_hours}h_kw_per_h"
        col_lag = f"pv_power_{lag_hours}h_ago"

        # Build a lag DataFrame with timestamps shifted forward.
        # When merged back on the original timestamp, each row will pair with
        # the row that was 'lag_hours' before it — even if there are gaps.
        lag_df = (
            df[["timestamp", "pv_power_kw"]]
            .copy()
            .rename(columns={"pv_power_kw": col_lag})
        )
        lag_df["timestamp"] = lag_df["timestamp"] + pd.Timedelta(hours=lag_hours)

        # Left-merge: keep all original rows; rows without a lag partner
        # (e.g. first N hours of the dataset) will get NaN.
        result = result.merge(lag_df[["timestamp", col_lag]], on="timestamp", how="left")

        # Compute ramp rate: average kW change per hour over the lag window
        result[col_ramp] = (result["pv_power_kw"] - result[col_lag]) / lag_hours

        # Drop the intermediate lag column to keep the DataFrame tidy
        result.drop(columns=[col_lag], inplace=True)

    return result


# ---------------------------------------------------------------------------
# Origin-time cloud scenario (Class A)
# ---------------------------------------------------------------------------


def add_origin_cloud_scenario(df: pd.DataFrame) -> pd.DataFrame:
    """Origin-time cloud scenario classification (Class A — safe for training).

    Uses origin-time ``clearsky_index_ghi`` (known at prediction time) to
    classify the sky condition into three mutually exclusive categories, then
    one-hot encodes them.

    - **cloud_clear** (CSI >= 0.7): clear or mostly clear sky
    - **cloud_mixed** (0.3 <= CSI < 0.7): partly cloudy, high variability
    - **cloud_overcast** (CSI < 0.3): heavily overcast or nighttime

    Nighttime rows (CSI near 0) are assigned to ``cloud_overcast``, which is
    safe because the model will learn to separate low-CSI night vs overcast
    via interaction with solar geometry features.

    Parameters
    ----------
    df : DataFrame
        Must contain a ``'clearsky_index_ghi'`` column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with ``cloud_clear``, ``cloud_mixed``,
        ``cloud_overcast`` columns added (``int8``).

    Notes
    -----
    **Pitfall**: In high-latitude winter, CSI can hover below 0.3 all day
    even when the sky is clear (low sun, high air mass).  At Golden, CO
    (39.7 N) this is less likely, but if a site shows an over-representation
    of ``cloud_overcast`` on clear winter days, consider raising the CSI
    threshold to 0.15 instead of 0.3 after adding solar-geometry features.
    """
    csi = df["clearsky_index_ghi"].fillna(0.0)

    df_result = df.copy()
    df_result["cloud_clear"] = (csi >= 0.7).astype("int8")
    df_result["cloud_mixed"] = ((csi >= 0.3) & (csi < 0.7)).astype("int8")
    df_result["cloud_overcast"] = (csi < 0.3).astype("int8")

    return df_result
