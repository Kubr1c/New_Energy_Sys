from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.optimization import run_stage5_optimization, write_stage5_report


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for Stage5 LightGBM diagnostics and optimization."""

    parser = argparse.ArgumentParser(description="Run Stage5 LightGBM diagnostics, ablation, and tuning.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    parser.add_argument(
        "--input",
        default="data/processed/nrel_opsd_weather/stage3_feature_dataset.parquet",
        help="Stage3 feature dataset path relative to project root.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory relative to project root. Defaults to the config processed_dir.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the full Stage5 workflow and persist all audit artifacts.

    输出文件分四类：
    - ablation_metrics：固定模型参数，只改变特征组，用来解释特征贡献；
    - tuned_metrics：只在验证集选参数，测试集用于最终评估；
    - grouped_errors：按小时、月份、GHI、云量、爬坡、预测分歧分组定位误差；
    - report：把关键结论固化成 Markdown/JSON，方便后续论文或阶段报告引用。
    """

    args = parse_args()
    runtime = load_config(args.config)

    input_path = runtime.root_dir / args.input
    output_dir = ensure_dir(runtime.root_dir / args.output_dir) if args.output_dir else ensure_dir(runtime.processed_dir)

    frame = pd.read_parquet(input_path)
    result = run_stage5_optimization(frame, runtime.raw, output_dir=output_dir)

    ablation_metrics_path = output_dir / "stage5_ablation_metrics.csv"
    tuned_metrics_path = output_dir / "stage5_tuned_metrics.csv"
    grouped_errors_path = output_dir / "stage5_grouped_errors.csv"
    importance_path = output_dir / "stage5_feature_importance.csv"
    report_json_path = output_dir / "stage5_optimization_report.json"
    report_md_path = output_dir / "stage5_optimization_report.md"

    result.ablation_metrics.to_csv(ablation_metrics_path, index=False)
    result.tuned_metrics.to_csv(tuned_metrics_path, index=False)
    result.grouped_errors.to_csv(grouped_errors_path, index=False)
    result.feature_importance.to_csv(importance_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_stage5_report(
        result.report,
        result.ablation_metrics,
        result.tuned_metrics,
        result.grouped_errors,
        result.feature_importance,
        report_md_path,
    )

    print(f"Ablation metrics: {ablation_metrics_path}")
    print(f"Tuned metrics: {tuned_metrics_path}")
    print(f"Grouped errors: {grouped_errors_path}")
    print(f"Feature importance: {importance_path}")
    print(f"Report JSON: {report_json_path}")
    print(f"Report Markdown: {report_md_path}")


if __name__ == "__main__":
    main()
