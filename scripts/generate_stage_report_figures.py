from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data/processed/nrel_opsd/stage2_cleaned_hourly_dataset.parquet"
FIGURE_DIR = ROOT / "reports/figures"


def _set_style() -> None:
    """Use a restrained report style with readable labels."""

    plt.rcParams.update(
        {
            "figure.dpi": 140,
            "savefig.dpi": 180,
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.labelsize": 10,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
        }
    )


def _save(fig: plt.Figure, name: str) -> None:
    """Save and close one figure."""

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / name, bbox_inches="tight")
    plt.close(fig)


def plot_pv_week(df: pd.DataFrame) -> None:
    """Show actual PV and NREL forecast features for one representative week."""

    week = df[(df["timestamp"] >= "2006-06-01") & (df["timestamp"] < "2006-06-08")].copy()
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.plot(week["timestamp"], week["pv_power_kw"] / 1000, label="Actual PV", linewidth=1.8)
    ax.plot(week["timestamp"], week["pv_forecast_da_kw"] / 1000, label="Day-ahead forecast", linewidth=1.2)
    ax.plot(week["timestamp"], week["pv_forecast_ha4_kw"] / 1000, label="4-hour-ahead forecast", linewidth=1.2)
    ax.set_title("PV Power and Forecast Features: Representative Week")
    ax.set_xlabel("Time")
    ax.set_ylabel("Power (MW)")
    ax.legend(ncol=3, loc="upper left")
    _save(fig, "stage_dataset_pv_week.png")


def plot_forecast_error_distribution(df: pd.DataFrame) -> None:
    """Compare DA and HA4 forecast errors against actual PV output."""

    da_error = (df["pv_forecast_da_kw"] - df["pv_power_kw"]) / 1000
    ha4_error = (df["pv_forecast_ha4_kw"] - df["pv_power_kw"]) / 1000

    fig, ax = plt.subplots(figsize=(9, 4.8))
    bins = np.linspace(
        min(da_error.quantile(0.01), ha4_error.quantile(0.01)),
        max(da_error.quantile(0.99), ha4_error.quantile(0.99)),
        60,
    )
    ax.hist(da_error, bins=bins, alpha=0.58, label="DA error", color="#3E7CB1")
    ax.hist(ha4_error, bins=bins, alpha=0.58, label="HA4 error", color="#D95D39")
    ax.axvline(0, color="#222222", linewidth=1)
    ax.set_title("Forecast Error Distribution")
    ax.set_xlabel("Forecast - Actual (MW)")
    ax.set_ylabel("Hour count")
    ax.legend()
    _save(fig, "stage_dataset_forecast_error_distribution.png")


def plot_load_price_profile(df: pd.DataFrame) -> None:
    """Display the OPSD-derived weekday/hour load and price profile."""

    profile = df.groupby("hour", as_index=False)[["load_mw", "price_eur_mwh"]].mean()

    fig, ax_load = plt.subplots(figsize=(10, 4.8))
    ax_price = ax_load.twinx()
    ax_load.plot(profile["hour"], profile["load_mw"], color="#2F6F4E", marker="o", label="Load")
    ax_price.plot(profile["hour"], profile["price_eur_mwh"], color="#A23E48", marker="s", label="Price")
    ax_load.set_title("Average Hourly Load and Price Profile")
    ax_load.set_xlabel("Hour of day")
    ax_load.set_ylabel("Load (MW)")
    ax_price.set_ylabel("Price (EUR/MWh)")
    ax_load.set_xticks(range(0, 24, 2))
    lines = ax_load.get_lines() + ax_price.get_lines()
    ax_load.legend(lines, [line.get_label() for line in lines], loc="upper left")
    _save(fig, "stage_dataset_load_price_profile.png")


def plot_storage_dispatch_week(df: pd.DataFrame) -> None:
    """Show battery SOC and dispatch power for one representative week."""

    week = df[(df["timestamp"] >= "2006-01-01") & (df["timestamp"] < "2006-01-08")].copy()
    dispatch_mw = (week["storage_discharge_kw"] - week["storage_charge_kw"]) / 1000

    fig, ax_soc = plt.subplots(figsize=(12, 4.8))
    ax_dispatch = ax_soc.twinx()
    ax_soc.plot(week["timestamp"], week["storage_soc"], color="#445E93", label="SOC", linewidth=1.8)
    ax_dispatch.bar(week["timestamp"], dispatch_mw, width=0.03, color="#D99A2B", alpha=0.55, label="Net dispatch")
    ax_soc.set_title("Storage SOC and Net Dispatch: First Week")
    ax_soc.set_xlabel("Time")
    ax_soc.set_ylabel("SOC")
    ax_dispatch.set_ylabel("Discharge - Charge (MW)")
    lines = ax_soc.get_lines() + [ax_dispatch.containers[0]]
    labels = ["SOC", "Net dispatch"]
    ax_soc.legend(lines, labels, loc="upper left")
    _save(fig, "stage_dataset_storage_dispatch_week.png")


def plot_quality_summary(df: pd.DataFrame) -> None:
    """Summarize data quality gates visually."""

    expected_hours = 8760
    observed_hours = len(df)
    missing_values = int(df.isna().sum().sum())
    duplicate_timestamps = int(df.duplicated(subset=["timestamp"]).sum())
    pv_out_of_bounds = int((~df["pv_power_kw"].between(0, 70000 * 1.05)).sum())

    labels = ["Observed hours", "Missing values", "Duplicate timestamps", "PV bound violations"]
    values = [observed_hours, missing_values, duplicate_timestamps, pv_out_of_bounds]
    colors = ["#2F6F4E", "#A23E48", "#A23E48", "#A23E48"]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    bars = ax.bar(labels, values, color=colors)
    ax.axhline(expected_hours, color="#555555", linestyle="--", linewidth=1, label="Expected annual hours")
    ax.set_title("Stage 2 Data Quality Summary")
    ax.set_ylabel("Count")
    ax.tick_params(axis="x", rotation=12)
    ax.legend(loc="upper right")
    for bar in bars:
        height = bar.get_height()
        ax.annotate(
            f"{int(height)}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
        )
    _save(fig, "stage_dataset_quality_summary.png")


def main() -> None:
    _set_style()
    df = pd.read_parquet(DATA_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    plot_pv_week(df)
    plot_forecast_error_distribution(df)
    plot_load_price_profile(df)
    plot_storage_dispatch_week(df)
    plot_quality_summary(df)

    print(f"Figures written to {FIGURE_DIR}")


if __name__ == "__main__":
    main()
