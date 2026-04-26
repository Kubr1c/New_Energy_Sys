from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.modeling import run_lightgbm_baseline, write_modeling_report


def parse_args() -> argparse.Namespace:
    """Parse the stage-4 baseline training arguments."""

    parser = argparse.ArgumentParser(description="Train stage-4 LightGBM baseline models.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    parser.add_argument(
        "--input",
        default="data/processed/nrel_opsd_weather/stage3_feature_dataset.parquet",
        help="Stage-3 feature dataset path relative to project root.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory relative to project root. Defaults to the config processed_dir.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime = load_config(args.config)

    input_path = runtime.root_dir / args.input
    output_dir = ensure_dir(runtime.root_dir / args.output_dir) if args.output_dir else ensure_dir(runtime.processed_dir)
    model_dir = ensure_dir(output_dir / "models")

    frame = pd.read_parquet(input_path)
    result = run_lightgbm_baseline(frame, runtime.raw, model_dir=model_dir)

    metrics_path = output_dir / "stage4_lightgbm_metrics.csv"
    predictions_path = output_dir / "stage4_lightgbm_predictions.csv"
    importance_path = output_dir / "stage4_lightgbm_feature_importance.csv"
    report_json_path = output_dir / "stage4_lightgbm_report.json"
    report_md_path = output_dir / "stage4_lightgbm_report.md"

    result.metrics.to_csv(metrics_path, index=False)
    result.predictions.to_csv(predictions_path, index=False)
    result.feature_importance.to_csv(importance_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_modeling_report(result.report, result.metrics, result.feature_importance, report_md_path)

    print(f"指标: {metrics_path}")
    print(f"预测: {predictions_path}")
    print(f"特征重要性: {importance_path}")
    print(f"报告JSON: {report_json_path}")
    print(f"报告Markdown: {report_md_path}")
    print(f"模型目录: {model_dir}")


if __name__ == "__main__":
    main()
