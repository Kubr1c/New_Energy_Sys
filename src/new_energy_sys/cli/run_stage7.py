from __future__ import annotations

import argparse

from new_energy_sys.config import load_config
from new_energy_sys.stage7_forecast_validation import run_stage7_forecast_validation


def parse_args() -> argparse.Namespace:
    """Parse Stage7 forecast-weather validation arguments."""

    parser = argparse.ArgumentParser(description="Run Stage7 forecast-weather availability validation.")
    parser.add_argument("--config", required=True, help="Main data-source config.")
    parser.add_argument("--stage3-input", required=True, help="Stage3 parquet path relative to project root.")
    parser.add_argument("--forecast-weather", required=True, help="Forecast weather CSV/parquet path relative to project root.")
    return parser.parse_args()


def main() -> None:
    """Execute Stage7 and persist datasets, TCN artifacts, and decision report."""

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
