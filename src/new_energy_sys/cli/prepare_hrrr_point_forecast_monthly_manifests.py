"""Create monthly HRRR point forecast manifests for resumable cloud runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.hrrr_point_forecast import (
    DEFAULT_CLOUD_BACKENDS,
    build_hrrr_monthly_point_forecast_manifests,
    parse_lead_times,
    write_hrrr_point_forecast_manifest,
)


def parse_args() -> argparse.Namespace:
    """Parse arguments for generating one manifest per month."""

    parser = argparse.ArgumentParser(description="Create monthly HRRR point forecast manifests.")
    parser.add_argument("--year", required=True, type=int, help="Forecast valid-time year, e.g. 2022.")
    parser.add_argument("--latitude", required=True, type=float, help="Site latitude.")
    parser.add_argument("--longitude", required=True, type=float, help="Site longitude.")
    parser.add_argument("--lead-times", required=True, help="Comma-separated lead times, e.g. 24 or 24,25,26,27,28,29.")
    parser.add_argument(
        "--lead-times-as-candidates",
        action="store_true",
        help="Treat lead times as ordered candidates and keep the first available row per valid timestamp.",
    )
    parser.add_argument("--bbox-deg", type=float, default=0.05, help="NOMADS fallback bbox half-width.")
    parser.add_argument("--manifest-dir", required=True, help="Directory for monthly manifest JSON files.")
    parser.add_argument("--output-dir", required=True, help="Directory for monthly forecast-weather parquet outputs.")
    parser.add_argument("--audit-dir", required=True, help="Directory for monthly audit JSON outputs.")
    parser.add_argument(
        "--local-output-budget-gb",
        type=float,
        default=1.0,
        help="Per-month budget for parquet/audit artifacts returned to the local project.",
    )
    parser.add_argument(
        "--remote-read-budget-gb",
        type=float,
        default=12.0,
        help="Per-month safety cap for HRRR Zarr bytes read by the cloud job.",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lead_times = parse_lead_times(args.lead_times)
    backends = tuple(item.strip() for item in args.backends.split(",") if item.strip())
    manifests = build_hrrr_monthly_point_forecast_manifests(
        year=int(args.year),
        latitude=float(args.latitude),
        longitude=float(args.longitude),
        lead_times=lead_times,
        lead_times_as_candidates=bool(args.lead_times_as_candidates),
        bbox_deg=float(args.bbox_deg),
        manifest_dir=args.manifest_dir,
        output_dir=args.output_dir,
        audit_dir=args.audit_dir,
        local_output_budget_gb=float(args.local_output_budget_gb),
        remote_read_budget_gb=float(args.remote_read_budget_gb),
        cache_dir=args.cache_dir,
        backends=backends,
    )
    for path, manifest in manifests:
        write_hrrr_point_forecast_manifest(manifest, Path(path))

    expected_rows = sum(item["collection"]["expected_rows"] for _, item in manifests)
    print(f"Monthly manifests: {len(manifests)}")
    print(f"Expected yearly rows: {expected_rows}")
    print(f"Manifest dir: {Path(args.manifest_dir)}")
    print(f"Output dir: {Path(args.output_dir)}")
    print(f"Audit dir: {Path(args.audit_dir)}")


if __name__ == "__main__":
    main()
