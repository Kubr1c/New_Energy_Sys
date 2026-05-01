"""LightGBM 基线模型训练模块。

模块设计原则：
- 对 Stage 3 特征数据集训练 LightGBM 基线预测模型
- 输出模型文件、预测结果、特征重要性及建模报告
- 产物为 processed_dir/models 及指标 CSV / JSON / Markdown

本模块对应项目 Stage 4 的基线模型训练功能。

入口命令: new-energy-sys train-baseline --config <path>
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.modeling import run_lightgbm_baseline, write_modeling_report


def parse_args() -> argparse.Namespace:
    """解析 Stage 4 基线训练命令行参数。"""

    parser = argparse.ArgumentParser(description="训练 Stage 4 LightGBM 基线模型。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument(
        "--input",
        default="data/processed/nrel_opsd_weather/stage3_feature_dataset.parquet",
        help="Stage 3 特征数据集路径（相对于项目根目录）。",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录（相对于项目根目录），默认使用配置中的 processed_dir。",
    )
    return parser.parse_args()


def main() -> None:
    """执行 Stage 4 核心逻辑：训练基线模型 → 输出指标与报告 → 落盘产物。"""
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
