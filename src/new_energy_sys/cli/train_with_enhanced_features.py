"""
Feature ablation CLI --- compare enhanced LightGBM models for PV power prediction.

Runs 3 experiments (E0/E1/E2) across 3 forecast horizons (t+1h, t+6h, t+24h),
using valid-time aligned evaluation with weather-stratified metrics.

Experiments
-----------
E0 (baseline): Original 163 Stage3 features only. No enhancements.
E1 (+solar +ramp): E0 + valid-time solar geometry features + origin-time ramp rates.
E2 (+cloud_scenario): E1 + origin-time cloud scenario one-hot encoding.

Usage
-----
    python -m new_energy_sys.cli.train_with_enhanced_features

Output
------
    data/processed/pvdaq_nsrdb_2020_2022/enhanced_models/
        lightgbm_{E0,E1,E2}_{t1h,t6h,t24h}.pkl    9 model files
        ablation_comparison.csv                   Metrics table (CSV)
        ablation_comparison.json                  Metrics table (JSON)
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
    add_origin_cloud_scenario,
)
from new_energy_sys.modeling import _chronological_split

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Stage3 feature dataset (relative to project root)
DATA_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet"
)
OUTPUT_DIR = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/enhanced_models"
)

# PVDAQ site: Golden, CO (NSRDB grid cell centre)
SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}

# All three forecast targets with their horizon hours
TARGETS: dict[str, int] = {
    "target_pv_power_t_plus_1h": 1,
    "target_pv_power_t_plus_6h": 6,
    "target_pv_power_t_plus_24h": 24,
}

# Three ablation experiments
EXPERIMENTS: list[str] = ["E0", "E1", "E2"]

# Stage5 tuned hyperparameters per horizon
# These were obtained via Optuna optimisation on the same Stage3 dataset
# and are held fixed across all ablation experiments.
HYPERPARAMS: dict[int, dict] = {
    1: {
        "learning_rate": 0.025,
        "max_depth": 8,
        "num_leaves": 31,
        "n_estimators": 1600,
        "colsample_bytree": 0.8,
        "min_child_samples": 45,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "subsample": 0.9,
    },
    6: {
        "learning_rate": 0.03,
        "max_depth": -1,
        "num_leaves": 63,
        "n_estimators": 1500,
        "colsample_bytree": 0.75,
        "min_child_samples": 50,
        "reg_alpha": 0.2,
        "reg_lambda": 1.2,
        "subsample": 0.8,
    },
    24: {
        "learning_rate": 0.02,
        "max_depth": 10,
        "num_leaves": 45,
        "n_estimators": 1800,
        "colsample_bytree": 0.8,
        "min_child_samples": 35,
        "reg_alpha": 0.1,
        "reg_lambda": 0.8,
        "subsample": 0.85,
    },
}

ALL_TARGET_COLS: list[str] = list(TARGETS.keys())
PREDICTION_UPPER: float = SITE["capacity_kw"] * 1.05  # 1.176 kW


# ---------------------------------------------------------------------------
# Feature helpers
# ---------------------------------------------------------------------------


def _exclude_columns() -> set[str]:
    """Columns that must never be used as model features.

    These include:
    - ``timestamp``: not a predictive signal
    - ``target_pv_power_t_plus_*h``: the label, would cause data leakage
    - ``pv_power_kw``: current power is unknown at prediction time in a
      true forecast setting; ramp features derived from it are fine.
    """
    return {"timestamp", *ALL_TARGET_COLS, "pv_power_kw"}


def _build_feature_list(df: pd.DataFrame) -> list[str]:
    """Return all numeric columns valid as features (exclude labels/timestamp).

    Uses explicit dtype whitelist to safely skip categorical/object/string cols
    that might appear in intermediate DataFrames.
    """
    exclude = _exclude_columns()
    allowed_dtypes = (
        np.float64,
        np.float32,
        np.int64,
        np.int32,
        np.int8,
        np.int16,
        np.uint8,
    )
    return [
        c
        for c in df.columns
        if c not in exclude and df[c].dtype in allowed_dtypes
    ]


def _prepare_experiment_data(
    df: pd.DataFrame,
    experiment: str,
    horizon_hours: int,
) -> pd.DataFrame:
    """Apply feature enhancement pipeline for the given experiment.

    Parameters
    ----------
    df : pd.DataFrame
        Original Stage3 dataset (unchanged between experiments).
    experiment : str
        One of ``"E0"``, ``"E1"``, ``"E2"``.
    horizon_hours : int
        Prediction horizon used to select the correct valid-time solar features.

    Returns
    -------
    pd.DataFrame
        Copy of ``df`` with experiment-specific columns added.
        The original ``df`` is never mutated.
    """
    result = df.copy()

    if experiment == "E0":
        # Baseline: no enhancements at all
        return result

    # E1 and E2 share: valid-time solar geometry + ramp rates
    result = add_valid_time_solar_features(
        result,
        horizon_hours,
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    result = add_ramp_features(result)

    if experiment == "E2":
        # E2 also adds origin-time cloud scenario one-hot encoding
        result = add_origin_cloud_scenario(result)

    return result


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _clip_predictions(pred: np.ndarray) -> np.ndarray:
    """Physically clip predictions to [0, 1.05 * capacity].

    LightGBM is an unconstrained regressor and may produce negative values
    during night hours or values exceeding capacity for over-optimistic
    clear-sky predictions.
    """
    return np.clip(pred, 0.0, PREDICTION_UPPER)


# ---------------------------------------------------------------------------
# Core: train + evaluate one experiment/target combination
# ---------------------------------------------------------------------------


def _train_and_evaluate(
    df: pd.DataFrame,
    experiment: str,
    target: str,
    horizon_hours: int,
    model_dir: Path,
) -> dict:
    """Train one LightGBM model and compute valid-time aligned metrics.

    The training-evaluation flow follows the Stage 4/5 pipeline pattern:
    1. Apply experiment-specific feature enhancements
    2. Chronological 70/15/15 split
    3. Train LightGBM with Stage5 hyperparams + early stopping (80 rounds)
    4. Predict on the test set
    5. Valid-time alignment: map each prediction to the actual PV at the
       forecast target time (``timestamp + horizon_hours``)
    6. Weather-stratified metrics (clear / mixed / overcast / evening)

    Returns
    -------
    dict
        Flat dictionary of metrics, one row per experiment x target.
    """
    target_short = target.replace("target_pv_power_t_plus_", "t")
    print(
        f"  Training {experiment} / {target} ...",
        end=" ",
        flush=True,
    )

    # ---- 1. Feature preparation -------------------------------------------
    feature_df = _prepare_experiment_data(df, experiment, horizon_hours)

    # ---- 2. Chronological split -------------------------------------------
    splits = _chronological_split(feature_df)

    # ---- 3. Feature selection ---------------------------------------------
    features = _build_feature_list(feature_df)
    print(f"[{len(features)} features]", end=" ", flush=True)

    # ---- 4. Train LightGBM ------------------------------------------------
    hp = HYPERPARAMS[horizon_hours]
    model = lgb.LGBMRegressor(
        objective="regression",
        boosting_type="gbdt",
        n_estimators=hp["n_estimators"],
        learning_rate=hp["learning_rate"],
        num_leaves=hp["num_leaves"],
        max_depth=hp["max_depth"],
        min_child_samples=hp["min_child_samples"],
        subsample=hp["subsample"],
        subsample_freq=1,
        colsample_bytree=hp["colsample_bytree"],
        reg_alpha=hp["reg_alpha"],
        reg_lambda=hp["reg_lambda"],
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        splits["train"][features],
        splits["train"][target],
        eval_set=[
            (splits["validation"][features], splits["validation"][target])
        ],
        eval_metric="rmse",
        callbacks=[
            lgb.early_stopping(stopping_rounds=80, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    )
    best_iter = int(model.best_iteration_ or model.n_estimators)
    print(f"[{best_iter} iters]", end=" ", flush=True)

    # ---- 5. Save model ----------------------------------------------------
    model_name = f"lightgbm_{experiment}_{target_short}.pkl"
    model_path = model_dir / model_name
    with open(model_path, "wb") as f:
        pickle.dump(
            {
                "model": model,
                "features": features,
                "target": target,
                "experiment": experiment,
                "horizon_hours": horizon_hours,
                "capacity_kw": SITE["capacity_kw"],
                "prediction_lower_bound_kw": 0.0,
                "prediction_upper_bound_kw": PREDICTION_UPPER,
            },
            f,
        )

    # ---- 6. Test-set evaluation with valid-time alignment -----------------
    test = splits["test"].copy()
    preds = model.predict(test[features], num_iteration=model.best_iteration_)
    preds = _clip_predictions(preds)
    test["prediction"] = preds

    # Valid-time = origin timestamp + forecast horizon
    test["valid_time"] = pd.to_datetime(test["timestamp"]) + pd.Timedelta(
        hours=horizon_hours
    )

    # Fetch actual PV and weather at valid time from the full dataset
    full_actual = feature_df[
        ["timestamp", "pv_power_kw", "ghi_wm2",
         "clearsky_ghi_wm2", "clearsky_index_ghi"]
    ].copy()
    full_actual = full_actual.rename(columns={"timestamp": "valid_time"})
    test = test.merge(
        full_actual, on="valid_time", how="left", suffixes=("", "_valid")
    )

    valid_exists = int(test["pv_power_kw_valid"].notna().sum())
    print(f"[{valid_exists} valid rows]", flush=True)

    # Solar elevation at valid time (for daytime filtering)
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(
        times=pd.DatetimeIndex(test["valid_time"])
    )
    test["solar_elev_valid"] = solar_pos["elevation"].values
    test["csi_valid"] = test["clearsky_index_ghi_valid"]

    # Prediction error (positive = overprediction)
    test["error"] = test["prediction"] - test["pv_power_kw_valid"]

    # Daytime: solar elevation > 5 degrees above horizon
    daytime = test["solar_elev_valid"] > 5

    # -- Overall metrics ----------------------------------------------------
    all_rmse = float(np.sqrt(np.nanmean(test["error"] ** 2)))
    day_mask = daytime & test["pv_power_kw_valid"].notna()
    day_rmse = float(np.sqrt(np.nanmean(test.loc[day_mask, "error"] ** 2)))
    all_mae = float(np.nanmean(np.abs(test["error"])))
    day_mae = float(
        np.nanmean(np.abs(test.loc[day_mask, "error"]))
    )
    all_bias = float(np.nanmean(test["error"]))
    day_mean_actual = float(
        test.loc[day_mask, "pv_power_kw_valid"].mean()
    )
    all_mean_actual = float(test["pv_power_kw_valid"].mean())

    # -- Weather-stratified at valid time -----------------------------------
    test["scenario_valid"] = "night"
    test.loc[
        daytime & (test["csi_valid"] >= 0.7), "scenario_valid"
    ] = "clear"
    test.loc[
        daytime & (test["csi_valid"] >= 0.3) & (test["csi_valid"] < 0.7),
        "scenario_valid",
    ] = "mixed"
    test.loc[
        daytime & (test["csi_valid"] < 0.3), "scenario_valid"
    ] = "overcast"

    metrics: dict = {
        "experiment": experiment,
        "target": target,
        "horizon_hours": horizon_hours,
        "feature_count": len(features),
        "best_iteration": best_iter,
        "n_test": len(test),
        "n_valid_valid_time": valid_exists,
        # Overall
        "all_rmse_kw": all_rmse,
        "all_nrmse": (
            all_rmse / all_mean_actual if all_mean_actual > 0 else float("nan")
        ),
        "all_mae_kw": all_mae,
        "all_bias_kw": all_bias,
        # Daytime
        "day_rmse_kw": day_rmse,
        "day_nrmse": (
            day_rmse / day_mean_actual if day_mean_actual > 0 else float("nan")
        ),
        "day_mae_kw": day_mae,
    }

    for scenario in ("clear", "mixed", "overcast"):
        sub = test[test["scenario_valid"] == scenario]
        n_sub = len(sub)
        if n_sub == 0:
            metrics.update(
                {
                    f"{scenario}_n": 0,
                    f"{scenario}_rmse_kw": float("nan"),
                    f"{scenario}_nrmse": float("nan"),
                    f"{scenario}_mae_kw": float("nan"),
                    f"{scenario}_bias_kw": float("nan"),
                }
            )
            continue
        err = sub["error"].values
        actual = sub["pv_power_kw_valid"].values
        sc_rmse = float(np.sqrt(np.nanmean(err**2)))
        sc_mean = float(np.nanmean(actual))
        metrics.update(
            {
                f"{scenario}_n": n_sub,
                f"{scenario}_rmse_kw": sc_rmse,
                f"{scenario}_nrmse": (
                    sc_rmse / sc_mean if sc_mean > 0 else float("nan")
                ),
                f"{scenario}_mae_kw": float(np.nanmean(np.abs(err))),
                f"{scenario}_bias_kw": float(np.nanmean(err)),
            }
        )

    # -- Evening bias (20--23 UTC valid hour) -------------------------------
    test["valid_hour"] = pd.DatetimeIndex(test["valid_time"]).hour
    evening = test[
        (test["valid_hour"] >= 20)
        & (test["valid_hour"] <= 23)
        & daytime
        & test["pv_power_kw_valid"].notna()
    ]
    if len(evening) > 0:
        metrics["evening_bias_kw"] = float(evening["error"].mean())
        metrics["evening_n"] = int(len(evening))
    else:
        metrics["evening_bias_kw"] = float("nan")
        metrics["evening_n"] = 0

    return metrics


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------


def _print_report_table(all_metrics: list[dict]) -> None:
    """Print the ablation comparison table and evening-bias analysis."""
    print("\n" + "=" * 90)
    print("ABLATION RESULTS")
    print("=" * 90)
    print(
        f"{'Exp':<4} {'Target':<8} {'All_nRMSE':<10} {'Day_nRMSE':<11} "
        f"{'Clear_nRMSE':<12} {'Mixed_nRMSE':<12} {'Overcast_nRMSE':<14} "
        f"{'EveBias':<10} {'Feat':<5}"
    )
    print("-" * 90)

    for m in all_metrics:
        target_display = m["target"].replace("target_pv_power_t_plus_", "t")
        print(
            f"{m['experiment']:<4} {target_display:<8} "
            f"{_fmt(m['all_nrmse']):<10} {_fmt(m['day_nrmse']):<11} "
            f"{_fmt(m['clear_nrmse']):<12} {_fmt(m['mixed_nrmse']):<12} "
            f"{_fmt(m['overcast_nrmse']):<14} "
            f"{m['evening_bias_kw']:<+10.3f} "
            f"{m['feature_count']:<5}"
        )

    print("=" * 90)

    # Evening bias change (E1 - E0)
    print("\nEvening Bias Change (E1 - E0):")
    for target_col, horizon in TARGETS.items():
        e0 = next(
            m
            for m in all_metrics
            if m["experiment"] == "E0" and m["target"] == target_col
        )
        e1 = next(
            m
            for m in all_metrics
            if m["experiment"] == "E1" and m["target"] == target_col
        )
        e0_bias = e0["evening_bias_kw"]
        e1_bias = e1["evening_bias_kw"]
        delta = e1_bias - e0_bias
        target_short = target_col.replace("target_pv_power_t_plus_", "t")
        print(
            f"  {target_short}: {e0_bias:+7.3f} -> {e1_bias:+7.3f} "
            f"(Delta = {delta:+7.3f})"
        )

    print("=" * 90)


def _fmt(val: float) -> str:
    """Format a float for the report table, handling NaN."""
    if np.isnan(val):
        return "  N/A     "
    return f"{val:<10.3f}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the full ablation experiment pipeline.

    Stages
    ------
    1. Load Stage3 parquet dataset
    2. For each experiment (E0, E1, E2) and each target (t+1h, t+6h, t+24h):
       a. Apply feature enhancements
       b. Train LightGBM with Stage5 hyperparams
       c. Evaluate with valid-time alignment
       d. Serialise model
    3. Save comparison CSV + JSON
    4. Print report table
    """
    print("=" * 60)
    print("Feature Ablation Experiment")
    print("=" * 60)

    # Resolve paths relative to this file's location on disk
    # file is at: src/new_energy_sys/cli/train_with_enhanced_features.py
    # project root: parents[3] = src/../.. = project root
    root_dir = Path(__file__).resolve().parents[3]
    data_path = root_dir / DATA_PATH
    output_dir = root_dir / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Data:   {data_path}")
    print(f"Output: {output_dir}")

    # ---- 1. Load data -----------------------------------------------------
    print("\nLoading Stage3 dataset ...")
    if not data_path.exists():
        print(
            f"ERROR: Stage3 dataset not found at {data_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.read_parquet(data_path)
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], errors="coerce", utc=True
    )
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    df = df.reset_index(drop=True)
    print(f"  {len(df):,} rows, {len(df.columns)} columns")

    # Verify all target columns exist
    missing = [c for c in ALL_TARGET_COLS if c not in df.columns]
    if missing:
        print(
            f"ERROR: Missing target columns in dataset: {missing}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---- 2. Run ablation experiments --------------------------------------
    print(f"\nRunning {len(EXPERIMENTS)} experiments x {len(TARGETS)} targets "
          f"= {len(EXPERIMENTS) * len(TARGETS)} models ...\n")

    all_metrics: list[dict] = []

    for experiment in EXPERIMENTS:
        for target, horizon_hours in TARGETS.items():
            metrics = _train_and_evaluate(
                df, experiment, target, horizon_hours, output_dir,
            )
            all_metrics.append(metrics)

    # ---- 3. Save results --------------------------------------------------
    print(f"\nAll {len(all_metrics)} experiments complete. Saving results ...")

    result_df = pd.DataFrame(all_metrics)

    # Order columns for readability
    column_order = [
        "experiment", "target", "horizon_hours", "feature_count",
        "best_iteration", "n_test", "n_valid_valid_time",
        "all_rmse_kw", "all_nrmse", "all_mae_kw", "all_bias_kw",
        "day_rmse_kw", "day_nrmse", "day_mae_kw",
        "clear_n", "clear_rmse_kw", "clear_nrmse", "clear_mae_kw",
        "clear_bias_kw",
        "mixed_n", "mixed_rmse_kw", "mixed_nrmse", "mixed_mae_kw",
        "mixed_bias_kw",
        "overcast_n", "overcast_rmse_kw", "overcast_nrmse", "overcast_mae_kw",
        "overcast_bias_kw",
        "evening_n", "evening_bias_kw",
    ]
    existing_cols = [c for c in column_order if c in result_df.columns]
    result_df = result_df[existing_cols]
    result_df = result_df.sort_values(
        ["experiment", "horizon_hours"]
    ).reset_index(drop=True)

    csv_path = output_dir / "ablation_comparison.csv"
    result_df.to_csv(csv_path, index=False, float_format="%.6f")
    print(f"CSV:  {csv_path}")

    json_path = output_dir / "ablation_comparison.json"
    json_path.write_text(
        json.dumps(all_metrics, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"JSON: {json_path}")

    # ---- 4. Print report --------------------------------------------------
    _print_report_table(all_metrics)

    print("\nDone.")


if __name__ == "__main__":
    main()
