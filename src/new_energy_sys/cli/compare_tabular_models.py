from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.tabular_comparison import (
    run_tabular_model_comparison,
    write_tabular_comparison_report,
)


def parse_args() -> argparse.Namespace:
    """Parse Stage8 tabular-comparison CLI arguments."""

    parser = argparse.ArgumentParser(description="Run Stage8 tabular model comparison.")
    parser.add_argument("--config", required=True, help="Path to JSON data-source configuration.")
    parser.add_argument("--input", required=True, help="Stage3 feature dataset path relative to project root.")
    return parser.parse_args()


def main() -> None:
    """Train Stage8 tabular models and persist comparison artifacts."""

    args = parse_args()
    runtime = load_config(args.config)
    output_dir = runtime.processed_dir
    frame = pd.read_parquet(runtime.root_dir / args.input)

    result = run_tabular_model_comparison(frame, runtime.raw, output_dir=output_dir)

    metrics_path = output_dir / "stage8_tabular_model_metrics.csv"
    predictions_path = output_dir / "stage8_tabular_model_predictions.csv"
    report_json_path = output_dir / "stage8_tabular_model_report.json"
    report_md_path = output_dir / "stage8_tabular_model_report.md"

    result.metrics.to_csv(metrics_path, index=False)
    result.predictions.to_csv(predictions_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_tabular_comparison_report(result.report, result.metrics, report_md_path)

    print(f"Stage8 metrics: {metrics_path}")
    print(f"Stage8 predictions: {predictions_path}")
    print(f"Stage8 report JSON: {report_json_path}")
    print(f"Stage8 report Markdown: {report_md_path}")
    print(f"Stage8 selected model: {result.report['recommendation']['selected_model']}")


if __name__ == "__main__":
    main()
