"""储能调度仿真模块。

模块设计原则：
- 基于 Stage 9 预测结果与 Stage 3 市场信号执行规则储能调度仿真
- 输出调度结果、调度指标及调度报告
- 支持自定义预测时距，将预测时间戳映射为调度时间戳

本模块对应项目 Stage 10 的储能调度仿真功能。

入口命令: new-energy-sys run-stage10 --config <path> --predictions <path> --feature-input <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.storage import (
    run_stage10_storage_dispatch,
    write_stage10_json,
    write_stage10_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage10 储能调度仿真命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 10 储能调度仿真。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument(
        "--predictions",
        required=True,
        help="Stage 9 预测 CSV 路径（相对于项目根目录）。",
    )
    parser.add_argument(
        "--feature-input",
        required=True,
        help="Stage 3 特征 parquet 路径（相对于项目根目录），用于价格/负荷对齐。",
    )
    parser.add_argument(
        "--horizon-hours",
        type=int,
        default=24,
        help="预测时距，用于将预测时间戳转换为调度时间戳。",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage10_storage_dispatch",
        help="输出文件名前缀（写入 processed_dir）。",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """解析项目内输入路径，支持绝对路径和相对仓库根目录路径。"""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """执行 Stage10 调度仿真并落盘 CSV、JSON、Markdown 产物。"""

    args = parse_args()
    runtime = load_config(args.config)

    prediction_path = _resolve_project_path(runtime.root_dir, args.predictions)
    feature_input_path = _resolve_project_path(runtime.root_dir, args.feature_input)
    predictions = pd.read_csv(prediction_path)
    feature_frame = pd.read_parquet(feature_input_path)

    output_paths = {
        "results_csv": runtime.processed_dir / f"{args.output_prefix}_results.csv",
        "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_metrics.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
    }
    result = run_stage10_storage_dispatch(
        predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        output_paths=output_paths,
    )

    result.results.to_csv(output_paths["results_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    write_stage10_json(result.report, output_paths["report_json"])
    write_stage10_report(result.report, result.metrics, output_paths["report_md"])

    print(f"Stage10 results: {output_paths['results_csv']}")
    print(f"Stage10 metrics: {output_paths['metrics_csv']}")
    print(f"Stage10 report JSON: {output_paths['report_json']}")
    print(f"Stage10 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
