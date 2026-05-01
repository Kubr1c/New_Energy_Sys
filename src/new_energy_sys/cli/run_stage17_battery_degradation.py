"""Command line entrypoint for Stage17 battery degradation replay."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage17_battery_degradation import (
    run_stage17_battery_degradation,
    write_stage17_json,
    write_stage17_report,
)


def _parse_float_list(value: str | None, *, name: str) -> list[float] | None:
    """Parse comma-separated numeric CLI values.

    A strict parser is used so a typo such as `150,,250` fails immediately
    instead of silently changing the sensitivity grid.
    """

    if value is None or value.strip() == "":
        return None
    parsed: list[float] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            raise ValueError(f"{name} contains an empty item: {value!r}")
        try:
            parsed.append(float(item))
        except ValueError as exc:
            raise ValueError(f"{name} must contain numeric values, got {item!r}") from exc
    return parsed


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """Resolve absolute paths and paths relative to the project root."""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def parse_args() -> argparse.Namespace:
    """Parse Stage17 CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Run Stage17 battery degradation replay over Stage12 dispatch results."
    )
    parser.add_argument("--config", required=True, help="Project JSON config path.")
    parser.add_argument(
        "--dispatch-input",
        required=True,
        help="Stage12 dispatch results CSV path, usually stage12_storage_rolling_optimization_results.csv.",
    )
    parser.add_argument(
        "--dispatch-scenario",
        default="rolling_optimization",
        help="Scenario inside the dispatch results to replay.",
    )
    parser.add_argument(
        "--replacement-costs",
        default=None,
        help="Optional comma-separated replacement cost sensitivity values in EUR/kWh.",
    )
    parser.add_argument(
        "--cycle-life-multipliers",
        default=None,
        help="Optional comma-separated multipliers applied to the DOD cycle-life curve.",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage17_battery_degradation",
        help="Output filename prefix written into processed_dir.",
    )
    return parser.parse_args()


def main() -> None:
    """Run Stage17 and write hourly replay, metrics, sensitivity, JSON, and Markdown."""

    args = parse_args()
    runtime = load_config(args.config)
    dispatch_input_path = _resolve_project_path(runtime.root_dir, args.dispatch_input)
    dispatch_results = pd.read_csv(dispatch_input_path)

    output_paths = {
        "results_csv": runtime.processed_dir / f"{args.output_prefix}_replay.csv",
        "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_metrics.csv",
        "sensitivity_csv": runtime.processed_dir / f"{args.output_prefix}_sensitivity_metrics.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
    }
    result = run_stage17_battery_degradation(
        dispatch_results,
        runtime.raw,
        dispatch_scenario=args.dispatch_scenario,
        replacement_costs=_parse_float_list(args.replacement_costs, name="replacement_costs"),
        cycle_life_multipliers=_parse_float_list(args.cycle_life_multipliers, name="cycle_life_multipliers"),
        output_paths=output_paths,
    )

    result.results.to_csv(output_paths["results_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    result.sensitivity.to_csv(output_paths["sensitivity_csv"], index=False)
    write_stage17_json(result.report, output_paths["report_json"])
    write_stage17_report(result.report, result.metrics, result.sensitivity, output_paths["report_md"])

    print(f"Stage17 replay: {output_paths['results_csv']}")
    print(f"Stage17 metrics: {output_paths['metrics_csv']}")
    print(f"Stage17 sensitivity: {output_paths['sensitivity_csv']}")
    print(f"Stage17 report JSON: {output_paths['report_json']}")
    print(f"Stage17 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
