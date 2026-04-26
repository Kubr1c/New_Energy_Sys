from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.deep_sequence_modeling import run_deep_learning_experiments, write_deep_learning_report
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for Stage14B multi-model experiments."""

    parser = argparse.ArgumentParser(description="Run Stage14B Persistence/CNN-LSTM/Attention-LSTM PV forecasting.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    parser.add_argument("--input", required=True, help="Stage3 feature dataset path relative to project root.")
    parser.add_argument(
        "--baseline-metrics",
        default=None,
        help="Optional Stage8 tabular metrics CSV path relative to project root.",
    )
    parser.add_argument(
        "--tcn-metrics",
        default=None,
        help="Optional Stage6 TCN metrics CSV path relative to project root.",
    )
    parser.add_argument(
        "--targets",
        default="24h",
        help="Comma-separated target aliases or full target columns. Default: 24h.",
    )
    parser.add_argument(
        "--windows",
        default="96,168",
        help="Comma-separated sequence window sizes in hours. Default: 96,168.",
    )
    parser.add_argument(
        "--feature-sets",
        default="history_only,weather_history_target_aligned",
        choices=None,
        help=(
            "Comma-separated feature sets: history_only, weather_history_target_aligned. "
            "The latter is an offline upper-bound group, not a real forecast-cycle input."
        ),
    )
    parser.add_argument(
        "--models",
        default="persistence,cnn_lstm,attention_lstm",
        help="Comma-separated models: persistence, cnn_lstm, attention_lstm.",
    )
    parser.add_argument("--max-epochs", type=int, default=30, help="Maximum training epochs per neural model.")
    parser.add_argument("--patience", type=int, default=5, help="Validation early-stopping patience.")
    parser.add_argument("--batch-size", type=int, default=256, help="Training and inference batch size.")
    parser.add_argument(
        "--torch-threads",
        type=int,
        default=None,
        help="Optional PyTorch CPU intra-op thread count. Use 0/omit to keep PyTorch default.",
    )
    return parser.parse_args()


def main() -> None:
    """Run Stage14B models and persist unified artifacts."""

    args = parse_args()
    runtime = load_config(args.config)
    output_dir = ensure_dir(runtime.processed_dir)
    frame = pd.read_parquet(runtime.root_dir / args.input)

    baseline_metrics = pd.read_csv(runtime.root_dir / args.baseline_metrics) if args.baseline_metrics else None
    tcn_metrics = pd.read_csv(runtime.root_dir / args.tcn_metrics) if args.tcn_metrics else None
    targets = [value.strip() for value in args.targets.split(",") if value.strip()]
    windows = [int(value.strip()) for value in args.windows.split(",") if value.strip()]
    feature_sets = [value.strip() for value in args.feature_sets.split(",") if value.strip()]
    models = [value.strip() for value in args.models.split(",") if value.strip()]

    result = run_deep_learning_experiments(
        frame,
        runtime.raw,
        output_dir=output_dir,
        window_sizes=windows,
        targets=targets,
        feature_set_names=feature_sets,
        model_names=models,
        baseline_metrics=baseline_metrics,
        tcn_metrics=tcn_metrics,
        max_epochs=int(args.max_epochs),
        patience=int(args.patience),
        batch_size=int(args.batch_size),
        torch_threads=args.torch_threads,
    )

    metrics_path = output_dir / "stage14_deep_learning_metrics.csv"
    predictions_path = output_dir / "stage14_deep_learning_predictions.csv"
    report_json_path = output_dir / "stage14_deep_learning_report.json"
    report_md_path = output_dir / "stage14_deep_learning_report.md"

    result.metrics.to_csv(metrics_path, index=False)
    result.predictions.to_csv(predictions_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_deep_learning_report(result.report, result.metrics, report_md_path)

    print(f"Stage14B metrics: {metrics_path}")
    print(f"Stage14B predictions: {predictions_path}")
    print(f"Stage14B report JSON: {report_json_path}")
    print(f"Stage14B report Markdown: {report_md_path}")


if __name__ == "__main__":
    main()
