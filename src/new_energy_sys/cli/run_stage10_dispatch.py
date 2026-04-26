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

    parser = argparse.ArgumentParser(description="Run Stage10 storage dispatch simulation.")
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
        "--output-prefix",
        default="stage10_storage_dispatch",
        help="Output filename prefix written under processed_dir.",
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
