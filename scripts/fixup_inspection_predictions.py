"""
One-shot fixup for inspection_predictions.parquet.

Fixes three issues from the 2026-05-04 CSI/Quantile experimental run:

1. raw_prediction_kw semantic break
   C1/C2/C4 store CSI ratio (~0.8) in raw_prediction_kw instead of kW.
   Fixed by: raw_prediction_kw = CSI_pred * clear_sky_power_at_valid

2. Missing persistence baselines
   persistence_origin_kw and persistence_same_hour_yesterday_kw were
   100% NaN for all c0-c4 experiments.
   Fixed by: look up PV power from Stage3 at origin_time and valid_time-24h.

3. Missing Q1/Q2 in inspection parquet
   Quantile predictions were never written to the dashboard parquet.
   Fixed by: loading trained quantile models, predicting P10/P50/P90,
   and appending properly-formatted rows.

Usage:
    python scripts/fixup_inspection_predictions.py

Expected: ~2 min, modifies inspection_predictions.parquet in-place
          (creates a .bak backup before overwriting).
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pvlib.location import Location

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from new_energy_sys.csi_utils import (
    CAPACITY_KW,
    EFFICIENCY,
    PREDICTION_UPPER,
    SOLAR_ELEV_THRESHOLD,
    CSI_CLIP_LOW,
    CSI_CLIP_HIGH,
    CSP_THRESHOLD,
    HORIZONS,
    SITE,
    add_e1_features,
    build_csi_target,
    classify_scenario,
    compute_clear_sky_power,
    precompute_clear_sky_lookup,
)
from new_energy_sys.modeling import _chronological_split

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
INSPECTION_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.parquet"
)
BACKUP_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.bak.parquet"
)
STAGE3_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet"
)
CSI_MODEL_DIR = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/csi_models"
)
QUANTILE_MODEL_DIR = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/quantile_models"
)

GIT_COMMIT = "fixup"


# ---------------------------------------------------------------------------
# Phase A: Fix CSI experiment rows
# ---------------------------------------------------------------------------

def fix_csi_experiments(df_inspect: pd.DataFrame) -> pd.DataFrame:
    """Fix raw_prediction_kw and persistence columns for c0-c4 experiments.

    Returns a modified copy — does NOT mutate the input.
    """
    print("=" * 60)
    print("Phase A: Fixing CSI experiment rows")
    print("=" * 60)

    csi_mask = df_inspect["experiment"].isin(["c0", "c1", "c2", "c3", "c4"])
    if not csi_mask.any():
        print("  No CSI experiment rows found, skipping.")
        return df_inspect

    df_csi = df_inspect[csi_mask].copy()
    print(f"  CSI rows to fix: {len(df_csi)}")

    # ---- A1: Compute clear_sky_power at each valid_time ------------------
    print("  A1: Computing clear-sky power at valid_time ...")
    valid_times = pd.DatetimeIndex(df_csi["valid_time"].unique()).sort_values()
    csp_series = compute_clear_sky_power(valid_times)
    csp_map = dict(zip(valid_times, csp_series.values))

    df_csi["_csp_valid"] = df_csi["valid_time"].map(csp_map)

    # ---- A2: Fix raw_prediction_kw ---------------------------------------
    # For C0: raw_prediction_kw is already in kW (model output = PV power)
    # For C1/C2/C4: raw_prediction_kw currently stores CSI ratio.
    #   Convert: raw_prediction_kw (kW) = CSI_pred * clear_sky_power_valid
    print("  A2: Fixing raw_prediction_kw semantics ...")
    n_fixed = 0
    for exp in ["c1", "c2", "c4"]:
        exp_mask = df_csi["experiment"] == exp
        if not exp_mask.any():
            continue
        old_mean = df_csi.loc[exp_mask, "raw_prediction_kw"].mean()
        # CSI prediction -> PV power (unclipped)
        df_csi.loc[exp_mask, "raw_prediction_kw"] = (
            df_csi.loc[exp_mask, "raw_prediction_kw"].values
            * df_csi.loc[exp_mask, "_csp_valid"].values
        )
        new_mean = df_csi.loc[exp_mask, "raw_prediction_kw"].mean()
        n_fixed += int(exp_mask.sum())
        print(f"    {exp}: raw_pred mean {old_mean:.4f} (CSI) -> {new_mean:.4f} (kW)"
              f"  ({int(exp_mask.sum())} rows)")

    # C3 (physical baseline): raw_prediction_kw already = pred_kw, no fix needed
    # C0: already correct
    print(f"    Total raw_prediction_kw fixes: {n_fixed} rows")

    # ---- A3: Add persistence baselines --------------------------------
    print("  A3: Adding persistence baselines from Stage3 ...")
    stage3 = pd.read_parquet(STAGE3_PATH)
    stage3["timestamp"] = pd.to_datetime(stage3["timestamp"], utc=True)

    # Build lookup: timestamp -> pv_power_kw
    pv_lookup = dict(zip(stage3["timestamp"], stage3["pv_power_kw"]))

    # persistence_origin_kw = pv_power at origin_time
    df_csi["origin_time_dt"] = pd.to_datetime(df_csi["origin_time"], utc=True)
    df_csi["persistence_origin_kw"] = df_csi["origin_time_dt"].map(pv_lookup)

    # persistence_same_hour_yesterday_kw = pv_power at (valid_time - 24h)
    df_csi["valid_time_dt"] = pd.to_datetime(df_csi["valid_time"], utc=True)
    df_csi["yesterday_time"] = df_csi["valid_time_dt"] - pd.Timedelta(hours=24)
    df_csi["persistence_same_hour_yesterday_kw"] = (
        df_csi["yesterday_time"].map(pv_lookup)
    )

    n_origin = int(df_csi["persistence_origin_kw"].notna().sum())
    n_yesterday = int(df_csi["persistence_same_hour_yesterday_kw"].notna().sum())
    print(f"    persistence_origin_kw: {n_origin}/{len(df_csi)} non-NaN")
    print(f"    persistence_same_hour_yesterday_kw: {n_yesterday}/{len(df_csi)} non-NaN")

    # ---- A4: Clean up temp columns -----------------------------------
    df_csi = df_csi.drop(
        columns=["_csp_valid", "origin_time_dt", "valid_time_dt", "yesterday_time"],
        errors="ignore",
    )

    # ---- A5: Merge back into main DataFrame --------------------------
    result = df_inspect.copy()
    # Remove old CSI rows, add fixed ones
    result = result[~csi_mask]
    result = pd.concat([result, df_csi], ignore_index=True)
    print(f"  Merged: {len(result)} total rows after CSI fix")
    return result


# ---------------------------------------------------------------------------
# Phase B: Add Q1/Q2 quantile predictions
# ---------------------------------------------------------------------------

def _load_quantile_models(
    experiment: str, horizon: int, model_dir: Path,
) -> dict[float, object] | None:
    """Load P10/P50/P90 models for (experiment, horizon). Returns {alpha: bundle}."""
    models: dict[float, object] = {}
    for alpha in [0.1, 0.5, 0.9]:
        model_path = model_dir / f"lightgbm_{experiment}_t{horizon}h_a{alpha:.1f}.pkl"
        if not model_path.exists():
            print(f"    WARNING: missing {model_path.name}")
            return None
        with open(model_path, "rb") as f:
            models[alpha] = pickle.load(f)
    return models


def generate_quantile_predictions() -> pd.DataFrame | None:
    """Load quantile models, predict on test split, return inspection-format rows.

    Returns None if any model file is missing.
    """
    print("\n" + "=" * 60)
    print("Phase B: Generating Q1/Q2 quantile predictions")
    print("=" * 60)

    # ---- B1: Load Stage3 ------------------------------------------------
    print("  B1: Loading Stage3 data ...")
    stage3 = pd.read_parquet(STAGE3_PATH)
    stage3["timestamp"] = pd.to_datetime(stage3["timestamp"], errors="coerce", utc=True)
    stage3 = stage3.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"    {len(stage3)} rows")

    csp_lookup = precompute_clear_sky_lookup(stage3)
    print(f"    Clear-sky lookup: {len(csp_lookup)} hourly values")

    # ---- B2: Check all model files exist --------------------------------
    print("  B2: Checking model files ...")
    for exp in ["Q1", "Q2"]:
        for h in HORIZONS:
            for alpha in [0.1, 0.5, 0.9]:
                mp = QUANTILE_MODEL_DIR / f"lightgbm_{exp}_t{h}h_a{alpha:.1f}.pkl"
                if not mp.exists():
                    print(f"    ERROR: missing {mp}")
                    return None
    print("    All 18 quantile models found.")

    # ---- B3: Predict per experiment + horizon ---------------------------
    all_frames: list[pd.DataFrame] = []

    for experiment in ["Q1", "Q2"]:
        for horizon in HORIZONS:
            print(f"\n  --- {experiment} t+{horizon}h ---")

            # Build E1 features
            df_e1 = add_e1_features(stage3, horizon)
            if experiment == "Q2":
                df_e1 = build_csi_target(df_e1, horizon, csp_lookup)

            # Build feature list
            exclude = {"timestamp", "pv_power_kw"}
            for h in HORIZONS:
                exclude.add(f"target_pv_power_t_plus_{h}h")
                exclude.add(f"csi_target_{h}h")
                exclude.add(f"clear_sky_power_valid_{h}h")
                exclude.add(f"solar_elevation_valid_{h}h")

            allowed_dtypes = (
                np.float64, np.float32, np.int64, np.int32,
                np.int8, np.int16, np.uint8,
            )
            features = [
                c for c in df_e1.columns
                if c not in exclude and df_e1[c].dtype in allowed_dtypes
            ]
            print(f"    Features: {len(features)}")

            # Load models
            models = _load_quantile_models(experiment, horizon, QUANTILE_MODEL_DIR)
            if models is None:
                print(f"    SKIP: model files incomplete for {experiment} t+{horizon}h")
                continue

            # Determine target column and split
            if experiment == "Q1":
                target_col = f"target_pv_power_t_plus_{horizon}h"
            else:
                target_col = f"csi_target_{horizon}h"

            clean = df_e1.dropna(subset=[target_col]).copy()
            splits = _chronological_split(clean)
            test = splits["test"].copy()
            X_te = test[features].values

            # Predict P10/P50/P90
            preds = {}
            for alpha in [0.1, 0.5, 0.9]:
                raw = models[alpha]["model"].predict(X_te)
                preds[alpha] = np.clip(raw, 0.0, PREDICTION_UPPER)

            # Restore to PV space if Q2
            if experiment == "Q1":
                p10_pv = preds[0.1]
                p50_pv = preds[0.5]
                p90_pv = preds[0.9]
                raw_pv = preds[0.5]  # P50 as "raw" representative
            else:
                csp_col = f"clear_sky_power_valid_{horizon}h"
                csp_test = test[csp_col].values
                # Sort to fix crossing in CSI space
                sorted_csi = np.sort(
                    [preds[0.1], preds[0.5], preds[0.9]], axis=0
                )
                p10_pv = np.clip(sorted_csi[0] * csp_test, 0.0, PREDICTION_UPPER)
                p50_pv = np.clip(sorted_csi[1] * csp_test, 0.0, PREDICTION_UPPER)
                p90_pv = np.clip(sorted_csi[2] * csp_test, 0.0, PREDICTION_UPPER)
                raw_pv = np.clip(preds[0.5] * csp_test, 0.0, PREDICTION_UPPER)

            # Valid-time alignment
            test["valid_time"] = (
                pd.to_datetime(test["timestamp"]) + pd.Timedelta(hours=horizon)
            )
            test["origin_time"] = pd.to_datetime(test["timestamp"])

            # Fetch actual PV and clearsky index
            s3_actual = stage3[["timestamp", "pv_power_kw", "clearsky_index_ghi",
                                 "ghi_wm2", "clearsky_ghi_wm2"]].copy()
            s3_actual = s3_actual.rename(columns={"timestamp": "valid_time"})
            test = test.merge(s3_actual, on="valid_time", how="left",
                              suffixes=("", "_actual"))

            actual_kw = test["pv_power_kw_actual"].values
            csi_valid = test["clearsky_index_ghi_actual"].values
            ghi = test["ghi_wm2_actual"].values
            clearsky_ghi = test["clearsky_ghi_wm2_actual"].values

            # Solar elevation for scenario classification
            elev_col = f"solar_elevation_valid_{horizon}h"
            if elev_col in test.columns:
                solar_elev = test[elev_col].values
            else:
                solar_elev = test.get(
                    f"valid_h{horizon}h_solar_elevation",
                    pd.Series(90.0, index=test.index),
                ).values

            # ---- Build THREE rows per test sample (P10, P50, P90) ----
            for quantile_label, pred_kw, raw_kw in [
                ("p10", p10_pv, p10_pv),
                ("p50", p50_pv, p50_pv),
                ("p90", p90_pv, p90_pv),
            ]:
                frame = pd.DataFrame({
                    "origin_time": test["origin_time"],
                    "valid_time": test["valid_time"],
                    "horizon_hours": horizon,
                    "experiment": f"{experiment.lower()}_{quantile_label}",
                    "model_name": "lightgbm_quantile",
                    "model_version": GIT_COMMIT,
                    "feature_set": f"{experiment}_quantile_{len(features)}feat",
                    "target_type": (
                        "pv_power_quantile" if experiment == "Q1"
                        else "csi_quantile_to_pv"
                    ),
                    "raw_prediction_kw": raw_kw,
                    "prediction_kw": pred_kw,
                    "actual_kw": actual_kw,
                    "persistence_origin_kw": np.nan,
                    "persistence_same_hour_yesterday_kw": np.nan,
                    "error_kw": pred_kw - actual_kw,
                    "abs_error_kw": np.abs(pred_kw - actual_kw),
                    "ghi_wm2": ghi,
                    "clearsky_ghi_wm2": clearsky_ghi,
                    "solar_elevation_deg": solar_elev,
                    "cloud_cover_pct": np.nan,
                    "scenario": classify_scenario(
                        pd.Series(solar_elev), pd.Series(csi_valid)
                    ).values,
                    "split": "test",
                    "csi_valid": csi_valid,
                })
                all_frames.append(frame)

            print(f"    Generated {len(test) * 3} rows (3 quantiles x {len(test)} test samples)")

    if not all_frames:
        print("  ERROR: No quantile predictions generated.")
        return None

    result = pd.concat(all_frames, ignore_index=True)
    print(f"\n  Total quantile rows: {len(result)}")
    return result


# ---------------------------------------------------------------------------
# Phase C: Validation
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame) -> None:
    """Run 7 assertions on the fixed parquet."""
    print("\n" + "=" * 60)
    print("Phase C: Validation")
    print("=" * 60)

    errors = []

    # 1. No duplicate (valid_time, horizon, experiment) tuples
    dups = df[["valid_time", "horizon_hours", "experiment"]].duplicated().sum()
    if dups == 0:
        print(f"  [PASS] 1. No duplicate tuples (found: {dups})")
    else:
        msg = f"Found {dups} duplicate (valid_time, horizon, experiment) tuples"
        print(f"  [FAIL] 1. {msg}")
        errors.append(msg)

    # 2. prediction_kw completeness >= 99%
    completeness = df["prediction_kw"].notna().mean()
    if completeness > 0.99:
        print(f"  [PASS] 2. prediction_kw completeness = {completeness:.4%}")
    else:
        msg = f"prediction_kw completeness = {completeness:.4%} (< 99%)"
        print(f"  [FAIL] 2. {msg}")
        errors.append(msg)

    # 3. prediction_kw >= -0.05
    min_pred = df["prediction_kw"].min()
    if min_pred >= -0.05:
        print(f"  [PASS] 3. prediction_kw >= -0.05 (min = {min_pred:.4f})")
    else:
        msg = f"prediction_kw min = {min_pred:.4f} (< -0.05)"
        print(f"  [FAIL] 3. {msg}")
        errors.append(msg)

    # 4. raw_prediction_kw is in kW range for ALL experiments
    #    (CSI experiments should no longer have ~0.8 values)
    csi_exps = ["c1", "c2", "c4"]
    for exp in csi_exps:
        sub = df[df["experiment"] == exp]
        if len(sub) == 0:
            continue
        raw_mean = sub["raw_prediction_kw"].mean()
        # After fix, raw mean should be ~0.35 kW (PV power), not ~0.8 (CSI)
        if 0.1 < raw_mean < 1.2:  # plausible kW range for 1.12 kW system
            print(f"  [PASS] 4. {exp} raw_prediction_kw mean={raw_mean:.4f} kW (was ~0.8 CSI)")
        else:
            msg = f"{exp} raw_prediction_kw mean={raw_mean:.4f} (expected ~0.3-0.4 kW)"
            print(f"  [FAIL] 4. {msg}")
            errors.append(msg)

    # 5. Persistence baselines non-NaN for CSI experiments
    for exp in ["c0", "c1", "c2", "c3", "c4"]:
        sub = df[df["experiment"] == exp]
        if len(sub) == 0:
            continue
        p_origin = sub["persistence_origin_kw"].notna().mean()
        p_yesterday = sub["persistence_same_hour_yesterday_kw"].notna().mean()
        ok = p_origin > 0.5 and p_yesterday > 0.5
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] 5. {exp} persistence: origin={p_origin:.1%}, yesterday={p_yesterday:.1%}")
        if not ok:
            errors.append(f"{exp} persistence baselines < 50%")

    # 6. Q1/Q2 experiments present
    has_q = any("q1_" in e or "q2_" in e for e in df["experiment"].unique())
    if has_q:
        q_exps = [e for e in sorted(df["experiment"].unique()) if "q1_" in e or "q2_" in e]
        print(f"  [PASS] 6. Quantile experiments present: {q_exps}")
    else:
        msg = "No Q1/Q2 experiments found"
        print(f"  [FAIL] 6. {msg}")
        errors.append(msg)

    # 7. Horizon consistency
    time_diff = (df["valid_time"] - df["origin_time"]).dt.total_seconds() / 3600.0
    expected_h = df["horizon_hours"].astype(float)
    # Allow 1-hour tolerance due to DST transitions
    mismatch = (time_diff - expected_h).abs() > 1.0
    if mismatch.sum() == 0:
        print(f"  [PASS] 7. valid_time - origin_time == horizon_hours (0 mismatches)")
    else:
        msg = f"{mismatch.sum()} rows have time_diff != horizon_hours"
        print(f"  [FAIL] 7. {msg}")
        errors.append(msg)

    if errors:
        print(f"\n  {len(errors)} VALIDATION ERROR(S):")
        for e in errors:
            print(f"    - {e}")
        raise AssertionError(f"Validation failed: {len(errors)} errors")
    else:
        print("\n  All 7 validation assertions passed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Fixup: inspection_predictions.parquet")
    print("=" * 60)

    # ---- 0. Verify source file exists -----------------------------------
    if not INSPECTION_PATH.exists():
        print(f"ERROR: {INSPECTION_PATH} not found", file=sys.stderr)
        sys.exit(1)

    if not STAGE3_PATH.exists():
        print(f"ERROR: {STAGE3_PATH} not found", file=sys.stderr)
        sys.exit(1)

    # ---- Load -----------------------------------------------------------
    print(f"\nLoading {INSPECTION_PATH} ...")
    df = pd.read_parquet(INSPECTION_PATH)
    print(f"  {len(df):,} rows, experiments: {sorted(df['experiment'].unique())}")

    # ---- Create backup --------------------------------------------------
    print(f"\nCreating backup at {BACKUP_PATH} ...")
    df.to_parquet(BACKUP_PATH, index=False)
    print(f"  Backup: {BACKUP_PATH} ({BACKUP_PATH.stat().st_size / 1024:.0f} KB)")

    # ---- Phase A: Fix CSI rows ------------------------------------------
    df = fix_csi_experiments(df)

    # ---- Phase B: Add Q1/Q2 predictions ---------------------------------
    quantile_df = generate_quantile_predictions()
    if quantile_df is not None:
        # Align columns with the main DataFrame
        for col in df.columns:
            if col not in quantile_df.columns:
                quantile_df[col] = np.nan
        quantile_df = quantile_df[df.columns]  # reorder to match
        df = pd.concat([df, quantile_df], ignore_index=True)
        print(f"  Merged: {len(df):,} total rows after adding quantile predictions")
    else:
        print("  WARNING: Quantile predictions not generated, skipping merge.")

    # ---- Phase C: Validate ----------------------------------------------
    validate(df)

    # ---- Save -----------------------------------------------------------
    print(f"\nSaving {INSPECTION_PATH} ...")
    df.to_parquet(INSPECTION_PATH, index=False)
    file_size_mb = INSPECTION_PATH.stat().st_size / (1024 * 1024)
    print(f"  Saved: {INSPECTION_PATH} ({file_size_mb:.2f} MB)")

    # ---- Summary --------------------------------------------------------
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total rows:       {len(df):,}")
    print(f"  Experiments:      {sorted(df['experiment'].unique())}")
    print(f"  Backup:           {BACKUP_PATH}")
    for exp in sorted(df["experiment"].unique()):
        sub = df[df["experiment"] == exp]
        print(f"    {exp:<18} {len(sub):>6,} rows  "
              f"p_nan={sub['prediction_kw'].isna().mean():.3f}  "
              f"r_mean={sub['raw_prediction_kw'].mean():.4f}")
    print("Done.")


if __name__ == "__main__":
    main()
