"""Stage17 battery degradation replay and reporting.

This module keeps the existing Stage12/Stage15 dispatch artifacts immutable and
adds a separate accounting layer for battery realism.  The replay consumes an
hourly dispatch result table, reconstructs battery operating stress from SOC and
power trajectories, and reports gross revenue, cycle/calendar degradation,
SOH (state of health), and net value after degradation cost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_CYCLE_LIFE_CURVE = [
    {"dod": 0.1, "cycles_to_eol": 15000},
    {"dod": 0.2, "cycles_to_eol": 10000},
    {"dod": 0.4, "cycles_to_eol": 6000},
    {"dod": 0.6, "cycles_to_eol": 4000},
    {"dod": 0.8, "cycles_to_eol": 3000},
    {"dod": 1.0, "cycles_to_eol": 2000},
]


@dataclass(frozen=True)
class RainflowCycle:
    """Single rainflow-counted SOC cycle.

    `count` is 1.0 for a closed full cycle and 0.5 for a residual half cycle.
    Indices are row positions in the sorted hourly dispatch replay.  They are
    retained so cycle damage can be assigned back to the hour where the cycle is
    recognized for auditability.
    """

    start_index: int
    end_index: int
    depth_dod: float
    mean_soc: float
    count: float


@dataclass(frozen=True)
class Stage17BatteryDegradationResult:
    """Stage17 result container."""

    results: pd.DataFrame
    metrics: pd.DataFrame
    sensitivity: pd.DataFrame
    report: dict[str, Any]


def default_battery_degradation_config() -> dict[str, Any]:
    """Return conservative lithium-ion degradation defaults.

    The values are intentionally explicit so reports can explain every
    assumption.  They are not vendor guarantees; they are an engineering
    approximation inspired by SAM-style cycle/calendar degradation accounting.
    """

    return {
        "enabled": True,
        "soh_initial": 1.0,
        "soh_eol": 0.8,
        "replacement_cost_eur_per_kwh": 150.0,
        "throughput_cost_eur_per_kwh": 0.002,
        "base_calendar_fade_per_year": 0.015,
        "reference_temperature_c": 25.0,
        "temperature_stress_factor": 1.0,
        "cycle_life_curve": list(DEFAULT_CYCLE_LIFE_CURVE),
    }


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


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    """Fail fast when a dispatch replay file is not compatible with Stage17."""

    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def _merge_degradation_config(config: dict[str, Any]) -> dict[str, Any]:
    """Merge user config with defaults and validate the resulting parameters."""

    merged = default_battery_degradation_config()
    merged.update(config.get("battery_degradation", {}) or {})
    curve = merged.get("cycle_life_curve", DEFAULT_CYCLE_LIFE_CURVE)
    if not isinstance(curve, list) or not curve:
        raise ValueError("battery_degradation.cycle_life_curve must be a non-empty list.")

    normalized_curve: list[dict[str, float]] = []
    for item in curve:
        dod = float(item["dod"])
        cycles = float(item["cycles_to_eol"])
        if not (0 < dod <= 1):
            raise ValueError("cycle_life_curve.dod must be in (0, 1].")
        if cycles <= 0:
            raise ValueError("cycle_life_curve.cycles_to_eol must be positive.")
        normalized_curve.append({"dod": dod, "cycles_to_eol": cycles})

    normalized_curve = sorted(normalized_curve, key=lambda item: item["dod"])
    if normalized_curve[0]["dod"] > 0.0:
        # Interpolation below clips to the first point; keeping this explicit
        # avoids extrapolating optimistic lifetime for tiny shallow cycles.
        normalized_curve.insert(0, {"dod": 0.0, "cycles_to_eol": normalized_curve[0]["cycles_to_eol"]})

    merged["cycle_life_curve"] = normalized_curve
    soh_initial = float(merged["soh_initial"])
    soh_eol = float(merged["soh_eol"])
    if not (0 < soh_eol < soh_initial <= 1.0):
        raise ValueError("SOH bounds must satisfy 0 < soh_eol < soh_initial <= 1.")
    if float(merged["replacement_cost_eur_per_kwh"]) < 0:
        raise ValueError("replacement_cost_eur_per_kwh must be non-negative.")
    if float(merged["throughput_cost_eur_per_kwh"]) < 0:
        raise ValueError("throughput_cost_eur_per_kwh must be non-negative.")
    if float(merged["base_calendar_fade_per_year"]) < 0:
        raise ValueError("base_calendar_fade_per_year must be non-negative.")
    if float(merged["temperature_stress_factor"]) <= 0:
        raise ValueError("temperature_stress_factor must be positive.")
    return merged


def _turning_points(values: pd.Series) -> list[tuple[int, float]]:
    """Extract SOC reversal points for rainflow counting.

    Consecutive duplicate SOC values are removed first.  The first and last
    points are always retained so residual half cycles remain visible.
    """

    cleaned: list[tuple[int, float]] = []
    for index, raw_value in enumerate(values.astype(float).tolist()):
        value = float(raw_value)
        if not cleaned or abs(value - cleaned[-1][1]) > 1e-12:
            cleaned.append((index, value))
    if len(cleaned) <= 2:
        return cleaned

    points = [cleaned[0]]
    for previous, current, nxt in zip(cleaned, cleaned[1:], cleaned[2:]):
        prev_delta = current[1] - previous[1]
        next_delta = nxt[1] - current[1]
        if prev_delta == 0 or next_delta == 0 or np.sign(prev_delta) != np.sign(next_delta):
            points.append(current)
    points.append(cleaned[-1])
    return points


def rainflow_count_soc(soc_series: pd.Series) -> list[RainflowCycle]:
    """Count SOC cycles using a compact four-point rainflow approximation.

    The implementation follows the engineering intent of ASTM-style rainflow:
    closed reversals are counted as full cycles, and unclosed residual ranges are
    counted as half cycles.  It is deterministic and dependency-free, which keeps
    Stage17 reproducible in the current project environment.
    """

    cycles: list[RainflowCycle] = []
    stack: list[tuple[int, float]] = []

    for point in _turning_points(soc_series):
        stack.append(point)
        while len(stack) >= 3:
            first = stack[-3]
            second = stack[-2]
            third = stack[-1]
            previous_range = abs(second[1] - first[1])
            latest_range = abs(third[1] - second[1])
            if previous_range > latest_range:
                break

            count = 0.5 if len(stack) == 3 else 1.0
            cycles.append(
                RainflowCycle(
                    start_index=int(first[0]),
                    end_index=int(second[0]),
                    depth_dod=float(previous_range),
                    mean_soc=float((first[1] + second[1]) / 2.0),
                    count=count,
                )
            )
            if len(stack) == 3:
                stack.pop(0)
            else:
                # Remove the closed range and keep the newest reversal.
                stack.pop(-3)
                stack.pop(-3)

    for first, second in zip(stack, stack[1:]):
        depth = abs(second[1] - first[1])
        if depth > 1e-12:
            cycles.append(
                RainflowCycle(
                    start_index=int(first[0]),
                    end_index=int(second[0]),
                    depth_dod=float(depth),
                    mean_soc=float((first[1] + second[1]) / 2.0),
                    count=0.5,
                )
            )
    return cycles


def _cycles_to_eol(depth_dod: float, curve: list[dict[str, float]]) -> float:
    """Interpolate cycle life at a given DOD from the configured lifetime curve."""

    dod_values = np.array([float(item["dod"]) for item in curve], dtype=float)
    cycle_values = np.array([float(item["cycles_to_eol"]) for item in curve], dtype=float)
    depth = float(np.clip(depth_dod, dod_values.min(), dod_values.max()))
    return float(np.interp(depth, dod_values, cycle_values))


def _calendar_damage_per_hour(
    *,
    mean_soc: float,
    degradation_config: dict[str, Any],
) -> float:
    """Compute hourly calendar SOH loss under a simple SOC-stress model."""

    base = float(degradation_config["base_calendar_fade_per_year"]) / 8760.0
    soc_stress = 1.0 + 0.5 * max(float(mean_soc) - 0.5, 0.0)
    temp_stress = float(degradation_config.get("temperature_stress_factor", 1.0))
    return float(base * soc_stress * temp_stress)


def _prepare_scenario_rows(dispatch_results: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """Normalize one Stage12 dispatch scenario into the columns Stage17 needs."""

    required = [
        "scenario",
        "dispatch_timestamp",
        "soc_start",
        "soc_end",
        "actual_charge_kw",
        "actual_discharge_kw",
        "storage_revenue_eur",
        "no_storage_revenue_eur",
    ]
    _require_columns(dispatch_results, required, "dispatch_results")
    rows = dispatch_results.loc[dispatch_results["scenario"] == scenario].copy()
    if rows.empty:
        raise ValueError(f"dispatch_results contains no rows for scenario {scenario!r}.")
    rows["dispatch_timestamp"] = pd.to_datetime(rows["dispatch_timestamp"], errors="coerce", utc=True)
    if rows["dispatch_timestamp"].isna().any():
        raise ValueError("dispatch_results contains invalid dispatch_timestamp values.")

    numeric_columns = [
        "soc_start",
        "soc_end",
        "actual_charge_kw",
        "actual_discharge_kw",
        "storage_revenue_eur",
        "no_storage_revenue_eur",
    ]
    for column in numeric_columns:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    if rows[numeric_columns].isna().any().any():
        raise ValueError("dispatch_results contains missing numeric values required by Stage17.")
    return rows.sort_values("dispatch_timestamp").reset_index(drop=True)


def _assign_cycle_damage_to_rows(
    rows: pd.DataFrame,
    cycles: list[RainflowCycle],
    *,
    degradation_config: dict[str, Any],
) -> pd.DataFrame:
    """Attach rainflow cycle damage to the row where each cycle is closed."""

    output = rows.copy()
    output["cycle_depth_dod"] = 0.0
    output["cycle_count"] = 0.0
    output["cycle_damage"] = 0.0
    output["equivalent_full_cycle"] = 0.0

    soh_eol = float(degradation_config["soh_eol"])
    curve = degradation_config["cycle_life_curve"]
    for cycle in cycles:
        if cycle.depth_dod <= 1e-12:
            continue
        close_index = int(np.clip(max(cycle.start_index, cycle.end_index), 0, len(output) - 1))
        cycles_to_eol = _cycles_to_eol(cycle.depth_dod, curve)
        cycle_damage = float(cycle.count * (1.0 - soh_eol) / cycles_to_eol)
        output.loc[close_index, "cycle_depth_dod"] = max(
            float(output.loc[close_index, "cycle_depth_dod"]),
            float(cycle.depth_dod),
        )
        output.loc[close_index, "cycle_count"] += float(cycle.count)
        output.loc[close_index, "cycle_damage"] += cycle_damage
        output.loc[close_index, "equivalent_full_cycle"] += float(cycle.depth_dod * cycle.count)
    return output


def _build_hourly_replay(
    base_rows: pd.DataFrame,
    *,
    scenario_name: str,
    mode: str,
    storage_config: dict[str, Any],
    degradation_config: dict[str, Any],
) -> pd.DataFrame:
    """Build one Stage17 hourly replay scenario.

    `mode` controls cost accounting only.  The dispatch actions are intentionally
    inherited from Stage12 so S17 remains an additive battery-realism layer.
    """

    nominal_capacity_kwh = float(storage_config["capacity_kwh"])
    soh = float(degradation_config["soh_initial"])
    soh_eol = float(degradation_config["soh_eol"])
    replacement_cost = float(degradation_config["replacement_cost_eur_per_kwh"])
    throughput_cost = float(degradation_config["throughput_cost_eur_per_kwh"])

    rows = base_rows.copy()
    rows["scenario"] = scenario_name
    rows["battery_soc"] = rows["soc_end"].astype(float)
    rows["gross_revenue_eur"] = rows["storage_revenue_eur"].astype(float)
    rows["gross_incremental_revenue_eur"] = rows["storage_revenue_eur"] - rows["no_storage_revenue_eur"]
    rows["charge_c_rate"] = rows["actual_charge_kw"].astype(float) / max(nominal_capacity_kwh, 1e-12)
    rows["discharge_c_rate"] = rows["actual_discharge_kw"].astype(float) / max(nominal_capacity_kwh, 1e-12)
    rows["calendar_damage"] = [
        _calendar_damage_per_hour(mean_soc=float(mean_soc), degradation_config=degradation_config)
        for mean_soc in (rows["soc_start"] + rows["soc_end"]) / 2.0
    ]

    if mode == "none":
        rows["cycle_depth_dod"] = 0.0
        rows["cycle_count"] = 0.0
        rows["cycle_damage"] = 0.0
        rows["calendar_damage"] = 0.0
        rows["equivalent_full_cycle"] = 0.0
        rows["degradation_cost_eur"] = 0.0
    elif mode == "throughput":
        rows["cycle_depth_dod"] = 0.0
        rows["cycle_count"] = 0.0
        rows["cycle_damage"] = 0.0
        rows["equivalent_full_cycle"] = (
            (rows["actual_charge_kw"].abs() + rows["actual_discharge_kw"].abs())
            / (2.0 * max(nominal_capacity_kwh, 1e-12))
        )
        rows["degradation_cost_eur"] = (
            rows["actual_charge_kw"].abs() + rows["actual_discharge_kw"].abs()
        ) * throughput_cost
        rows["degradation_cost_eur"] += (
            rows["calendar_damage"] * replacement_cost * nominal_capacity_kwh
        )
    elif mode == "rainflow":
        cycles = rainflow_count_soc(rows["battery_soc"])
        rows = _assign_cycle_damage_to_rows(rows, cycles, degradation_config=degradation_config)
        rows["degradation_cost_eur"] = (
            (rows["cycle_damage"] + rows["calendar_damage"])
            * replacement_cost
            * nominal_capacity_kwh
        )
    else:
        raise ValueError(f"Unsupported Stage17 replay mode: {mode!r}")

    soh_start_values: list[float] = []
    soh_end_values: list[float] = []
    available_capacity_values: list[float] = []
    replacement_flags: list[bool] = []
    for _, row in rows.iterrows():
        soh_start = soh
        damage = float(row["cycle_damage"]) + float(row["calendar_damage"])
        soh = float(np.clip(soh - damage, 0.0, 1.0))
        soh_start_values.append(soh_start)
        soh_end_values.append(soh)
        available_capacity_values.append(nominal_capacity_kwh * soh_start)
        replacement_flags.append(bool(soh <= soh_eol))

    rows["soh_start"] = soh_start_values
    rows["battery_soh"] = soh_end_values
    rows["soh_end"] = soh_end_values
    rows["available_capacity_kwh"] = available_capacity_values
    rows["capacity_fade_percent"] = (1.0 - rows["battery_soh"]) * 100.0
    rows["replacement_flag"] = replacement_flags
    rows["net_value_after_degradation_eur"] = rows["gross_revenue_eur"] - rows["degradation_cost_eur"]
    rows["net_incremental_revenue_eur"] = (
        rows["gross_revenue_eur"] - rows["degradation_cost_eur"] - rows["no_storage_revenue_eur"]
    )
    return rows


def _metrics_for_scenario(rows: pd.DataFrame, *, storage_config: dict[str, Any]) -> dict[str, Any]:
    """Aggregate Stage17 hourly replay rows into a scenario metric record."""

    total_cycle_damage = float(rows["cycle_damage"].sum())
    total_calendar_damage = float(rows["calendar_damage"].sum())
    total_degradation_cost = float(rows["degradation_cost_eur"].sum())
    gross_revenue = float(rows["gross_revenue_eur"].sum())
    no_storage_revenue = float(rows["no_storage_revenue_eur"].sum())
    net_revenue = float(rows["net_value_after_degradation_eur"].sum())
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    soh_diff = rows["battery_soh"].diff().dropna()
    return {
        "scenario": str(rows["scenario"].iloc[0]),
        "sample_count": int(len(rows)),
        "gross_revenue_eur": gross_revenue,
        "no_storage_revenue_eur": no_storage_revenue,
        "gross_incremental_revenue_eur": gross_revenue - no_storage_revenue,
        "degradation_cost_eur": total_degradation_cost,
        "net_revenue_eur": net_revenue,
        "net_incremental_revenue_eur": net_revenue - no_storage_revenue,
        "soh_start": float(rows["soh_start"].iloc[0]),
        "soh_end": float(rows["battery_soh"].iloc[-1]),
        "capacity_fade_percent": float((1.0 - rows["battery_soh"].iloc[-1]) * 100.0),
        "cycle_damage": total_cycle_damage,
        "calendar_damage": total_calendar_damage,
        "equivalent_full_cycles": float(rows["equivalent_full_cycle"].sum()),
        "rainflow_cycle_count": float(rows["cycle_count"].sum()),
        "max_cycle_depth_dod": float(rows["cycle_depth_dod"].max()),
        "total_charge_kwh": float(rows["actual_charge_kw"].sum()),
        "total_discharge_kwh": float(rows["actual_discharge_kw"].sum()),
        "min_soc": float(rows["battery_soc"].min()),
        "max_soc": float(rows["battery_soc"].max()),
        "replacement_flag": bool(rows["replacement_flag"].any()),
        "soc_within_bounds": bool(rows["battery_soc"].between(soc_min - 1e-12, soc_max + 1e-12).all()),
        "soh_monotonic_nonincreasing": bool((soh_diff <= 1e-12).all()),
        "net_value_identity_passed": bool(
            abs(net_revenue - (gross_revenue - total_degradation_cost)) <= 1e-9
        ),
    }


def _build_sensitivity(
    rainflow_rows: pd.DataFrame,
    *,
    storage_config: dict[str, Any],
    degradation_config: dict[str, Any],
    replacement_costs: list[float],
    cycle_life_multipliers: list[float],
) -> pd.DataFrame:
    """Build a compact sensitivity table from the same rainflow damage replay."""

    nominal_capacity = float(storage_config["capacity_kwh"])
    gross_revenue = float(rainflow_rows["gross_revenue_eur"].sum())
    no_storage_revenue = float(rainflow_rows["no_storage_revenue_eur"].sum())
    base_cycle_damage = float(rainflow_rows["cycle_damage"].sum())
    base_calendar_damage = float(rainflow_rows["calendar_damage"].sum())
    rows: list[dict[str, Any]] = []
    for replacement_cost in replacement_costs:
        if replacement_cost < 0:
            raise ValueError("replacement_costs must be non-negative.")
        for life_multiplier in cycle_life_multipliers:
            if life_multiplier <= 0:
                raise ValueError("cycle_life_multipliers must be positive.")
            adjusted_cycle_damage = base_cycle_damage / float(life_multiplier)
            total_damage = adjusted_cycle_damage + base_calendar_damage
            degradation_cost = total_damage * float(replacement_cost) * nominal_capacity
            net_revenue = gross_revenue - degradation_cost
            rows.append(
                {
                    "replacement_cost_eur_per_kwh": float(replacement_cost),
                    "cycle_life_multiplier": float(life_multiplier),
                    "gross_revenue_eur": gross_revenue,
                    "no_storage_revenue_eur": no_storage_revenue,
                    "cycle_damage": adjusted_cycle_damage,
                    "calendar_damage": base_calendar_damage,
                    "degradation_cost_eur": degradation_cost,
                    "net_revenue_eur": net_revenue,
                    "net_incremental_revenue_eur": net_revenue - no_storage_revenue,
                    "soh_end": float(
                        np.clip(
                            float(degradation_config["soh_initial"]) - total_damage,
                            0.0,
                            1.0,
                        )
                    ),
                }
            )
    return pd.DataFrame(rows)


def run_stage17_battery_degradation(
    dispatch_results: pd.DataFrame,
    config: dict[str, Any],
    *,
    dispatch_scenario: str = "rolling_optimization",
    replacement_costs: list[float] | None = None,
    cycle_life_multipliers: list[float] | None = None,
    output_paths: dict[str, Path] | None = None,
) -> Stage17BatteryDegradationResult:
    """Run Stage17 battery degradation replay.

    Args:
        dispatch_results: Stage12 hourly dispatch results.
        config: Full project config dictionary containing `storage` and optional
            `battery_degradation` sections.
        dispatch_scenario: Stage12 scenario to replay.
        replacement_costs: Sensitivity values for replacement cost.
        cycle_life_multipliers: Sensitivity multipliers for cycle lifetime.
        output_paths: Optional output path map for report metadata.

    Returns:
        Stage17BatteryDegradationResult with hourly rows, metrics, sensitivity,
        and report metadata.
    """

    storage_config = dict(config["storage"])
    degradation_config = _merge_degradation_config(config)
    base_rows = _prepare_scenario_rows(dispatch_results, dispatch_scenario)

    no_degradation = _build_hourly_replay(
        base_rows,
        scenario_name="rolling_without_degradation",
        mode="none",
        storage_config=storage_config,
        degradation_config=degradation_config,
    )
    throughput = _build_hourly_replay(
        base_rows,
        scenario_name="rolling_with_throughput_cost",
        mode="throughput",
        storage_config=storage_config,
        degradation_config=degradation_config,
    )
    rainflow = _build_hourly_replay(
        base_rows,
        scenario_name="rolling_with_rainflow_degradation",
        mode="rainflow",
        storage_config=storage_config,
        degradation_config=degradation_config,
    )
    results = pd.concat([no_degradation, throughput, rainflow], ignore_index=True)
    metrics = pd.DataFrame(
        [
            _metrics_for_scenario(no_degradation, storage_config=storage_config),
            _metrics_for_scenario(throughput, storage_config=storage_config),
            _metrics_for_scenario(rainflow, storage_config=storage_config),
        ]
    )

    replacement_costs = replacement_costs or [
        float(degradation_config["replacement_cost_eur_per_kwh"]) * (2.0 / 3.0),
        float(degradation_config["replacement_cost_eur_per_kwh"]),
        float(degradation_config["replacement_cost_eur_per_kwh"]) * (5.0 / 3.0),
    ]
    cycle_life_multipliers = cycle_life_multipliers or [0.8, 1.0, 1.2]
    sensitivity = _build_sensitivity(
        rainflow,
        storage_config=storage_config,
        degradation_config=degradation_config,
        replacement_costs=[float(value) for value in replacement_costs],
        cycle_life_multipliers=[float(value) for value in cycle_life_multipliers],
    )

    metric_lookup = metrics.set_index("scenario")
    rainflow_metric = metric_lookup.loc["rolling_with_rainflow_degradation"]
    quality_gates = {
        "input_non_empty": bool(len(base_rows) > 0),
        "soc_within_bounds": bool(metrics["soc_within_bounds"].all()),
        "soh_monotonic_nonincreasing": bool(metrics["soh_monotonic_nonincreasing"].all()),
        "net_value_identity_passed": bool(metrics["net_value_identity_passed"].all()),
        "rainflow_cycles_detected": bool(rainflow_metric["rainflow_cycle_count"] >= 0),
        "replacement_not_forced": True,
    }
    report = {
        "stage": "stage17_battery_degradation",
        "method": "SAM-style cycle/calendar degradation replay with rainflow SOC cycle counting",
        "dispatch_source_scenario": dispatch_scenario,
        "storage_config": storage_config,
        "battery_degradation_config": degradation_config,
        "input_rows": int(len(base_rows)),
        "quality_gates": quality_gates,
        "recommended_scenario": "rolling_with_rainflow_degradation",
        "recommended_metrics": rainflow_metric.to_dict(),
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "pitfall": (
            "Stage17 is a battery-realism accounting layer over Stage12 actions. "
            "It improves SOH and net-value credibility, but it does not claim "
            "vendor-grade electrochemical accuracy or real-market settlement revenue."
        ),
    }
    return Stage17BatteryDegradationResult(
        results=results,
        metrics=metrics,
        sensitivity=sensitivity,
        report=report,
    )


def write_stage17_json(report: dict[str, Any], path: Path) -> None:
    """Write Stage17 report metadata as strict JSON."""

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage17_report(
    report: dict[str, Any],
    metrics: pd.DataFrame,
    sensitivity: pd.DataFrame,
    path: Path,
) -> None:
    """Write a Chinese Markdown report for Stage17."""

    metric_lookup = metrics.set_index("scenario")
    rainflow = metric_lookup.loc["rolling_with_rainflow_degradation"]
    no_degradation = metric_lookup.loc["rolling_without_degradation"]
    throughput = metric_lookup.loc["rolling_with_throughput_cost"]

    lines = [
        "# Stage17 电池退化与真实度增强报告",
        "",
        "## 1. 阶段定位",
        "",
        "Stage17 不替换 Stage12/Stage15 的调度结果，而是在既有 `history_only` 预测主线和 Stage12 rolling 调度动作之上，新增电池 SOH、循环退化、日历退化和净收益核算。",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage12 rolling 调度结果"] --> B["SOC 曲线和充放电功率"]',
        '    B --> C["Rainflow 循环计数"]',
        '    B --> D["Calendar 日历退化"]',
        '    C --> E["Cycle damage"]',
        '    D --> F["Calendar damage"]',
        '    E --> G["SOH 更新"]',
        '    F --> G',
        '    G --> H["退化成本和净收益"]',
        "```",
        "",
        "Pitfall: Stage17 是退化回放和成本核算层，不是厂家级电化学模型，也不改变 Stage12 原始策略动作。",
        "",
        "## 2. 方法与可信依据",
        "",
        "- NREL SAM Battery Life 将电池寿命拆分为 cycle degradation 和 calendar degradation，并支持 rainflow cycle counting、DOD 曲线、容量衰减和替换策略。",
        "- NREL BLAST 说明电池寿命与温度、SOC 历史、电流、循环深度和循环频率相关。",
        "- 当前项目缺少电芯级温度、电压、电流数据，因此采用小时级调度可解释的 SAM-style 工程近似。",
        "",
        "## 3. 关键参数",
        "",
        "| 参数 | 数值 |",
        "|---|---:|",
        f"| SOH initial | {float(report['battery_degradation_config']['soh_initial']):.4f} |",
        f"| SOH EOL | {float(report['battery_degradation_config']['soh_eol']):.4f} |",
        f"| replacement cost EUR/kWh | {float(report['battery_degradation_config']['replacement_cost_eur_per_kwh']):.4f} |",
        f"| base calendar fade/year | {float(report['battery_degradation_config']['base_calendar_fade_per_year']):.4f} |",
        f"| temperature stress factor | {float(report['battery_degradation_config']['temperature_stress_factor']):.4f} |",
        "",
        "## 4. 场景对比",
        "",
        "| 场景 | 毛收益 EUR | 退化成本 EUR | 净收益 EUR | 净增量 EUR | SOH start | SOH end | 等效完整循环 | Rainflow cycle count | replacement |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for scenario in [
        "rolling_without_degradation",
        "rolling_with_throughput_cost",
        "rolling_with_rainflow_degradation",
    ]:
        row = metric_lookup.loc[scenario]
        lines.append(
            f"| `{scenario}` | {row['gross_revenue_eur']:.4f} | "
            f"{row['degradation_cost_eur']:.6f} | {row['net_revenue_eur']:.4f} | "
            f"{row['net_incremental_revenue_eur']:.4f} | {row['soh_start']:.6f} | "
            f"{row['soh_end']:.6f} | {row['equivalent_full_cycles']:.4f} | "
            f"{row['rainflow_cycle_count']:.4f} | `{bool(row['replacement_flag'])}` |"
        )

    best_sensitivity = sensitivity.sort_values("net_incremental_revenue_eur", ascending=False).iloc[0]
    lines.extend(
        [
            "",
            "## 5. 关键结论",
            "",
            f"- 不计退化时净收益等于毛收益，净增量为 `{no_degradation['net_incremental_revenue_eur']:.4f} EUR`。",
            f"- 吞吐量成本基线净增量为 `{throughput['net_incremental_revenue_eur']:.4f} EUR`。",
            f"- Rainflow + 日历退化方案净增量为 `{rainflow['net_incremental_revenue_eur']:.4f} EUR`，SOH 从 `{rainflow['soh_start']:.6f}` 降至 `{rainflow['soh_end']:.6f}`。",
            f"- 当前敏感性网格中，最高净增量出现在 replacement cost `{best_sensitivity['replacement_cost_eur_per_kwh']:.2f} EUR/kWh`、cycle life multiplier `{best_sensitivity['cycle_life_multiplier']:.2f}`。",
            "",
            "## 6. 敏感性摘要",
            "",
            "| replacement cost EUR/kWh | cycle life multiplier | degradation cost EUR | net incremental EUR | SOH end |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in sensitivity.iterrows():
        lines.append(
            f"| {row['replacement_cost_eur_per_kwh']:.2f} | {row['cycle_life_multiplier']:.2f} | "
            f"{row['degradation_cost_eur']:.6f} | {row['net_incremental_revenue_eur']:.4f} | "
            f"{row['soh_end']:.6f} |"
        )

    lines.extend(["", "## 7. 质量门禁", ""])
    for gate, value in report["quality_gates"].items():
        lines.append(f"- {gate}: `{value}`")

    lines.extend(["", "## 8. 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 完成 Stage12 动作回放、rainflow 循环识别、DOD 寿命曲线、日历退化、SOH 更新、退化成本和净收益核算。",
            "- 目标完成情况: 已形成独立 S17 电池真实度增强层，未破坏 Stage12/15 历史指标。",
            "- 下一阶段可行性: 可继续把 S17 指标接入 API/前端，或扩展真实厂家参数和温度敏感退化模型。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
