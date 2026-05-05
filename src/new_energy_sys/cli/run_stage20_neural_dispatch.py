"""Stage20 调度侧深度学习补强 CLI。

两个实验：
1. DL 预测驱动调度消融
2. MLP 调度策略蒸馏

运行方式:
    $env:PYTHONPATH='src'
    python -m new_energy_sys.cli.run_stage20_neural_dispatch --config <path> ...

Output (写入 processed_dir):
    {prefix}_dl_dispatch_metrics.csv
    {prefix}_neural_policy_replay.csv
    {prefix}_neural_policy_metrics.csv
    {prefix}_report.json
    {prefix}_report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage20_neural_dispatch import (
    POLICY_MODE_TWO_STAGE,
    run_stage20_neural_dispatch,
    write_stage20b_report,
    write_stage20_json,
    write_stage20_report,
)


def parse_args() -> argparse.Namespace:
    """解析 Stage20 命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Stage20: 调度侧 DL 预测消融 + MLP 策略蒸馏。"
    )
    parser.add_argument(
        "--config", required=True,
        help="JSON 数据源配置文件路径。",
    )
    parser.add_argument(
        "--stage9-predictions", required=True,
        help="Stage9 LightGBM 预测 CSV 路径。",
    )
    parser.add_argument(
        "--stage14-predictions", required=True,
        help="Stage14 DL 预测 CSV 路径。",
    )
    parser.add_argument(
        "--stage12-results", required=True,
        help="Stage12 rolling 优化结果 CSV 路径（含 planned_charge/discharge_kw）。",
    )
    parser.add_argument(
        "--feature-input", required=True,
        help="Stage3 特征 parquet 路径（市场信号对齐）。",
    )
    parser.add_argument(
        "--horizon-hours", type=int, default=24,
        help="预测时距，固定 t+24h。",
    )
    parser.add_argument(
        "--dl-candidates",
        default="tcn:history_only,tcn:csi_enhanced,dlinear:history_only",
        help="逗号分隔的 model:feature_set 候选对。",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="输出文件名前缀（写入 processed_dir）。",
    )
    parser.add_argument(
        "--skip-policy", action="store_true",
        help="跳过 MLP 策略蒸馏，只跑 DL 预测消融。",
    )
    parser.add_argument(
        "--policy-mode",
        choices=["regression", "two-stage"],
        default="two-stage",
        help="Policy distillation mode. two-stage is the Stage20B default.",
    )
    parser.add_argument(
        "--policy-start",
        default="2021-01-01",
        help="Inclusive UTC start timestamp for two-stage policy distillation.",
    )
    parser.add_argument(
        "--policy-end",
        default="2022-12-31",
        help="Inclusive UTC end timestamp for two-stage policy distillation.",
    )
    parser.add_argument(
        "--action-eps-ratio",
        type=float,
        default=0.01,
        help="Direction-label threshold ratio relative to max charge/discharge power.",
    )
    parser.add_argument(
        "--policy-epochs", type=int, default=50,
        help="MLP policy 最大训练轮数。",
    )
    parser.add_argument(
        "--policy-patience", type=int, default=10,
        help="MLP policy 早停耐心值。",
    )
    parser.add_argument(
        "--policy-hidden-size", type=int, default=128,
        help="MLP 隐层大小。",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    """解析项目内路径，支持绝对路径和相对仓库根目录路径。"""
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def main() -> None:
    """Stage20 入口：加载数据 → 运行消融+蒸馏 → 落盘产物。"""

    args = parse_args()
    runtime = load_config(args.config)
    output_prefix = args.output_prefix or (
        "stage20b_two_stage_policy"
        if args.policy_mode == POLICY_MODE_TWO_STAGE
        else "stage20_neural_dispatch"
    )

    # Resolve input paths
    stage9_path = _resolve_project_path(runtime.root_dir, args.stage9_predictions)
    stage14_path = _resolve_project_path(runtime.root_dir, args.stage14_predictions)
    stage12_path = _resolve_project_path(runtime.root_dir, args.stage12_results)
    feature_path = _resolve_project_path(runtime.root_dir, args.feature_input)

    print("Stage20: 调度侧深度学习补强")
    print(f"  Config:        {args.config}")
    print(f"  Stage9 preds:  {stage9_path}")
    print(f"  Stage14 preds: {stage14_path}")
    print(f"  Stage12 results:{stage12_path}")
    print(f"  Feature input: {feature_path}")
    print(f"  Horizon:       {args.horizon_hours}h")
    print(f"  Skip policy:   {args.skip_policy}")
    print(f"  Policy mode:   {args.policy_mode}")

    # Load data
    stage9 = pd.read_csv(stage9_path)
    stage14 = pd.read_csv(stage14_path)
    stage12 = pd.read_csv(stage12_path)
    feature_frame = pd.read_parquet(feature_path)

    print(f"  Stage9 rows:   {len(stage9)}")
    print(f"  Stage14 rows:  {len(stage14)}")
    print(f"  Stage12 rows:  {len(stage12)}")

    # Parse DL candidates
    dl_candidates: list[dict[str, str]] = []
    for pair in args.dl_candidates.split(","):
        pair = pair.strip()
        if ":" not in pair:
            print(f"  WARNING: malformed candidate '{pair}', expected model:feature_set")
            continue
        model, feature_set = pair.split(":", 1)
        dl_candidates.append({"model": model.strip(), "feature_set": feature_set.strip()})
    print(f"  DL candidates: {dl_candidates}")

    # Output paths.  Stage20B uses concise file names because the prefix already
    # identifies the two-stage policy artifact family.
    if args.policy_mode == POLICY_MODE_TWO_STAGE:
        output_paths = {
            "results_csv": runtime.processed_dir / f"{output_prefix}_dl_dispatch_metrics.csv",
            "metrics_csv": runtime.processed_dir / f"{output_prefix}_dl_dispatch_metrics.csv",
            "policy_replay_csv": runtime.processed_dir / f"{output_prefix}_replay.csv",
            "policy_metrics_csv": runtime.processed_dir / f"{output_prefix}_metrics.csv",
            "report_json": runtime.processed_dir / f"{output_prefix}_report.json",
            "report_md": runtime.processed_dir / f"{output_prefix}_report.md",
        }
    else:
        output_paths = {
            "results_csv": runtime.processed_dir / f"{output_prefix}_dl_dispatch_metrics.csv",
            "metrics_csv": runtime.processed_dir / f"{output_prefix}_dl_dispatch_metrics.csv",
            "policy_replay_csv": runtime.processed_dir / f"{output_prefix}_neural_policy_replay.csv",
            "policy_metrics_csv": runtime.processed_dir / f"{output_prefix}_neural_policy_metrics.csv",
            "report_json": runtime.processed_dir / f"{output_prefix}_report.json",
            "report_md": runtime.processed_dir / f"{output_prefix}_report.md",
        }

    try:
        result = run_stage20_neural_dispatch(
            stage9_predictions=stage9,
            stage14_predictions=stage14,
            stage12_results=stage12,
            feature_frame=feature_frame,
            config=runtime.raw,
            dl_candidates=dl_candidates,
            horizon_hours=args.horizon_hours,
            policy_hidden_size=args.policy_hidden_size,
            policy_epochs=args.policy_epochs,
            policy_patience=args.policy_patience,
            policy_mode=args.policy_mode,
            policy_start=args.policy_start,
            policy_end=args.policy_end,
            action_eps_ratio=args.action_eps_ratio,
            skip_policy=args.skip_policy,
            output_paths=output_paths,
        )
    except Exception as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    # Write the remaining outputs that the orchestrator didn't write
    if not result.dl_dispatch_metrics.empty:
        result.dl_dispatch_metrics.to_csv(output_paths["results_csv"], index=False)
    if not result.neural_policy_replay.empty:
        result.neural_policy_replay.to_csv(output_paths["policy_replay_csv"], index=False)
    if not result.neural_policy_metrics.empty:
        result.neural_policy_metrics.to_csv(output_paths["policy_metrics_csv"], index=False)

    # Attach prior Stage20 regression metrics if they exist.  This keeps the
    # Stage20B report comparable without mutating the Stage20 audit artifacts.
    baseline_report = runtime.processed_dir / "stage20_neural_dispatch_report.json"
    if args.policy_mode == POLICY_MODE_TWO_STAGE and baseline_report.exists():
        try:
            import json

            baseline = json.loads(baseline_report.read_text(encoding="utf-8"))
            result.report["stage20_regression_baseline"] = baseline.get("policy_training", {})
        except Exception as exc:
            result.report["stage20_regression_baseline_error"] = str(exc)

    # Report (overwrite if orchestrator already wrote it)
    write_stage20_json(result.report, output_paths["report_json"])
    if args.policy_mode == POLICY_MODE_TWO_STAGE:
        write_stage20b_report(
            result.report,
            result.dl_dispatch_metrics,
            result.neural_policy_metrics,
            output_paths["report_md"],
        )
    else:
        write_stage20_report(
            result.report,
            result.dl_dispatch_metrics,
            result.neural_policy_metrics,
            output_paths["report_md"],
        )

    print(f"\nStage20 outputs:")
    for key, p in output_paths.items():
        exists = "OK" if p.exists() else "MISSING"
        print(f"  [{exists}] {key}: {p}")


if __name__ == "__main__":
    main()
