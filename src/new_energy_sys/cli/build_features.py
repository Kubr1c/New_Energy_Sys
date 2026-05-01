"""特征工程模块。

模块设计原则：
- 对 Stage 2 清洗后数据集构建时间滞后、滑动统计、日历、交叉等派生特征
- 输出特征数据集及特征工程报告
- 产物为 processed_dir 下的 parquet / CSV / JSON / Markdown

本模块对应项目 Stage 3 的特征工程功能。

入口命令: new-energy-sys build-features --config <path>
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.features import build_stage_three_features, write_feature_report
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """解析第三阶段命令行参数。

    默认输入使用当前主实验链路的阶段二清洗结果。保留 --input 参数，是为了后续
    切换站点、年份或接入真实气象数据时，不需要修改代码即可复用同一入口。
    """

    parser = argparse.ArgumentParser(description="执行 Stage 3 特征工程。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument(
        "--input",
        default="data/processed/nrel_opsd/stage2_cleaned_hourly_dataset.parquet",
        help="Stage 2 清洗后数据集路径（相对于项目根目录）。",
    )
    return parser.parse_args()


def main() -> None:
    """执行 Stage 3 核心逻辑：特征构建 → 报告 → 落盘产物。"""
    args = parse_args()
    runtime = load_config(args.config)
    processed_dir = ensure_dir(runtime.processed_dir)

    input_path = runtime.root_dir / args.input
    frame = pd.read_parquet(input_path)

    result = build_stage_three_features(frame, runtime.raw)

    dataset_path = processed_dir / "stage3_feature_dataset.parquet"
    preview_path = processed_dir / "stage3_feature_dataset_preview.csv"
    report_json_path = processed_dir / "stage3_feature_report.json"
    report_md_path = processed_dir / "stage3_feature_report.md"

    result.dataset.to_parquet(dataset_path, index=False)
    result.dataset.head(200).to_csv(preview_path, index=False)
    report_json_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_feature_report(result.report, report_md_path)

    print(f"特征数据: {dataset_path}")
    print(f"特征预览: {preview_path}")
    print(f"质量报告JSON: {report_json_path}")
    print(f"质量报告Markdown: {report_md_path}")
    print(f"最终样本数: {len(result.dataset)}")
    print(f"派生特征数: {result.report['engineered_feature_count']}")


if __name__ == "__main__":
    main()
