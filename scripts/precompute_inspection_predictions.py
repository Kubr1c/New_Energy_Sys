#!/usr/bin/env python3
"""
Precompute inspection predictions parquet for the PV prediction dashboard.

Generates:
  data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.parquet

Models used (6 total, 2 experiments x 3 horizons):
  - Stage5 tuned (full_features_163): lightgbm_tuned_full_features_*_t_plus_{1,6,24}h.pkl
  - E1 enhanced  (solar_ramp_171):    lightgbm_E1_t{1,6,24}h.pkl

Baselines:
  - persistence_origin_kw:             pv_power at origin time t
  - persistence_same_hour_yesterday_kw: pv_power at (valid_time - 24h)

Weather scenarios at valid_time (pvlib solar_elevation):
  - night:    solar_elevation <= 5
  - clear:    solar_elevation > 5 AND ghi/clearsky_ghi >= 0.7
  - mixed:    solar_elevation > 5 AND 0.3 <= ghi/clearsky_ghi < 0.7
  - overcast: solar_elevation > 5 AND ghi/clearsky_ghi < 0.3

Validation: 5 assertions post-build.

Usage:
    python scripts/precompute_inspection_predictions.py

Expected: ~2 min, ~22800 rows (3804 test rows x 6 model-horizon combinations).
"""

from __future__ import annotations

import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
from pvlib.location import Location

# ---------------------------------------------------------------------------
# Bootstrap: ensure src/ is on sys.path so project modules are importable
# ---------------------------------------------------------------------------
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from new_energy_sys.modeling import _chronological_split
from new_energy_sys.feature_enhancements import (
    add_valid_time_solar_features,
    add_ramp_features,
)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA_PATH = Path("data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet")
MODEL_BASE = Path("data/processed/pvdaq_nsrdb_2020_2022")
OUTPUT_PATH = Path("data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.parquet")

SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}
PREDICTION_UPPER: float = SITE["capacity_kw"] * 1.05  # 1.176 kW

HORIZONS: list[int] = [1, 6, 24]

# Model pickle filename patterns. Both accept integer horizon {1,6,24}.
STAGE5_PATTERN: str = (
    "stage5_models/lightgbm_tuned_full_features_target_pv_power_t_plus_{}h.pkl"
)
E1_PATTERN: str = "enhanced_models/lightgbm_E1_t{}h.pkl"

# Parquet output columns in schema order
PARQUET_COLUMNS: list[str] = [
    "origin_time",
    "valid_time",
    "horizon_hours",
    "experiment",
    "model_name",
    "model_version",
    "feature_set",
    "target_type",
    "raw_prediction_kw",
    "prediction_kw",
    "actual_kw",
    "persistence_origin_kw",
    "persistence_same_hour_yesterday_kw",
    "error_kw",
    "abs_error_kw",
    "ghi_wm2",
    "clearsky_ghi_wm2",
    "solar_elevation_deg",
    "cloud_cover_pct",
    "scenario",
    "split",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_git_commit_short() -> str:
    """Return the short git commit hash of HEAD, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _compute_cloud_cover_pct(
    ghi: pd.Series,
    clearsky_ghi: pd.Series,
) -> pd.Series:
    """Estimate cloud cover percentage from clearsky index.

    cloud_cover_pct ~ (1 - CSI) * 100, clipped to [0, 100].
    CSI = ghi / clearsky_ghi where CSI=1 means fully clear and
    CSI=0 means fully overcast.  Returns NaN when clearsky_ghi == 0
    (sun below horizon).
    """
    safe = clearsky_ghi.replace(0, np.nan)
    csi = ghi / safe
    return np.clip((1.0 - csi) * 100.0, 0.0, 100.0)


def _classify_scenario(
    solar_elevation_deg: pd.Series,
    ghi: pd.Series,
    clearsky_ghi: pd.Series,
) -> pd.Series:
    """Classify each row into a weather scenario at valid_time.

    Rules (from the dashboard spec):
      - night:    solar_elevation <= 5
      - clear:    solar_elevation > 5 AND ghi/clearsky_ghi >= 0.7
      - mixed:    solar_elevation > 5 AND 0.3 <= ghi/clearsky_ghi < 0.7
      - overcast: solar_elevation > 5 AND ghi/clearsky_ghi < 0.3
    """
    result = pd.Series("night", index=solar_elevation_deg.index)

    # Daytime rows with valid clearsky data
    daytime = (
        (solar_elevation_deg > 5)
        & clearsky_ghi.notna()
        & (clearsky_ghi > 0)
    )
    if not daytime.any():
        return result

    csi = ghi / clearsky_ghi  # CSI only where clearsky_ghi > 0

    result = result.copy()
    result.loc[daytime & (csi >= 0.7)] = "clear"
    result.loc[daytime & (csi >= 0.3) & (csi < 0.7)] = "mixed"
    result.loc[daytime & (csi < 0.3)] = "overcast"
    return result


def _run_validation(df_preds: pd.DataFrame) -> None:
    """Run the 5 post-build validation assertions."""
    print("\n" + "-" * 60)
    print("Running 5 validation assertions ...")
    print("-" * 60)

    # 1. valid_time - origin_time == horizon
    time_diffs = (df_preds["valid_time"] - df_preds["origin_time"]).dt.total_seconds()
    assert time_diffs.isin([3600, 21600, 86400]).all(), (
        f"Time diff mismatch: unique diffs = {sorted(time_diffs.unique())}"
    )
    print("  [PASS] 1. valid_time - origin_time == horizon for all rows")

    # 2. Only horizons 1/6/24
    actual_horizons = set(df_preds["horizon_hours"].unique())
    assert actual_horizons <= {1, 6, 24}, (
        f"Unexpected horizon_hours: {actual_horizons}"
    )
    print(f"  [PASS] 2. Only horizons 1/6/24 (found: {sorted(actual_horizons)})")

    # 3. Unique (valid_time, horizon, experiment)
    dups = df_preds[["valid_time", "horizon_hours", "experiment"]].duplicated().sum()
    assert dups == 0, (
        f"Found {dups} duplicate (valid_time, horizon, experiment) tuples"
    )
    print(f"  [PASS] 3. No duplicate (valid_time, horizon, experiment) tuples")

    # 4. prediction_kw missing < 1%
    completeness = df_preds["prediction_kw"].notna().mean()
    assert completeness > 0.99, (
        f"prediction_kw completeness = {completeness:.4%} (< 99%)"
    )
    print(f"  [PASS] 4. prediction_kw completeness = {completeness:.4%}")

    # 5. Clipped prediction >= -0.05
    min_pred = df_preds["prediction_kw"].min()
    assert min_pred >= -0.05, (
        f"prediction_kw min = {min_pred:.4f} (< -0.05)"
    )
    print(f"  [PASS] 5. prediction_kw >= -0.05 (min = {min_pred:.4f})")

    print("\n  All 5 validation assertions passed.")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    t_start = time.perf_counter()

    print("=" * 60)
    print("Precompute Inspection Predictions")
    print("=" * 60)

    # ---- 1. Load & prepare Stage3 dataset --------------------------------
    print("\n[1/6] Loading Stage3 dataset ...")
    df = pd.read_parquet(DATA_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    print(f"  Timestamp range: {df['timestamp'].min()} -> {df['timestamp'].max()}")

    # ---- 2. Determine chronological split positions (70/15/15) ----------
    print("\n[2/6] Determining chronological split (70/15/15) boundaries ...")
    n_total = len(df)
    _train_end = int(n_total * 0.70)
    test_start = int(n_total * 0.85)
    test = df.iloc[test_start:].copy()
    print(f"  Test set: {len(test)} rows (rows {test_start}:{n_total})")
    print(f"  Test range: {test['timestamp'].min()} -> {test['timestamp'].max()}")

    # ---- 3. Git commit hash for model_version ---------------------------
    print("\n[3/6] Getting model version ...")
    model_version = _get_git_commit_short()
    print(f"  Git commit: {model_version}")

    # ---- 4. Build pvlib Location (used once for solar elevation) --------
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )

    # ---- 5. Pre-compute ramp features on FULL dataset -------------------
    # Ramp features need historical context; computing on the full dataset
    # ensures the test-set boundary rows have correct values (the lag merge
    # spans backward into training/validation data).
    print("\n[4/6] Pre-computing ramp features on full dataset (for E1 models) ...")
    full_with_ramp = add_ramp_features(df)
    ramp_cols = [c for c in full_with_ramp.columns if "pv_ramp" in c]
    print(f"  Ramp columns added: {ramp_cols}")

    # ---- 6. Generate predictions for all (experiment, horizon) ----------
    print("\n[5/6] Generating predictions ...")
    all_frames: list[pd.DataFrame] = []

    # (experiment_name, needs_enhancement, pickle_pattern, feature_set_label)
    experiment_defs = [
        ("stage5", False, STAGE5_PATTERN, "full_features_163"),
        ("e1",     True,  E1_PATTERN,     "solar_ramp_171"),
    ]

    for exp_name, needs_enhancement, pattern, feature_set_label in experiment_defs:
        for horizon_hours in HORIZONS:
            model_path = MODEL_BASE / pattern.format(horizon_hours)
            print(
                f"  {exp_name} / t+{horizon_hours}h: "
                f"loading {model_path.name} ...",
                end=" ",
                flush=True,
            )

            # ---- 6a. Load pickle bundle -------------------------------
            with open(model_path, "rb") as f:
                bundle = pickle.load(f)
            model = bundle["model"]
            model_feature_names = list(bundle["features"])

            # ---- 6b. Prepare inference data ---------------------------
            if needs_enhancement:
                # E1: add valid-time solar geometry for this horizon
                #      (ramp features were already added to full_with_ramp)
                inference_data = add_valid_time_solar_features(
                    full_with_ramp,
                    horizon_hours,
                    latitude=SITE["latitude"],
                    longitude=SITE["longitude"],
                    altitude=SITE["altitude"],
                )
            else:
                # Stage5: original data only, no enhancement
                inference_data = df

            # Select test rows by position (sorted DataFrame has stable
            # ordering, so iloc[test_start:] works on any copy)
            test_features = inference_data.iloc[test_start:][model_feature_names]

            # Warn about NaN features (should not happen for well-trained
            # models applied to the same pipeline)
            n_nan = int(test_features.isna().sum().sum())
            if n_nan > 0:
                print(f"[WARN] {n_nan} NaN values in features, filling with 0 ...", end=" ", flush=True)
                test_features = test_features.fillna(0.0)

            # ---- 6c. Predict & clip -----------------------------------
            raw_preds = model.predict(test_features)
            clipped_preds = np.clip(raw_preds, 0.0, PREDICTION_UPPER)

            # ---- 6d. Build result slice -------------------------------
            # Use pandas Series ops (preserves tz-aware datetime dtype
            # instead of stripping via .values)
            result = pd.DataFrame({
                "origin_time": test["timestamp"],
                "valid_time": test["timestamp"]
                + pd.Timedelta(hours=horizon_hours),
                "horizon_hours": horizon_hours,
                "experiment": exp_name,
                "model_name": "lightgbm",
                "model_version": model_version,
                "feature_set": feature_set_label,
                "target_type": "pv_power",
                "raw_prediction_kw": raw_preds,
                "prediction_kw": clipped_preds,
            })
            all_frames.append(result)
            print(f"done ({len(result)} rows)", flush=True)

    # ---- 7. Concatenate all prediction frames ---------------------------
    print("\n[6/6] Assembling final DataFrame ...")
    df_preds = pd.concat(all_frames, ignore_index=True)
    print(f"  Total prediction rows: {len(df_preds):,}")

    # ---- 7a. Merge actuals and weather at valid_time --------------------
    # Ensure consistent tz-aware datetime64 for merges
    print("  Merging actuals and weather at valid_time ...")
    full_weather = (
        df[["timestamp", "pv_power_kw", "ghi_wm2", "clearsky_ghi_wm2"]]
        .copy()
        .rename(columns={"timestamp": "valid_time"})
    )
    full_weather["valid_time"] = pd.to_datetime(full_weather["valid_time"], utc=True)
    df_preds = df_preds.merge(full_weather, on="valid_time", how="left")
    df_preds = df_preds.rename(columns={"pv_power_kw": "actual_kw"})

    # ---- 7b. Persistence baselines ------------------------------------
    print("  Computing persistence baselines ...")

    # persistence_origin_kw = pv_power at origin time t
    origin_power = (
        df[["timestamp", "pv_power_kw"]]
        .copy()
        .rename(columns={"timestamp": "origin_time", "pv_power_kw": "persistence_origin_kw"})
    )
    origin_power["origin_time"] = pd.to_datetime(origin_power["origin_time"], utc=True)
    df_preds = df_preds.merge(origin_power, on="origin_time", how="left")

    # persistence_same_hour_yesterday_kw = pv_power at (valid_time - 24h)
    # Shift dataset timestamps FORWARD by 24h, then merge on valid_time
    yesterday_power = df[["timestamp", "pv_power_kw"]].copy()
    yesterday_power = yesterday_power.rename(
        columns={"pv_power_kw": "persistence_same_hour_yesterday_kw"}
    )
    yesterday_power["valid_time"] = yesterday_power["timestamp"] + pd.Timedelta(hours=24)
    yesterday_power["valid_time"] = pd.to_datetime(yesterday_power["valid_time"], utc=True)
    df_preds = df_preds.merge(
        yesterday_power[["valid_time", "persistence_same_hour_yesterday_kw"]],
        on="valid_time",
        how="left",
    )

    # ---- 7c. Solar elevation at valid_time (pvlib) ---------------------
    print("  Computing solar elevation at valid_time (pvlib) ...")
    unique_valid_times = pd.DatetimeIndex(df_preds["valid_time"].unique()).sort_values()
    solar_pos = loc.get_solarposition(times=unique_valid_times)
    solar_elev_map = pd.DataFrame({
        "valid_time": unique_valid_times,
        "solar_elevation_deg": solar_pos["elevation"].values,
    })
    solar_elev_map["valid_time"] = pd.to_datetime(solar_elev_map["valid_time"], utc=True)
    df_preds = df_preds.merge(solar_elev_map, on="valid_time", how="left")

    # ---- 7d. Cloud cover proxy -----------------------------------------
    print("  Computing cloud_cover_pct proxy ...")
    df_preds["cloud_cover_pct"] = _compute_cloud_cover_pct(
        df_preds["ghi_wm2"], df_preds["clearsky_ghi_wm2"]
    )

    # ---- 7e. Weather scenario classification ---------------------------
    print("  Classifying weather scenarios ...")
    df_preds["scenario"] = _classify_scenario(
        df_preds["solar_elevation_deg"],
        df_preds["ghi_wm2"],
        df_preds["clearsky_ghi_wm2"],
    )

    # ---- 7f. Error metrics ---------------------------------------------
    df_preds["error_kw"] = df_preds["prediction_kw"] - df_preds["actual_kw"]
    df_preds["abs_error_kw"] = df_preds["error_kw"].abs()

    # ---- 7g. Split label -----------------------------------------------
    df_preds["split"] = "test"

    # ---- 7h. Reorder columns to match schema ---------------------------
    df_preds = df_preds[PARQUET_COLUMNS]

    # ---- 8. Run 5 validation assertions --------------------------------
    _run_validation(df_preds)

    # ---- 9. Save to parquet --------------------------------------------
    print(f"\nSaving to {OUTPUT_PATH} ...")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_preds.to_parquet(OUTPUT_PATH, index=False)
    file_size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(f"  Saved: {OUTPUT_PATH} ({file_size_mb:.2f} MB)")

    # ---- 10. Print summary ---------------------------------------------
    t_elapsed = time.perf_counter() - t_start
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total rows:              {len(df_preds):,}")
    print(f"  Experiments:             {sorted(df_preds['experiment'].unique())}")
    print(f"  Horizons:                {sorted(df_preds['horizon_hours'].unique())}")
    print(f"  Valid time range:        {df_preds['valid_time'].min()} -> "
          f"{df_preds['valid_time'].max()}")
    scenario_counts = df_preds["scenario"].value_counts()
    print(f"  Scenario breakdown:")
    for sc in ["clear", "mixed", "overcast", "night"]:
        cnt = scenario_counts.get(sc, 0)
        pct = cnt / len(df_preds) * 100
        print(f"    {sc:<12} {cnt:>6,} ({pct:>5.1f}%)")
    print(f"  Elapsed time:            {t_elapsed:.1f} s")
    print(f"  Output:                  {OUTPUT_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
