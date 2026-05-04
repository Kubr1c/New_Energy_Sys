"""
Weather-regime gating experiment for PV prediction (t+24h).

Resolves the CSI dilemma
=========================
C0 (direct PV power with E1 features) is best for Clear sky (nRMSE=0.280)
but worse for Mixed/Overcast (0.752/1.287).  C2 (CSI target + HRRR) is
better for Mixed (0.692) and Overcast (1.191) but worse for Clear (0.386).

Hypothesis
----------
A gating model that routes Clear samples to C0 and Mixed/Overcast to C2
will outperform either strategy alone across all weather regimes.

Strategies
----------
A – Hard oracle gate (valid-time NSRDB CSI): upper-bound reference
B – Origin-time weather classifier (deployable, no future information)
C – Soft blending by clear-sky probability

Usage
-----
    python -m new_energy_sys.cli.train_gating_experiment

Output
------
    Console comparison table + threshold-sweep results.

References
----------
- CSI experiments: train_csi_target.py  (C0 … C4)
- Enhanced features: train_with_enhanced_features.py  (E0 … E2)
- Best C0 t+24h day_nRMSE = 0.335, clear=0.280, mixed=0.752, overcast=1.287
- Best C2 t+24h day_nRMSE = 0.432, clear=0.386, mixed=0.692, overcast=1.191
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
from pvlib.location import Location
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet"
)
INSPECTION_PATH = Path(
    "data/processed/pvdaq_nsrdb_2020_2022/inspection_predictions.parquet"
)

# PVDAQ site: Golden, CO
SITE: dict[str, float] = {
    "latitude": 39.74,
    "longitude": -105.18,
    "altitude": 1730.0,
    "capacity_kw": 1.12,
}
CAPACITY_KW: float = SITE["capacity_kw"]
EFFICIENCY: float = 0.75

SOLAR_ELEV_THRESHOLD: float = 5.0   # degrees above horizon for daytime
CSI_CLEAR_THRESHOLD: float = 0.7    # NSRDB CSI >= this => "clear"

PREDICTION_UPPER: float = CAPACITY_KW * 1.05

# ---------------------------------------------------------------------------
# Clear-sky power (LEAKAGE-FREE, same as train_csi_target.py)
# ---------------------------------------------------------------------------


def compute_clear_sky_power(timestamps: pd.DatetimeIndex) -> pd.Series:
    """Compute clear-sky PV power from solar geometry only (no data leakage).

    P_clear = capacity_kw * GHI_clear / 1000 * efficiency
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


def _precompute_clear_sky_lookup(df: pd.DataFrame) -> pd.Series:
    """Build a timestamp-indexed Series of clear-sky power for t+24h lookups."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    full_range = pd.date_range(
        start=ts.min() - pd.Timedelta(hours=1),
        end=ts.max() + pd.Timedelta(hours=25),
        freq="h",
        tz="UTC",
    )
    return compute_clear_sky_power(full_range)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_and_align_predictions(
    inspection_path: Path,
) -> pd.DataFrame:
    """Load C0 and C2 t+24h test predictions and merge on valid_time.

    Returns a DataFrame with one row per overlapping valid_time and columns:
        valid_time, origin_time,
        actual_kw, csi_valid, solar_elevation_deg,
        pred_c0_kw, pred_c2_kw,
        scenario  (based on C0's scenario classification)
        plus origin-time features from the C0 prediction row.
    """
    df = pd.read_parquet(inspection_path)
    mask = (df["horizon_hours"] == 24) & (df["split"] == "test")

    c0 = df[mask & (df["experiment"] == "c0")].copy()
    c2 = df[mask & (df["experiment"] == "c2")].copy()

    if c0.empty or c2.empty:
        print("ERROR: C0 or C2 predictions not found.", file=sys.stderr)
        sys.exit(1)

    print(f"  C0 t+24h test: {len(c0)} rows")
    print(f"  C2 t+24h test: {len(c2)} rows")

    # Keep origin-time features from C0 for the deployable classifier
    common_cols = [
        "valid_time", "origin_time", "prediction_kw", "actual_kw",
        "csi_valid", "solar_elevation_deg", "scenario",
        "ghi_wm2", "clearsky_ghi_wm2", "cloud_cover_pct",
    ]
    # Only keep columns that actually exist
    c0_cols = [c for c in common_cols if c in c0.columns]
    c0_sub = c0[c0_cols].rename(columns={"prediction_kw": "pred_c0_kw"})

    c2_sub = c2[["valid_time", "prediction_kw"]].rename(
        columns={"prediction_kw": "pred_c2_kw"}
    )

    aligned = c0_sub.merge(c2_sub, on="valid_time", how="inner")
    print(f"  Aligned (overlap): {len(aligned)} rows")
    return aligned


def load_stage3_for_classifier(
    df: pd.DataFrame,
    csp_lookup: pd.Series,
) -> pd.DataFrame:
    """Add valid-time CSI label to Stage3 data for classifier training.

    Parameters
    ----------
    df : pd.DataFrame
        Stage3 dataset with columns: timestamp, ghi_wm2, clearsky_ghi_wm2,
        target_pv_power_t_plus_24h, and other numeric features.
    csp_lookup : pd.Series
        Timestamp-indexed clear-sky power lookup.

    Returns
    -------
    pd.DataFrame
        Input ``df`` with additional columns:
        - csi_valid_24h: valid-time clearsky index for t+24h
        - solar_elev_valid_24h: solar elevation at valid time
        - origin_csi: origin-time clearsky index
        - pv_power_ratio: current PV power / capacity
    """
    df = df.copy()

    # Compute valid-time CSI for t+24h:
    #   csi_valid_24h = actual_PV_at_valid / clear_sky_power_at_valid
    valid_time = pd.DatetimeIndex(
        df["timestamp"] + pd.Timedelta(hours=24)
    )
    csp_valid = csp_lookup.reindex(valid_time).values
    pv_actual = df["target_pv_power_t_plus_24h"].values
    # Avoid division by zero: only compute where csp > 0.5% of capacity
    min_csp = 0.005 * CAPACITY_KW
    csi_raw = np.where(
        csp_valid > min_csp,
        pv_actual / np.maximum(csp_valid, 1e-6),
        np.nan,
    )
    df["csi_valid_24h"] = np.clip(csi_raw, 0.0, 2.0)

    # Solar elevation at valid time (for daytime filtering later)
    loc = Location(
        latitude=SITE["latitude"],
        longitude=SITE["longitude"],
        altitude=SITE["altitude"],
    )
    solar_pos = loc.get_solarposition(times=valid_time)
    df["solar_elev_valid_24h"] = solar_pos["elevation"].values

    # Compute origin-time clearsky index (feature for classifier)
    df["origin_csi"] = np.where(
        df["clearsky_ghi_wm2"] > 1.0,
        df["ghi_wm2"] / df["clearsky_ghi_wm2"],
        1.0,
    )
    df["origin_csi"] = df["origin_csi"].clip(0.0, 2.0)

    # Normalize current PV power by capacity (feature for classifier)
    df["pv_power_ratio"] = df["pv_power_kw"] / CAPACITY_KW

    return df


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_gating(
    actual_kw: np.ndarray,
    predictions: np.ndarray,
    solar_elevation: np.ndarray,
    csi_valid: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> dict:
    """Compute stratified metrics for a gating strategy.

    Returns dict with keys:
        day_nrmse, clear_nrmse, mixed_nrmse, overcast_nrmse,
        regime_weighted_nrmse, day_rmse_kw, day_bias_kw
    """
    if valid_mask is None:
        valid_mask = solar_elevation > SOLAR_ELEV_THRESHOLD

    error = predictions - actual_kw
    day_mask = valid_mask & np.isfinite(actual_kw) & np.isfinite(predictions)
    day_err = error[day_mask]
    day_actual = actual_kw[day_mask]

    if len(day_err) == 0:
        return {
            "day_n": 0,
            "day_rmse_kw": float("nan"),
            "day_nrmse": float("nan"),
            "day_bias_kw": float("nan"),
        }

    day_rmse = float(np.sqrt(np.nanmean(day_err**2)))
    day_mean = float(np.nanmean(day_actual))
    day_nrmse = day_rmse / day_mean if day_mean > 0 else float("nan")
    day_bias = float(np.nanmean(day_err))

    metrics = {
        "day_n": int(day_mask.sum()),
        "day_rmse_kw": day_rmse,
        "day_nrmse": day_nrmse,
        "day_bias_kw": day_bias,
    }

    # Per-regime metrics
    regime_nrmse_list = []
    for scenario, (lo, hi) in [
        ("clear", (CSI_CLEAR_THRESHOLD, float("inf"))),
        ("mixed", (0.3, CSI_CLEAR_THRESHOLD)),
        ("overcast", (0.0, 0.3)),
    ]:
        sc_mask = day_mask & (csi_valid >= lo) & (csi_valid < hi)
        n = int(sc_mask.sum())
        if n == 0:
            metrics.update({
                f"{scenario}_n": 0,
                f"{scenario}_rmse_kw": float("nan"),
                f"{scenario}_nrmse": float("nan"),
            })
            continue
        sc_err = error[sc_mask]
        sc_actual = actual_kw[sc_mask]
        sc_rmse = float(np.sqrt(np.nanmean(sc_err**2)))
        sc_mean = float(np.nanmean(sc_actual))
        sc_nrmse = sc_rmse / sc_mean if sc_mean > 0 else float("nan")
        metrics.update({
            f"{scenario}_n": n,
            f"{scenario}_rmse_kw": sc_rmse,
            f"{scenario}_nrmse": sc_nrmse,
        })
        regime_nrmse_list.append(sc_nrmse)

    # Regime-weighted nRMSE (equal weight per regime)
    valid_nrmse = [v for v in regime_nrmse_list if np.isfinite(v)]
    metrics["regime_weighted_nrmse"] = (
        float(np.mean(valid_nrmse)) if valid_nrmse else float("nan")
    )
    return metrics


# ---------------------------------------------------------------------------
# Gating strategies
# ---------------------------------------------------------------------------


def strategy_a_hard_oracle(
    aligned: pd.DataFrame,
    threshold: float = CSI_CLEAR_THRESHOLD,
) -> np.ndarray:
    """Strategy A: Hard oracle gate using valid-time NSRDB CSI.

    This is an ORACLE — uses future information.  It shows the upper
    bound of what any gating scheme can achieve.
    """
    gate_clear = aligned["csi_valid"].values >= threshold
    return np.where(
        gate_clear,
        aligned["pred_c0_kw"].values,
        aligned["pred_c2_kw"].values,
    )


def _train_regime_classifier(
    stage3: pd.DataFrame,
    csp_lookup: pd.Series,
    seed: int = 42,
) -> tuple[RandomForestClassifier, list[str], float]:
    """Train a binary classifier to predict clear vs not-clear regime at t+24h.

    Uses ONLY origin-time features (available at forecast issue time).
    Trains on the chronological training portion of Stage3 data.
    Label: valid-time NSRDB CSI >= 0.7  (same definition as experiment)

    Returns
    -------
    tuple[RandomForestClassifier, list[str], float]
        (trained_classifier, feature_names, train_accuracy)
    """
    # Chronological split: 70/15/15
    ordered = stage3.sort_values("timestamp").reset_index(drop=True)
    train_end = int(len(ordered) * 0.70)
    valid_end = int(len(ordered) * 0.85)

    train_val = pd.concat(
        [ordered.iloc[:train_end], ordered.iloc[train_end:valid_end]],
        ignore_index=True,
    )

    # Daytime filter for training: solar elevation at valid time > 5 deg
    day_mask = train_val["solar_elev_valid_24h"] > SOLAR_ELEV_THRESHOLD

    # Features: origin-time variables only (AVAILABLE AT FORECAST TIME)
    feature_names = [
        "origin_csi",           # origin-time NSRDB clearsky index (key!)
        "hour", "month",        # time features
        "hour_sin", "hour_cos",
        "month_sin", "month_cos",
        "pv_power_ratio",       # current generation (reflects recent weather)
        "pv_power_roll_3h_mean",  # recent average
        "temperature_c",
        "relative_humidity_pct",
        "wind_speed_ms",
        "pressure_hpa",
        "clearsky_index_ghi",   # another form of origin clearsky info
    ]
    # Only keep features that exist in the DataFrame
    feature_names = [f for f in feature_names if f in train_val.columns]

    # Prepare training data
    train_df = train_val[day_mask].dropna(subset=["csi_valid_24h"] + feature_names)
    X_train = train_df[feature_names].values
    # Label: clear = valid-time CSI >= 0.7
    y_train = (train_df["csi_valid_24h"].values >= CSI_CLEAR_THRESHOLD).astype(int)

    n_clear = int(y_train.sum())
    n_not_clear = int((1 - y_train).sum())
    print(f"  Classifier training: {len(train_df)} daytime samples"
          f" ({n_clear} clear, {n_not_clear} not-clear)")

    # Handle extreme class imbalance by class_weight
    clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=20,
        class_weight="balanced",
        random_state=seed,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)

    # Training accuracy (on training set, for reference only)
    train_acc = accuracy_score(y_train, clf.predict(X_train))
    print(f"  Classifier train accuracy: {train_acc:.4f}")

    # Feature importance
    imp_sorted = sorted(
        zip(feature_names, clf.feature_importances_),
        key=lambda x: -x[1],
    )
    print("  Top classifier features:")
    for name, imp in imp_sorted[:6]:
        print(f"    {name}: {imp:.4f}")

    return clf, feature_names, train_acc


def _extract_test_features(
    aligned: pd.DataFrame,
    stage3: pd.DataFrame,
    feature_names: list[str],
) -> np.ndarray:
    """Extract origin-time classifier features for the test set.

    For each test row (valid_time), look up the Stage3 row at
    origin_time = valid_time - 24h to get origin-time features.
    """
    aligned = aligned.copy()
    # origin_time from the prediction data, or compute from valid_time
    if "origin_time" in aligned.columns:
        origin = pd.to_datetime(aligned["origin_time"], utc=True)
    else:
        origin = pd.to_datetime(
            aligned["valid_time"], utc=True
        ) - pd.Timedelta(hours=24)
    aligned["_origin"] = origin

    stage3_origin = stage3[["timestamp"] + feature_names].copy()
    stage3_origin = stage3_origin.rename(columns={"timestamp": "_origin"})
    aligned = aligned.merge(stage3_origin, on="_origin", how="left")

    missing = aligned[feature_names].isna().any(axis=1).sum()
    if missing > 0:
        print(f"  WARNING: {missing} test rows have missing origin-time features")

    return aligned[feature_names].fillna(0.0).values


def strategy_b_origin_time_gate(
    aligned: pd.DataFrame,
    clf: RandomForestClassifier,
    feature_names: list[str],
    stage3: pd.DataFrame,
) -> np.ndarray:
    """Strategy B: Deployable gate using origin-time classifier prediction."""
    X_test = _extract_test_features(aligned, stage3, feature_names)
    gate_clear = clf.predict(X_test).astype(bool)
    return np.where(
        gate_clear,
        aligned["pred_c0_kw"].values,
        aligned["pred_c2_kw"].values,
    )


def strategy_c_soft_blend(
    aligned: pd.DataFrame,
    clf: RandomForestClassifier,
    feature_names: list[str],
    stage3: pd.DataFrame,
) -> np.ndarray:
    """Strategy C: Soft blend weighted by clear-sky probability."""
    X_test = _extract_test_features(aligned, stage3, feature_names)
    clear_prob = clf.predict_proba(X_test)[:, 1]  # P(clear)
    pred_c0 = aligned["pred_c0_kw"].values
    pred_c2 = aligned["pred_c2_kw"].values
    return clear_prob * pred_c0 + (1.0 - clear_prob) * pred_c2


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(val: float) -> str:
    """Format float for table, handling NaN."""
    if np.isnan(val) or np.isinf(val):
        return "  N/A    "
    return f"{val:<8.3f}"


def print_comparison_table(
    results: dict[str, dict],
    sweep_results: list[dict] | None = None,
) -> None:
    """Print the main comparison table."""
    print("\n" + "=" * 90)
    print("WEATHER-REGIME GATING EXPERIMENT — t+24h Comparison")
    print("=" * 90)
    header = (
        f"{'Strategy':<25} {'Day_nRMSE':<10} {'Clear_nRMSE':<11}"
        f" {'Mixed_nRMSE':<12} {'Overcast_nRMSE':<14} {'RegW_Avg':<10}"
    )
    print(header)
    print("-" * 90)

    for label in ["C0 (E1 baseline)", "C2 (CSI best)",
                   "Gating Hard (oracle)", "Gating Origin-time",
                   "Gating Soft Blend"]:
        if label not in results:
            continue
        r = results[label]
        # Determine which nrmse values to show based on strategy type
        clr = r.get("clear_nrmse", float("nan"))
        mxd = r.get("mixed_nrmse", float("nan"))
        ovc = r.get("overcast_nrmse", float("nan"))
        rwg = r.get("regime_weighted_nrmse", float("nan"))
        day = r.get("day_nrmse", float("nan"))
        print(
            f"{label:<25} {_fmt(day)} {_fmt(clr)}"
            f" {_fmt(mxd)} {_fmt(ovc)} {_fmt(rwg)}"
        )

    print("-" * 90)
    # Identify best per regime
    best_clear = min(
        (r for r in results.values() if r.get("clear_nrmse", float("nan")) is not None),
        key=lambda r: r.get("clear_nrmse", float("inf")) if np.isfinite(r.get("clear_nrmse", float("inf"))) else float("inf"),
    )
    best_mixed = min(
        (r for r in results.values() if r.get("mixed_nrmse", float("nan")) is not None),
        key=lambda r: r.get("mixed_nrmse", float("inf")) if np.isfinite(r.get("mixed_nrmse", float("inf"))) else float("inf"),
    )
    best_overcast = min(
        (r for r in results.values() if r.get("overcast_nrmse", float("nan")) is not None),
        key=lambda r: r.get("overcast_nrmse", float("inf")) if np.isfinite(r.get("overcast_nrmse", float("inf"))) else float("inf"),
    )
    best_regw = min(
        (r for r in results.values() if r.get("regime_weighted_nrmse", float("nan")) is not None),
        key=lambda r: r.get("regime_weighted_nrmse", float("inf")) if np.isfinite(r.get("regime_weighted_nrmse", float("inf"))) else float("inf"),
    )
    print(f"  Best Clear:   {_fmt(best_clear['clear_nrmse'])}"
          f" ({[k for k,v in results.items() if v is best_clear][0]})")
    print(f"  Best Mixed:   {_fmt(best_mixed['mixed_nrmse'])}"
          f" ({[k for k,v in results.items() if v is best_mixed][0]})")
    print(f"  Best Overcast:{_fmt(best_overcast['overcast_nrmse'])}"
          f" ({[k for k,v in results.items() if v is best_overcast][0]})")
    print(f"  Best RegW:    {_fmt(best_regw['regime_weighted_nrmse'])}"
          f" ({[k for k,v in results.items() if v is best_regw][0]})")
    print("=" * 90)

    # ---- Threshold sweep table ----
    if sweep_results:
        print("\n" + "=" * 90)
        print("STRATEGY A — Threshold Sweep (oracle gate)")
        print("=" * 90)
        print(
            f"{'Threshold':<12} {'Day_nRMSE':<10} {'Clear_nRMSE':<11}"
            f" {'Mixed_nRMSE':<12} {'Overcast_nRMSE':<14} {'RegW_Avg':<10}"
            f" {'Clear_n':<8} {'N_ClearDay':<10}"
        )
        print("-" * 90)
        for sr in sweep_results:
            t = sr["threshold"]
            clr_ok = " OK" if sr.get("clear_nrmse", float("inf")) < 0.300 else ""
            print(
                f"{t:<8.2f}{clr_ok:<4} {_fmt(sr.get('day_nrmse', float('nan')))}"
                f" {_fmt(sr.get('clear_nrmse', float('nan')))}"
                f" {_fmt(sr.get('mixed_nrmse', float('nan')))}"
                f" {_fmt(sr.get('overcast_nrmse', float('nan')))}"
                f" {_fmt(sr.get('regime_weighted_nrmse', float('nan')))}"
                f" {sr.get('clear_n', 0):<8} {sr.get('n_clear_days', 0):<10}"
            )
        print("=" * 90)

    # ---- Classifier confusion matrix ----
    if "clf" in results:
        cm = results["clf"].get("cm")
        if cm is not None:
            print("\nClassifier Confusion Matrix (test set):")
            tn, fp, fn, tp = cm.ravel()
            print(f"              Predicted")
            print(f"              NotClear  Clear")
            print(f"Actual NotClear  {tn:4d}     {fp:4d}")
            print(f"       Clear     {fn:4d}     {tp:4d}")
            acc = results["clf"].get("accuracy", float("nan"))
            print(f"Accuracy: {acc:.4f}")

    # ---- Success criteria check ----
    print("\n" + "=" * 90)
    print("SUCCESS CRITERIA CHECK")
    print("=" * 90)
    c0 = results.get("C0 (E1 baseline)", {})
    best_gating = results.get("Gating Hard (oracle)", {})
    if best_gating:
        c0_regw = c0.get("regime_weighted_nrmse", float("nan"))
        g_regw = best_gating.get("regime_weighted_nrmse", float("nan"))
        g_clear = best_gating.get("clear_nrmse", float("nan"))
        c0_clear = c0.get("clear_nrmse", float("nan"))
        g_mixed = best_gating.get("mixed_nrmse", float("nan"))
        c0_mixed = c0.get("mixed_nrmse", float("nan"))

        checks = [
            ("RegW nRMSE < C0 baseline",
             g_regw < c0_regw if np.isfinite(g_regw) and np.isfinite(c0_regw) else False),
            ("Clear nRMSE < 0.300 (within 7% of C0)",
             g_clear < 0.300 if np.isfinite(g_clear) else False),
            ("Mixed nRMSE improved over C0",
             g_mixed < c0_mixed if np.isfinite(g_mixed) and np.isfinite(c0_mixed) else False),
        ]
        for desc, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {desc}")
    print("=" * 90)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the full gating experiment."""
    print("=" * 70)
    print("Weather-Regime Gating Experiment for PV Prediction")
    print("=" * 70)

    root_dir = Path(__file__).resolve().parents[3]
    data_path = root_dir / DATA_PATH
    inspection_path = root_dir / INSPECTION_PATH
    print(f"Data:       {data_path}")
    print(f"Inspection: {inspection_path}")
    print(f"Capacity:   {CAPACITY_KW} kW, Efficiency: {EFFICIENCY}")

    # ---- 1. Load predictions and align --------------------------------------
    print("\n[1/5] Loading and aligning C0 / C2 predictions ...")
    aligned = load_and_align_predictions(inspection_path)

    # ---- 2. Prepare Stage3 for classifier training --------------------------
    print("\n[2/5] Loading Stage3 data for classifier training ...")
    if not data_path.exists():
        print(f"ERROR: Stage3 data not found at {data_path}", file=sys.stderr)
        sys.exit(1)

    stage3 = pd.read_parquet(data_path)
    stage3["timestamp"] = pd.to_datetime(
        stage3["timestamp"], errors="coerce", utc=True
    )
    stage3 = stage3.dropna(subset=["timestamp"]).sort_values("timestamp")
    stage3 = stage3.reset_index(drop=True)
    print(f"  Stage3: {len(stage3)} rows")

    print("  Precomputing clear-sky power lookup ...")
    csp_lookup = _precompute_clear_sky_lookup(stage3)
    stage3_with_csi = load_stage3_for_classifier(stage3, csp_lookup)

    # ---- 3. Baseline metrics (C0 and C2) ------------------------------------
    print("\n[3/5] Computing baseline metrics ...")
    day_mask = aligned["solar_elevation_deg"] > SOLAR_ELEV_THRESHOLD
    csi_valid = aligned["csi_valid"].values
    actual_kw = aligned["actual_kw"].values
    solar_elev = aligned["solar_elevation_deg"].values

    results: dict[str, dict] = {}

    # C0 baseline
    m_c0 = evaluate_gating(
        actual_kw, aligned["pred_c0_kw"].values,
        solar_elev, csi_valid, day_mask,
    )
    results["C0 (E1 baseline)"] = m_c0

    # C2 baseline
    m_c2 = evaluate_gating(
        actual_kw, aligned["pred_c2_kw"].values,
        solar_elev, csi_valid, day_mask,
    )
    results["C2 (CSI best)"] = m_c2

    # ---- 4. Gating strategies -----------------------------------------------
    print("\n[4/5] Running gating strategies ...")

    # --- 4a. Strategy A: Hard oracle gate ---
    print("\n  Strategy A: Hard oracle gate (valid-time NSRDB CSI) ...")
    pred_a = strategy_a_hard_oracle(aligned, CSI_CLEAR_THRESHOLD)
    m_a = evaluate_gating(actual_kw, pred_a, solar_elev, csi_valid, day_mask)
    results["Gating Hard (oracle)"] = m_a

    # --- 4b. Strategy B: Origin-time weather classifier ---
    print("\n  Strategy B: Origin-time weather classifier ...")
    clf, feature_names, train_acc = _train_regime_classifier(
        stage3_with_csi, csp_lookup,
    )
    pred_b = strategy_b_origin_time_gate(
        aligned, clf, feature_names, stage3_with_csi,
    )
    m_b = evaluate_gating(actual_kw, pred_b, solar_elev, csi_valid, day_mask)
    results["Gating Origin-time"] = m_b

    # --- 4c. Strategy C: Soft blending ---
    print("\n  Strategy C: Soft blending by clear-sky probability ...")
    pred_c = strategy_c_soft_blend(
        aligned, clf, feature_names, stage3_with_csi,
    )
    m_c = evaluate_gating(actual_kw, pred_c, solar_elev, csi_valid, day_mask)
    results["Gating Soft Blend"] = m_c

    # Classifier metrics for report (confusion matrix on test set)
    X_test = _extract_test_features(aligned, stage3_with_csi, feature_names)
    y_pred = clf.predict(X_test)

    # True regime from valid-time CSI (ground truth)
    y_true = (aligned["csi_valid"].values >= CSI_CLEAR_THRESHOLD).astype(int)
    # Only evaluate on daytime samples
    day_idx = day_mask.values if hasattr(day_mask, "values") else day_mask
    y_true_day = y_true[day_idx]
    y_pred_day = y_pred[day_idx]

    if len(y_true_day) > 0 and len(np.unique(y_true_day)) > 1:
        cm_day = confusion_matrix(y_true_day, y_pred_day)
        acc_day = accuracy_score(y_true_day, y_pred_day)
    else:
        cm_day = None
        acc_day = float("nan")
    results["clf"] = {"cm": cm_day, "accuracy": acc_day}

    # ---- 5. Threshold sweep for Strategy A ----------------------------------
    print("\n[5/5] Threshold sweep for Strategy A ...")
    sweep_results: list[dict] = []
    for threshold in np.arange(0.50, 0.95, 0.05):
        pred_t = strategy_a_hard_oracle(aligned, threshold)
        m_t = evaluate_gating(
            actual_kw, pred_t, solar_elev, csi_valid, day_mask,
        )
        # Count "clear days" at this threshold
        clear_mask = day_mask & (csi_valid >= threshold)
        n_clear_days = int(clear_mask.sum())
        sweep_results.append({
            "threshold": threshold,
            **m_t,
            "clear_n": int(clear_mask.sum()),
            "n_clear_days": n_clear_days,
        })

    # Find best threshold
    valid_sweep = [
        sr for sr in sweep_results
        if np.isfinite(sr.get("clear_nrmse", float("nan")))
        and sr.get("clear_nrmse", float("inf")) < 0.300
        and np.isfinite(sr.get("day_nrmse", float("nan")))
    ]
    if valid_sweep:
        # Best = lowest day_nRMSE while keeping Clear nRMSE < 0.300
        best_sweep = min(valid_sweep, key=lambda sr: sr["day_nrmse"])
        print(f"  Best threshold: {best_sweep['threshold']:.2f}"
              f" (day_nRMSE={best_sweep['day_nrmse']:.4f},"
              f" clear_nRMSE={best_sweep.get('clear_nrmse', float('nan')):.4f})")
    else:
        print("  No valid threshold meets Clear nRMSE < 0.300 criterion")

    # ---- Print report -------------------------------------------------------
    print_comparison_table(results, sweep_results)

    # ---- Summary ------------------------------------------------------------
    print(f"\nTest set: {len(aligned)} rows ({aligned['valid_time'].min()} to"
          f" {aligned['valid_time'].max()})")
    print(f"Daytime:  {int(day_mask.sum())} rows")


if __name__ == "__main__":
    main()
