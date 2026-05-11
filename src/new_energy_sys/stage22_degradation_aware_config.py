"""Stage22 degradation-aware storage configuration optimisation.

This module answers the question: which storage sizing, SOC interval, and dispatch
penalty configuration yields positive net incremental revenue after accounting for
battery degradation (rainflow cycle counting + calendar fade)?

It runs a two-pass grid scan over five dimensions:
  1. capacity multiplier
  2. power multiplier
  3. SOC operating range
  4. degradation penalty weight λ (applied as a multiplier on the Stage12
     throughput-cost proxy in the dispatch objective)
  5. minimum arbitrage spread (EUR/MWh gate on charge/discharge decisions)

Pass 1 — Stage12 rolling dispatch with the proxy penalty and spread gate.
Pass 2 — Stage17 rainflow degradation replay on the dispatch trajectory.

Configurations are filtered on:
  - net_incremental_revenue_eur > 0
  - soh_end >= min_soh
  - all physical constraints passed

The best config is the one with the highest net incremental revenue after filtering.
If no config passes, the result explicitly states that fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.stage12_storage_rolling import _simulate_fast_rolling_optimization
from new_energy_sys.stage17_battery_degradation import (
    _build_hourly_replay,
    _merge_degradation_config,
    _metrics_for_scenario,
    _prepare_scenario_rows,
)
from new_energy_sys.storage import _constraint_summary, _prepare_dispatch_input


def _json_safe(value: Any) -> Any:
    """Convert numpy/pandas values into strict JSON-serializable objects."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (dict, list, tuple)) else False:
        return None
    return value


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage22Result:
    """Stage22 grid scan result.

    Attributes:
        metrics: Per-config aggregated metrics DataFrame.
        report: Quality-gate and best-config summary dict.
    """

    metrics: pd.DataFrame
    report: dict[str, Any]


# ---------------------------------------------------------------------------
# Grid builder
# ---------------------------------------------------------------------------


def _fmt_mult(value: float) -> str:
    """Format a multiplier float as a short ID token, e.g. 1.25 → '1p25'."""
    s = f"{value:.2f}".replace(".", "p")
    parts = s.split("p", 1)
    if len(parts) == 2:
        frac = parts[1].rstrip("0")
        if not frac:
            frac = "0"
        s = f"{parts[0]}p{frac}"
    return s


def _build_stage22_grid(
    base_storage: dict[str, Any],
    *,
    soc_ranges: list[tuple[float, float]] | None = None,
    lambda_values: list[float] | None = None,
    min_spreads: list[float] | None = None,
    capacity_multipliers: list[float] | None = None,
    power_multipliers: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Build a five-dimensional configuration grid.

    Each item is a dict with keys:
        config_id, capacity_multiplier, power_multiplier,
        soc_min, soc_max, lambda, min_spread_eur_mwh,
        storage_config (deep copy with scaled values),
        effective_cycle_cost_eur_per_kwh.
    """
    soc_ranges = soc_ranges or [(0.10, 0.90), (0.20, 0.80), (0.25, 0.75), (0.30, 0.70)]
    lambda_values = lambda_values or [0.0, 0.5, 1.0, 2.0]
    min_spreads = min_spreads or [0.0, 5.0, 10.0, 15.0]
    capacity_multipliers = capacity_multipliers or [1.0, 1.25, 1.5, 2.0]
    power_multipliers = power_multipliers or [0.5, 0.75, 1.0, 1.25]

    base_capacity_kwh = float(base_storage["capacity_kwh"])
    base_max_charge_kw = float(base_storage["max_charge_kw"])
    base_max_discharge_kw = float(base_storage["max_discharge_kw"])
    base_cycle_cost = float(base_storage.get("cycle_cost_eur_per_kwh", 0.002))
    soc_initial = float(base_storage.get("soc_initial", 0.5))

    # Calibrate λ → min_spread mapping from degradation economics.
    # A full cycle at moderate DOD (~0.5) costs roughly:
    #   replacement_cost * (1 - soh_eol) / cycles_at_dod
    #   = 150 * 0.2 / 5000 ≈ 0.006 EUR/kWh per half-cycle
    # Per MWh of throughput: 0.006 * 1000 = 6 EUR/MWh.
    # We double this to account for calendar + shallow-cycle damage,
    # giving a reference spread of ~12–15 EUR/MWh at λ=1.0.
    _ref_spread_eur_mwh = 15.0  # λ=1.0 → 15 EUR/MWh min spread

    grid: list[dict[str, Any]] = []
    for cap_mult in capacity_multipliers:
        for pow_mult in power_multipliers:
            for soc_min, soc_max in soc_ranges:
                for lam in lambda_values:
                    for spread in min_spreads:
                        cap_str = _fmt_mult(cap_mult)
                        pow_str = _fmt_mult(pow_mult)
                        soc_min_str = _fmt_mult(soc_min)
                        soc_max_str = _fmt_mult(soc_max)
                        lam_str = _fmt_mult(lam)
                        spread_str = _fmt_mult(spread)
                        config_id = (
                            f"cap{cap_str}_pow{pow_str}"
                            f"_soc{soc_min_str}_{soc_max_str}"
                            f"_lambda{lam_str}_spread{spread_str}"
                        )

                        storage_config = dict(base_storage)
                        storage_config["capacity_kwh"] = base_capacity_kwh * cap_mult
                        storage_config["max_charge_kw"] = base_max_charge_kw * pow_mult
                        storage_config["max_discharge_kw"] = base_max_discharge_kw * pow_mult
                        storage_config["soc_min"] = soc_min
                        storage_config["soc_max"] = soc_max
                        storage_config["soc_initial"] = float(
                            np.clip(soc_initial, soc_min, soc_max)
                        )

                        # λ contributes a degradation-calibrated spread floor;
                        # the explicit min_spread adds an independent gate on top.
                        _lambda_spread = lam * _ref_spread_eur_mwh
                        _effective_spread = _lambda_spread + spread

                        grid.append({
                            "config_id": config_id,
                            "capacity_multiplier": cap_mult,
                            "power_multiplier": pow_mult,
                            "soc_min": soc_min,
                            "soc_max": soc_max,
                            "lambda": lam,
                            "min_spread_eur_mwh": _effective_spread,
                            "storage_config": storage_config,
                            "effective_cycle_cost_eur_per_kwh": lam * base_cycle_cost,
                        })
    return grid


# ---------------------------------------------------------------------------
# Per-config two-pass simulator
# ---------------------------------------------------------------------------


def _simulate_stage22_config(
    dispatch_input: pd.DataFrame,
    grid_item: dict[str, Any],
    *,
    capacity_kw: float,
    lookahead_hours: int,
    degradation_config: dict[str, Any],
    base_shortfall_penalty: float,
    base_terminal_penalty: float,
    base_terminal_target: float,
    base_smooth_ramp_limit: float,
    base_smooth_step: float,
    base_action_change_penalty: float,
) -> dict[str, Any]:
    """Pass 1 (dispatch) + Pass 2 (degradation) for a single config.

    Returns a flat metrics dict; dispatch failure is captured as net = -inf.
    """
    storage_config = grid_item["storage_config"]
    config_id = grid_item["config_id"]

    # --- Pass 1: rolling dispatch ---
    try:
        dispatch_results = _simulate_fast_rolling_optimization(
            dispatch_input,
            storage_config,
            capacity_kw=capacity_kw,
            lookahead_hours=lookahead_hours,
            cycle_cost_eur_per_kwh=grid_item["effective_cycle_cost_eur_per_kwh"],
            shortfall_risk_penalty_eur_per_kwh=base_shortfall_penalty,
            terminal_soc_target=base_terminal_target,
            dispatch_mode="economic",
            smooth_power_ramp_limit_kw=base_smooth_ramp_limit,
            smooth_action_step_kw=base_smooth_step,
            action_change_penalty_eur_per_kw=base_action_change_penalty,
            min_spread_eur_mwh=grid_item["min_spread_eur_mwh"],
        )
    except Exception as exc:
        return {
            "config_id": config_id,
            "capacity_multiplier": grid_item["capacity_multiplier"],
            "power_multiplier": grid_item["power_multiplier"],
            "soc_min": grid_item["soc_min"],
            "soc_max": grid_item["soc_max"],
            "lambda": grid_item["lambda"],
            "min_spread_eur_mwh": grid_item["min_spread_eur_mwh"],
            "capacity_kwh": storage_config["capacity_kwh"],
            "max_charge_kw": storage_config["max_charge_kw"],
            "max_discharge_kw": storage_config["max_discharge_kw"],
            "gross_incremental_revenue_eur": float("-inf"),
            "degradation_cost_eur": float("nan"),
            "net_incremental_revenue_eur": float("-inf"),
            "net_revenue_eur": float("nan"),
            "soh_end": float("nan"),
            "equivalent_full_cycles": float("nan"),
            "max_cycle_depth_dod": float("nan"),
            "total_charge_kwh": float("nan"),
            "total_discharge_kwh": float("nan"),
            "rainflow_cycle_count": float("nan"),
            "sample_count": 0,
            "cycle_damage": float("nan"),
            "calendar_damage": float("nan"),
            "replacement_flag": False,
            "capacity_fade_percent": float("nan"),
            "constraints_passed": False,
            "exception": str(exc),
        }

    # --- Constraints ---
    constraints = _constraint_summary(dispatch_results, storage_config)
    constraints_passed = all([
        constraints.get("soc_within_bounds", False),
        constraints.get("charge_power_within_limit", False),
        constraints.get("discharge_power_within_limit", False),
        constraints.get("no_simultaneous_charge_discharge", False),
        constraints.get("energy_balance_error_within_tolerance", False),
    ])

    # --- Pass 2: degradation accounting ---
    try:
        base_rows = _prepare_scenario_rows(dispatch_results, "rolling_optimization")
        replay = _build_hourly_replay(
            base_rows,
            scenario_name=config_id,
            mode="rainflow",
            storage_config=storage_config,
            degradation_config=degradation_config,
        )
        metrics = _metrics_for_scenario(replay, storage_config=storage_config)
    except Exception as exc:
        return {
            "config_id": config_id,
            "capacity_multiplier": grid_item["capacity_multiplier"],
            "power_multiplier": grid_item["power_multiplier"],
            "soc_min": grid_item["soc_min"],
            "soc_max": grid_item["soc_max"],
            "lambda": grid_item["lambda"],
            "min_spread_eur_mwh": grid_item["min_spread_eur_mwh"],
            "capacity_kwh": storage_config["capacity_kwh"],
            "max_charge_kw": storage_config["max_charge_kw"],
            "max_discharge_kw": storage_config["max_discharge_kw"],
            "gross_incremental_revenue_eur": float("-inf"),
            "degradation_cost_eur": float("nan"),
            "net_incremental_revenue_eur": float("-inf"),
            "net_revenue_eur": float("nan"),
            "soh_end": float("nan"),
            "equivalent_full_cycles": float("nan"),
            "max_cycle_depth_dod": float("nan"),
            "total_charge_kwh": float("nan"),
            "total_discharge_kwh": float("nan"),
            "rainflow_cycle_count": float("nan"),
            "sample_count": 0,
            "cycle_damage": float("nan"),
            "calendar_damage": float("nan"),
            "replacement_flag": False,
            "capacity_fade_percent": float("nan"),
            "constraints_passed": constraints_passed,
            "exception": f"degradation: {exc}",
        }

    return {
        "config_id": config_id,
        "capacity_multiplier": grid_item["capacity_multiplier"],
        "power_multiplier": grid_item["power_multiplier"],
        "soc_min": grid_item["soc_min"],
        "soc_max": grid_item["soc_max"],
        "lambda": grid_item["lambda"],
        "min_spread_eur_mwh": grid_item["min_spread_eur_mwh"],
        "capacity_kwh": storage_config["capacity_kwh"],
        "max_charge_kw": storage_config["max_charge_kw"],
        "max_discharge_kw": storage_config["max_discharge_kw"],
        "gross_incremental_revenue_eur": float(metrics["gross_incremental_revenue_eur"]),
        "degradation_cost_eur": float(metrics["degradation_cost_eur"]),
        "net_incremental_revenue_eur": float(metrics["net_incremental_revenue_eur"]),
        "net_revenue_eur": float(metrics["net_revenue_eur"]),
        "soh_end": float(metrics["soh_end"]),
        "equivalent_full_cycles": float(metrics["equivalent_full_cycles"]),
        "max_cycle_depth_dod": float(metrics["max_cycle_depth_dod"]),
        "total_charge_kwh": float(metrics["total_charge_kwh"]),
        "total_discharge_kwh": float(metrics["total_discharge_kwh"]),
        "rainflow_cycle_count": float(metrics["rainflow_cycle_count"]),
        "sample_count": int(metrics["sample_count"]),
        "cycle_damage": float(metrics["cycle_damage"]),
        "calendar_damage": float(metrics["calendar_damage"]),
        "replacement_flag": bool(metrics["replacement_flag"]),
        "capacity_fade_percent": float(metrics["capacity_fade_percent"]),
        "constraints_passed": constraints_passed,
        "exception": None,
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_stage22_degradation_aware_config(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    lookahead_hours: int = 24,
    soc_ranges: list[tuple[float, float]] | None = None,
    lambda_values: list[float] | None = None,
    min_spreads: list[float] | None = None,
    capacity_multipliers: list[float] | None = None,
    power_multipliers: list[float] | None = None,
    min_net_revenue_eur: float = 0.0,
    min_soh: float = 0.95,
    output_paths: dict[str, Path] | None = None,
) -> Stage22Result:
    """Run the degradation-aware configuration grid scan.

    Args:
        predictions: Stage9-format prediction DataFrame.
        feature_frame: Stage3-format feature DataFrame with price_eur_mwh, load_mw.
        config: Full runtime config dict (site + storage + battery_degradation).
        horizon_hours: Forecast horizon used in dispatch input alignment.
        lookahead_hours: Rolling look-ahead window width.
        soc_ranges: List of (soc_min, soc_max) tuples to scan.
        lambda_values: Degradation penalty weights.
        min_spreads: Minimum arbitrage spread thresholds (EUR/MWh).
        capacity_multipliers: Capacity scaling factors.
        power_multipliers: Power scaling factors.
        min_net_revenue_eur: Minimum net incremental revenue filter.
        min_soh: Minimum end-of-period SOH filter.
        output_paths: Optional dict with keys 'metrics_csv', 'report_json', 'report_md'.

    Returns:
        Stage22Result with metrics DataFrame and report dict.
    """
    # --- Extract base parameters ---
    capacity_kw = float(config["site"]["capacity_kw"])
    base_storage = dict(config["storage"])
    base_shortfall_penalty = float(base_storage.get("shortfall_risk_penalty_eur_per_kwh", 0.001))
    base_terminal_penalty = float(base_storage.get("terminal_soc_penalty_eur_per_kwh", 0.02))
    base_terminal_target = float(base_storage.get("terminal_soc_target", 0.5))
    base_smooth_ramp_limit = float(base_storage.get("smooth_power_ramp_limit_kw", 250.0))
    base_smooth_step = float(base_storage.get("smooth_action_step_kw", 250.0))
    base_action_change_penalty = float(base_storage.get("action_change_penalty_eur_per_kw", 0.0))

    degradation_config = _merge_degradation_config(config)

    # --- Build grid ---
    grid = _build_stage22_grid(
        base_storage,
        soc_ranges=soc_ranges,
        lambda_values=lambda_values,
        min_spreads=min_spreads,
        capacity_multipliers=capacity_multipliers,
        power_multipliers=power_multipliers,
    )

    # --- Prepare dispatch input once ---
    dispatch_input = _prepare_dispatch_input(
        predictions, feature_frame, horizon_hours=horizon_hours
    )

    # --- Scan ---
    metrics_rows: list[dict[str, Any]] = []
    total = len(grid)
    for idx, item in enumerate(grid):
        row = _simulate_stage22_config(
            dispatch_input,
            item,
            capacity_kw=capacity_kw,
            lookahead_hours=lookahead_hours,
            degradation_config=degradation_config,
            base_shortfall_penalty=base_shortfall_penalty,
            base_terminal_penalty=base_terminal_penalty,
            base_terminal_target=base_terminal_target,
            base_smooth_ramp_limit=base_smooth_ramp_limit,
            base_smooth_step=base_smooth_step,
            base_action_change_penalty=base_action_change_penalty,
        )
        metrics_rows.append(row)
        if (idx + 1) % 50 == 0 or idx == 0 or idx == total - 1:
            print(f"[stage22] {idx + 1}/{total} configs scanned")

    metrics_df = pd.DataFrame(metrics_rows)

    # --- Filter ---
    has_constraints = metrics_df["constraints_passed"].fillna(False)
    has_soh = metrics_df["soh_end"] >= min_soh
    has_net = metrics_df["net_incremental_revenue_eur"] > min_net_revenue_eur
    mask = has_constraints & has_soh & has_net
    passed = metrics_df.loc[mask].sort_values(
        "net_incremental_revenue_eur", ascending=False
    )

    # --- Best config ---
    best_config: dict[str, Any] | None = None
    decision: str
    if passed.empty:
        n_total = len(metrics_df)
        n_constraint_fail = int((~has_constraints).sum())
        n_soh_fail = int((~has_soh).sum())
        n_net_fail = int((~has_net).sum())
        decision = (
            f"All {n_total} configurations failed the filter: "
            f"{n_constraint_fail} constraint violations, "
            f"{n_soh_fail} SOH < {min_soh}, "
            f"{n_net_fail} net_incremental_revenue_eur <= {min_net_revenue_eur}."
        )
    else:
        best_row = passed.iloc[0]
        best_config = {
            "config_id": str(best_row["config_id"]),
            "capacity_multiplier": float(best_row["capacity_multiplier"]),
            "power_multiplier": float(best_row["power_multiplier"]),
            "soc_min": float(best_row["soc_min"]),
            "soc_max": float(best_row["soc_max"]),
            "lambda": float(best_row["lambda"]),
            "min_spread_eur_mwh": float(best_row["min_spread_eur_mwh"]),
            "capacity_kwh": float(best_row["capacity_kwh"]),
            "max_charge_kw": float(best_row["max_charge_kw"]),
            "max_discharge_kw": float(best_row["max_discharge_kw"]),
            "gross_incremental_revenue_eur": float(best_row["gross_incremental_revenue_eur"]),
            "degradation_cost_eur": float(best_row["degradation_cost_eur"]),
            "net_incremental_revenue_eur": float(best_row["net_incremental_revenue_eur"]),
            "soh_end": float(best_row["soh_end"]),
            "equivalent_full_cycles": float(best_row["equivalent_full_cycles"]),
            "max_cycle_depth_dod": float(best_row["max_cycle_depth_dod"]),
            "total_charge_kwh": float(best_row["total_charge_kwh"]),
            "total_discharge_kwh": float(best_row["total_discharge_kwh"]),
            "rainflow_cycle_count": float(best_row["rainflow_cycle_count"]),
        }
        decision = (
            f"Best config: {best_config['config_id']} "
            f"with net_incremental_revenue_eur = {best_config['net_incremental_revenue_eur']:.2f}. "
            f"{len(passed)}/{len(metrics_df)} configs passed the filter."
        )

    # --- Effective scan parameters (resolve defaults) ---
    eff_soc_ranges = soc_ranges or [(0.10, 0.90), (0.20, 0.80), (0.25, 0.75), (0.30, 0.70)]
    eff_lambda_values = lambda_values or [0.0, 0.5, 1.0, 2.0]
    eff_min_spreads = min_spreads or [0.0, 5.0, 10.0, 15.0]
    eff_cap_mults = capacity_multipliers or [1.0, 1.25, 1.5, 2.0]
    eff_pow_mults = power_multipliers or [0.5, 0.75, 1.0, 1.25]

    # --- Build report ---
    report: dict[str, Any] = {
        "stage": "stage22_degradation_aware_config",
        "decision": decision,
        "grid": {
            "total_configs": int(len(metrics_df)),
            "soc_ranges": [(float(a), float(b)) for a, b in eff_soc_ranges],
            "lambda_values": [float(v) for v in eff_lambda_values],
            "min_spreads_eur_mwh": [float(v) for v in eff_min_spreads],
            "capacity_multipliers": [float(v) for v in eff_cap_mults],
            "power_multipliers": [float(v) for v in eff_pow_mults],
        },
        "filters": {
            "min_net_revenue_eur": float(min_net_revenue_eur),
            "min_soh": float(min_soh),
            "passed_count": int(len(passed)),
            "total_count": int(len(metrics_df)),
        },
        "best_config": best_config,
        "quality_gates": {
            "configs_with_net_positive": int((metrics_df["net_incremental_revenue_eur"] > 0).sum()),
            "configs_with_constraints_passed": int((metrics_df["constraints_passed"].fillna(False)).sum()),
            "configs_with_soh_above_min": int((metrics_df["soh_end"] >= min_soh).sum()),
        },
    }

    # --- Write outputs ---
    if output_paths:
        metrics_csv = output_paths.get("metrics_csv")
        report_json = output_paths.get("report_json")
        report_md = output_paths.get("report_md")

        if metrics_csv:
            metrics_csv.parent.mkdir(parents=True, exist_ok=True)
            metrics_df.to_csv(metrics_csv, index=False)
            print(f"[stage22] metrics written to {metrics_csv}")

        if report_json:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(
                json.dumps(_json_safe(report), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[stage22] JSON report written to {report_json}")

        if report_md:
            report_md.parent.mkdir(parents=True, exist_ok=True)
            report_md.write_text(_build_markdown_report(report, metrics_df), encoding="utf-8")
            print(f"[stage22] Markdown report written to {report_md}")

    return Stage22Result(metrics=metrics_df, report=report)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _build_markdown_report(report: dict[str, Any], metrics: pd.DataFrame) -> str:
    """Render a Markdown summary of the Stage22 scan."""

    grid = report["grid"]
    filters = report["filters"]
    gates = report["quality_gates"]
    best = report.get("best_config")

    lines: list[str] = [
        "# Stage22 — Degradation-Aware Storage Configuration Scan",
        "",
        "## Grid",
        "",
        f"- Total configs scanned: **{grid['total_configs']}**",
        f"- Capacity multipliers: {grid['capacity_multipliers']}",
        f"- Power multipliers: {grid['power_multipliers']}",
        f"- SOC ranges: {grid['soc_ranges']}",
        f"- λ values: {grid['lambda_values']}",
        f"- Min spread thresholds (EUR/MWh): {grid['min_spreads_eur_mwh']}",
        "",
        "## Filters",
        "",
        f"- min_net_revenue_eur: {filters['min_net_revenue_eur']}",
        f"- min_soh: {filters['min_soh']}",
        f"- **{filters['passed_count']} / {filters['total_count']}** configs passed",
        "",
        "## Quality Gates",
        "",
        f"- Configs with net_incremental_revenue_eur > 0: {gates['configs_with_net_positive']}",
        f"- Configs with all constraints passed: {gates['configs_with_constraints_passed']}",
        f"- Configs with SOH >= min_soh: {gates['configs_with_soh_above_min']}",
        "",
        "## Decision",
        "",
        report["decision"],
        "",
    ]

    if best is not None:
        lines.extend([
            "## Best Configuration",
            "",
            "| Parameter | Value |",
            "|-----------|-------|",
            f"| config_id | `{best['config_id']}` |",
            f"| capacity_multiplier | {best['capacity_multiplier']} |",
            f"| power_multiplier | {best['power_multiplier']} |",
            f"| soc_min | {best['soc_min']} |",
            f"| soc_max | {best['soc_max']} |",
            f"| lambda | {best['lambda']} |",
            f"| min_spread_eur_mwh | {best['min_spread_eur_mwh']} |",
            f"| capacity_kwh | {best['capacity_kwh']:.1f} |",
            f"| max_charge_kw | {best['max_charge_kw']:.1f} |",
            f"| max_discharge_kw | {best['max_discharge_kw']:.1f} |",
            f"| **gross_incremental_revenue_eur** | **{best['gross_incremental_revenue_eur']:.2f}** |",
            f"| **degradation_cost_eur** | **{best['degradation_cost_eur']:.2f}** |",
            f"| **net_incremental_revenue_eur** | **{best['net_incremental_revenue_eur']:.2f}** |",
            f"| soh_end | {best['soh_end']:.6f} |",
            f"| equivalent_full_cycles | {best['equivalent_full_cycles']:.2f} |",
            f"| max_cycle_depth_dod | {best['max_cycle_depth_dod']:.4f} |",
            f"| total_charge_kwh | {best['total_charge_kwh']:.1f} |",
            f"| total_discharge_kwh | {best['total_discharge_kwh']:.1f} |",
            f"| rainflow_cycle_count | {best['rainflow_cycle_count']:.1f} |",
            "",
        ])

    # Top passed configs table
    passed_df = metrics.loc[
        metrics["net_incremental_revenue_eur"] > filters["min_net_revenue_eur"]
    ].sort_values("net_incremental_revenue_eur", ascending=False)

    if not passed_df.empty:
        top_n = min(20, len(passed_df))
        lines.extend([
            f"## Top {top_n} Configs (by net_incremental_revenue_eur)",
            "",
            "| config_id | gross_inc | deg_cost | net_inc | SOH | equiv_cycles | max_DoD | constraints |",
            "|-----------|-----------|----------|---------|-----|-------------|---------|-------------|",
        ])
        for _, row in passed_df.head(top_n).iterrows():
            lines.append(
                f"| `{row['config_id']}` "
                f"| {float(row['gross_incremental_revenue_eur']):.2f} "
                f"| {float(row['degradation_cost_eur']):.2f} "
                f"| {float(row['net_incremental_revenue_eur']):.2f} "
                f"| {float(row['soh_end']):.6f} "
                f"| {float(row['equivalent_full_cycles']):.2f} "
                f"| {float(row['max_cycle_depth_dod']):.4f} "
                f"| {bool(row['constraints_passed'])} |"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage22B — economic condition sensitivity analysis
# ---------------------------------------------------------------------------

# Reference calendar fade rate used in the base Stage17 degradation config.
_STAGE17_BASE_CALENDAR_FADE = 0.015


def _require_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    """Fail fast when required columns are missing from a DataFrame."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{label} missing required columns: {', '.join(missing)}. "
            f"Re-run Stage22 with the updated code to regenerate metrics with all fields."
        )


def run_stage22b_economic_sensitivity(
    metrics: pd.DataFrame,
    *,
    replacement_costs: list[float] | None = None,
    cycle_life_multipliers: list[float] | None = None,
    calendar_fade_rates: list[float] | None = None,
    discharge_value_eur_per_mwh: list[float] | None = None,
    capacity_value_eur_per_kw_year: list[float] | None = None,
    fixed_subsidy_eur_per_kwh: list[float] | None = None,
    min_net_revenue_eur: float = 0.0,
    min_soh: float = 0.90,
    output_paths: dict[str, Path] | None = None,
) -> Stage22Result:
    """Post-process Stage22 metrics through an economic parameter sensitivity matrix.

    This function does NOT re-run dispatch.  It recomputes degradation cost and
    adds non-arbitrage revenue streams using closed-form rescaling of the
    rainflow cycle-damage and calendar-damage aggregates that Stage22 already
    produced.  Only the *spread amplification* experiment (see the separate
    ``run_stage22b_spread_amplification`` function) needs a fresh dispatch pass.

    Args:
        metrics: Stage22 metrics DataFrame (must contain the columns written by
            the updated ``_simulate_stage22_config``, including ``cycle_damage``,
            ``calendar_damage``, and ``sample_count``).
        replacement_costs: Battery replacement costs to scan (EUR/kWh).
        cycle_life_multipliers: Cycle-life multipliers (>1 = longer life).
        calendar_fade_rates: Annual calendar fade rates to scan.
        discharge_value_eur_per_mwh: Additional value per MWh discharged
            (e.g. curtailment reduction, renewable integration value).
        capacity_value_eur_per_kw_year: Annual capacity-reserve value per kW
            of qualified power capacity.
        fixed_subsidy_eur_per_kwh: One-time capital subsidy per kWh of
            installed capacity.
        min_net_revenue_eur: Net incremental revenue filter threshold.
        min_soh: Minimum end-of-period SOH filter.
        output_paths: Optional output file paths.

    Returns:
        Stage22Result with the full economic-sensitivity metrics DataFrame
        and a summary report dict.
    """
    # --- Defaults ---
    replacement_costs = replacement_costs or [150.0, 100.0, 75.0, 50.0]
    cycle_life_multipliers = cycle_life_multipliers or [1.0, 2.0, 3.0]
    calendar_fade_rates = calendar_fade_rates or [0.015, 0.01, 0.005, 0.0]
    discharge_values = discharge_value_eur_per_mwh or [0.0, 10.0, 20.0]
    capacity_values = capacity_value_eur_per_kw_year or [0.0, 20.0, 50.0]
    fixed_subsidies = fixed_subsidy_eur_per_kwh or [0.0, 20.0, 50.0]

    # --- Validate input columns ---
    _require_columns(
        metrics,
        [
            "config_id", "gross_incremental_revenue_eur",
            "net_revenue_eur", "degradation_cost_eur",
            "total_charge_kwh", "total_discharge_kwh", "capacity_kwh",
            "max_charge_kw", "max_discharge_kw", "constraints_passed",
            "cycle_damage", "calendar_damage", "sample_count",
        ],
        "Stage22 metrics",
    )

    base_sample_count = int(metrics["sample_count"].max())
    simulation_years = base_sample_count / 8760.0 if base_sample_count > 0 else 1.0

    rows: list[dict[str, Any]] = []
    total_combos = (
        len(replacement_costs)
        * len(cycle_life_multipliers)
        * len(calendar_fade_rates)
        * len(discharge_values)
        * len(capacity_values)
        * len(fixed_subsidies)
    )

    for _, phys_row in metrics.iterrows():
        # Physical parameters carried from the original dispatch
        cid = str(phys_row["config_id"])
        gross_inc = float(phys_row["gross_incremental_revenue_eur"])
        net_rev_orig = float(phys_row["net_revenue_eur"])
        deg_cost_orig = float(phys_row["degradation_cost_eur"])
        # Derive no-storage baseline from identity: net_rev = gross_rev - deg_cost
        # gross_rev = gross_inc + no_storage_rev
        # → no_storage_rev = net_rev + deg_cost - gross_inc
        no_storage_rev = net_rev_orig + deg_cost_orig - gross_inc
        gross_rev = gross_inc + no_storage_rev
        charge_kwh = float(phys_row["total_charge_kwh"])
        discharge_kwh = float(phys_row["total_discharge_kwh"])
        cap_kwh = float(phys_row["capacity_kwh"])
        max_chg = float(phys_row["max_charge_kw"])
        max_dchg = float(phys_row["max_discharge_kw"])
        qualified_kw = min(max_chg, max_dchg)
        constraints_ok = bool(phys_row.get("constraints_passed", False))
        base_cycle_dmg = float(phys_row["cycle_damage"])
        base_cal_dmg = float(phys_row["calendar_damage"])
        lambda_val = float(phys_row.get("lambda", float("nan")))
        soc_min = float(phys_row.get("soc_min", float("nan")))
        soc_max = float(phys_row.get("soc_max", float("nan")))
        cap_mult = float(phys_row.get("capacity_multiplier", float("nan")))
        pow_mult = float(phys_row.get("power_multiplier", float("nan")))
        efc = float(phys_row.get("equivalent_full_cycles", float("nan")))
        max_dod = float(phys_row.get("max_cycle_depth_dod", float("nan")))

        for repl_cost in replacement_costs:
            for life_mult in cycle_life_multipliers:
                for cal_rate in calendar_fade_rates:
                    # Degradation cost recomputation
                    adj_cycle_dmg = base_cycle_dmg / life_mult
                    adj_cal_dmg = base_cal_dmg * (cal_rate / _STAGE17_BASE_CALENDAR_FADE)
                    total_dmg = adj_cycle_dmg + adj_cal_dmg
                    deg_cost = total_dmg * repl_cost * cap_kwh
                    soh_end = float(np.clip(1.0 - total_dmg, 0.0, 1.0))

                    for d_val in discharge_values:
                        discharge_bonus = discharge_kwh / 1000.0 * d_val

                        for c_val in capacity_values:
                            capacity_bonus = qualified_kw * c_val * simulation_years

                            for subsidy in fixed_subsidies:
                                sub_bonus = cap_kwh * subsidy
                                additional = discharge_bonus + capacity_bonus + sub_bonus
                                net_inc = gross_inc - deg_cost + additional
                                net_rev = gross_rev - deg_cost + additional

                                rows.append({
                                    "config_id": cid,
                                    "capacity_multiplier": cap_mult,
                                    "power_multiplier": pow_mult,
                                    "soc_min": soc_min,
                                    "soc_max": soc_max,
                                    "lambda": lambda_val,
                                    "replacement_cost_eur_per_kwh": repl_cost,
                                    "cycle_life_multiplier": life_mult,
                                    "calendar_fade_rate": cal_rate,
                                    "discharge_value_eur_per_mwh": d_val,
                                    "capacity_value_eur_per_kw_year": c_val,
                                    "fixed_subsidy_eur_per_kwh": subsidy,
                                    "gross_incremental_revenue_eur": gross_inc,
                                    "degradation_cost_eur": deg_cost,
                                    "additional_revenue_eur": additional,
                                    "discharge_bonus_eur": discharge_bonus,
                                    "capacity_bonus_eur": capacity_bonus,
                                    "subsidy_bonus_eur": sub_bonus,
                                    "net_incremental_revenue_eur": net_inc,
                                    "net_revenue_eur": net_rev,
                                    "soh_end": soh_end,
                                    "equivalent_full_cycles": efc,
                                    "max_cycle_depth_dod": max_dod,
                                    "constraints_passed": constraints_ok,
                                    "qualified_capacity_kw": qualified_kw,
                                    "equivalent_simulation_years": simulation_years,
                                })

    metrics_df = pd.DataFrame(rows)

    # --- Filter ---
    has_constraints = metrics_df["constraints_passed"].fillna(False)
    has_soh = metrics_df["soh_end"] >= min_soh
    has_net = metrics_df["net_incremental_revenue_eur"] > min_net_revenue_eur
    mask = has_constraints & has_soh & has_net
    passed = metrics_df.loc[mask].sort_values(
        "net_incremental_revenue_eur", ascending=False
    )

    # --- Best config ---
    best_config: dict[str, Any] | None = None
    decision: str
    n_by_soh_conservative = int((metrics_df["soh_end"] >= 0.95).sum())

    if passed.empty:
        n_total = len(metrics_df)
        n_constraint_fail = int((~has_constraints).sum())
        n_soh_fail = int((~has_soh).sum())
        n_net_fail = int((~has_net).sum())
        decision = (
            f"All {n_total} economic-parameter combinations failed the filter: "
            f"{n_constraint_fail} constraint violations, "
            f"{n_soh_fail} SOH < {min_soh}, "
            f"{n_net_fail} net_incremental_revenue_eur <= {min_net_revenue_eur}."
        )
        # Show the closest-to-zero configs
        closest = metrics_df.nlargest(5, "net_incremental_revenue_eur")
        best_config = {
            "top5_closest": [
                {
                    "config_id": str(r["config_id"]),
                    "replacement_cost": float(r["replacement_cost_eur_per_kwh"]),
                    "cycle_life_mult": float(r["cycle_life_multiplier"]),
                    "calendar_fade": float(r["calendar_fade_rate"]),
                    "discharge_value": float(r["discharge_value_eur_per_mwh"]),
                    "capacity_value": float(r["capacity_value_eur_per_kw_year"]),
                    "subsidy": float(r["fixed_subsidy_eur_per_kwh"]),
                    "net_incremental_revenue_eur": float(r["net_incremental_revenue_eur"]),
                    "soh_end": float(r["soh_end"]),
                }
                for _, r in closest.iterrows()
            ]
        }
    else:
        best_row = passed.iloc[0]
        best_config = {
            "config_id": str(best_row["config_id"]),
            "replacement_cost": float(best_row["replacement_cost_eur_per_kwh"]),
            "cycle_life_mult": float(best_row["cycle_life_multiplier"]),
            "calendar_fade": float(best_row["calendar_fade_rate"]),
            "discharge_value": float(best_row["discharge_value_eur_per_mwh"]),
            "capacity_value": float(best_row["capacity_value_eur_per_kw_year"]),
            "subsidy": float(best_row["fixed_subsidy_eur_per_kwh"]),
            "net_incremental_revenue_eur": float(best_row["net_incremental_revenue_eur"]),
            "soh_end": float(best_row["soh_end"]),
        }
        decision = (
            f"Best config: {best_config['config_id']} "
            f"with net_incremental_revenue_eur = {best_config['net_incremental_revenue_eur']:.2f}. "
            f"{len(passed)}/{len(metrics_df)} combinations passed the filter."
        )

    # --- Build report ---
    total_phys = int(metrics["config_id"].nunique())
    report: dict[str, Any] = {
        "stage": "stage22b_economic_sensitivity",
        "decision": decision,
        "grid": {
            "total_physical_configs": total_phys,
            "total_economic_combinations": total_combos,
            "total_rows": int(len(metrics_df)),
            "replacement_costs_eur_per_kwh": [float(v) for v in replacement_costs],
            "cycle_life_multipliers": [float(v) for v in cycle_life_multipliers],
            "calendar_fade_rates": [float(v) for v in calendar_fade_rates],
            "discharge_value_eur_per_mwh": [float(v) for v in discharge_values],
            "capacity_value_eur_per_kw_year": [float(v) for v in capacity_values],
            "fixed_subsidy_eur_per_kwh": [float(v) for v in fixed_subsidies],
            "equivalent_simulation_years": simulation_years,
        },
        "filters": {
            "min_net_revenue_eur": float(min_net_revenue_eur),
            "min_soh_hard": float(min_soh),
            "min_soh_conservative": 0.95,
            "passed_count_hard": int(len(passed)),
            "passed_count_conservative": int(
                (mask & (metrics_df["soh_end"] >= 0.95)).sum()
            ),
            "total_count": int(len(metrics_df)),
        },
        "best_config": best_config,
        "soh_summary": {
            "soh_0.90_passed": int(n_by_soh_conservative),
            "soh_0.95_passed": int((metrics_df["soh_end"] >= 0.95).sum()),
        },
        "quality_gates": {
            "combinations_with_net_positive": int(
                (metrics_df["net_incremental_revenue_eur"] > 0).sum()
            ),
            "combinations_with_constraints_passed": int(
                (metrics_df["constraints_passed"].fillna(False)).sum()
            ),
            "combinations_with_soh_0.90": int((metrics_df["soh_end"] >= 0.90).sum()),
            "combinations_with_soh_0.95": n_by_soh_conservative,
        },
    }

    # --- Write outputs ---
    if output_paths:
        metrics_csv = output_paths.get("metrics_csv")
        report_json = output_paths.get("report_json")
        report_md = output_paths.get("report_md")

        if metrics_csv:
            metrics_csv.parent.mkdir(parents=True, exist_ok=True)
            metrics_df.to_csv(metrics_csv, index=False)
            print(f"[stage22b] metrics written to {metrics_csv}")

        if report_json:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(
                json.dumps(_json_safe(report), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[stage22b] JSON report written to {report_json}")

        if report_md:
            report_md.parent.mkdir(parents=True, exist_ok=True)
            report_md.write_text(
                _build_stage22b_markdown_report(report, metrics_df), encoding="utf-8"
            )
            print(f"[stage22b] Markdown report written to {report_md}")

    return Stage22Result(metrics=metrics_df, report=report)


# ---------------------------------------------------------------------------
# Stage22B Markdown report
# ---------------------------------------------------------------------------


def _build_stage22b_markdown_report(
    report: dict[str, Any], metrics: pd.DataFrame
) -> str:
    """Render a Markdown summary of the Stage22B economic sensitivity scan."""
    grid = report["grid"]
    filters = report["filters"]
    gates = report["quality_gates"]
    best = report.get("best_config")
    soh_info = report["soh_summary"]

    lines: list[str] = [
        "# Stage22B — Economic Condition Sensitivity Analysis",
        "",
        "## Scope",
        "",
        "This is a *post-processing* sensitivity scan built on Stage22 dispatch + "
        "degradation results. It does **not** re-run the dispatch optimisation; "
        "instead it rescales the rainflow cycle-damage and calendar-damage "
        "aggregates under alternative economic assumptions.  The only exception "
        "is the *price-volatility amplification* experiment, which requires a "
        "fresh dispatch pass and is reported separately.",
        "",
        "Revenue streams NOT modelled in this version:",
        "- Curtailment reduction value (requires hourly curtailment data; planned for V2)",
        "",
        "## Economic Parameters Scanned",
        "",
        f"- Replacement costs (EUR/kWh): {grid['replacement_costs_eur_per_kwh']}",
        f"- Cycle-life multipliers: {grid['cycle_life_multipliers']}",
        f"- Calendar fade rates (per year): {grid['calendar_fade_rates']}",
        f"- Discharge value (EUR/MWh): {grid['discharge_value_eur_per_mwh']}",
        f"- Capacity value (EUR/kW·year): {grid['capacity_value_eur_per_kw_year']}",
        f"- Fixed subsidy (EUR/kWh): {grid['fixed_subsidy_eur_per_kwh']}",
        "",
        f"- Physical configs evaluated: **{grid['total_physical_configs']}**",
        f"- Economic parameter combinations: **{grid['total_economic_combinations']}**",
        f"- Total rows in result matrix: **{grid['total_rows']}**",
        f"- Equivalent simulation years: **{grid['equivalent_simulation_years']:.2f}**",
        "  (computed as `sample_count / 8760`; an approximation, not exact calendar years)",
        "",
        "## Filters",
        "",
        f"| Filter | Threshold | Passed | Total |",
        f"|--------|-----------|--------|-------|",
        f"| SOH (hard) | ≥ {filters['min_soh_hard']} | {soh_info['soh_0.90_passed']} | {filters['total_count']} |",
        f"| SOH (conservative) | ≥ 0.95 | {soh_info['soh_0.95_passed']} | {filters['total_count']} |",
        f"| Net incremental | > {filters['min_net_revenue_eur']} | {filters['passed_count_hard']} | {filters['total_count']} |",
        f"| All (hard SOH + net) | — | **{filters['passed_count_hard']}** | {filters['total_count']} |",
        f"| All (conservative SOH + net) | — | **{filters['passed_count_conservative']}** | {filters['total_count']} |",
        "",
        "## Decision",
        "",
        report["decision"],
        "",
    ]

    if best is not None:
        if "top5_closest" in best:
            lines.extend([
                "## Top 5 Closest to Zero (none passed)",
                "",
                "| config_id | repl_cost | life_mult | cal_fade | discharge_val | "
                "capacity_val | subsidy | **net_inc** | SOH |",
                "|-----------|-----------|-----------|----------|---------------|"
                "-------------|---------|-------------|-----|",
            ])
            for entry in best["top5_closest"]:
                lines.append(
                    f"| `{entry['config_id']}` "
                    f"| {entry['replacement_cost']} "
                    f"| {entry['cycle_life_mult']} "
                    f"| {entry['calendar_fade']} "
                    f"| {entry['discharge_value']} "
                    f"| {entry['capacity_value']} "
                    f"| {entry['subsidy']} "
                    f"| **{entry['net_incremental_revenue_eur']:.2f}** "
                    f"| {entry['soh_end']:.6f} |"
                )
            lines.append("")
        else:
            lines.extend([
                "## Best Configuration (by net_incremental_revenue_eur)",
                "",
                "| Parameter | Value |",
                "|-----------|-------|",
                f"| config_id | `{best['config_id']}` |",
                f"| replacement_cost (EUR/kWh) | {best['replacement_cost']} |",
                f"| cycle_life_multiplier | {best['cycle_life_mult']} |",
                f"| calendar_fade_rate | {best['calendar_fade']} |",
                f"| discharge_value (EUR/MWh) | {best['discharge_value']} |",
                f"| capacity_value (EUR/kW·year) | {best['capacity_value']} |",
                f"| fixed_subsidy (EUR/kWh) | {best['subsidy']} |",
                f"| **net_incremental_revenue_eur** | **{best['net_incremental_revenue_eur']:.2f}** |",
                f"| soh_end | {best['soh_end']:.6f} |",
                "",
            ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage22B — spread (price volatility) amplification
# ---------------------------------------------------------------------------


def run_stage22b_spread_amplification(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    amplification_factors: list[float] | None = None,
    capacity_kw: float,
    lookahead_hours: int = 24,
    degradation_config: dict[str, Any] | None = None,
    output_paths: dict[str, Path] | None = None,
) -> Stage22Result:
    """Re-run dispatch on amplified price volatility for 3 representative configs.

    Price is transformed as ``mean + (price - mean) × amp``, which preserves
    the long-term mean while scaling the intraday spread.  Results must be
    reported as a *price-volatility-amplification scenario*, NOT as a market
    forecast or true price increase.

    The three configs are hard-coded to cover the strategy spectrum:
      - Zero-cycle lower bound (λ=2.0)
      - Best active-cycling config from Stage22
      - Original Stage15-style aggressive config
    """
    from new_energy_sys.stage17_battery_degradation import _merge_degradation_config

    amplification_factors = amplification_factors or [1.0, 1.5, 2.0, 3.0]

    if degradation_config is None:
        degradation_config = _merge_degradation_config(config)

    base_storage = dict(config["storage"])
    base_shortfall = float(base_storage.get("shortfall_risk_penalty_eur_per_kwh", 0.001))
    base_terminal_target = float(base_storage.get("terminal_soc_target", 0.5))
    base_smooth_ramp = float(base_storage.get("smooth_power_ramp_limit_kw", 250.0))
    base_smooth_step = float(base_storage.get("smooth_action_step_kw", 250.0))
    base_action_penalty = float(base_storage.get("action_change_penalty_eur_per_kw", 0.0))
    base_terminal_penalty = float(base_storage.get("terminal_soc_penalty_eur_per_kwh", 0.02))

    # Three representative configs (hard-coded per the approved plan)
    representative_configs: list[dict[str, Any]] = [
        {
            "label": "zero_cycle_lower_bound",
            "capacity_multiplier": 1.0,
            "power_multiplier": 0.5,
            "soc_min": 0.2,
            "soc_max": 0.8,
            "lambda": 2.0,
            "min_spread_eur_mwh": 30.0,
            "effective_cycle_cost_eur_per_kwh": 2.0 * float(
                base_storage.get("cycle_cost_eur_per_kwh", 0.002)
            ),
        },
        {
            "label": "best_active_config",
            "capacity_multiplier": 1.0,
            "power_multiplier": 0.75,
            "soc_min": 0.1,
            "soc_max": 0.9,
            "lambda": 1.0,
            "min_spread_eur_mwh": 15.0,
            "effective_cycle_cost_eur_per_kwh": 1.0 * float(
                base_storage.get("cycle_cost_eur_per_kwh", 0.002)
            ),
        },
        {
            "label": "stage15_aggressive_baseline",
            "capacity_multiplier": 1.5,
            "power_multiplier": 1.0,
            "soc_min": 0.1,
            "soc_max": 0.9,
            "lambda": 0.0,
            "min_spread_eur_mwh": 0.0,
            "effective_cycle_cost_eur_per_kwh": 0.0,
        },
    ]

    # Build storage configs once
    for rep in representative_configs:
        sc = dict(base_storage)
        sc["capacity_kwh"] = float(base_storage["capacity_kwh"]) * rep["capacity_multiplier"]
        sc["max_charge_kw"] = float(base_storage["max_charge_kw"]) * rep["power_multiplier"]
        sc["max_discharge_kw"] = float(base_storage["max_discharge_kw"]) * rep["power_multiplier"]
        sc["soc_min"] = rep["soc_min"]
        sc["soc_max"] = rep["soc_max"]
        sc["soc_initial"] = float(
            np.clip(float(base_storage.get("soc_initial", 0.5)), rep["soc_min"], rep["soc_max"])
        )
        rep["storage_config"] = sc

    mean_price = float(feature_frame["price_eur_mwh"].mean())

    rows: list[dict[str, Any]] = []
    total = len(amplification_factors) * len(representative_configs)
    idx = 0
    for amp in amplification_factors:
        # Amplify price volatility, preserve mean
        amp_frame = feature_frame.copy()
        amp_frame["price_eur_mwh"] = mean_price + (
            feature_frame["price_eur_mwh"] - mean_price
        ) * amp

        dispatch_input = _prepare_dispatch_input(predictions, amp_frame, horizon_hours=24)

        for rep in representative_configs:
            idx += 1
            print(f"[stage22b spread] {idx}/{total}: amp={amp}, {rep['label']}")

            cid = (
                f"{rep['label']}_amp{_fmt_mult(amp)}"
            )

            try:
                dispatch_results = _simulate_fast_rolling_optimization(
                    dispatch_input,
                    rep["storage_config"],
                    capacity_kw=capacity_kw,
                    lookahead_hours=lookahead_hours,
                    cycle_cost_eur_per_kwh=rep["effective_cycle_cost_eur_per_kwh"],
                    shortfall_risk_penalty_eur_per_kwh=base_shortfall,
                    terminal_soc_target=base_terminal_target,
                    dispatch_mode="economic",
                    smooth_power_ramp_limit_kw=base_smooth_ramp,
                    smooth_action_step_kw=base_smooth_step,
                    action_change_penalty_eur_per_kw=base_action_penalty,
                    min_spread_eur_mwh=rep["min_spread_eur_mwh"],
                )
                constraints = _constraint_summary(
                    dispatch_results, rep["storage_config"]
                )
                constraints_ok = all([
                    constraints.get("soc_within_bounds", False),
                    constraints.get("charge_power_within_limit", False),
                    constraints.get("discharge_power_within_limit", False),
                    constraints.get("no_simultaneous_charge_discharge", False),
                    constraints.get("energy_balance_error_within_tolerance", False),
                ])
                base_rows = _prepare_scenario_rows(dispatch_results, "rolling_optimization")
                replay = _build_hourly_replay(
                    base_rows,
                    scenario_name=cid,
                    mode="rainflow",
                    storage_config=rep["storage_config"],
                    degradation_config=degradation_config,
                )
                m = _metrics_for_scenario(replay, storage_config=rep["storage_config"])
                rows.append({
                    "config_id": cid,
                    "label": rep["label"],
                    "amplification_factor": amp,
                    "capacity_multiplier": rep["capacity_multiplier"],
                    "power_multiplier": rep["power_multiplier"],
                    "soc_min": rep["soc_min"],
                    "soc_max": rep["soc_max"],
                    "lambda": rep["lambda"],
                    "gross_incremental_revenue_eur": float(m["gross_incremental_revenue_eur"]),
                    "degradation_cost_eur": float(m["degradation_cost_eur"]),
                    "net_incremental_revenue_eur": float(m["net_incremental_revenue_eur"]),
                    "soh_end": float(m["soh_end"]),
                    "equivalent_full_cycles": float(m["equivalent_full_cycles"]),
                    "max_cycle_depth_dod": float(m["max_cycle_depth_dod"]),
                    "total_charge_kwh": float(m["total_charge_kwh"]),
                    "total_discharge_kwh": float(m["total_discharge_kwh"]),
                    "rainflow_cycle_count": float(m["rainflow_cycle_count"]),
                    "constraints_passed": constraints_ok,
                })
            except Exception as exc:
                rows.append({
                    "config_id": cid,
                    "label": rep["label"],
                    "amplification_factor": amp,
                    "gross_incremental_revenue_eur": float("-inf"),
                    "net_incremental_revenue_eur": float("-inf"),
                    "constraints_passed": False,
                    "exception": str(exc),
                })

    metrics_df = pd.DataFrame(rows)
    report: dict[str, Any] = {
        "stage": "stage22b_spread_amplification",
        "scope_note": (
            "Price transformation: mean + (price - mean) × amp. "
            "Mean price preserved; only volatility amplified. "
            "This is a PRICE-VOLATILITY scenario, NOT a market forecast."
        ),
        "amplification_factors": [float(a) for a in amplification_factors],
        "mean_price_eur_mwh": float(mean_price),
        "representative_configs": [
            {
                "label": r["label"],
                "capacity_multiplier": r["capacity_multiplier"],
                "power_multiplier": r["power_multiplier"],
                "soc_min": r["soc_min"],
                "soc_max": r["soc_max"],
                "lambda": r["lambda"],
                "min_spread_eur_mwh": r["min_spread_eur_mwh"],
            }
            for r in representative_configs
        ],
        "results": [
            {
                "config_id": str(r["config_id"]),
                "label": str(r.get("label", "")),
                "amplification_factor": float(r["amplification_factor"]),
                "gross_incremental_revenue_eur": float(r.get("gross_incremental_revenue_eur", float("nan"))),
                "degradation_cost_eur": float(r.get("degradation_cost_eur", float("nan"))),
                "net_incremental_revenue_eur": float(r.get("net_incremental_revenue_eur", float("nan"))),
                "soh_end": float(r.get("soh_end", float("nan"))),
                "equivalent_full_cycles": float(r.get("equivalent_full_cycles", float("nan"))),
            }
            for _, r in metrics_df.iterrows()
        ],
    }

    if output_paths:
        metrics_csv = output_paths.get("metrics_csv")
        report_json = output_paths.get("report_json")
        report_md = output_paths.get("report_md")
        if metrics_csv:
            metrics_csv.parent.mkdir(parents=True, exist_ok=True)
            metrics_df.to_csv(metrics_csv, index=False)
            print(f"[stage22b spread] metrics written to {metrics_csv}")
        if report_json:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(
                json.dumps(_json_safe(report), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[stage22b spread] JSON report written to {report_json}")
        if report_md:
            report_md.parent.mkdir(parents=True, exist_ok=True)
            report_md.write_text(
                _build_spread_amplification_md(report, metrics_df), encoding="utf-8"
            )
            print(f"[stage22b spread] Markdown report written to {report_md}")

    return Stage22Result(metrics=metrics_df, report=report)


def _build_spread_amplification_md(
    report: dict[str, Any], metrics: pd.DataFrame
) -> str:
    """Render a Markdown summary of the spread amplification experiment."""
    lines: list[str] = [
        "# Stage22B — Price Volatility Amplification Experiment",
        "",
        "## Scope Note",
        "",
        report["scope_note"],
        "",
        f"Mean price: {report['mean_price_eur_mwh']:.2f} EUR/MWh",
        f"Amplification factors: {report['amplification_factors']}",
        "",
        "## Results",
        "",
        "| config | amp | gross_inc | deg_cost | **net_inc** | SOH | EFC |",
        "|--------|-----|-----------|----------|-------------|-----|-----|",
    ]
    for _, row in metrics.iterrows():
        lines.append(
            f"| {row.get('label', row['config_id'])} "
            f"| {float(row['amplification_factor'])} "
            f"| {float(row.get('gross_incremental_revenue_eur', float('nan'))):.2f} "
            f"| {float(row.get('degradation_cost_eur', float('nan'))):.2f} "
            f"| **{float(row.get('net_incremental_revenue_eur', float('nan'))):.2f}** "
            f"| {float(row.get('soh_end', float('nan'))):.6f} "
            f"| {float(row.get('equivalent_full_cycles', float('nan'))):.2f} |"
        )
    lines.append("")
    return "\n".join(lines)
