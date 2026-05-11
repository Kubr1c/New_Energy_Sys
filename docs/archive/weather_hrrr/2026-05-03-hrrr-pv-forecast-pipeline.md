# HRRR PV Forecast Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an HRRR forecast-driven PV power prediction pipeline: multi-time aggregation, feature engineering, LightGBM training, and 4-baseline ablation.

**Architecture:** Three new files — `hrrr_feature_aligner.py` (aggregation + features + audit), `train_hrrr_pv.py` (training CLI + ablation runner), `test_hrrr_feature_aligner.py` (P0 leak + P1 safety tests). Reuses existing `modeling.py` splitter and LightGBM patterns.

**Tech Stack:** Python 3.14, pandas, numpy, lightgbm, pvlib (solar position), pytest

---

## File Map

| File | Responsibility | New/Modify |
|------|---------------|------------|
| `src/new_energy_sys/hrrr_feature_aligner.py` | Multi-time aggregation, feature engineering (causal rolling, derived ratios), audit columns, outlier protection | **New** |
| `src/new_energy_sys/cli/train_hrrr_pv.py` | CLI: load data → align → split → train → evaluate → ablation | **New** |
| `tests/test_hrrr_feature_aligner.py` | P0 leak tests, P1 safety tests, audit column tests | **New** |
| `src/new_energy_sys/irradiance_decomposition.py` | Reuse DISC decomposition | Existing |

---

### Task 1: Write P0 data leak prevention tests

**Files:**
- Create: `tests/test_hrrr_feature_aligner.py`

These tests MUST be written first and MUST fail before any implementation.

- [ ] **Step 1: Create test file with import structure**

```python
"""P0 data leak prevention and P1 safety tests for HRRR feature aligner."""

import numpy as np
import pandas as pd
import pytest


# Import will fail until we create the module — expected TDD pattern.
# hrrr_feature_aligner = pytest.importorskip("new_energy_sys.hrrr_feature_aligner")
```

Run: `python -c "pass"` (syntax check only)
Expected: PASS

- [ ] **Step 2: Write test — no forecast with lead_time < 24h**

```python
def test_no_forecast_with_lead_time_less_than_24h():
    """P0: Every forecast used must have lead_time >= 24 hours."""
    pass  # will be filled after aligner exists
```

- [ ] **Step 3: Write test — no forecast issued after T-24h**

```python
def test_no_forecast_issued_after_target_minus_24h():
    """P0: Issue time must be <= target_time - 24h for all forecasts."""
    pass
```

- [ ] **Step 4: Write test — one row per target_time**

```python
def test_one_feature_row_per_target_time():
    """P0: Each target_time must appear exactly once in feature table."""
    pass
```

- [ ] **Step 5: Write test — audit columns present**

```python
def test_audit_columns_are_present():
    """P1: Feature table must include audit columns for leak detection."""
    required_cols = {
        "target_time", "issue_time_min", "issue_time_max",
        "lead_time_min", "lead_time_max", "n_forecasts_used",
        "weather_missing",
    }
    pass
```

- [ ] **Step 6: Write test — dhi_ghi_ratio is safe**

```python
def test_dhi_ghi_ratio_is_safe():
    """P1: No inf values; all finite values within [0, 1.5]."""
    pass
```

- [ ] **Step 7: Write test — clearsky_index range**

```python
def test_clearsky_index_range():
    """P1: No inf values; all finite values within [0, 2]."""
    pass
```

- [ ] **Step 8: Write test — kt range**

```python
def test_kt_range():
    """P1: No inf values; all finite values within [0, 1.2]."""
    pass
```

- [ ] **Step 9: Write test — rolling features are causal**

```python
def test_rolling_features_are_causal():
    """P1: Rolling stats must only use valid_time <= T."""
    pass
```

Run: `pytest tests/test_hrrr_feature_aligner.py -v --tb=line`
Expected: 9 collected, all SKIPPED or FAILED (module doesn't exist yet)

- [ ] **Step 10: Commit skeleton tests**

```bash
git add tests/test_hrrr_feature_aligner.py
git commit -m "test: add P0/P1 test skeletons for HRRR feature aligner"
```

---

### Task 2: Implement multi-time aggregation core

**Files:**
- Create: `src/new_energy_sys/hrrr_feature_aligner.py`
- Modify: `tests/test_hrrr_feature_aligner.py`

- [ ] **Step 1: Create aligner module with aggregation function**

```python
"""HRRR multi-time forecast aggregation for PV power prediction.

Converts HRRR forecast rows (issue_time, lead_time_hour, valid_time=timestamp)
into target-time-aligned feature rows suitable for PV prediction training.

Every target_time T gets one row, built from HRRR forecasts satisfying:
  valid_time == T
  24h <= lead_time_hour <= 48h

Aggregation method: inverse lead-time weighted mean.
Audit columns are preserved for leak detection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate_hrrr_to_target_times(
    hrrr_decomposed: pd.DataFrame,
    target_timestamps: pd.DatetimeIndex,
    agg_method: str = "inverse_lead_weighted",
) -> pd.DataFrame:
    """Aggregate HRRR forecast rows to per-target-time feature rows.

    Parameters
    ----------
    hrrr_decomposed : pd.DataFrame
        HRRR Stage7 data with DISC-decomposed DNI/DHI. Must contain columns:
        timestamp, weather_forecast_issue_time, weather_forecast_lead_time_hour,
        ghi_wm2, dni_wm2, dhi_wm2, temperature_c, cloud_cover_pct.
    target_timestamps : pd.DatetimeIndex
        PV target timestamps (UTC) to aggregate features for.
    agg_method : str
        Aggregation method: "inverse_lead_weighted", "simple_mean", or
        "nearest_lead".

    Returns
    -------
    pd.DataFrame
        One row per target_time with columns:
        target_time, ghi_wm2, dni_wm2, dhi_wm2, temperature_c, cloud_cover_pct,
        issue_time_min, issue_time_max, lead_time_min, lead_time_max,
        n_forecasts_used, weather_missing.
    """
    df = hrrr_decomposed.copy()
    df["valid_time"] = df["timestamp"]
    df["lead_time_hour"] = df["weather_forecast_lead_time_hour"].astype(float)
    df["issue_time"] = pd.to_datetime(df["weather_forecast_issue_time"], utc=True)

    results = []
    for T in target_timestamps:
        results.append(
            _aggregate_single_target(T, df, agg_method)
        )

    return pd.DataFrame(results)


def _aggregate_single_target(
    T: pd.Timestamp,
    hrrr: pd.DataFrame,
    agg_method: str,
) -> dict:
    """Aggregate HRRR forecasts for a single target timestamp T."""
    mask = (
        (hrrr["valid_time"] == T)
        & (hrrr["lead_time_hour"] >= 24)
        & (hrrr["lead_time_hour"] <= 48)
    )
    candidates = hrrr[mask]

    weather_cols = [
        "ghi_wm2", "dni_wm2", "dhi_wm2",
        "temperature_c", "cloud_cover_pct",
    ]

    if len(candidates) == 0:
        row = {"target_time": T, "n_forecasts_used": 0, "weather_missing": True}
        for col in weather_cols:
            row[col] = np.nan
        row["issue_time_min"] = pd.NaT
        row["issue_time_max"] = pd.NaT
        row["lead_time_min"] = np.nan
        row["lead_time_max"] = np.nan
        return row

    leads = candidates["lead_time_hour"].values.astype(float)

    if agg_method == "nearest_lead":
        idx = np.argmin(np.abs(leads - 24))
        selected = candidates.iloc[[idx]]
        weights = np.array([1.0])
    elif agg_method == "simple_mean":
        selected = candidates
        weights = np.ones(len(selected)) / len(selected)
    else:  # inverse_lead_weighted
        selected = candidates
        raw = 1.0 / leads
        weights = raw / raw.sum()

    row = {
        "target_time": T,
        "n_forecasts_used": len(selected),
        "weather_missing": False,
        "issue_time_min": selected["issue_time"].min(),
        "issue_time_max": selected["issue_time"].max(),
        "lead_time_min": leads.min(),
        "lead_time_max": leads.max(),
    }

    for col in weather_cols:
        vals = selected[col].values.astype(float)
        row[col] = np.average(vals, weights=weights)

    return row
```

- [ ] **Step 2: Verify import works**

Run: `python -c "import sys; sys.path.insert(0,'src'); from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Update tests to run against real data (integration-style validation)**

```python
import numpy as np
import pandas as pd
import pytest
import sys
sys.path.insert(0, "src")
from new_energy_sys.hrrr_feature_aligner import aggregate_hrrr_to_target_times


@pytest.fixture
def real_hrrr():
    return pd.read_parquet(
        "data/processed/pvdaq_nsrdb_2020_2022/"
        "stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
    )


@pytest.fixture
def real_targets():
    pv = pd.read_parquet(
        "data/processed/pvdaq_nsrdb_2020_2022/"
        "stage2_cleaned_hourly_dataset.parquet"
    )
    pv_2021 = pv[
        (pv["timestamp"] >= "2021-01-01")
        & (pv["timestamp"] <= "2022-12-31")
    ]
    return pd.DatetimeIndex(pv_2021["timestamp"])


def test_no_forecast_with_lead_time_less_than_24h(real_hrrr, real_targets):
    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)
    valid = features[~features["weather_missing"]]
    assert (valid["lead_time_min"] >= 24).all(), (
        f"Found rows with lead_time_min < 24: "
        f"{(valid['lead_time_min'] < 24).sum()}"
    )


def test_no_forecast_issued_after_target_minus_24h(real_hrrr, real_targets):
    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)
    valid = features[~features["weather_missing"]]
    issue_max = pd.to_datetime(valid["issue_time_max"], utc=True)
    target = pd.to_datetime(valid["target_time"], utc=True)
    assert (issue_max <= target - pd.Timedelta(hours=24)).all(), (
        f"Found forecasts issued within 24h of target"
    )


def test_one_feature_row_per_target_time(real_hrrr, real_targets):
    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)
    assert not features["target_time"].duplicated().any()


def test_audit_columns_are_present(real_hrrr, real_targets):
    features = aggregate_hrrr_to_target_times(real_hrrr, real_targets)
    required = {
        "target_time", "issue_time_min", "issue_time_max",
        "lead_time_min", "lead_time_max", "n_forecasts_used",
        "weather_missing",
    }
    assert required.issubset(features.columns), (
        f"Missing: {required - set(features.columns)}"
    )
```

- [ ] **Step 4: Run P0/P1 tests**

Run: `pytest tests/test_hrrr_feature_aligner.py::test_no_forecast_with_lead_time_less_than_24h tests/test_hrrr_feature_aligner.py::test_no_forecast_issued_after_target_minus_24h tests/test_hrrr_feature_aligner.py::test_one_feature_row_per_target_time tests/test_hrrr_feature_aligner.py::test_audit_columns_are_present -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit aggregation core**

```bash
git add src/new_energy_sys/hrrr_feature_aligner.py tests/test_hrrr_feature_aligner.py
git commit -m "feat: add HRRR multi-time aggregation with P0 leak prevention tests"
```

---

### Task 3: Build full feature table (aggregation + rolling + ratios + clearsky)

**Files:**
- Modify: `src/new_energy_sys/hrrr_feature_aligner.py`

- [ ] **Step 1: Add feature table builder that joins existing feature groups**

Add to `hrrr_feature_aligner.py`:

```python
def build_complete_step1_feature_table(
    hrrr_path: str,
    pv_path: str,
) -> pd.DataFrame:
    """Build complete Step 1 feature table (69 features) end-to-end.

    Parameters
    ----------
    hrrr_path : str
        Path to HRRR decomposed parquet.
    pv_path : str
        Path to PV clean dataset (stage2_cleaned_hourly_dataset.parquet).

    Returns
    -------
    pd.DataFrame
        Full feature table with target, ready for chronological split.
    """
    from new_energy_sys.features import (
        _add_time_features,
        _add_historical_power_features,
    )

    hrrr = pd.read_parquet(hrrr_path)
    pv = pd.read_parquet(pv_path)

    # Filter to HRRR overlap period (2021-2022)
    pv_overlap = pv[
        (pv["timestamp"] >= "2021-01-01")
        & (pv["timestamp"] <= "2022-12-31")
    ].copy()
    pv_overlap = pv_overlap.sort_values("timestamp").reset_index(drop=True)

    targets = pd.DatetimeIndex(pv_overlap["timestamp"])

    # Step 1: Aggregate HRRR
    agg = aggregate_hrrr_to_target_times(hrrr, targets)

    # Step 2: Build time features
    pv_time, time_cols = _add_time_features(pv_overlap)

    # Step 3: Build history features
    pv_hist, hist_cols = _add_historical_power_features(
        pv_overlap, capacity_kw=1.12
    )

    # Step 4: Merge everything
    df = agg.rename(columns={"target_time": "timestamp"})
    df = df.merge(
        pv_overlap[["timestamp", "pv_power_kw"]], on="timestamp", how="left"
    )
    df = df.merge(pv_time, on="timestamp", how="left")
    df = df.merge(pv_hist, on="timestamp", how="left")

    # Step 5: Causal rolling weather features
    weather_cols = ["ghi_wm2", "dni_wm2", "dhi_wm2",
                    "temperature_c", "cloud_cover_pct"]
    for col in weather_cols:
        df[f"{col}_roll_24h_mean"] = (
            df[col].rolling(window=24, min_periods=1).mean()
        )
        df[f"{col}_roll_24h_std"] = (
            df[col].rolling(window=24, min_periods=1).std().fillna(0)
        )

    # Step 6: Derived ratio features with outlier protection
    df["dhi_ghi_ratio"] = np.where(
        df["ghi_wm2"] > 20,
        (df["dhi_wm2"] / df["ghi_wm2"]).clip(0, 1.5),
        np.nan,
    )

    # Compute solar zenith for clearsky features
    import pvlib
    loc = pvlib.location.Location(latitude=39.74, longitude=-105.18, altitude=1730)
    ts_dti = pd.DatetimeIndex(df["timestamp"])
    solar_pos = loc.get_solarposition(times=ts_dti)
    zenith = solar_pos["zenith"].values.astype(float)

    _add_clearsky_features(df, zenith)

    return df


def _add_clearsky_features(df: pd.DataFrame, zenith: np.ndarray) -> None:
    """Add clearsky_index and kt to feature table (internal helper)."""
    import pvlib

    z_rad = np.radians(zenith)
    cos_z = np.cos(z_rad)
    target_dt = pd.DatetimeIndex(df["timestamp"])
    doy = target_dt.dayofyear.astype(float)
    eccentricity = 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)
    dni_extra = 1367.0 * eccentricity
    ghi_extra = dni_extra * cos_z
    ghi_extra = np.maximum(ghi_extra, 1e-6)

    clearsky_ghi = pvlib.irradiance.ineichen(
        apparent_zenith=zenith,
        airmass=pvlib.atmosphere.get_relative_airmass(zenith),
        linke_turbidity=3.0,
        altitude=1700.0,
        dni_extra=dni_extra,
    )

    df["clearsky_index"] = np.where(
        (clearsky_ghi > 20) & (cos_z > 0),
        (df["ghi_wm2"] / np.maximum(clearsky_ghi, 1.0)).clip(0, 2),
        np.nan,
    )
    df["kt"] = np.where(
        (ghi_extra > 20) & (cos_z > 0),
        (df["ghi_wm2"] / ghi_extra).clip(0, 1.2),
        np.nan,
    )
```

- [ ] **Step 2: Verify feature count**

Run:
```python
python -c "
import sys; sys.path.insert(0,'src')
from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table
df = build_complete_step1_feature_table(
    'data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet',
    'data/processed/pvdaq_nsrdb_2020_2022/stage2_cleaned_hourly_dataset.parquet',
)
print(f'Feature table shape: {df.shape}')
print(f'Columns: {list(df.columns)}')
# Check: weather_missing stats
print(f'weather_missing=True: {df[\"weather_missing\"].sum()}')
print(f'target column present: {\"target_pv_power_t_plus_24h\" in df.columns}')
"
```
Expected: Shape ~(17470, ~80), weather_missing < 5%

- [ ] **Step 3: Commit feature table builder**

```bash
git add src/new_energy_sys/hrrr_feature_aligner.py
git commit -m "feat: add end-to-end Step 1 feature table builder"
```

---

### Task 4: Training + evaluation script with ablation

**Files:**
- Create: `src/new_energy_sys/cli/train_hrrr_pv.py`

- [ ] **Step 1: Create training script with experiment runner**

```python
"""Train LightGBM PV power prediction with HRRR forecast features.

Runs a minimal ablation experiment:
  A. history_only (time + PV history only)
  B. HRRR-GHI-only (A + HRRR GHI)
  C1. HRRR-DISC (A + HRRR GHI + DISC DNI/DHI + T + cloud)
  D. NSRDB oracle (existing full_features model reference)

Outputs: metrics CSV, predictions parquet, feature importance, report.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table
from new_energy_sys.modeling import _chronological_split


DAYTIME_ZENITH_THRESHOLD = 85.0
TARGET_COL = "target_pv_power_t_plus_24h"


def _train_eval_lgb(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    model_path: str | None = None,
) -> dict:
    """Train LightGBM and return metrics dict."""
    train_ds = lgb.Dataset(X_train, y_train)
    val_ds = lgb.Dataset(X_val, y_val, reference=train_ds)
    params = {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "n_estimators": 1800,
        "learning_rate": 0.02,
        "max_depth": 10,
        "num_leaves": 31,
        "reg_lambda": 0.8,
        "verbose": -1,
        "seed": 42,
    }
    model = lgb.train(
        params, train_ds, valid_sets=[val_ds],
        callbacks=[lgb.early_stopping(80), lgb.log_evaluation(0)],
    )
    if model_path:
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

    preds = model.predict(X_test)
    actual = y_test.values
    error = preds - actual

    daytime = X_test.get("solar_zenith_deg", pd.Series(0, index=X_test.index)) < DAYTIME_ZENITH_THRESHOLD
    all_rmse = np.sqrt(np.mean(error ** 2))
    day_rmse = np.sqrt(np.mean(error[daytime] ** 2)) if daytime.any() else np.nan
    all_mae = np.mean(np.abs(error))
    day_mae = np.mean(np.abs(error[daytime])) if daytime.any() else np.nan
    mean_actual = y_test.mean()
    all_nrmse = all_rmse / mean_actual if mean_actual > 0 else np.nan
    day_nrmse = day_rmse / y_test[daytime].mean() if daytime.any() and y_test[daytime].mean() > 0 else np.nan

    return {
        "all_rmse": all_rmse, "all_nrmse": all_nrmse, "all_mae": all_mae,
        "daytime_rmse": day_rmse, "daytime_nrmse": day_nrmse, "daytime_mae": day_mae,
        "n_test": len(y_test), "n_daytime": daytime.sum(),
    }


def run_ablation_experiment(feature_df: pd.DataFrame, output_dir: str) -> pd.DataFrame:
    """Run 4-baseline ablation and return metrics table.

    A. history_only — time features + PV history (no weather)
    B. HRRR-GHI-only — A + HRRR GHI
    C1. HRRR-DISC — B + DNI/DHI/T/cloud (Step 1 full)
    D. NSRDB oracle — reference metrics from existing model
    """
    # TARGET: t+24h
    feature_df = feature_df.copy()
    if TARGET_COL not in feature_df.columns:
        raise ValueError(f"Target column '{TARGET_COL}' missing from feature table")

    # Feature groups
    time_cols = [c for c in feature_df.columns if c.startswith(("sin_", "cos_"))]
    hist_cols = [c for c in feature_df.columns if
                 c.startswith(("lag_", "rolling_", "ramp_", "forecast_error_"))]
    weather_cols = [
        "ghi_wm2", "dni_wm2", "dhi_wm2",
        "temperature_c", "cloud_cover_pct",
    ]
    weather_roll_cols = [c for c in feature_df.columns if
                         any(c.startswith(w + "_roll") for w in weather_cols)]
    derived_cols = ["dhi_ghi_ratio", "clearsky_index", "kt"]
    flag_cols = ["weather_missing"]

    non_feature = {
        "timestamp", TARGET_COL, "pv_power_kw",
        "solar_zenith_deg", "target_time",
        "issue_time_min", "issue_time_max", "lead_time_min", "lead_time_max",
        "n_forecasts_used",
    }

    # Experiment definitions
    experiments = {
        "A_history_only": {
            "label": "A. history_only",
            "features": [c for c in time_cols + hist_cols
                         if c in feature_df.columns],
        },
        "B_hrrr_ghi_only": {
            "label": "B. HRRR-GHI-only",
            "features": [c for c in time_cols + hist_cols + ["ghi_wm2"]
                         + [c for c in weather_roll_cols if "ghi" in c]
                         + flag_cols if c in feature_df.columns],
        },
        "C1_hrrr_disc": {
            "label": "C1. HRRR-DISC",
            "features": [c for c in time_cols + hist_cols
                         + weather_cols + weather_roll_cols + derived_cols + flag_cols
                         if c in feature_df.columns],
        },
    }

    # Chronological split
    ordered = feature_df.sort_values("timestamp").reset_index(drop=True)
    splits = _chronological_split(ordered)

    results = []
    for exp_id, exp in experiments.items():
        feats = exp["features"]
        print(f"\n--- {exp['label']} ({len(feats)} features) ---")
        X_tr = splits["train"][feats]
        y_tr = splits["train"][TARGET_COL]
        X_v = splits["validation"][feats]
        y_v = splits["validation"][TARGET_COL]
        X_te = splits["test"][feats]
        y_te = splits["test"][TARGET_COL]

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        m = _train_eval_lgb(
            X_tr, y_tr, X_v, y_v, X_te, y_te,
            model_path=f"{output_dir}/lightgbm_{exp_id}.pkl",
        )
        m["experiment"] = exp_id
        m["label"] = exp["label"]
        m["n_features"] = len(feats)
        results.append(m)
        print(f"  Day nRMSE={m['daytime_nrmse']:.4f}  All nRMSE={m['all_nrmse']:.4f}")

    # Add NSRDB oracle reference
    results.append({
        "experiment": "D_nsrdb_oracle",
        "label": "D. NSRDB oracle",
        "all_nrmse": 0.0784, "daytime_nrmse": 0.0903,
        "all_rmse": np.nan, "daytime_rmse": np.nan,
        "n_features": 163, "n_test": 0,
    })

    return pd.DataFrame(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train HRRR PV prediction.")
    parser.add_argument("--hrrr", required=True, help="HRRR decomposed parquet path")
    parser.add_argument("--pv", required=True, help="PV dataset parquet path")
    parser.add_argument("--output-dir", default="data/processed/hrrr_pv_models")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df = build_complete_step1_feature_table(args.hrrr, args.pv)
    metrics = run_ablation_experiment(df, args.output_dir)
    print("\n" + str(metrics.to_string(index=False)))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(out / "ablation_metrics.csv", index=False)
    print(f"\nMetrics saved to {out / 'ablation_metrics.csv'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify training runs (quick smoke test with small params)**

Run:
```bash
python -c "
import sys; sys.path.insert(0,'src')
from new_energy_sys.cli.train_hrrr_pv import build_complete_step1_feature_table, run_ablation_experiment
df = build_complete_step1_feature_table(
    'data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet',
    'data/processed/pvdaq_nsrdb_2020_2022/stage2_cleaned_hourly_dataset.parquet',
)
# Smoke: reduce n_estimators → 50 for quick test
# (modify _train_eval_lgb to accept n_estimators param)
metrics = run_ablation_experiment(df, 'data/processed/hrrr_pv_test')
print(metrics)
"
```
Expected: 4 rows in metrics table, no crashes

- [ ] **Step 3: Commit training script**

```bash
git add src/new_energy_sys/cli/train_hrrr_pv.py
git commit -m "feat: add HRRR PV training CLI with ablation runner"
```

---

### Task 5: Full ablation run and comparison report

**Files:**
- Create: `scripts/compare_hrrr_pv_baselines.py`

- [ ] **Step 1: Create comparison report script**

```python
"""Generate HRRR PV prediction baseline comparison report.

Computes gap_closure, weather-scenario breakdown, and aggregation ablation.
"""

import sys
sys.path.insert(0, "src")

import numpy as np
import pandas as pd

from new_energy_sys.hrrr_feature_aligner import (
    build_complete_step1_feature_table,
    aggregate_hrrr_to_target_times,
)
from new_energy_sys.cli.train_hrrr_pv import run_ablation_experiment


def main() -> None:
    HRRR_PATH = (
        "data/processed/pvdaq_nsrdb_2020_2022/"
        "stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
    )
    PV_PATH = (
        "data/processed/pvdaq_nsrdb_2020_2022/"
        "stage2_cleaned_hourly_dataset.parquet"
    )
    OUTPUT = "data/processed/hrrr_pv_models"

    print("Building feature table ...")
    df = build_complete_step1_feature_table(HRRR_PATH, PV_PATH)
    print(f"  Shape: {df.shape}")

    print("\nRunning 4-baseline ablation ...")
    metrics = run_ablation_experiment(df, OUTPUT)

    # Compute gap_closure
    hist_nrmse = metrics[metrics["experiment"] == "A_history_only"]["daytime_nrmse"].values[0]
    disc_nrmse = metrics[metrics["experiment"] == "C1_hrrr_disc"]["daytime_nrmse"].values[0]
    oracle_nrmse = metrics[metrics["experiment"] == "D_nsrdb_oracle"]["daytime_nrmse"].values[0]
    gap_closure = (hist_nrmse - disc_nrmse) / (hist_nrmse - oracle_nrmse) * 100

    print("\n" + "=" * 60)
    print("ABLATION RESULTS (daytime nRMSE)")
    print("=" * 60)
    for _, row in metrics.iterrows():
        print(f"  {row['label']:<30} {row['daytime_nrmse']:.4f}")
    print(f"\n  Gap closure: {gap_closure:.1f}%")
    print(f"  (HRRR-DISC closes {gap_closure:.1f}% of the gap between")
    print(f"   history_only [{hist_nrmse:.4f}] and NSRDB oracle [{oracle_nrmse:.4f}])")

    # Weather scenario breakdown
    print("\n" + "=" * 60)
    print("BY WEATHER SCENARIO (daytime nRMSE)")
    print("=" * 60)

    # Load test predictions for C1 model
    import pickle
    test_df = df.sort_values("timestamp").reset_index(drop=True)
    n_test = int(len(test_df) * 0.15)
    test = test_df.iloc[-n_test:].copy()
    with open(f"{OUTPUT}/lightgbm_C1_hrrr_disc.pkl", "rb") as f:
        model = pickle.load(f)
    feats = [c for c in metrics[metrics["experiment"] == "C1_hrrr_disc"]["features"].iloc[0]
             if c in test.columns]
    test["pred"] = model.predict(test[feats])
    test["error"] = test["pred"] - test[TARGET_COL]
    test["cloud_bin"] = pd.cut(
        test["cloud_cover_pct"], bins=[0, 20, 80, 100],
        labels=["clear (<20%)", "partly_cloudy (20-80%)", "overcast (>80%)"],
    )
    test["is_daytime"] = test["solar_zenith_deg"] < 85.0
    for (bin_name, grp) in test[test["is_daytime"]].groupby("cloud_bin", observed=False):
        if len(grp) < 10:
            continue
        rmse = np.sqrt(np.mean(grp["error"] ** 2))
        actual_mean = grp[TARGET_COL].mean()
        nrmse = rmse / actual_mean if actual_mean > 0 else np.nan
        mae = np.mean(np.abs(grp["error"]))
        bias = np.mean(grp["error"])
        print(f"  {bin_name:<30} n={len(grp):>5}  nRMSE={nrmse:.4f}  "
              f"MAE={mae:.4f}  Bias={bias:+.4f}")

    metrics.to_csv(f"{OUTPUT}/ablation_metrics.csv", index=False)
    print(f"\nReport saved to {OUTPUT}/ablation_metrics.csv")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full ablation**

Run: `python scripts/compare_hrrr_pv_baselines.py`
Expected: ~5 minute run, metric table with gap_closure

- [ ] **Step 3: Commit report script and results**

```bash
git add scripts/compare_hrrr_pv_baselines.py
git commit -m "feat: add HRRR PV baseline comparison report script"
```

---

### Task 6: Final test suite verification and cleanup

- [ ] **Step 1: Run all tests**

```bash
python -m pytest tests/test_hrrr_feature_aligner.py tests/test_irradiance_decomposition.py -v
```
Expected: 9 (new aligner tests) + 28 (irradiance) = 37 PASSED

- [ ] **Step 2: Run existing HRRR tests to verify no regressions**

```bash
python -m pytest tests/test_hrrr_stage7_contract.py tests/test_hrrr_probe_contract.py tests/test_hrrr_point_forecast.py -v
```
Expected: 30 PASSED

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete HRRR PV forecast pipeline with ablation results"
```
