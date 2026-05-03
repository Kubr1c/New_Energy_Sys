"""Validate Erbs vs DISC decomposition against NSRDB REST2 pseudo-ground-truth.

NSRDB provides independently-computed GHI, DNI, DHI from the REST2
clear-sky model.  We feed NSRDB GHI through our decomposition models
and compare the output DNI/DHI against NSRDB's values.

Metrics: RMSE, MBE, MAE, R2 — overall and by sky condition / season.
"""

from __future__ import annotations

import sys
sys.path.insert(0, "src")

import numpy as np
import pandas as pd
from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

DATA_PATH = "data/processed/pvdaq_nsrdb_2020_2022/stage2_cleaned_hourly_dataset.parquet"
df = pd.read_parquet(DATA_PATH)
ts = pd.to_datetime(df["timestamp"], utc=True)

# Filter to daytime rows (zenith < 85°).
daytime = df["solar_zenith_angle_deg"] < 85.0
df_day = df[daytime].copy()
ts_day = ts[daytime]

print(f"Total rows: {len(df)}, daytime: {len(df_day)}")

# ---------------------------------------------------------------------------
# Run both models
# ---------------------------------------------------------------------------

ghi = df_day["ghi_wm2"]
pressure = df_day["pressure_hpa"] * 100.0  # hPa -> Pa
cc = df_day.get("cloud_type")  # not used by DISC, only Erbs; NSRDB uses cloud_type not cloud_cover_pct

print("Running Erbs ...")
res_erbs = decompose_ghi_to_dhi_dni(
    ghi=ghi, timestamp=ts_day,
    latitude=39.74, longitude=-105.18, altitude=1730.0,
    cloud_cover=None, model="erbs",
)

print("Running DISC ...")
res_disc = decompose_ghi_to_dhi_dni(
    ghi=ghi, timestamp=ts_day,
    latitude=39.74, longitude=-105.18, altitude=1730.0,
    model="disc", pressure=pressure,
)

# ---------------------------------------------------------------------------
# Ground truth from NSRDB REST2
# ---------------------------------------------------------------------------

truth_dni = df_day["dni_wm2"].values
truth_dhi = df_day["dhi_wm2"].values
truth_ghi = ghi.values

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def metrics(label: str, pred: np.ndarray, truth: np.ndarray) -> dict:
    """Compute standard regression metrics."""
    valid = np.isfinite(pred) & np.isfinite(truth)
    p, t = pred[valid], truth[valid]
    n = len(p)
    if n == 0:
        return {"n": 0}
    error = p - t
    rmse = np.sqrt(np.mean(error**2))
    mbe = np.mean(error)
    mae = np.mean(np.abs(error))
    # R2
    ss_res = np.sum(error**2)
    ss_tot = np.sum((t - np.mean(t))**2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    # Relative RMSE (normalised by mean truth)
    rrmse = rmse / np.mean(t) * 100 if np.mean(t) > 0 else np.nan
    return {
        "n": n, "RMSE": rmse, "MBE": mbe, "MAE": mae,
        "R2": r2, "RRMSE_%": rrmse,
    }


# ---- Overall ----
print("\n" + "=" * 75)
print("OVERALL METRICS (daytime, zenith < 85°)")
print("=" * 75)

for name, res in [("Erbs", res_erbs), ("DISC", res_disc)]:
    m_dni = metrics(f"{name} DNI", res["dni_wm2"].values, truth_dni)
    m_dhi = metrics(f"{name} DHI", res["dhi_wm2"].values, truth_dhi)
    print(f"\n{name}:")
    print(f"  DNI   RMSE={m_dni['RMSE']:.1f}  MBE={m_dni['MBE']:+.1f}  MAE={m_dni['MAE']:.1f}  R2={m_dni['R2']:.3f}  RRMSE={m_dni['RRMSE_%']:.1f}%")
    print(f"  DHI   RMSE={m_dhi['RMSE']:.1f}  MBE={m_dhi['MBE']:+.1f}  MAE={m_dhi['MAE']:.1f}  R2={m_dhi['R2']:.3f}  RRMSE={m_dhi['RRMSE_%']:.1f}%")

# ---- By sky condition (using Erbs kt as reference) ----
print("\n" + "=" * 75)
print("BY SKY CONDITION (based on Erbs kt)")
print("=" * 75)

kt = res_erbs["kt"].values
bins = [
    ("Overcast    (kt <= 0.3)", kt <= 0.3),
    ("Broken cloud (0.3 < kt <= 0.7)", (kt > 0.3) & (kt <= 0.7)),
    ("Clear sky    (kt > 0.7)", kt > 0.7),
]

for label, mask in bins:
    n = mask.sum()
    if n == 0:
        continue
    print(f"\n{label}  (n={n})")
    print(f"  {'':<6} {'RMSE':>8} {'MBE':>8} {'R2':>7} {'RRMSE':>7}")
    for name, res in [("Erbs", res_erbs), ("DISC", res_disc)]:
        m_dni = metrics(f"{name}", res["dni_wm2"].values[mask], truth_dni[mask])
        m_dhi = metrics(f"{name}", res["dhi_wm2"].values[mask], truth_dhi[mask])
        print(f"  {name:<4} DNI: {m_dni['RMSE']:>8.1f} {m_dni['MBE']:>+8.1f} {m_dni['R2']:>7.3f} {m_dni['RRMSE_%']:>7.1f}%")
        print(f"  {name:<4} DHI: {m_dhi['RMSE']:>8.1f} {m_dhi['MBE']:>+8.1f} {m_dhi['R2']:>7.3f} {m_dhi['RRMSE_%']:>7.1f}%")

# ---- By season ----
print("\n" + "=" * 75)
print("BY SEASON")
print("=" * 75)

months = pd.DatetimeIndex(ts_day).month.values
seasons = [
    ("DJF (Winter)", np.isin(months, [12, 1, 2])),
    ("MAM (Spring)", np.isin(months, [3, 4, 5])),
    ("JJA (Summer)", np.isin(months, [6, 7, 8])),
    ("SON (Autumn)", np.isin(months, [9, 10, 11])),
]

for label, mask in seasons:
    n = mask.sum()
    if n == 0:
        continue
    print(f"\n{label}  (n={n})")
    print(f"  {'':<6} {'RMSE':>8} {'MBE':>8} {'R2':>7}")
    for name, res in [("Erbs", res_erbs), ("DISC", res_disc)]:
        m_dni = metrics(f"{name}", res["dni_wm2"].values[mask], truth_dni[mask])
        m_dhi = metrics(f"{name}", res["dhi_wm2"].values[mask], truth_dhi[mask])
        print(f"  {name:<4} DNI: {m_dni['RMSE']:>8.1f} {m_dni['MBE']:>+8.1f} {m_dni['R2']:>7.3f}")
        print(f"  {name:<4} DHI: {m_dhi['RMSE']:>8.1f} {m_dhi['MBE']:>+8.1f} {m_dhi['R2']:>7.3f}")

# ---- Winner summary ----
print("\n" + "=" * 75)
print("HEAD-TO-HEAD: (DISC RMSE - Erbs RMSE) — negative = DISC better")
print("=" * 75)

for label, mask in [("Overall", slice(None))] + bins + seasons:
    n = np.sum(mask) if hasattr(mask, 'sum') else len(truth_dni)
    if n == 0:
        continue
    e_dni = metrics("e", res_erbs["dni_wm2"].values[mask], truth_dni[mask])
    d_dni = metrics("d", res_disc["dni_wm2"].values[mask], truth_dni[mask])
    e_dhi = metrics("e", res_erbs["dhi_wm2"].values[mask], truth_dhi[mask])
    d_dhi = metrics("d", res_disc["dhi_wm2"].values[mask], truth_dhi[mask])
    dni_delta = d_dni["RMSE"] - e_dni["RMSE"]
    dhi_delta = d_dhi["RMSE"] - e_dhi["RMSE"]
    winner_dni = "DISC OK" if dni_delta < 0 else "Erbs OK"
    winner_dhi = "DISC OK" if dhi_delta < 0 else "Erbs OK"
    print(f"  {label:<35} DNI: {dni_delta:>+7.1f} ({winner_dni})   DHI: {dhi_delta:>+7.1f} ({winner_dhi})")

print("\nDone.")
