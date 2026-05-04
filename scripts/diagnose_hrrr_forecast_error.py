#!/usr/bin/env python3
"""
HRRR Forecast Error Diagnosis  -  8 Checks + 3 Charts + Decision Matrix.

Systematically diagnoses HRRR NWP GHI forecast errors by checking:
  1. valid_time alignment
  2. Overall error (MAE, RMSE, Bias, R2)
  3. Skill score vs clear-sky & persistence baselines
  4. Error by lead-time bin
  5. Error by weather scenario (CSI-based)
  6. Error by valid hour
  7. Error by NSRDB weather_fill_flag
  8. HRRR GHI error vs PV prediction error correlation

Outputs a Markdown report + 3 diagnostic PNG charts.

Usage:
    python scripts/diagnose_hrrr_forecast_error.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from scipy.stats import pearsonr
from sklearn.metrics import r2_score

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================================
# Paths & Configuration
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "processed" / "pvdaq_nsrdb_2020_2022"

HRRR_PATH = DATA_DIR / "stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet"
NSRDB_PATH = DATA_DIR / "stage2_cleaned_hourly_dataset.parquet"
INSPECTION_PATH = DATA_DIR / "inspection_predictions.parquet"

REPORT_DIR = BASE_DIR / "reports"
FIGURE_DIR = REPORT_DIR / "figures"
REPORT_PATH = REPORT_DIR / "hrrr_forecast_error_diagnosis.md"

os.makedirs(FIGURE_DIR, exist_ok=True)

# Matplotlib style  -  keep labels in English to avoid CJK font dependency
plt.rcParams.update({
    "figure.dpi": 150,
    "font.size": 9,
    "axes.titlesize": 12,
    "axes.labelsize": 10,
    "axes.unicode_minus": False,
})

# === Thresholds ===
DAYTIME_ELEV = 5.0  # solar elevation > 5 deg = daytime

# Lead-time bins (half-open intervals)
LEAD_BIN_EDGES = [24, 30, 36, 42, 45]
LEAD_BIN_LABELS = ["24-29h", "30-35h", "36-41h", "42-44h"]

# Clear-sky index thresholds for scenario stratification
CSI_CLEAR = 0.7
CSI_OVERCAST = 0.3

# Scenario palette
SCENARIO_COLORS = {"clear": "gold", "mixed": "orange", "overcast": "gray"}
SCENARIO_ORDER = ["clear", "mixed", "overcast"]


# ============================================================================
# Utility: Error Metrics
# ============================================================================

def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Compute MAE, RMSE, Bias, R2 from paired arrays.

    Returns a dict with keys: mae, rmse, bias, r2, n.
    Sets NaN when n < 2 (insufficient data for meaningful metrics).
    """
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    n = int(mask.sum())
    if n < 2:
        return {"mae": np.nan, "rmse": np.nan, "bias": np.nan, "r2": np.nan, "n": n}
    yt, yp = y_true[mask], y_pred[mask]
    err = yp - yt
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    bias = float(np.mean(err))
    r2 = float(r2_score(yt, yp))
    return {"mae": mae, "rmse": rmse, "bias": bias, "r2": r2, "n": n}


def _fmt(val, decimals: int = 1) -> str:
    """Format a numeric value or return 'N/A'."""
    if isinstance(val, (int, float)) and not np.isnan(val):
        return f"{val:.{decimals}f}"
    return "N/A"


# ============================================================================
# 1. Data Loading
# ============================================================================

def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load and normalize the three source parquet datasets.

    Returns:
        hrrr (pd.DataFrame): HRRR decomposed forecast weather.
        nsrdb (pd.DataFrame): NSRDB Stage2 cleaned hourly observations.
        insp (pd.DataFrame): inspection predictions from PV models.
    """
    print("=" * 56)
    print("  HRRR Forecast Error Diagnosis")
    print("=" * 56)

    hrrr = pd.read_parquet(HRRR_PATH)
    nsrdb = pd.read_parquet(NSRDB_PATH)
    insp = pd.read_parquet(INSPECTION_PATH)

    # Normalise all timestamps to UTC-aware datetime64[ns]
    # This ensures HRRR (stored as datetime64[us, UTC]) and NSRDB
    # (datetime64[ns, UTC]) share a common type for cross-joins.
    for df, cols in [
        (hrrr, ["timestamp", "weather_forecast_issue_time"]),
        (nsrdb, ["timestamp"]),
        (insp, ["valid_time"]),
    ]:
        for col in cols:
            df[col] = pd.to_datetime(df[col], utc=True)

    print(f"\n  HRRR decomposed:  {len(hrrr):>6} rows  "
          f"{hrrr['timestamp'].min()} .. {hrrr['timestamp'].max()}")
    print(f"  NSRDB Stage2:     {len(nsrdb):>6} rows  "
          f"{nsrdb['timestamp'].min()} .. {nsrdb['timestamp'].max()}")
    print(f"  Inspection preds: {len(insp):>6} rows  "
          f"{insp['valid_time'].min()} .. {insp['valid_time'].max()}")
    return hrrr, nsrdb, insp


# ============================================================================
# 2. Build Merged Dataset (HRRR  x NSRDB)
# ============================================================================

def build_merged(hrrr: pd.DataFrame, nsrdb: pd.DataFrame) -> pd.DataFrame:
    """Inner-join HRRR with NSRDB on valid_time, compute derived columns.

    Adds: solar_elevation_deg, is_daytime, ghi_error, valid_hour,
          lead_bin (categorical), csi, scenario, persistence_ghi.
    """
    # Select only the NSRDB columns we need to minimise suffix clutter
    nsrdb_cols = [
        "timestamp", "ghi_wm2", "clearsky_ghi_wm2",
        "solar_zenith_angle_deg", "weather_fill_flag",
    ]

    merged = pd.merge(
        hrrr,
        nsrdb[nsrdb_cols],
        on="timestamp",
        how="inner",
        suffixes=("_hrrr", "_nsrdb"),
    )

    # Solar elevation proxy: 90 deg - zenith (from HRRR forecast)
    merged["solar_elevation_deg"] = 90.0 - merged["solar_zenith_deg"]
    merged["is_daytime"] = merged["solar_elevation_deg"] > DAYTIME_ELEV

    # GHI error: positive = HRRR overestimates
    merged["ghi_error"] = merged["ghi_wm2_hrrr"] - merged["ghi_wm2_nsrdb"]

    # UTC hour at valid time
    merged["valid_hour"] = merged["timestamp"].dt.hour.astype(int)

    # Lead-time categorical bin
    merged["lead_bin"] = pd.cut(
        merged["weather_forecast_lead_time_hour"],
        bins=LEAD_BIN_EDGES,
        labels=LEAD_BIN_LABELS,
        right=False,
    )

    # Clear-Sky Index (CSI): observed GHI / clearsky GHI
    # Replace 0 in divisor with NaN to avoid infinite values at night
    csi_raw = merged["ghi_wm2_nsrdb"] / merged["clearsky_ghi_wm2"].replace(0, np.nan)
    merged["csi"] = np.clip(csi_raw, 0.0, 2.0)

    # Weather scenario (diagnostic-only stratification)
    merged["scenario"] = "clear"
    merged.loc[merged["csi"] < CSI_CLEAR, "scenario"] = "mixed"
    merged.loc[merged["csi"] < CSI_OVERCAST, "scenario"] = "overcast"
    merged["scenario"] = pd.Categorical(
        merged["scenario"], categories=SCENARIO_ORDER, ordered=True,
    )

    # Persistence baseline: NSRDB GHI at forecast issue_time
    nsrdb_ghi_issue = nsrdb.set_index("timestamp")["ghi_wm2"]
    merged["persistence_ghi"] = (
        merged["weather_forecast_issue_time"].map(nsrdb_ghi_issue)
    )

    n_day = merged["is_daytime"].sum()
    print(f"  Merged: {len(merged)} rows  "
          f"(daytime={n_day}, nighttime={len(merged) - n_day})")
    return merged


# ============================================================================
# Check 1: valid_time Alignment
# ============================================================================

def check1_valid_time_alignment(
    hrrr: pd.DataFrame, merged: pd.DataFrame
) -> dict:
    """Verify timestamp == issue_time + lead_time and report merge rate."""
    print("\n[Check 1/8] valid_time alignment")

    expected = (
        hrrr["weather_forecast_issue_time"]
        + pd.to_timedelta(hrrr["weather_forecast_lead_time_hour"], unit="h")
    )
    diff_sec = (hrrr["timestamp"] - expected).dt.total_seconds().abs()
    aligned = int((diff_sec <= 1.0).sum())
    n_hrrr = len(hrrr)
    n_matched = len(merged)
    match_pct = n_matched / n_hrrr * 100.0
    all_perfect = bool((diff_sec > 0.001).sum() == 0)

    result = {
        "n_hrrr": n_hrrr,
        "n_aligned": aligned,
        "n_misaligned": n_hrrr - aligned,
        "n_matched": n_matched,
        "match_pct": match_pct,
        "all_perfect": all_perfect,
    }

    print(f"  Internal: {aligned}/{n_hrrr} rows valid  "
          f"({'perfect' if all_perfect else f'{n_hrrr - aligned} misaligned'})")
    print(f"  Merge:    {n_matched}/{n_hrrr} matched ({match_pct:.1f}%)")
    return result


# ============================================================================
# Check 2: Overall Error
# ============================================================================

def check2_overall_error(merged: pd.DataFrame) -> dict:
    """Overall HRRR vs NSRDB GHI error, split by day/night."""
    print("\n[Check 2/8] Overall GHI error")

    day = merged[merged["is_daytime"]]
    night = merged[~merged["is_daytime"]]

    day_m = _metrics(day["ghi_wm2_nsrdb"].values, day["ghi_wm2_hrrr"].values)
    night_m = _metrics(night["ghi_wm2_nsrdb"].values, night["ghi_wm2_hrrr"].values)

    print(f"  Daytime  (n={day_m['n']}):  "
          f"MAE={_fmt(day_m['mae'])}  RMSE={_fmt(day_m['rmse'])}  "
          f"Bias={_fmt(day_m['bias'])}  R2={_fmt(day_m['r2'], 3)}")
    print(f"  Nightime (n={night_m['n']}): "
          f"MAE={_fmt(night_m['mae'])}  (RMSE/Bias/R2 not meaningful at night)")

    return {"daytime": day_m, "nighttime": night_m}


# ============================================================================
# Check 3: Skill Score vs Baselines
# ============================================================================

def check3_skill_score(merged: pd.DataFrame) -> dict:
    """Skill score vs clear-sky and persistence baselines (daytime only).

    Skill = 1 - MSE_HRRR / MSE_baseline. Positive = HRRR is better.
    """
    print("\n[Check 3/8] Skill score vs baselines")

    day = merged[merged["is_daytime"]]

    # MSE for HRRR forecast (daytime only, using all available rows)
    m_hrrr = _metrics(day["ghi_wm2_nsrdb"].values, day["ghi_wm2_hrrr"].values)
    mse_hrrr = m_hrrr["rmse"] ** 2

    # --- Clear-sky baseline: predict clearsky_ghi at valid_time ---
    cs_ok = day["clearsky_ghi_wm2"].notna() & day["ghi_wm2_nsrdb"].notna()
    mse_cs = float(np.nan)
    skill_cs = float(np.nan)
    n_cs = int(cs_ok.sum())
    if n_cs > 0:
        mse_cs = float(np.mean(
            (day.loc[cs_ok, "ghi_wm2_nsrdb"] - day.loc[cs_ok, "clearsky_ghi_wm2"]) ** 2
        ))
        skill_cs = 1.0 - mse_hrrr / mse_cs if mse_cs > 0 else float(np.nan)

    # --- Persistence baseline: predict GHI at issue_time ---
    persist_ok = day["persistence_ghi"].notna() & day["ghi_wm2_nsrdb"].notna()
    mse_persist = float(np.nan)
    skill_persist = float(np.nan)
    n_persist = int(persist_ok.sum())
    if n_persist > 0:
        mse_persist = float(np.mean(
            (day.loc[persist_ok, "ghi_wm2_nsrdb"] - day.loc[persist_ok, "persistence_ghi"]) ** 2
        ))
        skill_persist = 1.0 - mse_hrrr / mse_persist if mse_persist > 0 else float(np.nan)

    print(f"  Clear-sky MSE:   {_fmt(mse_cs)}   ->  Skill = {_fmt(skill_cs, 3)}  (n={n_cs})")
    print(f"  Persistence MSE: {_fmt(mse_persist)}   ->  Skill = {_fmt(skill_persist, 3)}  (n={n_persist})")
    print(f"  HRRR MSE:        {_fmt(mse_hrrr)}")

    return {
        "mse_hrrr": mse_hrrr,
        "mse_clearsky": mse_cs,
        "mse_persistence": mse_persist,
        "skill_clearsky": skill_cs,
        "skill_persistence": skill_persist,
        "n_clearsky": n_cs,
        "n_persistence": n_persist,
    }


# ============================================================================
# Check 4: Error by Lead-Time Bin
# ============================================================================

def check4_by_lead_time(merged: pd.DataFrame) -> dict:
    """Error metrics across lead-time bins (daytime only)."""
    print("\n[Check 4/8] Error by lead-time bin")

    day = merged[merged["is_daytime"] & merged["lead_bin"].notna()]
    results = {}
    for label in LEAD_BIN_LABELS:
        sub = day[day["lead_bin"] == label]
        m = _metrics(sub["ghi_wm2_nsrdb"].values, sub["ghi_wm2_hrrr"].values)
        results[label] = {
            "n": m["n"],
            "day_frac": m["n"] / len(sub) if len(sub) > 0 else 0.0,
            "mean_elev": float(sub["solar_elevation_deg"].mean()) if len(sub) > 0 else np.nan,
            "mae": m["mae"],
            "rmse": m["rmse"],
            "bias": m["bias"],
        }
        print(f"  {label}: n={m['n']:>4}  "
              f"MAE={_fmt(m['mae'])}  RMSE={_fmt(m['rmse'])}  Bias={_fmt(m['bias'])}")
    return results


# ============================================================================
# Check 5: Error by Weather Scenario (NSRDB CSI)
# ============================================================================

def check5_by_scenario(merged: pd.DataFrame) -> dict:
    """Error metrics by weather scenario (daytime only)."""
    print("\n[Check 5/8] Error by weather scenario")

    day = merged[merged["is_daytime"]]
    results = {}
    for scenario in SCENARIO_ORDER:
        sub = day[day["scenario"] == scenario]
        m = _metrics(sub["ghi_wm2_nsrdb"].values, sub["ghi_wm2_hrrr"].values)
        results[scenario] = {
            "n": m["n"],
            "mae": m["mae"],
            "rmse": m["rmse"],
            "bias": m["bias"],
        }
        print(f"  {scenario:>8}: n={m['n']:>5}  "
              f"MAE={_fmt(m['mae'])}  RMSE={_fmt(m['rmse'])}  Bias={_fmt(m['bias'])}")
    return results


# ============================================================================
# Check 6: Error by Valid Hour (UTC)
# ============================================================================

def check6_by_hour(merged: pd.DataFrame) -> dict:
    """Error metrics per valid UTC hour (daytime only)."""
    print("\n[Check 6/8] Error by valid hour")

    day = merged[merged["is_daytime"]]
    results = {}
    for hour in sorted(day["valid_hour"].unique()):
        sub = day[day["valid_hour"] == hour]
        m = _metrics(sub["ghi_wm2_nsrdb"].values, sub["ghi_wm2_hrrr"].values)
        h = int(hour)
        results[h] = {
            "n": m["n"],
            "mae": m["mae"],
            "rmse": m["rmse"],
            "bias": m["bias"],
        }
        print(f"  hour={h:>2d} UTC: n={m['n']:>4}  "
              f"MAE={_fmt(m['mae'])}  RMSE={_fmt(m['rmse'])}  "
              f"Bias={_fmt(m['bias'])}")
    return results


# ============================================================================
# Check 7: Error by NSRDB weather_fill_flag
# ============================================================================

def check7_by_fill_flag(merged: pd.DataFrame) -> dict:
    """Error metrics grouped by NSRDB weather_fill_flag (daytime, n >= 100)."""
    print("\n[Check 7/8] Error by weather_fill_flag")

    day = merged[merged["is_daytime"]]
    results = {}
    for flag, sub in day.groupby("weather_fill_flag"):
        if len(sub) < 100:
            continue
        m = _metrics(sub["ghi_wm2_nsrdb"].values, sub["ghi_wm2_hrrr"].values)
        f = int(flag)
        results[f] = {"n": m["n"], "mae": m["mae"], "rmse": m["rmse"]}
        print(f"  flag={f:>3d}: n={m['n']:>5}  "
              f"daytime_MAE={_fmt(m['mae'])}  daytime_RMSE={_fmt(m['rmse'])}")

    zero_n = results.get(0, {}).get("n", 0)
    total_n = int(day["weather_fill_flag"].notna().sum())
    print(f"   -> {zero_n}/{total_n} daytime samples have weather_fill_flag=0 (original)")
    return results


# ============================================================================
# Check 8: HRRR GHI Error vs PV Prediction Error
# ============================================================================

def check8_pv_correlation(merged: pd.DataFrame, insp: pd.DataFrame) -> dict:
    """Pearson correlation between HRRR GHI error and PV prediction error.

    Uses inspection_predictions (experiment=stage5, horizon=24h).
    Reports overall, daytime-only, and per-scenario correlations.
    """
    print("\n[Check 8/8] HRRR GHI error vs PV error correlation")

    # Filter to our experiment of interest
    insp_f = insp[
        (insp["experiment"] == "stage5") & (insp["horizon_hours"] == 24)
    ].copy()
    insp_f = insp_f.rename(columns={"valid_time": "timestamp"})
    insp_f["timestamp"] = pd.to_datetime(insp_f["timestamp"], utc=True)

    # Merge GHI error from the HRRR-NSRDB dataset onto each PV prediction
    combined = pd.merge(
        insp_f[["timestamp", "error_kw", "actual_kw"]].dropna(subset=["error_kw"]),
        merged[["timestamp", "ghi_error", "scenario", "is_daytime"]].dropna(
            subset=["ghi_error"]
        ),
        on="timestamp",
        how="inner",
    )

    combined_day = combined[combined["is_daytime"]].copy()

    def _corr(df: pd.DataFrame, label: str) -> dict:
        """Compute Pearson r and p-value; return dict with corr, pvalue, n."""
        if len(df) < 3:
            print(f"  {label}: insufficient samples ({len(df)})")
            return {"corr": np.nan, "pvalue": np.nan, "n": len(df)}
        c, p = pearsonr(df["ghi_error"], df["error_kw"])
        print(f"  {label}: corr={c:.3f}  (p={p:.2e}, n={len(df)})")
        return {"corr": c, "pvalue": p, "n": len(df)}

    results = {
        "overall": _corr(combined, "overall"),
        "daytime": _corr(combined_day, "daytime"),
        "by_scenario": {},
    }

    for scenario in SCENARIO_ORDER:
        sub = combined_day[combined_day["scenario"] == scenario]
        results["by_scenario"][scenario] = _corr(sub, f"  {scenario}")

    return results


# ============================================================================
# Charts
# ============================================================================

def _annotate_heatmap(
    ax: plt.Axes, values: np.ndarray, fmt: str = ".0f", fontsize: int = 7
) -> None:
    """Overlay numeric annotations on each heatmap cell (skips NaN)."""
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            v = values[i, j]
            if not np.isnan(v):
                ax.text(
                    j, i, format(v, fmt),
                    ha="center", va="center", fontsize=fontsize,
                    color="black", fontweight="bold",
                )


def make_chart1_boxplot(merged: pd.DataFrame) -> None:
    """Chart 1: Boxplot of GHI error by lead-time bin (daytime)."""
    df = merged[merged["is_daytime"] & merged["lead_bin"].notna()].copy()

    fig, ax = plt.subplots(figsize=(10, 5))
    groups = [df[df["lead_bin"] == lb]["ghi_error"].values for lb in LEAD_BIN_LABELS]

    bp = ax.boxplot(
        groups,
        tick_labels=LEAD_BIN_LABELS,
        patch_artist=True,
        showfliers=True,
        widths=0.6,
    )

    # Colour each box
    box_colors = ["#4C72B0", "#55A868", "#DD8452", "#C44E52"]
    for box, color in zip(bp["boxes"], box_colors):
        box.set_facecolor(color)
        box.set_alpha(0.7)

    # Overlay mean as red diamond (use NaN for empty bins to suppress warning)
    means = [float(g.mean()) if len(g) > 0 else np.nan for g in groups]
    ax.scatter(
        range(1, len(LEAD_BIN_LABELS) + 1), means,
        color="red", marker="D", s=60, zorder=5, label="Mean",
    )

    ax.axhline(0, color="gray", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Lead-Time Bin")
    ax.set_ylabel("GHI Error (W/m2)")
    ax.set_title("HRRR GHI Error by Lead-Time Bin (Daytime)")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "hrrr_error_by_lead_time.png")
    plt.close(fig)
    print("  [Chart 1] hrrr_error_by_lead_time.png")


def make_chart2_heatmaps(merged: pd.DataFrame) -> None:
    """Chart 2: Bias and RMSE heatmaps by valid_hour x scenario (daytime)."""
    df = merged[merged["is_daytime"]].copy()

    bias_pivot = df.pivot_table(
        values="ghi_error", index="scenario", columns="valid_hour", aggfunc="mean",
    ).reindex(SCENARIO_ORDER)

    rmse_pivot = df.pivot_table(
        values="ghi_error", index="scenario", columns="valid_hour",
        aggfunc=lambda x: np.sqrt(np.mean(x ** 2)),
    ).reindex(SCENARIO_ORDER)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5))

    # --- Left: Bias heatmap (RdBu_r, centred at zero) ---
    bias_vals = bias_pivot.values
    vlim = max(abs(np.nanmin(bias_vals)), abs(np.nanmax(bias_vals))) or 1.0
    im1 = ax1.imshow(
        bias_vals, aspect="auto", cmap="RdBu_r",
        norm=TwoSlopeNorm(vcenter=0, vmin=-vlim, vmax=vlim),
    )
    ax1.set_xticks(range(24))
    ax1.set_xticklabels([str(h) for h in range(24)], fontsize=7)
    ax1.set_yticks(range(len(SCENARIO_ORDER)))
    ax1.set_yticklabels(SCENARIO_ORDER)
    ax1.set_title("Bias (W/m2)")
    ax1.set_xlabel("Valid Hour (UTC)")
    ax1.set_ylabel("Scenario")
    plt.colorbar(im1, ax=ax1, shrink=0.8)
    _annotate_heatmap(ax1, bias_vals, fmt=".0f")

    # --- Right: RMSE heatmap (Reds) ---
    rmse_vals = rmse_pivot.values
    vmax_r = float(np.nanmax(rmse_vals)) or 1.0
    im2 = ax2.imshow(rmse_vals, aspect="auto", cmap="Reds", vmin=0, vmax=vmax_r)
    ax2.set_xticks(range(24))
    ax2.set_xticklabels([str(h) for h in range(24)], fontsize=7)
    ax2.set_yticks(range(len(SCENARIO_ORDER)))
    ax2.set_yticklabels(SCENARIO_ORDER)
    ax2.set_title("RMSE (W/m2)")
    ax2.set_xlabel("Valid Hour (UTC)")
    ax2.set_ylabel("Scenario")
    plt.colorbar(im2, ax=ax2, shrink=0.8)
    _annotate_heatmap(ax2, rmse_vals, fmt=".0f")

    fig.suptitle(
        "HRRR GHI Error by Valid Hour and Weather Scenario (Daytime)",
        fontsize=14, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "hrrr_bias_rmse_by_scenario_hour.png",
                bbox_inches="tight")
    plt.close(fig)
    print("  [Chart 2] hrrr_bias_rmse_by_scenario_hour.png")


def make_chart3_scatter(merged: pd.DataFrame) -> None:
    """Chart 3: HRRR vs NSRDB GHI scatter coloured by scenario (daytime)."""
    df = merged[merged["is_daytime"]].copy()

    fig, ax = plt.subplots(figsize=(8, 8))

    for scenario in SCENARIO_ORDER:
        sub = df[df["scenario"] == scenario]
        ax.scatter(
            sub["ghi_wm2_nsrdb"], sub["ghi_wm2_hrrr"],
            c=SCENARIO_COLORS[scenario], alpha=0.3, s=2,
            label=f"{scenario} (n={len(sub)})",
        )

    lims = [
        0,
        max(df["ghi_wm2_nsrdb"].max(), df["ghi_wm2_hrrr"].max()) * 1.02,
    ]
    ax.plot(lims, lims, "k--", linewidth=1, alpha=0.5)
    ax.set_xlim(lims)
    ax.set_ylim(lims)
    ax.set_xlabel("NSRDB Observed GHI (W/m2)")
    ax.set_ylabel("HRRR Forecast GHI (W/m2)")
    ax.set_aspect("equal")

    r2 = r2_score(df["ghi_wm2_nsrdb"], df["ghi_wm2_hrrr"])
    ax.set_title(f"HRRR vs NSRDB GHI (Daytime) - R2 = {r2:.3f}")
    ax.legend(markerscale=5, fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "hrrr_vs_nsrdb_scatter_by_scenario.png")
    plt.close(fig)
    print("  [Chart 3] hrrr_vs_nsrdb_scatter_by_scenario.png")


def make_charts(merged: pd.DataFrame) -> None:
    """Generate all three diagnostic charts."""
    print("\n[Charts] Generating figures...")
    make_chart1_boxplot(merged)
    make_chart2_heatmaps(merged)
    make_chart3_scatter(merged)


# ============================================================================
# Report Generation
# ============================================================================

def _row(*values) -> str:
    """Format a markdown table row."""
    return " | ".join(str(v) for v in values)


def generate_report(results: dict) -> str:
    """Build the full Markdown report with decision matrix."""
    c1, c2, c3 = results["c1"], results["c2"], results["c3"]
    c4, c5, c6 = results["c4"], results["c5"], results["c6"]
    c7, c8 = results["c7"], results["c8"]
    dt, nt = c2["daytime"], c2["nighttime"]

    # --- Decision logic ---
    alignment_ok = c1["match_pct"] > 95
    has_skill = (
        not np.isnan(c3["skill_clearsky"]) and c3["skill_clearsky"] > 0
    )
    pv_corr = c8.get("daytime", {}).get("corr", np.nan)
    pv_corr_strong = not np.isnan(pv_corr) and abs(pv_corr) > 0.3

    clear_rmse = c5.get("clear", {}).get("rmse", np.nan)
    mixed_rmse = c5.get("mixed", {}).get("rmse", np.nan)
    mixed_degraded = (
        not np.isnan(clear_rmse)
        and not np.isnan(mixed_rmse)
        and mixed_rmse > clear_rmse * 1.2
    )

    lines: list[str] = []

    def L(text: str = "") -> None:
        lines.append(text)

    # ===================== 0. Title =====================
    L("# HRRR 预报误差诊断报告")
    L()
    L(f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    L()
    L(f"- HRRR 数据: `{HRRR_PATH.name}` ({c1['n_hrrr']} 行)")
    L(f"- NSRDB 数据: `{NSRDB_PATH.name}`")
    L(f"- 匹配行数: {c1['n_matched']} ({c1['match_pct']:.1f}%)")
    L()
    L("---")
    L()

    # ===================== 1. Alignment =====================
    L("## 1. 时间对齐验证")
    L()
    if c1["all_perfect"]:
        L("- HRRR 内部一致性: **全部通过** (timestamp = issue_time + lead_time)")
    else:
        L(f"- HRRR 内部一致性: **{c1['n_aligned']}/{c1['n_hrrr']} 通过** "
          f"({c1['n_misaligned']} 行不匹配)")
    L(f"- HRRR  x NSRDB 匹配率: **{c1['match_pct']:.1f}%** "
      f"({c1['n_matched']}/{c1['n_hrrr']})")
    L()
    L("---")
    L()

    # ===================== 2. Overall Error =====================
    L("## 2. 总体误差 (HRRR GHI vs NSRDB 观测)")
    L()
    L("| 时段 | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) | R2 | N |")
    L("|------|-----------|-------------|-------------|-----|----|")
    L(_row(
        "白天 (日间)",
        _fmt(dt["mae"], 1), _fmt(dt["rmse"], 1),
        _fmt(dt["bias"], 1), _fmt(dt["r2"], 3), dt["n"],
    ))
    L(_row(
        "夜间",
        _fmt(nt["mae"], 1), "-", "-", "-", nt["n"],
    ))
    L()
    L("---")
    L()

    # ===================== 3. Skill Score =====================
    L("## 3. Skill Score vs Baselines (日间)")
    L()
    L("| Baseline | MSE_baseline | MSE_HRRR | Skill_MSE | N |")
    L("|----------|-------------|----------|-----------|-----|")
    L(_row(
        "Clear-sky",
        _fmt(c3["mse_clearsky"], 1), _fmt(c3["mse_hrrr"], 1),
        _fmt(c3["skill_clearsky"], 3), c3["n_clearsky"],
    ))
    L(_row(
        "Persistence",
        _fmt(c3["mse_persistence"], 1), _fmt(c3["mse_hrrr"], 1),
        _fmt(c3["skill_persistence"], 3), c3["n_persistence"],
    ))
    L()
    L("- **Skill > 0** = HRRR 优于该 baseline")
    L("- Clear-sky: 使用 NSRDB 理论晴空 GHI (`clearsky_ghi_wm2`)")
    L("- Persistence: GHI(t+h) = GHI(t=issue_time)")
    L()
    L("---")
    L()

    # ===================== 4. By Lead Time =====================
    L("## 4. 按 Lead Time 分桶 (日间)")
    L()
    L("| Lead_Bin | N | Day_Frac | Mean_Elev ( deg) | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |")
    L("|----------|---|----------|---------------|------------|-------------|-------------|")
    for label in LEAD_BIN_LABELS:
        r = c4.get(label, {})
        L(_row(
            label,
            r.get("n", 0),
            f"{r.get('day_frac', 0):.2f}",
            _fmt(r.get("mean_elev"), 1),
            _fmt(r.get("mae"), 1),
            _fmt(r.get("rmse"), 1),
            _fmt(r.get("bias"), 1),
        ))
    L()
    L("---")
    L()

    # ===================== 5. By Scenario =====================
    L("## 5. 按天气场景 (日间)")
    L()
    L("> **说明**: 天气场景基于 NSRDB 观测 CSI (clear-sky index) 对样本进行诊断分层，"
      "不作为可部署的预报特征使用。")
    L("> CSI = GHI / clearsky_GHI: Clear >= 0.7, Mixed 0.3~0.7, Overcast < 0.3")
    L()
    L("| Scenario | N | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |")
    L("|----------|---|------------|-------------|-------------|")
    for scenario in SCENARIO_ORDER:
        r = c5.get(scenario, {})
        L(_row(
            scenario,
            r.get("n", 0),
            _fmt(r.get("mae"), 1),
            _fmt(r.get("rmse"), 1),
            _fmt(r.get("bias"), 1),
        ))
    L()
    L("---")
    L()

    # ===================== 6. By Hour =====================
    L("## 6. 按 Valid Hour (日间)")
    L()
    L("| Hour (UTC) | N | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |")
    L("|------------|---|------------|-------------|-------------|")
    for hour in sorted(c6.keys()):
        r = c6[hour]
        L(_row(
            f"{hour:>2d}",
            r.get("n", 0),
            _fmt(r.get("mae"), 1),
            _fmt(r.get("rmse"), 1),
            _fmt(r.get("bias"), 1),
        ))
    L()
    L("---")
    L()

    # ===================== 7. By Fill Flag =====================
    L("## 7. 按 weather_fill_flag")
    L()
    L("> Primary metrics: weather_fill_flag=0 (原始观测) 样本为主。")
    L()
    L("| Flag | N | Daytime_MAE (W/m2) | Daytime_RMSE (W/m2) |")
    L("|------|---|---------------------|---------------------|")
    for flag in sorted(c7.keys()):
        r = c7[flag]
        L(_row(
            flag,
            r.get("n", 0),
            _fmt(r.get("mae"), 1),
            _fmt(r.get("rmse"), 1),
        ))
    L()
    L("---")
    L()

    # ===================== 8. PV Correlation =====================
    L("## 8. HRRR GHI 误差 vs PV 预测误差相关分析")
    L()
    L("- 数据: `inspection_predictions.parquet`, experiment=stage5, horizon=24h")
    L("- 方法: Pearson 相关系数 corr(HRRR GHI error, PV error_kw)")
    L()
    L("| 分组 | Corr | p-value | N |")
    L("|------|------|---------|-----|")

    def _corr_row(r: dict, label: str) -> str:
        return _row(
            label,
            _fmt(r.get("corr"), 3),
            f"{r.get('pvalue', 0):.2e}" if not np.isnan(r.get("pvalue", np.nan)) else "N/A",
            r.get("n", 0),
        )

    L(_corr_row(c8.get("overall", {}), "Overall (all hours)"))
    L(_corr_row(c8.get("daytime", {}), "Daytime"))
    for scenario in SCENARIO_ORDER:
        r = c8.get("by_scenario", {}).get(scenario, {})
        L(_corr_row(r, f"  {scenario}"))

    L()
    L("> **解释**: 正相关表示 HRRR 高估 GHI 时 PV 模型也倾向于高估功率，"
      "说明 PV 模型有承接 HRRR 输入误差的趋势。")
    L()
    L("---")
    L()

    # ===================== 9. Conclusions =====================
    L("## 9. 结论与决策")
    L()
    L("### 核心问题评估")
    L()
    L(f"1. **时间对齐**: {'通过 V' if alignment_ok else '失败 ✗'}")
    L(f"2. **HRRR 技能**: "
      f"{'正向 Skill V (vs clear-sky: ' + _fmt(c3['skill_clearsky'], 3) + ')' if has_skill else '无正向 Skill ✗ (vs clear-sky: ' + _fmt(c3['skill_clearsky'], 3) + ')'}")
    if pv_corr_strong:
        L(f"3. **PV 误差传导**: HRRR 误差强相关 PV 误差 "
          f"(r={pv_corr:.3f}) - 输入瓶颈确认成立")
    else:
        L(f"3. **PV 误差传导**: HRRR 误差与 PV 误差相关性弱 "
          f"(r={_fmt(pv_corr, 3)})")
    if mixed_degraded:
        L(f"4. **场景退化**: Mixed RMSE ({_fmt(mixed_rmse, 1)}) > "
          f"Clear RMSE ({_fmt(clear_rmse, 1)}) - "
          f"HRRR 在非晴空条件显著退化")
    else:
        L(f"4. **场景退化**: Mixed/Overcast RMSE 与 Clear 差距在正常范围")
    L()
    L("### 决策矩阵")
    L()
    L("| 诊断结果 | 下一步 |")
    L("|---------|--------|")

    decisions = []
    if not alignment_ok:
        decisions.append(("时间对齐失败", "修数据，不训模型"))
    if not has_skill:
        decisions.append(
            ("HRRR 无 skill (skill < 0 vs clear-sky)",
             "不继续挖 HRRR，转 CSI/概率预测")
        )
    if pv_corr_strong:
        decisions.append(
            ("HRRR 误差强相关 PV 误差",
             "输入瓶颈确认成立  -> CSI / 概率 / 更好 NWP")
        )
    elif not np.isnan(pv_corr):
        decisions.append(
            ("HRRR 有 skill 但 PV 没吃到 (Check 8 corr 低)",
             "改特征使用方式")
        )
    if mixed_degraded:
        decisions.append(
            ("HRRR 在 Mixed/Overcast 崩溃", "分场景建模或概率区间")
        )

    for diag, action in decisions:
        L(_row(diag, action))
    L()
    L("---")
    L()
    L("### 图表")
    L()
    L("![Lead-Time Boxplot](figures/hrrr_error_by_lead_time.png)")
    L("![Bias/RMSE Heatmaps](figures/hrrr_bias_rmse_by_scenario_hour.png)")
    L("![GHI Scatter](figures/hrrr_vs_nsrdb_scatter_by_scenario.png)")

    return "\n".join(lines)


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    """Orchestrate loading, all 8 checks, charts, and report generation."""
    hrrr, nsrdb, insp = load_data()
    merged = build_merged(hrrr, nsrdb)

    c1 = check1_valid_time_alignment(hrrr, merged)
    c2 = check2_overall_error(merged)
    c3 = check3_skill_score(merged)
    c4 = check4_by_lead_time(merged)
    c5 = check5_by_scenario(merged)
    c6 = check6_by_hour(merged)
    c7 = check7_by_fill_flag(merged)
    c8 = check8_pv_correlation(merged, insp)

    make_charts(merged)

    results = {
        "c1": c1, "c2": c2, "c3": c3, "c4": c4,
        "c5": c5, "c6": c6, "c7": c7, "c8": c8,
    }

    md = generate_report(results)
    REPORT_PATH.write_text(md, encoding="utf-8")
    print(f"\n[Done] Report: {REPORT_PATH}")
    print(f"       Charts: {FIGURE_DIR}/")


if __name__ == "__main__":
    main()
