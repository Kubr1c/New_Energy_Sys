"""Prepare a budget-aware HRRR point forecast table for Stage7."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from new_energy_sys.hrrr_point_forecast import (
    DEFAULT_BACKENDS,
    collect_hrrr_point_forecast,
    parse_lead_times,
    write_hrrr_point_forecast_outputs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect budget-aware HRRR point forecasts.")
    parser.add_argument("--start", required=True, help="Start date or UTC timestamp.")
    parser.add_argument("--end", required=True, help="End date or UTC timestamp.")
    parser.add_argument("--latitude", required=True, type=float, help="Site latitude.")
    parser.add_argument("--longitude", required=True, type=float, help="Site longitude.")
    parser.add_argument("--lead-times", required=True, help="Comma-separated lead times, e.g. 24.")
    parser.add_argument("--bbox-deg", type=float, default=0.05, help="Half-width bbox around the site.")
    parser.add_argument("--budget-gb", type=float, required=True, help="Hard download budget in GiB.")
    parser.add_argument("--output-parquet", required=True, help="Output Stage7 forecast-weather parquet.")
    parser.add_argument("--audit-json", required=True, help="Output JSON audit report.")
    parser.add_argument(
        "--cache-dir",
        default="data/raw/hrrr_point_forecast_cache",
        help="Directory for transient small HRRR payload cache.",
    )
    parser.add_argument(
        "--backends",
        default=",".join(DEFAULT_BACKENDS),
        help="Comma-separated backend order. Defaults to nomads_bbox,zarr_chunk.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=120, help="HTTP timeout per request.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lead_times = parse_lead_times(args.lead_times)
    backends = tuple(item.strip() for item in args.backends.split(",") if item.strip())
    result = collect_hrrr_point_forecast(
        start=args.start,
        end=args.end,
        latitude=float(args.latitude),
        longitude=float(args.longitude),
        lead_times=lead_times,
        bbox_deg=float(args.bbox_deg),
        budget_gb=float(args.budget_gb),
        cache_dir=Path(args.cache_dir),
        backends=backends,
        timeout_seconds=int(args.timeout_seconds),
    )
    write_hrrr_point_forecast_outputs(
        result,
        output_parquet=Path(args.output_parquet),
        audit_json=Path(args.audit_json),
    )

    print(f"HRRR point forecast rows: {len(result.forecast_weather)}")
    print(f"Downloaded bytes: {result.audit['downloaded_bytes']}/{result.audit['budget_bytes']}")
    print(f"Audit status: {result.audit['status']}")
    print(f"Forecast parquet: {Path(args.output_parquet)}")
    print(f"Audit JSON: {Path(args.audit_json)}")
    if result.audit["status"] in {"budget_exceeded", "estimated_budget_exceeded", "failed_no_rows"}:
        raise SystemExit(2)
    if result.audit["status"] == "completed_with_missing":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
