"""滚动优化调度模块。

模块设计原则：
- 基于 Stage 9 预测与 Stage 3 市场信号，执行滚动时域动态规划储能优化
- 与 Stage 11 阈值策略、规则基线做同口径对比
- 支持 SOC 网格、动作步长、退化成本、终端惩罚等参数精细调优

本模块对应项目 Stage 12 的滚动优化调度功能。

入口命令: new-energy-sys run-stage12 --config <path> --predictions <path> --feature-input <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage12_storage_rolling import (
    run_stage12_rolling_optimization,
    write_stage12_json,
    write_stage12_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage12 滚动优化调度命令行参数。

    默认参数对应交接锚点中的 S12 推荐范围：使用 Stage9 t+24h 预测、
    Stage3 市场信号、24h look-ahead，并输出滚动优化与三个基准的同口径比较。
    """

    parser = argparse.ArgumentParser(description="执行 Stage 12 滚动储能优化。")
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
        "--lookahead-hours",
        type=int,
        default=24,
        help="滚动优化器可见的未来调度小时数。",
    )
    parser.add_argument(
        "--soc-grid-count",
        type=int,
        default=81,
        help="动态规划使用的离散 SOC 网格点数。",
    )
    parser.add_argument(
        "--action-step-kw",
        type=float,
        default=0.056,
        help="充放电动作离散化步长（kW）。",
    )
    parser.add_argument(
        "--cycle-cost-eur-per-kwh",
        type=float,
        default=None,
        help="可选：充放电吞吐量退化成本（EUR/kWh）。",
    )
    parser.add_argument(
        "--shortfall-risk-penalty-eur-per-kwh",
        type=float,
        default=None,
        help="可选：放电支持的计划出口保守惩罚（EUR/kWh）。",
    )
    parser.add_argument(
        "--terminal-soc-target",
        type=float,
        default=None,
        help="可选：滚动窗口终端 SOC 目标值。",
    )
    parser.add_argument(
        "--terminal-soc-penalty-eur-per-kwh",
        type=float,
        default=None,
        help="可选：终端 SOC 惩罚系数。",
    )
    parser.add_argument(
        "--stage11-charge-threshold",
        type=float,
        default=24.58,
        help="Stage 11 最优充电价格阈值，用作对比基线。",
    )
    parser.add_argument(
        "--stage11-discharge-threshold",
        type=float,
        default=35.7025,
        help="Stage 11 最优放电价格阈值，用作对比基线。",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage12_storage_rolling_optimization",
        help="输出文件名前缀（写入 processed_dir）。",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """解析项目内路径，支持绝对路径和相对仓库根目录路径。"""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """执行 Stage12 滚动优化并落盘 CSV、JSON、Markdown 产物。"""

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
    result = run_stage12_rolling_optimization(
        predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        lookahead_hours=args.lookahead_hours,
        soc_grid_count=args.soc_grid_count,
        action_step_kw=args.action_step_kw,
        cycle_cost_eur_per_kwh=args.cycle_cost_eur_per_kwh,
        shortfall_risk_penalty_eur_per_kwh=args.shortfall_risk_penalty_eur_per_kwh,
        terminal_soc_target=args.terminal_soc_target,
        terminal_soc_penalty_eur_per_kwh=args.terminal_soc_penalty_eur_per_kwh,
        stage11_charge_threshold=args.stage11_charge_threshold,
        stage11_discharge_threshold=args.stage11_discharge_threshold,
        output_paths=output_paths,
    )

    result.results.to_csv(output_paths["results_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    write_stage12_json(result.report, output_paths["report_json"])
    write_stage12_report(result.report, result.metrics, output_paths["report_md"])

    print(f"Stage12 results: {output_paths['results_csv']}")
    print(f"Stage12 metrics: {output_paths['metrics_csv']}")
    print(f"Stage12 report JSON: {output_paths['report_json']}")
    print(f"Stage12 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
