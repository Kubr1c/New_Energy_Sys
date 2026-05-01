"""深度学习多模型实验模块。

模块设计原则：
- 对 Stage 3 特征数据集训练 Persistence / CNN-LSTM / Attention-LSTM 等深度模型
- 支持多窗口、多目标、多特征组与多模型架构的网格实验
- 与 Stage 8 表格模型、Stage 6 TCN 统一对比

本模块对应项目 Stage 14B 的深度学习光伏预测功能。

入口命令: new-energy-sys run-stage14 --config <path> --input <path>
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.deep_sequence_modeling import run_deep_learning_experiments, write_deep_learning_report
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """解析 Stage 14B 多模型实验命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 14B Persistence/CNN-LSTM/Attention-LSTM 光伏预测。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument("--input", required=True, help="Stage 3 特征数据集路径（相对于项目根目录）。")
    parser.add_argument(
        "--baseline-metrics",
        default=None,
        help="可选：Stage 8 表格模型指标 CSV 路径（相对于项目根目录）。",
    )
    parser.add_argument(
        "--tcn-metrics",
        default=None,
        help="可选：Stage 6 TCN 指标 CSV 路径（相对于项目根目录）。",
    )
    parser.add_argument(
        "--targets",
        default="24h",
        help="逗号分隔的目标别名或完整目标列名。默认：24h。",
    )
    parser.add_argument(
        "--windows",
        default="96,168",
        help="逗号分隔的序列窗口大小（小时）。默认：96,168。",
    )
    parser.add_argument(
        "--feature-sets",
        default="history_only,weather_history_target_aligned",
        choices=None,
        help=(
            "逗号分隔的特征组：history_only, weather_history_target_aligned。"
            "后者为离线上界组，不可用于真实预报周期。"
        ),
    )
    parser.add_argument(
        "--models",
        default="persistence,cnn_lstm,attention_lstm",
        help="逗号分隔的模型名：persistence, cnn_lstm, attention_lstm。",
    )
    parser.add_argument("--max-epochs", type=int, default=30, help="每个神经网络模型最大训练轮数。")
    parser.add_argument("--patience", type=int, default=5, help="验证集早停耐心值。")
    parser.add_argument("--batch-size", type=int, default=256, help="训练与推理批次大小。")
    parser.add_argument(
        "--torch-threads",
        type=int,
        default=None,
        help="可选：PyTorch CPU intra-op 线程数。0 或省略则使用 PyTorch 默认值。",
    )
    return parser.parse_args()


def main() -> None:
    """执行 Stage 14B 多模型实验并落盘统一产物。"""

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
