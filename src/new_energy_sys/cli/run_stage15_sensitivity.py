from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage15_storage_sensitivity import (
    _parse_float_list,
    run_stage15_storage_sensitivity,
    write_stage15_json,
    write_stage15_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage15 储能配置敏感性 CLI 参数。"""

    parser = argparse.ArgumentParser(description="Run Stage15 storage configuration sensitivity analysis.")
    parser.add_argument("--config", required=True, help="Path to JSON data-source configuration.")
    parser.add_argument("--predictions", required=True, help="Stage9 prediction CSV path.")
    parser.add_argument("--feature-input", required=True, help="Stage3 feature parquet path.")
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument(
        "--capacity-multipliers",
        default="0.5,1.0,1.5",
        help="Comma-separated storage capacity multipliers. Default covers three levels.",
    )
    parser.add_argument(
        "--power-multipliers",
        default="0.5,1.0,1.5",
        help="Comma-separated charge/discharge power multipliers. Default covers three levels.",
    )
    parser.add_argument(
        "--cycle-costs",
        default="0.001,0.002,0.004",
        help="Comma-separated degradation cost candidates in EUR/kWh.",
    )
    parser.add_argument(
        "--shortfall-penalties",
        default="0.0005,0.001,0.003",
        help="Comma-separated shortfall risk penalty candidates in EUR/kWh.",
    )
    parser.add_argument(
        "--terminal-penalties",
        default="0.005,0.02,0.05",
        help="Comma-separated terminal SOC penalty candidates in EUR/kWh.",
    )
    parser.add_argument(
        "--terminal-soc-target",
        type=float,
        default=None,
        help="Optional terminal SOC target. Defaults to storage.soc_initial.",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage15_storage_configuration_sensitivity",
        help="Output filename prefix under processed_dir.",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """解析绝对路径或相对项目根目录路径。"""

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """执行 Stage15 敏感性分析并写出 CSV、JSON、Markdown 产物。"""

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
    result = run_stage15_storage_sensitivity(
        predictions,
        feature_frame,
        runtime.raw,
        horizon_hours=args.horizon_hours,
        lookahead_hours=args.lookahead_hours,
        capacity_multipliers=_parse_float_list(args.capacity_multipliers, default=[0.5, 1.0, 1.5], name="capacity_multipliers"),
        power_multipliers=_parse_float_list(args.power_multipliers, default=[0.5, 1.0, 1.5], name="power_multipliers"),
        cycle_costs=_parse_float_list(args.cycle_costs, default=[0.001, 0.002, 0.004], name="cycle_costs"),
        shortfall_penalties=_parse_float_list(
            args.shortfall_penalties,
            default=[0.0005, 0.001, 0.003],
            name="shortfall_penalties",
        ),
        terminal_penalties=_parse_float_list(
            args.terminal_penalties,
            default=[0.005, 0.02, 0.05],
            name="terminal_penalties",
        ),
        terminal_soc_target=args.terminal_soc_target,
        output_paths=output_paths,
    )

    result.results.to_csv(output_paths["results_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    write_stage15_json(result.report, output_paths["report_json"])
    write_stage15_report(result.report, result.metrics, output_paths["report_md"])

    print(f"Stage15 results: {output_paths['results_csv']}")
    print(f"Stage15 metrics: {output_paths['metrics_csv']}")
    print(f"Stage15 report JSON: {output_paths['report_json']}")
    print(f"Stage15 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
