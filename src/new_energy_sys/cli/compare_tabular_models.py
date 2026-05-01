"""表格模型横向对比模块。

模块设计原则：
- 对 Stage 3 特征数据集同时训练多种表格模型（LightGBM / XGBoost / CatBoost / RandomForest 等）
- 统一评估指标，输出推荐模型及对比报告
- 产物为 processed_dir 下的指标 CSV / 预测 CSV / JSON / Markdown

本模块对应项目 Stage 8 的表格模型横向对比功能。

入口命令: new-energy-sys compare-tabular-models --config <path> --input <path>
"""

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
    """解析 Stage 8 表格模型对比命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 8 表格模型横向对比。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument("--input", required=True, help="Stage 3 特征数据集路径（相对于项目根目录）。")
    return parser.parse_args()


def main() -> None:
    """训练 Stage 8 表格模型并落盘对比产物。"""

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
