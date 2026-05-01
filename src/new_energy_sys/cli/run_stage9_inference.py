"""主模型推理模块。

模块设计原则：
- 加载 Stage 8 选出的最优表格模型，对特征数据集执行推理
- 输出预测结果、评估指标及推理报告
- 支持自定义模型包路径，默认使用 processed_dir 下 Stage 8 最优模型

本模块对应项目 Stage 9 的主模型推理功能。

入口命令: new-energy-sys run-stage9 --config <path> --input <path>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage9_inference import (
    DEFAULT_STAGE9_MODEL_BUNDLE,
    run_stage9_inference,
    write_stage9_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage9 主模型推理命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 Stage 9 主模型推理。")
    parser.add_argument("--config", required=True, help="JSON 数据源配置文件路径。")
    parser.add_argument("--input", required=True, help="特征数据集路径（相对于项目根目录）。")
    parser.add_argument(
        "--model-bundle",
        default=None,
        help=(
            "模型包路径。默认使用 processed_dir 下 Stage 8 选出的 "
            "LightGBM history_only t+24h 模型包。"
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="stage9_main_model",
        help="输出文件名前缀（写入 processed_dir）。",
    )
    return parser.parse_args()


def _resolve_path(root_dir: Path, processed_dir: Path, value: str | None) -> Path:
    """解析模型路径。

    - 绝对路径: 原样使用。
    - 显式相对路径: 相对项目根目录。
    - 默认路径: 相对 processed_dir，因为 Stage8 模型保存在 processed_dir/stage8_models。
    """

    if value is None:
        return processed_dir / DEFAULT_STAGE9_MODEL_BUNDLE
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """执行 Stage9 推理并落盘 CSV、JSON、Markdown 三类产物。"""

    args = parse_args()
    runtime = load_config(args.config)
    input_path = runtime.root_dir / args.input
    model_bundle_path = _resolve_path(runtime.root_dir, runtime.processed_dir, args.model_bundle)

    frame = pd.read_parquet(input_path)
    output_paths = {
        "predictions_csv": runtime.processed_dir / f"{args.output_prefix}_predictions.csv",
        "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_metrics.csv",
        "report_json": runtime.processed_dir / f"{args.output_prefix}_report.json",
        "report_md": runtime.processed_dir / f"{args.output_prefix}_report.md",
    }

    result = run_stage9_inference(
        frame,
        runtime.raw,
        model_bundle_path=model_bundle_path,
        output_paths=output_paths,
    )

    result.predictions.to_csv(output_paths["predictions_csv"], index=False)
    result.metrics.to_csv(output_paths["metrics_csv"], index=False)
    output_paths["report_json"].write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_stage9_report(result.report, result.metrics, output_paths["report_md"])

    print(f"Stage9 predictions: {output_paths['predictions_csv']}")
    print(f"Stage9 metrics: {output_paths['metrics_csv']}")
    print(f"Stage9 report JSON: {output_paths['report_json']}")
    print(f"Stage9 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
