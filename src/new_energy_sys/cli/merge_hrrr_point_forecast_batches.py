"""Merge monthly HRRR point forecast batches into a Stage7 weather table."""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.hrrr_point_forecast import merge_hrrr_point_forecast_batches


def parse_args() -> argparse.Namespace:
    """Parse arguments for the monthly HRRR batch merge gate."""

    parser = argparse.ArgumentParser(description="Merge monthly HRRR point forecast batch outputs.")
    parser.add_argument("--input-dir", required=True, help="Directory containing monthly parquet outputs.")
    parser.add_argument("--audit-dir", required=True, help="Directory containing monthly audit JSON files.")
    parser.add_argument("--output-parquet", required=True, help="Merged Stage7 forecast-weather parquet.")
    parser.add_argument("--audit-json", required=True, help="Merged validation audit JSON.")
    parser.add_argument("--expected-start", required=True, help="Expected first valid date or UTC timestamp.")
    parser.add_argument("--expected-end", required=True, help="Expected final valid date or UTC timestamp.")
    parser.add_argument("--lead-time", required=True, type=int, help="Stage7 forecast horizon hour.")
    parser.add_argument("--min-lead-time", type=int, help="Minimum accepted HRRR lead time. Defaults to --lead-time.")
    parser.add_argument("--max-lead-time", type=int, help="Maximum accepted HRRR lead time. Defaults to --lead-time.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audit = merge_hrrr_point_forecast_batches(
        input_dir=Path(args.input_dir),
        audit_dir=Path(args.audit_dir),
        output_parquet=Path(args.output_parquet),
        audit_json=Path(args.audit_json),
        expected_start=args.expected_start,
        expected_end=args.expected_end,
        lead_time_hour=int(args.lead_time),
        min_lead_time_hour=args.min_lead_time,
        max_lead_time_hour=args.max_lead_time,
    )

    print(f"Merge status: {audit['status']}")
    print(f"Input rows: {audit.get('input_rows', 0)}")
    print(f"Output rows: {audit.get('output_rows', 0)}/{audit.get('expected_rows', 0)}")
    print(f"Missing timestamps: {len(audit.get('missing_timestamps', []))}")
    print(f"Remote read bytes: {audit.get('remote_read_bytes', 0)}")
    print(f"Audit JSON: {Path(args.audit_json)}")
    if audit["status"] == "failed_validation":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
