"""策略治理模块。

模块设计原则：
- 汇总 Stage 10/11/12 调度指标，输出统一策略治理评分表
- 结构化 JSON 供机器消费，中文 Markdown 报告供人工审阅
- 静态 HTML 仪表盘支持快速可视化审查

本模块对应项目 Stage 13 的策略治理功能。

入口命令: new-energy-sys run-stage13 --config <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.config import load_config
from new_energy_sys.stage13_storage_governance import (
    run_stage13_storage_governance,
    write_stage13_dashboard,
    write_stage13_json,
    write_stage13_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage13 策略治理命令行参数。

    默认路径对应 S13 交接锚点：读取 Stage10/11/12 已生成指标，输出统一策略
    治理评分表、结构化 JSON、中文 Markdown 报告和静态 HTML 仪表盘。
    """

    parser = argparse.ArgumentParser(description="执行 Stage 13 储能策略治理。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument(
        "--stage10-metrics",
        default="stage10_storage_dispatch_metrics.csv",
        help="Stage 10 指标 CSV 路径；相对路径在 processed_dir 下解析。",
    )
    parser.add_argument(
        "--stage11-metrics",
        default="stage11_storage_strategy_sensitivity_metrics.csv",
        help="Stage 11 指标 CSV 路径；相对路径在 processed_dir 下解析。",
    )
    parser.add_argument(
        "--stage12-metrics",
        default="stage12_storage_rolling_optimization_metrics.csv",
        help="Stage 12 指标 CSV 路径；相对路径在 processed_dir 下解析。",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage13_storage_strategy_governance",
        help="输出文件名前缀（写入 processed_dir）。",
    )
    return parser.parse_args()


def _resolve_processed_path(processed_dir: Path, value: str) -> Path:
    """解析指标输入路径。

    绝对路径直接使用；相对路径默认位于当前主线 processed_dir 下，避免 CLI
    调用者每次重复输入长路径。
    """

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return processed_dir / candidate


def main() -> None:
    """执行 Stage13 策略治理并落盘所有展示与机器消费产物。"""

    args = parse_args()
    runtime = load_config(args.config)
    stage10_metrics = _resolve_processed_path(runtime.processed_dir, args.stage10_metrics)
    stage11_metrics = _resolve_processed_path(runtime.processed_dir, args.stage11_metrics)
    stage12_metrics = _resolve_processed_path(runtime.processed_dir, args.stage12_metrics)
    output_paths = {
        "scorecard_csv": runtime.processed_dir / f"{args.output_prefix}_scorecard.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
        "dashboard_html": runtime.processed_dir / f"{args.output_prefix}_dashboard.html",
    }

    result = run_stage13_storage_governance(
        stage10_metrics_path=stage10_metrics,
        stage11_metrics_path=stage11_metrics,
        stage12_metrics_path=stage12_metrics,
        output_paths=output_paths,
    )
    result.scorecard.to_csv(output_paths["scorecard_csv"], index=False)
    write_stage13_json(result.report, output_paths["report_json"])
    write_stage13_report(result.report, result.scorecard, output_paths["report_md"])
    write_stage13_dashboard(result.report, result.scorecard, output_paths["dashboard_html"])

    print(f"Stage13 scorecard: {output_paths['scorecard_csv']}")
    print(f"Stage13 report JSON: {output_paths['report_json']}")
    print(f"Stage13 report Markdown: {output_paths['report_md']}")
    print(f"Stage13 dashboard HTML: {output_paths['dashboard_html']}")


if __name__ == "__main__":
    main()
