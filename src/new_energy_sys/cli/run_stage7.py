"""预报气象可用性验证模块。

模块设计原则：
- 检验预报气象源与 Stage 3 特征的时间对齐与字段完整性
- 输出验证数据集、TCN 工件及决策报告
- 决策报告明确是否可接入预报气象进行模型升级

本模块对应项目 Stage 7 的预报气象可用性验证功能。

入口命令: new-energy-sys run-stage7 --config <path> --stage3-input <path> --forecast-weather <path>
"""

from __future__ import annotations

import argparse

from new_energy_sys.config import load_config
from new_energy_sys.stage7_forecast_validation import run_stage7_forecast_validation


def parse_args() -> argparse.Namespace:
    """解析 Stage 7 预报气象验证命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 7 预报气象可用性验证。")
    parser.add_argument("--config", required=True, help="主数据源配置文件路径。")
    parser.add_argument("--stage3-input", required=True, help="Stage 3 parquet 路径（相对于项目根目录）。")
    parser.add_argument("--forecast-weather", required=True, help="预报气象 CSV/parquet 路径（相对于项目根目录）。")
    return parser.parse_args()


def main() -> None:
    """执行 Stage 7 核心逻辑并落盘数据集、TCN 工件及决策报告。"""

    args = parse_args()
    runtime = load_config(args.config)
    result = run_stage7_forecast_validation(
        config=runtime.raw,
        stage3_path=runtime.root_dir / args.stage3_input,
        forecast_weather_path=runtime.root_dir / args.forecast_weather,
        output_dir=runtime.processed_dir,
    )
    print(f"Stage7 rows: {len(result.feature_dataset)}")
    print(f"Stage7 recommendation: {result.report['decision']['recommendation']}")
    print(f"Stage7 report: {runtime.processed_dir / 'stage7_forecast_validation_report.md'}")


if __name__ == "__main__":
    main()
