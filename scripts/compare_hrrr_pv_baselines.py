"""Generate HRRR PV prediction baseline comparison report.

Full 4-baseline ablation (A/B/C1/D) with gap_closure and weather-scenario breakdown.

Usage:
    python scripts/compare_hrrr_pv_baselines.py
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

from new_energy_sys.cli.train_hrrr_pv import (
    _build_experiment_feature_sets,
    _detect_feature_groups,
    EXP_LABELS,
    run_ablation_experiment,
)
from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table

# ============================================================================
# Constants
# ============================================================================

CAPACITY_KW = 1.12  # system capacity in kW, used for nRMSE_cap alternative metric
HRRR_PATH = (
    "data/processed/pvdaq_nsrdb_2020_2022/"
    "stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
)
PV_PATH = (
    "data/processed/pvdaq_nsrdb_2020_2022/"
    "stage2_cleaned_hourly_dataset.parquet"
)
OUTPUT_DIR = "data/processed/hrrr_pv_models"

# Oracle reference metrics from the original full-features model.
# These are hardcoded because Experiment D (NSRDB oracle) requires NSRDB
# weather data that is not part of the HRRR pipeline — they serve as the
# theoretical upper-bound reference.
ORACLE_ALL_NRMSE = 0.0784
ORACLE_DAY_NRMSE = 0.0903


# ============================================================================
# Gap closure
# ============================================================================

def compute_gap_closure(metrics_df: pd.DataFrame) -> float:
    """Compute how much of the performance gap C1 closes toward the oracle.

    Formula:
        gap_closure = (A_day_nrmse - C1_day_nrmse) / (A_day_nrmse - oracle_day_nrmse) * 100

    A value of 100% means C1 matches the oracle; 0% means C1 matches history_only.
    """
    hist = metrics_df[metrics_df["exp_id"] == "A"]
    disc = metrics_df[metrics_df["exp_id"] == "C1"]

    if hist.empty or disc.empty:
        print("  WARNING: Cannot compute gap_closure — missing A or C1 metrics.")
        return float("nan")

    hist_val = hist["daytime_nrmse"].values[0]
    disc_val = disc["daytime_nrmse"].values[0]
    denominator = hist_val - ORACLE_DAY_NRMSE

    if abs(denominator) < 1e-10:
        return float("nan")

    return float((hist_val - disc_val) / denominator * 100)


# ============================================================================
# Weather scenario breakdown (C1 daytime only)
# ============================================================================

TARGET_COL = "target_pv_power_t_plus_24h"


def weather_scenario_breakdown(
    feature_df: pd.DataFrame,
    output_dir: str,
    metrics_df: pd.DataFrame,
) -> list[dict]:
    """Compute per-cloud-cover-bin metrics for C1 (HRRR-DISC) on daytime samples.

    Uses the same chronological test split (last 15%) as ``run_ablation_experiment``,
    then groups daytime predictions by ``cloud_cover_pct`` into three bins:
    clear (<20%), partly_cloudy (20-80%), overcast (>80%).

    Parameters
    ----------
    feature_df : pd.DataFrame
        Full feature table from ``build_complete_step1_feature_table``.
    output_dir : str
        Directory where the C1 model was saved (used to find the pickle file).
    metrics_df : pd.DataFrame
        Metrics DataFrame returned by ``run_ablation_experiment``; used to
        locate the saved model path.

    Returns
    -------
    list[dict]
        Each entry contains {bin, n, rmse, nrmse, mae, bias}.
    """
    # ---- Locate saved C1 model ----
    c1_row = metrics_df[metrics_df["exp_id"] == "C1"]
    if c1_row.empty:
        print("  WARNING: C1 metrics not found. Skipping weather breakdown.")
        return []

    model_path = c1_row["model_path"].values[0]
    if not model_path or not Path(model_path).is_file():
        print(f"  WARNING: C1 model not found at {model_path}. Skipping weather breakdown.")
        return []

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    # ---- Replicate test split (chronological 15%) ----
    ordered = feature_df.sort_values("timestamp").reset_index(drop=True)
    n_test = int(len(ordered) * 0.15)
    test_raw = ordered.iloc[-n_test:].copy()

    # Drop rows with NaN targets (same as run_ablation_experiment)
    test = test_raw.dropna(subset=[TARGET_COL]).copy()
    if len(test) == 0:
        print("  WARNING: No test samples after dropping NaN targets.")
        return []

    # ---- Detect C1 feature list ----
    groups = _detect_feature_groups(feature_df)
    exp_sets = _build_experiment_feature_sets(groups)
    c1_features = exp_sets["C1"]

    # ---- Predict ----
    y_true = test[TARGET_COL].values.astype(float)
    y_pred = model.predict(test[c1_features])
    y_pred = np.maximum(y_pred, 0.0)  # physical clip: no negative power

    test = test.copy()
    test["pred"] = y_pred
    test["error"] = y_pred - y_true
    test["abs_error"] = np.abs(test["error"])

    # ---- Daytime filter ----
    daytime_mask = test.get("ghi_wm2", pd.Series(0, index=test.index)) > 10.0
    day_test = test[daytime_mask].copy()
    if len(day_test) == 0:
        print("  WARNING: No daytime samples in test set.")
        return []

    # ---- Bin by cloud cover ----
    if "cloud_cover_pct" not in day_test.columns:
        print("  WARNING: cloud_cover_pct not in test set. Skipping weather breakdown.")
        return []

    # Drop rows where cloud_cover_pct is NaN (e.g. weather_missing=True)
    day_test = day_test.dropna(subset=["cloud_cover_pct"]).copy()
    if len(day_test) == 0:
        print("  WARNING: All daytime samples have NaN cloud_cover_pct.")
        return []

    day_test["cloud_bin"] = pd.cut(
        day_test["cloud_cover_pct"],
        bins=[-1, 20, 80, 101],
        labels=["clear (<20%)", "partly_cloudy (20-80%)", "overcast (>80%)"],
    )

    # ---- Per-bin metrics ----
    results: list[dict] = []
    for bin_name, grp in day_test.groupby("cloud_bin", observed=False):
        if len(grp) < 10:
            continue
        rmse = float(np.sqrt(np.mean(grp["error"] ** 2)))
        mae = float(grp["abs_error"].mean())
        bias = float(grp["error"].mean())
        mean_actual = float(grp[TARGET_COL].mean())
        nrmse = rmse / mean_actual if mean_actual > 1e-10 else 0.0

        results.append({
            "bin": str(bin_name),
            "n": int(len(grp)),
            "rmse": round(rmse, 4),
            "nrmse": round(nrmse, 4),
            "mae": round(mae, 4),
            "bias": round(bias, 4),
        })
        print(
            f"  {str(bin_name):<30} n={len(grp):>5}  "
            f"RMSE={rmse:.4f}  nRMSE={nrmse:.4f}  "
            f"MAE={mae:.4f}  Bias={bias:+.4f}"
        )

    return results


# ============================================================================
# Report formatting
# ============================================================================

def print_report(
    metrics_df: pd.DataFrame,
    gap_closure: float,
    weather_results: list[dict],
    output_dir: str,
) -> None:
    """Print the formatted ablation comparison table."""
    print()
    print("=" * 77)
    print("HRRR PV PREDICTION — ABLATION RESULTS")
    print("=" * 77)

    header = f"{'Baseline':<44} {'Day nRMSE':>10} {'All nRMSE':>10}"
    print(header)
    print("-" * 77)

    for _, row in metrics_df.iterrows():
        if row["exp_id"] == "D":
            label = f"{row['exp_id']}. {row['label']} (reference)"
            day_nrmse = ORACLE_DAY_NRMSE
            all_nrmse = ORACLE_ALL_NRMSE
        else:
            label = (
                f"{row['exp_id']}. {row['label']} "
                f"({int(row['n_features'])} features)"
            )
            day_nrmse = row["daytime_nrmse"]
            all_nrmse = row["all_nrmse"]

        print(f"  {label:<42} {day_nrmse:>10.4f} {all_nrmse:>10.4f}")

    print("-" * 77)
    gc_str = f"{gap_closure:.1f}%" if not np.isnan(gap_closure) else "N/A"
    print(f"{'Gap closure:':<44} {gc_str:>10}")
    print()

    # ---- Weather breakdown ----
    if weather_results:
        print("WEATHER SCENARIO BREAKDOWN (C1: HRRR-DISC, daytime)")
        print("-" * 77)
        for r in weather_results:
            print(
                f"  {r['bin']:<30} n={r['n']:>5}  "
                f"nRMSE={r['nrmse']:.4f}  Bias={r['bias']:+.4f}"
            )
        print()

    print(f"Report saved to {output_dir}/")


def print_metrics_csv(metrics_df: pd.DataFrame) -> None:
    """Print the raw metrics DataFrame for inspection."""
    print()
    print("Raw metrics DataFrame:")
    print(metrics_df.to_string(index=False))
    print()


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    """Build feature table -> run ablation -> compute gap_closure -> weather breakdown -> report."""
    print("=" * 77)
    print("HRRR PV Baseline Comparison Report")
    print("=" * 77)
    print(f"HRRR: {HRRR_PATH}")
    print(f"PV:   {PV_PATH}")
    print(f"Out:  {OUTPUT_DIR}")
    print()

    # ---- 1. Build feature table ----
    print("Building feature table...")
    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)
    print(f"  Feature table shape: {df.shape}")
    print(f"  Timestamp range: {df['target_time'].min()}  to  {df['target_time'].max()}")
    print()

    # ---- 2. Run full ablation (A/B/C1 train + eval, D is hardcoded reference) ----
    print("Running full ablation (3 x 1800-tree LightGBM)...")
    print("  This will take 10-20 minutes...")
    print()
    metrics_df = run_ablation_experiment(df, OUTPUT_DIR)
    print()

    # ---- 3. Gap closure ----
    print("Computing gap closure...")
    gap_closure = compute_gap_closure(metrics_df)
    gc_str = f"{gap_closure:.1f}%" if not np.isnan(gap_closure) else "N/A"
    print(f"  Gap closure: {gc_str}")
    print()

    # ---- 4. Weather scenario breakdown ----
    print("Weather scenario breakdown (C1 daytime, by cloud cover):")
    weather_results = weather_scenario_breakdown(df, OUTPUT_DIR, metrics_df)
    print()

    # ---- 5. Print formatted summary ----
    print_report(metrics_df, gap_closure, weather_results, OUTPUT_DIR)

    # ---- 6. Save comprehensive JSON report ----
    report = {
        "experiments": metrics_df.to_dict(orient="records"),
        "gap_closure_pct": (
            round(gap_closure, 2) if not np.isnan(gap_closure) else None
        ),
        "weather_breakdown": weather_results,
        "oracle_reference": {
            "all_nrmse": ORACLE_ALL_NRMSE,
            "daytime_nrmse": ORACLE_DAY_NRMSE,
        },
        "config": {
            "capacity_kw": CAPACITY_KW,
            "hrrr_path": HRRR_PATH,
            "pv_path": PV_PATH,
        },
    }

    report_path = Path(OUTPUT_DIR) / "ablation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Full report saved: {report_path}")


if __name__ == "__main__":
    main()
