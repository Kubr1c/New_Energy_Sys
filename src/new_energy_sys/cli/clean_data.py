"""数据清洗与质量验证模块。

模块设计原则：
- 对 Stage 1 小时级训练表执行缺失值填充、异常值检测、时间对齐
- 输出清洗后数据集、标准化特征数据集及质量报告
- 产物为 processed_dir 下的 parquet / CSV / JSON / Markdown

本模块对应项目 Stage 2 的数据清洗与质量验证功能。

入口命令: new-energy-sys clean-data --config <path>
"""

from __future__ import annotations

import argparse
import json

from new_energy_sys.cleaning import clean_stage_two_dataset, write_quality_report
from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir

import pandas as pd


def parse_args() -> argparse.Namespace:
    """解析 Stage 2 命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 2 数据清洗与质量验证。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument(
        "--input",
        default="data/processed/hourly_training_with_storage.parquet",
        help="Stage 1 小时级数据集路径（相对于项目根目录）。",
    )
    return parser.parse_args()


def main() -> None:
    """执行 Stage 2 核心逻辑：清洗 → 标准化 → 质量报告 → 落盘产物。"""
    args = parse_args()
    runtime = load_config(args.config)
    processed_dir = ensure_dir(runtime.processed_dir)

    input_path = runtime.root_dir / args.input
    frame = pd.read_parquet(input_path)

    result = clean_stage_two_dataset(frame, runtime.raw)

    cleaned_path = processed_dir / "stage2_cleaned_hourly_dataset.parquet"
    cleaned_preview_path = processed_dir / "stage2_cleaned_hourly_dataset_preview.csv"
    standardized_path = processed_dir / "stage2_standardized_feature_dataset.parquet"
    report_json_path = processed_dir / "stage2_quality_report.json"
    report_md_path = processed_dir / "stage2_quality_report.md"

    result.cleaned.to_parquet(cleaned_path, index=False)
    result.cleaned.head(200).to_csv(cleaned_preview_path, index=False)
    result.standardized.to_parquet(standardized_path, index=False)
    report_json_path.write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_quality_report(result.report, report_md_path)

    print(f"清洗数据: {cleaned_path}")
    print(f"清洗预览: {cleaned_preview_path}")
    print(f"标准化特征: {standardized_path}")
    print(f"质量报告JSON: {report_json_path}")
    print(f"质量报告Markdown: {report_md_path}")
    print(f"最终样本数: {len(result.cleaned)}")
    print(f"目标小时覆盖率: {result.report['time_alignment']['target_hour_coverage']}")


if __name__ == "__main__":
    main()
