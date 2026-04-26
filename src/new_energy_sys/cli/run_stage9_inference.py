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

    parser = argparse.ArgumentParser(description="Run Stage9 main-model inference.")
    parser.add_argument("--config", required=True, help="Path to JSON data-source configuration.")
    parser.add_argument("--input", required=True, help="Feature dataset path relative to project root.")
    parser.add_argument(
        "--model-bundle",
        default=None,
        help=(
            "Model bundle path. Defaults to the Stage8 selected "
            "LightGBM history_only t+24h bundle under processed_dir."
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="stage9_main_model",
        help="Output filename prefix written under processed_dir.",
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
