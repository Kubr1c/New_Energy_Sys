"""Stage23 scenario dispatch showcase CLI.

Entry point: new-energy-run-stage23-scenario-dispatch-showcase
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from new_energy_sys.stage23_scenario_dispatch_showcase import (
    build_stage23_report,
    load_stage23_inputs,
    select_representative_scenarios,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage23 scenario-based dispatch showcase report."
    )
    parser.add_argument(
        "--economic-metrics",
        required=True,
        help="Path to Stage22B economic sensitivity metrics CSV.",
    )
    parser.add_argument(
        "--economic-report",
        default=None,
        help="Optional path to Stage22B economic sensitivity report JSON.",
    )
    parser.add_argument(
        "--spread-metrics",
        default=None,
        help="Optional path to Stage22B spread amplification metrics CSV.",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage23_scenario_dispatch_showcase",
    )
    parser.add_argument(
        "--report-date",
        default=date.today().isoformat(),
        help=f"Report date string (default: {date.today().isoformat()}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    eco_path = Path(args.economic_metrics)
    if not eco_path.is_absolute():
        eco_path = Path.cwd() / eco_path
    if not eco_path.exists():
        raise FileNotFoundError(f"Economic metrics not found: {eco_path}")

    spread_path = None
    if args.spread_metrics:
        spread_path = Path(args.spread_metrics)
        if not spread_path.is_absolute():
            spread_path = Path.cwd() / spread_path
        if not spread_path.exists():
            print(f"[stage23] spread metrics not found: {spread_path}; skipping spread scenarios")
            spread_path = None

    inputs = load_stage23_inputs(
        str(eco_path),
        str(spread_path) if spread_path else None,
    )

    selected = select_representative_scenarios(
        inputs["economic_metrics"],
        inputs.get("spread_metrics"),
    )

    output_dir = eco_path.parent
    output_paths = {
        "metrics_csv": output_dir / f"{args.output_prefix}_metrics.csv",
        "report_json": output_dir / f"{args.output_prefix}_report.json",
        "report_md": output_dir / f"{args.output_prefix}_report.md",
    }

    result = build_stage23_report(
        selected, output_paths=output_paths, report_date=args.report_date
    )
    print(f"[stage23] {len(selected)} scenarios selected")
    gates = result.report.get("quality_gates", {})
    print(f"[stage23] baseline_net_negative: {gates.get('baseline_net_negative')}")
    print(f"[stage23] at_least_one_positive: {gates.get('at_least_one_positive')}")


if __name__ == "__main__":
    main()
