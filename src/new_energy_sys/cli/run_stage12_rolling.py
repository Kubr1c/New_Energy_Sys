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

    parser = argparse.ArgumentParser(description="Run Stage12 rolling storage optimization.")
    parser.add_argument("--config", required=True, help="Path to JSON data-source configuration.")
    parser.add_argument(
        "--predictions",
        required=True,
        help="Stage9 prediction CSV path relative to project root.",
    )
    parser.add_argument(
        "--feature-input",
        required=True,
        help="Stage3 feature parquet path relative to project root; used for price/load alignment.",
    )
    parser.add_argument(
        "--horizon-hours",
        type=int,
        default=24,
        help="Forecast horizon used to convert forecast timestamp into dispatch timestamp.",
    )
    parser.add_argument(
        "--lookahead-hours",
        type=int,
        default=24,
        help="Number of future dispatch hours visible to the rolling optimizer.",
    )
    parser.add_argument(
        "--soc-grid-count",
        type=int,
        default=81,
        help="Number of discrete SOC grid points used by dynamic programming.",
    )
    parser.add_argument(
        "--action-step-kw",
        type=float,
        default=0.056,
        help="Charge/discharge action discretization step in kW.",
    )
    parser.add_argument(
        "--cycle-cost-eur-per-kwh",
        type=float,
        default=None,
        help="Optional degradation cost applied to charge and discharge throughput.",
    )
    parser.add_argument(
        "--shortfall-risk-penalty-eur-per-kwh",
        type=float,
        default=None,
        help="Optional conservative penalty for discharge-backed planned export.",
    )
    parser.add_argument(
        "--terminal-soc-target",
        type=float,
        default=None,
        help="Optional target SOC used by the rolling window terminal penalty.",
    )
    parser.add_argument(
        "--terminal-soc-penalty-eur-per-kwh",
        type=float,
        default=None,
        help="Optional terminal SOC penalty coefficient.",
    )
    parser.add_argument(
        "--stage11-charge-threshold",
        type=float,
        default=24.58,
        help="Stage11 best-threshold charge price used as comparison baseline.",
    )
    parser.add_argument(
        "--stage11-discharge-threshold",
        type=float,
        default=35.7025,
        help="Stage11 best-threshold discharge price used as comparison baseline.",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage12_storage_rolling_optimization",
        help="Output filename prefix written under processed_dir.",
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
