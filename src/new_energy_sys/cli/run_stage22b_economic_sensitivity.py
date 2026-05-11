"""Stage22B economic condition sensitivity CLI.

Entry point: new-energy-run-stage22b-economic-sensitivity

Two modes:
  --mode postprocess  (default)  Recompute degradation cost + additional
                                 revenue for every economic-parameter
                                 combination on top of existing Stage22
                                 dispatch results.

  --mode spread                  Re-run dispatch with amplified price
                                 volatility on three representative configs.
                                 Requires --config, --predictions, and
                                 --feature-input.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.stage22_degradation_aware_config import (
    run_stage22b_economic_sensitivity,
    run_stage22b_spread_amplification,
)


def _parse_float_list(value: str) -> list[float]:
    return [float(token.strip()) for token in value.split(",") if token.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage22B economic condition sensitivity analysis."
    )
    parser.add_argument(
        "--mode",
        choices=["postprocess", "spread"],
        default="postprocess",
        help="postprocess: recompute economics on Stage22 results. "
             "spread: re-run dispatch with amplified price volatility.",
    )
    parser.add_argument(
        "--stage22-metrics",
        required=True,
        help="Comma-separated paths to Stage22 metrics CSV files. "
             "Merged by config_id with deduplication.",
    )
    parser.add_argument(
        "--replacement-costs",
        default="150,100,75,50",
        help="Comma-separated replacement costs (EUR/kWh).",
    )
    parser.add_argument(
        "--cycle-life-multipliers",
        default="1.0,2.0,3.0",
        help="Comma-separated cycle-life multipliers.",
    )
    parser.add_argument(
        "--calendar-fade-rates",
        default="0.015,0.01,0.005,0.0",
        help="Comma-separated annual calendar fade rates.",
    )
    parser.add_argument(
        "--discharge-values",
        default="0.0,10.0,20.0",
        help="Comma-separated discharge value (EUR/MWh).",
    )
    parser.add_argument(
        "--capacity-values",
        default="0.0,20.0,50.0",
        help="Comma-separated capacity value (EUR/kW·year).",
    )
    parser.add_argument(
        "--fixed-subsidies",
        default="0.0,20.0,50.0",
        help="Comma-separated fixed subsidy (EUR/kWh).",
    )
    parser.add_argument(
        "--spread-amplification",
        default=None,
        help="Comma-separated spread amplification factors (e.g. 1.5,2.0,3.0). "
             "Only used in --mode spread.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to JSON data-source config (required for --mode spread).",
    )
    parser.add_argument(
        "--predictions",
        default=None,
        help="Path to Stage9 predictions CSV (required for --mode spread).",
    )
    parser.add_argument(
        "--feature-input",
        default=None,
        help="Path to Stage3 features parquet (required for --mode spread).",
    )
    parser.add_argument(
        "--min-net-revenue-eur",
        type=float,
        default=0.0,
    )
    parser.add_argument(
        "--min-soh",
        type=float,
        default=0.90,
        help="Minimum SOH filter (hard constraint). Report also gives 0.95 conservative.",
    )
    parser.add_argument(
        "--output-prefix",
        default="stage22b_economic_sensitivity",
    )
    return parser.parse_args()


def _resolve_project_path(root_dir: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root_dir / candidate


def _load_and_merge_metrics(path_strings: str) -> pd.DataFrame:
    """Load multiple CSV files, merge, and deduplicate by config_id."""
    frames: list[pd.DataFrame] = []
    for raw in path_strings.split(","):
        p = raw.strip()
        if not p:
            continue
        path = Path(p)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise FileNotFoundError(f"Metrics file not found: {path}")
        df = pd.read_csv(path)
        print(f"[stage22b] loaded {len(df)} rows from {path}")
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates(subset="config_id", keep="last")
    after = len(merged)
    if before != after:
        print(f"[stage22b] deduplicated {before} → {after} rows by config_id")
    return merged


def main() -> None:
    args = parse_args()
    metrics_csv_list = args.stage22_metrics

    if args.mode == "spread":
        if not args.config or not args.predictions or not args.feature_input:
            raise SystemExit(
                "--mode spread requires --config, --predictions, and --feature-input"
            )
        runtime = load_config(args.config)
        root = runtime.root_dir
        predictions = pd.read_csv(_resolve_project_path(root, args.predictions))
        feature_frame = pd.read_parquet(_resolve_project_path(root, args.feature_input))
        amp_factors = (
            _parse_float_list(args.spread_amplification)
            if args.spread_amplification
            else None
        )
        output_paths = {
            "metrics_csv": runtime.processed_dir / f"{args.output_prefix}_spread_metrics.csv",
            "report_json": runtime.processed_dir / f"{args.output_prefix}_spread_report.json",
            "report_md": runtime.processed_dir / f"{args.output_prefix}_spread_report.md",
        }
        result = run_stage22b_spread_amplification(
            predictions=predictions,
            feature_frame=feature_frame,
            config=runtime.raw,
            amplification_factors=amp_factors,
            capacity_kw=float(runtime.raw["site"]["capacity_kw"]),
            output_paths=output_paths,
        )
        print(result.report.get("scope_note", ""))
        for entry in result.report.get("results", []):
            print(
                f"  {entry['label']} amp={entry['amplification_factor']}: "
                f"net={entry['net_incremental_revenue_eur']:.2f}, "
                f"SOH={entry['soh_end']:.6f}"
            )
    else:
        # --mode postprocess (default)
        metrics = _load_and_merge_metrics(metrics_csv_list)
        output_paths = {
            "metrics_csv": Path(f"{args.output_prefix}_metrics.csv"),
            "report_json": Path(f"{args.output_prefix}_report.json"),
            "report_md": Path(f"{args.output_prefix}_report.md"),
        }
        result = run_stage22b_economic_sensitivity(
            metrics=metrics,
            replacement_costs=_parse_float_list(args.replacement_costs),
            cycle_life_multipliers=_parse_float_list(args.cycle_life_multipliers),
            calendar_fade_rates=_parse_float_list(args.calendar_fade_rates),
            discharge_value_eur_per_mwh=_parse_float_list(args.discharge_values),
            capacity_value_eur_per_kw_year=_parse_float_list(args.capacity_values),
            fixed_subsidy_eur_per_kwh=_parse_float_list(args.fixed_subsidies),
            min_net_revenue_eur=args.min_net_revenue_eur,
            min_soh=args.min_soh,
            output_paths=output_paths,
        )
        print(result.report["decision"])


if __name__ == "__main__":
    main()
