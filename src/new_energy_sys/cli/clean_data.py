from __future__ import annotations

import argparse
import json

from new_energy_sys.cleaning import clean_stage_two_dataset, write_quality_report
from new_energy_sys.config import load_config
from new_energy_sys.io_utils import ensure_dir

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stage-2 data cleaning and quality validation.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    parser.add_argument(
        "--input",
        default="data/processed/hourly_training_with_storage.parquet",
        help="Stage-1 hourly dataset path relative to project root.",
    )
    return parser.parse_args()


def main() -> None:
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
