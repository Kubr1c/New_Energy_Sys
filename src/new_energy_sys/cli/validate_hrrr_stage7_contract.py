"""Validate the HRRR forecast-weather contract before Stage7 reruns."""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.config import load_config
from new_energy_sys.hrrr_stage7_contract import (
    validate_hrrr_stage7_contract_paths,
    write_hrrr_stage7_contract_report,
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the HRRR Stage7 data-contract gate."""

    parser = argparse.ArgumentParser(description="Validate HRRR forecast weather before running Stage7.")
    parser.add_argument("--config", required=True, help="Project data-source config JSON.")
    parser.add_argument("--stage2-input", required=True, help="Stage2 cleaned hourly parquet.")
    parser.add_argument("--stage3-input", required=True, help="Stage3 feature parquet.")
    parser.add_argument("--hrrr-weather", required=True, help="Merged HRRR forecast-weather parquet.")
    parser.add_argument("--hrrr-audit", required=True, help="Merged HRRR audit JSON.")
    parser.add_argument("--output-json", help="Optional output JSON report path.")
    parser.add_argument("--output-md", help="Optional output Markdown report path.")
    return parser.parse_args()


def main() -> None:
    """Run the HRRR contract gate and fail with a non-zero exit code on blockers."""

    args = parse_args()
    runtime = load_config(args.config)
    output_json = Path(args.output_json) if args.output_json else runtime.processed_dir / "stage7_hrrr_contract_audit.json"
    output_md = Path(args.output_md) if args.output_md else runtime.processed_dir / "stage7_hrrr_contract_audit.md"

    result = validate_hrrr_stage7_contract_paths(
        config=runtime.raw,
        stage2_path=runtime.root_dir / args.stage2_input,
        stage3_path=runtime.root_dir / args.stage3_input,
        hrrr_weather_path=runtime.root_dir / args.hrrr_weather,
        hrrr_audit_path=runtime.root_dir / args.hrrr_audit,
    )
    write_hrrr_stage7_contract_report(result.report, json_path=output_json, markdown_path=output_md)

    print(f"HRRR Stage7 contract passed: {result.passed}")
    print(f"Decision: {result.report['decision']}")
    print(f"Report JSON: {output_json}")
    print(f"Report MD: {output_md}")
    if not result.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
