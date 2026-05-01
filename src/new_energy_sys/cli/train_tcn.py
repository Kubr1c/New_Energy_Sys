"""TCN 时序模型训练模块。

模块设计原则：
- 对 Stage 3 特征数据集训练时序卷积网络（TCN）预测模型
- 支持多窗口、多目标、多特征组与多模型配置的网格实验
- 泄漏安全：严格按时间切分训练/验证/测试集

本模块对应项目 Stage 6 的 TCN 时序建模功能。

入口命令: new-energy-sys train-tcn --config <path> --input <path>
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.sequence_modeling import run_tcn_experiments, write_tcn_report


def parse_args() -> argparse.Namespace:
    """解析 Stage 6 TCN 时序建模命令行参数。"""

    parser = argparse.ArgumentParser(description="执行泄漏安全的 TCN 时序建模。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument("--input", required=True, help="Stage 3 特征数据集路径（相对于项目根目录）。")
    parser.add_argument(
        "--baseline-metrics",
        default=None,
        help="可选：调优后 LightGBM 指标 CSV 路径（相对于项目根目录），用于对比。",
    )
    parser.add_argument(
        "--windows",
        default="24,48,72",
        help="逗号分隔的序列窗口大小（小时）。",
    )
    parser.add_argument(
        "--targets",
        default=None,
        help=(
            "可选：逗号分隔的目标列名或短别名（1h, 6h, 24h）。"
        ),
    )
    parser.add_argument(
        "--feature-set",
        default="all",
        choices=["all", "weather_history", "weather_history_target_aligned"],
        help="TCN 特征组。weather_history 排除负荷、价格、储能和日历噪声。",
    )
    parser.add_argument(
        "--tcn-configs",
        default="baseline",
        help="逗号分隔的轻量 TCN 配置名：baseline, compact, regularized。",
    )
    parser.add_argument("--max-epochs", type=int, default=20, help="每个 TCN 模型最大训练轮数。")
    parser.add_argument("--patience", type=int, default=4, help="验证集早停耐心值。")
    parser.add_argument("--batch-size", type=int, default=256, help="训练与推理批次大小。")
    return parser.parse_args()


def main() -> None:
    """训练 TCN 模型并落盘指标、预测、模型与报告。"""

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
