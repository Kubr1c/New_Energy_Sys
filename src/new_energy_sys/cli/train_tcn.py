from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.sequence_modeling import run_tcn_experiments, write_tcn_report


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for TCN sequence modeling."""

    parser = argparse.ArgumentParser(description="Run leakage-safe TCN sequence modeling.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    parser.add_argument("--input", required=True, help="Stage3 feature dataset path relative to project root.")
    parser.add_argument(
        "--baseline-metrics",
        default=None,
        help="Optional tuned LightGBM metrics CSV path relative to project root for comparison.",
    )
    parser.add_argument(
        "--windows",
        default="24,48,72",
        help="Comma-separated sequence window sizes in hours.",
    )
    parser.add_argument(
        "--targets",
        default=None,
        help=(
            "Optional comma-separated targets. Supports full target column names "
            "or short horizon aliases: 1h, 6h, 24h."
        ),
    )
    parser.add_argument(
        "--feature-set",
        default="all",
        choices=["all", "weather_history", "weather_history_target_aligned"],
        help="TCN feature group. Use weather_history to exclude load, price, storage, and calendar noise.",
    )
    parser.add_argument(
        "--tcn-configs",
        default="baseline",
        help="Comma-separated lightweight TCN configs: baseline, compact, regularized.",
    )
    parser.add_argument("--max-epochs", type=int, default=20, help="Maximum training epochs per TCN model.")
    parser.add_argument("--patience", type=int, default=4, help="Validation early-stopping patience.")
    parser.add_argument("--batch-size", type=int, default=256, help="Training and inference batch size.")
    return parser.parse_args()


def main() -> None:
    """Train TCN models and persist metrics, predictions, models, and report."""

    args = parse_args()
    runtime = load_config(args.config)
    output_dir = ensure_dir(runtime.processed_dir)
    input_path = runtime.root_dir / args.input
    frame = pd.read_parquet(input_path)

    window_sizes = [int(value.strip()) for value in args.windows.split(",") if value.strip()]
    targets = [value.strip() for value in args.targets.split(",") if value.strip()] if args.targets else None
    tcn_configs = [value.strip() for value in args.tcn_configs.split(",") if value.strip()]
    result = run_tcn_experiments(
        frame,
        runtime.raw,
        output_dir=output_dir,
        window_sizes=window_sizes,
        targets=targets,
        feature_set=args.feature_set,
        tcn_config_names=tcn_configs,
        max_epochs=int(args.max_epochs),
        patience=int(args.patience),
        batch_size=int(args.batch_size),
    )

    metrics_path = output_dir / "stage6_tcn_metrics.csv"
    predictions_path = output_dir / "stage6_tcn_predictions.csv"
    report_json_path = output_dir / "stage6_tcn_report.json"
    report_md_path = output_dir / "stage6_tcn_report.md"

    result.metrics.to_csv(metrics_path, index=False)
    result.predictions.to_csv(predictions_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")

    baseline_metrics = None
    if args.baseline_metrics:
        baseline_metrics = pd.read_csv(runtime.root_dir / args.baseline_metrics)
    write_tcn_report(result.report, result.metrics, baseline_metrics, report_md_path)

    print(f"TCN metrics: {metrics_path}")
    print(f"TCN predictions: {predictions_path}")
    print(f"TCN report JSON: {report_json_path}")
    print(f"TCN report Markdown: {report_md_path}")


if __name__ == "__main__":
    main()
