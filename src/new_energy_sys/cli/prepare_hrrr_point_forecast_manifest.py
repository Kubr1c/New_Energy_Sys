"""Create a reproducible manifest for near-source HRRR point extraction."""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.hrrr_point_forecast import (
    DEFAULT_CLOUD_BACKENDS,
    build_hrrr_point_forecast_manifest,
    parse_lead_times,
    write_hrrr_point_forecast_manifest,
)


def parse_args() -> argparse.Namespace:
    """Parse the local manifest-generation command.

    This command does not read HRRR data. It only creates the JSON contract that
    a cloud runner can execute near the public HRRR Zarr archive.
    """

    parser = argparse.ArgumentParser(description="Create an HRRR point forecast batch manifest.")
    parser.add_argument("--start", required=True, help="Start date or UTC timestamp.")
    parser.add_argument("--end", required=True, help="End date or UTC timestamp.")
    parser.add_argument("--latitude", required=True, type=float, help="Site latitude.")
    parser.add_argument("--longitude", required=True, type=float, help="Site longitude.")
    parser.add_argument("--lead-times", required=True, help="Comma-separated lead times, e.g. 24.")
    parser.add_argument(
        "--lead-times-as-candidates",
        action="store_true",
        help="Treat lead times as ordered candidates and keep the first available row per valid timestamp.",
    )
    parser.add_argument("--bbox-deg", type=float, default=0.05, help="NOMADS fallback bbox half-width.")
    parser.add_argument(
        "--local-output-budget-gb",
        type=float,
        default=10.0,
        help="Budget for artifacts downloaded back to the local project.",
    )
    parser.add_argument(
        "--remote-read-budget-gb",
        type=float,
        default=120.0,
        help="Safety cap for bytes read by the near-source cloud job.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/raw/hrrr_point_forecast_cache",
        help="Cloud/local cache directory for stable HRRR grid metadata.",
    )
    parser.add_argument(
        "--backends",
        default=",".join(DEFAULT_CLOUD_BACKENDS),
        help="Comma-separated backend order for the batch runner. Defaults to zarr_chunk.",
    )
    parser.add_argument("--output-parquet", required=True, help="Batch forecast-weather parquet path.")
    parser.add_argument("--audit-json", required=True, help="Batch audit JSON path.")
    parser.add_argument("--manifest-json", required=True, help="Manifest JSON output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lead_times = parse_lead_times(args.lead_times)
    backends = tuple(item.strip() for item in args.backends.split(",") if item.strip())
    manifest = build_hrrr_point_forecast_manifest(
        start=args.start,
        end=args.end,
        latitude=float(args.latitude),
        longitude=float(args.longitude),
        lead_times=lead_times,
        lead_times_as_candidates=bool(args.lead_times_as_candidates),
        bbox_deg=float(args.bbox_deg),
        local_output_budget_gb=float(args.local_output_budget_gb),
        remote_read_budget_gb=float(args.remote_read_budget_gb),
        cache_dir=args.cache_dir,
        backends=backends,
        output_parquet=args.output_parquet,
        audit_json=args.audit_json,
    )
    write_hrrr_point_forecast_manifest(manifest, Path(args.manifest_json))

    print(f"Manifest JSON: {Path(args.manifest_json)}")
    print(f"Expected rows: {manifest['collection']['expected_rows']}")
    print(f"Lead times as candidates: {manifest['collection']['lead_times_as_candidates']}")
    print(f"Backends: {','.join(manifest['execution']['backends'])}")
    print(f"Local output budget bytes: {manifest['execution']['local_output_budget_bytes']}")
    print(f"Remote read budget bytes: {manifest['execution']['remote_read_budget_bytes']}")


if __name__ == "__main__":
    main()
