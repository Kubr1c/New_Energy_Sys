from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from new_energy_sys.stage21_rawhide_weather_dispatch import (
    run_stage21_rawhide_weather_dispatch,
    write_stage21_json,
    write_stage21_report,
)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage21 Rawhide weather-driven price-scenario dispatch.")
    parser.add_argument("--config", required=True, help="Rawhide JSON config path.")
    parser.add_argument("--weather", required=True, help="Open-Meteo or standard weather CSV/parquet.")
    parser.add_argument("--price-scenarios", required=True, help="Stage21 market price scenario JSON.")
    parser.add_argument(
        "--feature-input",
        default="data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
        help="Optional Stage3 feature table used by the OPSD reference price scenario.",
    )
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument("--performance-ratio", type=float, default=0.82)
    args = parser.parse_args()

    config_path = Path(args.config)
    weather_path = Path(args.weather)
    scenario_path = Path(args.price_scenarios)
    with config_path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    with scenario_path.open(encoding="utf-8") as handle:
        scenario_config = json.load(handle)

    weather = _read_table(weather_path)
    feature_path = Path(args.feature_input)
    feature_frame = _read_table(feature_path) if feature_path.exists() else None

    processed_dir = Path(config["project"]["processed_dir"])
    output_paths = {
        "weather_predictions_csv": processed_dir / "stage21_rawhide_weather_predictions.csv",
        "price_scenarios_csv": processed_dir / "stage21_rawhide_price_scenarios.csv",
        "dispatch_results_csv": processed_dir / "stage21_rawhide_dispatch_results.csv",
        "dispatch_metrics_csv": processed_dir / "stage21_rawhide_dispatch_metrics.csv",
        "report_json": processed_dir / "stage21_rawhide_weather_price_dispatch_report.json",
        "report_md": processed_dir / "stage21_rawhide_weather_price_dispatch_report.md",
    }

    result = run_stage21_rawhide_weather_dispatch(
        weather,
        config,
        scenario_config,
        feature_frame=feature_frame,
        horizon_hours=args.horizon_hours,
        lookahead_hours=args.lookahead_hours,
        performance_ratio=args.performance_ratio,
        output_paths=output_paths,
    )

    processed_dir.mkdir(parents=True, exist_ok=True)
    result.weather_predictions.to_csv(output_paths["weather_predictions_csv"], index=False)
    result.price_scenarios.to_csv(output_paths["price_scenarios_csv"], index=False)
    result.dispatch_results.to_csv(output_paths["dispatch_results_csv"], index=False)
    result.dispatch_metrics.to_csv(output_paths["dispatch_metrics_csv"], index=False)
    write_stage21_json(result.report, output_paths["report_json"])
    write_stage21_report(result.report, result.dispatch_metrics, output_paths["report_md"])

    print(f"Stage21 weather predictions: {output_paths['weather_predictions_csv']}")
    print(f"Stage21 price scenarios: {output_paths['price_scenarios_csv']}")
    print(f"Stage21 dispatch results: {output_paths['dispatch_results_csv']}")
    print(f"Stage21 dispatch metrics: {output_paths['dispatch_metrics_csv']}")
    print(f"Stage21 report JSON: {output_paths['report_json']}")
    print(f"Stage21 report Markdown: {output_paths['report_md']}")


if __name__ == "__main__":
    main()
