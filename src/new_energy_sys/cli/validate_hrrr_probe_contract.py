"""Validate strict HRRR probe artifacts and generate visual review outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.hrrr_probe_contract import (
    validate_hrrr_probe_contract_paths,
    write_hrrr_probe_review,
)


def _resolve(root: Path, value: str) -> Path:
    """Resolve project-relative CLI paths without changing absolute paths."""

    path = Path(value)
    return path if path.is_absolute() else root / path


def parse_args() -> argparse.Namespace:
    """Parse strict probe validation arguments."""

    parser = argparse.ArgumentParser(description="Validate HRRR strict probe before full extraction.")
    parser.add_argument("--config", required=True, help="Project data-source config JSON.")
    parser.add_argument("--stage2-input", required=True, help="Stage2 cleaned hourly parquet.")
    parser.add_argument("--hrrr-weather", required=True, help="Strict probe forecast-weather parquet.")
    parser.add_argument("--hrrr-audit", required=True, help="Strict probe audit JSON.")
    parser.add_argument("--manifest", required=True, help="Strict probe manifest JSON.")
    parser.add_argument("--output-json", default="reports/hrrr_probe_weather_review.json")
    parser.add_argument("--output-md", default="reports/hrrr_probe_weather_review.md")
    parser.add_argument("--figure", default="reports/figures/hrrr_probe_weather_features.png")
    return parser.parse_args()


def main() -> None:
    """Run strict probe validation, write review artifacts, and fail on blockers."""

    args = parse_args()
    runtime = load_config(args.config)
    stage2_path = _resolve(runtime.root_dir, args.stage2_input)
    hrrr_weather_path = _resolve(runtime.root_dir, args.hrrr_weather)

    result = validate_hrrr_probe_contract_paths(
        config=runtime.raw,
        stage2_path=stage2_path,
        hrrr_weather_path=hrrr_weather_path,
        hrrr_audit_path=_resolve(runtime.root_dir, args.hrrr_audit),
        manifest_path=_resolve(runtime.root_dir, args.manifest),
    )
    write_hrrr_probe_review(
        report=result.report,
        stage2=pd.read_parquet(stage2_path),
        hrrr_weather=pd.read_parquet(hrrr_weather_path),
        json_path=_resolve(runtime.root_dir, args.output_json),
        markdown_path=_resolve(runtime.root_dir, args.output_md),
        figure_path=_resolve(runtime.root_dir, args.figure),
    )

    print(f"HRRR strict probe passed: {result.passed}")
    print(f"Decision: {result.report['decision']}")
    print(f"Review JSON: {_resolve(runtime.root_dir, args.output_json)}")
    print(f"Review MD: {_resolve(runtime.root_dir, args.output_md)}")
    print(f"Review figure: {_resolve(runtime.root_dir, args.figure)}")
    if not result.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
