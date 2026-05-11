"""Stage22 degradation-aware storage configuration CLI.

Entry point: new-energy-run-stage22-degradation-aware-config

This module scans a five-dimensional grid of storage configurations (capacity
multiplier, power multiplier, SOC range, degradation penalty weight lambda,
minimum arbitrage spread) through a two-pass pipeline: Stage12 rolling dispatch
followed by Stage17 rainflow degradation accounting.  Configurations that pass
the net-revenue / SOH / constraint filters are ranked; the best is reported.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage22_degradation_aware_config import run_stage22_degradation_aware_config


def _parse_soc_ranges(value: str) -> list[tuple[float, float]]:
    """Parse a comma-separated list of hyphen-separated SOC pairs.

    Example: "0.1-0.9,0.2-0.8,0.25-0.75"  →  [(0.1, 0.9), (0.2, 0.8), (0.25, 0.75)]
    """
    pairs: list[tuple[float, float]] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split("-")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"Invalid SOC range '{token}': expected format 'min-max' (e.g. 0.2-0.8)"
            )
        pairs.append((float(parts[0]), float(parts[1])))
    if not pairs:
        raise argparse.ArgumentTypeError("At least one SOC range is required.")
    return pairs


def _parse_float_list(value: str) -> list[float]:
    """Parse a comma-separated list of floats."""
    return [float(token.strip()) for token in value.split(",") if token.strip()]


def parse_args() -> argparse.Namespace:
    """Parse Stage22 CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Execute Stage22 degradation-aware storage configuration scan."
    )
    parser.add_argument("--config", required=True, help="Path to JSON data-source config.")
    parser.add_argument("--predictions", required=True, help="Path to Stage9 predictions CSV.")
    parser.add_argument("--feature-input", required=True, help="Path to Stage3 features parquet.")
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument(
        "--soc-ranges",
        default="0.1-0.9,0.2-0.8,0.25-0.75,0.3-0.7",
        help="Comma-separated SOC min-max pairs, e.g. '0.1-0.9,0.2-0.8'.",
    )
    parser.add_argument(
        "--lambda-values",
        default="0.0,0.5,1.0,2.0",
        help="Comma-separated degradation penalty weights.",
    )
    parser.add_argument(
        "--min-spreads",
        default="0.0,5.0,10.0,15.0",
        help="Comma-separated minimum arbitrage spread thresholds (EUR/MWh).",
    )
    parser.add_argument(
        "--capacity-multipliers",
        default="1.0,1.25,1.5,2.0",
        help="Comma-separated capacity multipliers.",
    )
    parser.add_argument(
        "--power-multipliers",
        default="0.5,0.75,1.0,1.25",
        help="Comma-separated power multipliers.",
    )
    parser.add_argument(
        "--min-net-revenue-eur",
        type=float,
        default=0.0,
        help="Minimum net incremental revenue filter (default 0.0).",
    )
    parser.add_argument(
        "--min-soh",
        type=float,
        default=0.95,
        help="Minimum end-of-period SOH filter (default 0.95).",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage22_degradation_aware_config",
        help="Output filename prefix (written to processed_dir).",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """Resolve an absolute path or a path relative to the project root."""
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """Run the Stage22 degradation-aware configuration scan."""
    args = parse_args()
    runtime = load_config(args.config)
    prediction_path = _resolve_project_path(runtime.root_dir, args.predictions)
    feature_input_path = _resolve_project_path(runtime.root_dir, args.feature_input)

    predictions = pd.read_csv(prediction_path)
    feature_frame = pd.read_parquet(feature_input_path)

    output_paths = {
        "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_metrics.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
    }

    result = run_stage22_degradation_aware_config(
        predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        lookahead_hours=args.lookahead_hours,
        soc_ranges=_parse_soc_ranges(args.soc_ranges),
        lambda_values=_parse_float_list(args.lambda_values),
        min_spreads=_parse_float_list(args.min_spreads),
        capacity_multipliers=_parse_float_list(args.capacity_multipliers),
        power_multipliers=_parse_float_list(args.power_multipliers),
        min_net_revenue_eur=args.min_net_revenue_eur,
        min_soh=args.min_soh,
        output_paths=output_paths,
    )

    print(result.report["decision"])
    print(f"Stage22 metrics: {output_paths['metrics_csv']}")
    print(f"Stage22 report JSON: {output_paths['report_json']}")
    print(f"Stage22 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
