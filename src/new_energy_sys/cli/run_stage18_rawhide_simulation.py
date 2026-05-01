"""Command line entrypoint for Stage18 Rawhide reference simulation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage18_rawhide_simulation import (
    run_stage18_rawhide_simulation,
    write_stage18_json,
    write_stage18_report,
)


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """Resolve absolute paths and project-root-relative paths."""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def parse_args() -> argparse.Namespace:
    """Parse Stage18 CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Run Stage18 Rawhide Prairie Solar scaled PV-storage simulation."
    )
    parser.add_argument("--config", required=True, help="Rawhide JSON config path.")
    parser.add_argument(
        "--base-predictions",
        required=True,
        help="Stage9 base prediction CSV path before Rawhide capacity scaling.",
    )
    parser.add_argument(
        "--feature-input",
        required=True,
        help="Stage3 feature parquet path used for OPSD-mapped price/load signals.",
    )
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument(
        "--output-prefix",
        default="stage18_rawhide",
        help="Output filename prefix written into processed_dir.",
    )
    return parser.parse_args()


def main() -> None:
    """Run Stage18 and write all requested Rawhide artifacts."""

    args = parse_args()
    runtime = load_config(args.config)
    base_predictions_path = _resolve_project_path(runtime.root_dir, args.base_predictions)
    feature_input_path = _resolve_project_path(runtime.root_dir, args.feature_input)

    base_predictions = pd.read_csv(base_predictions_path)
    feature_frame = pd.read_parquet(feature_input_path)
    prefix = args.output_prefix
    output_paths = {
        "scaled_predictions_csv": runtime.processed_dir / f"{prefix}_scaled_predictions.csv",
        "rolling_results_csv": runtime.processed_dir / f"{prefix}_rolling_results.csv",
        "dispatch_metrics_csv": runtime.processed_dir / f"{prefix}_dispatch_metrics.csv",
        "sensitivity_results_csv": runtime.processed_dir / f"{prefix}_sensitivity_results.csv",
        "sensitivity_metrics_csv": runtime.processed_dir / f"{prefix}_sensitivity_metrics.csv",
        "degradation_replay_csv": runtime.processed_dir / f"{prefix}_degradation_replay.csv",
        "degradation_metrics_csv": runtime.processed_dir / f"{prefix}_degradation_metrics.csv",
        "degradation_sensitivity_csv": runtime.processed_dir / f"{prefix}_degradation_sensitivity_metrics.csv",
        "simulation_report_json": runtime.processed_dir / f"{prefix}_simulation_report.json",
        "simulation_report_md": runtime.processed_dir / f"{prefix}_simulation_report.md",
    }

    result = run_stage18_rawhide_simulation(
        base_predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        lookahead_hours=args.lookahead_hours,
        output_paths=output_paths,
    )

    result.scaled_predictions.to_csv(output_paths["scaled_predictions_csv"], index=False)
    result.rolling_results.to_csv(output_paths["rolling_results_csv"], index=False)
    result.dispatch_metrics.to_csv(output_paths["dispatch_metrics_csv"], index=False)
    result.sensitivity_results.to_csv(output_paths["sensitivity_results_csv"], index=False)
    result.sensitivity_metrics.to_csv(output_paths["sensitivity_metrics_csv"], index=False)
    result.degradation_results.to_csv(output_paths["degradation_replay_csv"], index=False)
    result.degradation_metrics.to_csv(output_paths["degradation_metrics_csv"], index=False)
    result.degradation_sensitivity.to_csv(output_paths["degradation_sensitivity_csv"], index=False)
    write_stage18_json(result.report, output_paths["simulation_report_json"])
    write_stage18_report(
        result.report,
        result.dispatch_metrics,
        result.sensitivity_metrics,
        result.degradation_metrics,
        output_paths["simulation_report_md"],
    )

    print(f"Stage18 scaled predictions: {output_paths['scaled_predictions_csv']}")
    print(f"Stage18 rolling results: {output_paths['rolling_results_csv']}")
    print(f"Stage18 dispatch metrics: {output_paths['dispatch_metrics_csv']}")
    print(f"Stage18 sensitivity metrics: {output_paths['sensitivity_metrics_csv']}")
    print(f"Stage18 degradation metrics: {output_paths['degradation_metrics_csv']}")
    print(f"Stage18 report JSON: {output_paths['simulation_report_json']}")
    print(f"Stage18 report Markdown: {output_paths['simulation_report_md']}")


if __name__ == "__main__":
    main()
