"""储能策略敏感性分析模块。

模块设计原则：
- 基于 Stage 9 预测与 Stage 3 市场信号，对充放电价格阈值执行网格搜索
- 输出策略敏感性结果、指标及报告
- 分位数阈值严格校验，避免错误搜索空间生成不可解释报告

本模块对应项目 Stage 11 的储能策略敏感性分析功能。

入口命令: new-energy-sys run-stage11 --config <path> --predictions <path> --feature-input <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage11_storage_strategy import (
    run_stage11_strategy_sensitivity,
    write_stage11_json,
    write_stage11_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage11 储能策略敏感性分析命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 11 储能策略敏感性分析。")
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
        "--charge-quantiles",
        default="0.05,0.10,0.20,0.30,0.40",
        help="逗号分隔的充电价格分位数阈值。",
    )
    parser.add_argument(
        "--discharge-quantiles",
        default="0.60,0.70,0.80,0.90,0.95",
        help="逗号分隔的放电价格分位数阈值。",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage11_storage_strategy_sensitivity",
        help="输出文件名前缀（写入 processed_dir）。",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """解析项目内输入路径，支持绝对路径和相对仓库根目录路径。"""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def _parse_quantiles(value: str, label: str) -> list[float]:
    """解析 CLI 分位数字符串，并在入口处执行严格校验。

    分位数必须落在 `[0, 1]`。这里不做静默修正，因为错误的阈值网格会直接改变策略
    搜索空间，继续运行会生成不可解释的报告。
    """

    try:
        quantiles = [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError(f"{label} contains non-numeric values: {value}") from exc
    if not quantiles:
        raise ValueError(f"{label} must contain at least one quantile.")
    invalid = [item for item in quantiles if item < 0 or item > 1]
    if invalid:
        raise ValueError(f"{label} contains values outside [0, 1]: {invalid}")
    return quantiles


def main() -> None:
    """执行 Stage11 敏感性分析并落盘 CSV、JSON、Markdown 产物。"""

    args = parse_args()
    runtime = load_config(args.config)

    prediction_path = _resolve_project_path(runtime.root_dir, args.predictions)
    feature_input_path = _resolve_project_path(runtime.root_dir, args.feature_input)
    predictions = pd.read_csv(prediction_path)
    feature_frame = pd.read_parquet(feature_input_path)
    charge_quantiles = _parse_quantiles(args.charge_quantiles, "charge_quantiles")
    discharge_quantiles = _parse_quantiles(args.discharge_quantiles, "discharge_quantiles")

    output_paths = {
        "results_csv": runtime.processed_dir / f"{args.output_prefix}_results.csv",
        "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_metrics.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
    }
    result = run_stage11_strategy_sensitivity(
        predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        charge_quantiles=charge_quantiles,
        discharge_quantiles=discharge_quantiles,
        output_paths=output_paths,
    )

    result.results.to_csv(output_paths["results_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    write_stage11_json(result.report, output_paths["report_json"])
    write_stage11_report(result.report, result.metrics, output_paths["report_md"])

    print(f"Stage11 results: {output_paths['results_csv']}")
    print(f"Stage11 metrics: {output_paths['metrics_csv']}")
    print(f"Stage11 report JSON: {output_paths['report_json']}")
    print(f"Stage11 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
