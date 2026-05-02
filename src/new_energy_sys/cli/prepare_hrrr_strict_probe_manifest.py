"""Create the mandatory strict HRRR probe manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.config import load_config
from new_energy_sys.hrrr_point_forecast import (
    DEFAULT_CLOUD_BACKENDS,
    build_hrrr_strict_probe_manifest,
    parse_lead_times,
    write_hrrr_point_forecast_manifest,
)


def parse_args() -> argparse.Namespace:
    """Parse local strict-probe manifest generation arguments."""

    parser = argparse.ArgumentParser(description="Create a stratified HRRR strict probe manifest.")
    parser.add_argument("--config", required=True, help="Project data-source config JSON.")
    parser.add_argument("--year", type=int, default=2022, help="Forecast valid-time year.")
    parser.add_argument("--lead-times", default=",".join(str(value) for value in range(24, 49)))
    parser.add_argument("--bbox-deg", type=float, default=0.05, help="Station bbox half-width for NOMADS fallback.")
    parser.add_argument("--window-hours", type=int, default=48, help="Hours per seasonal probe window.")
    parser.add_argument(
        "--local-output-budget-gb",
        type=float,
        default=0.05,
        help="Budget for returned strict-probe parquet/audit artifacts.",
    )
    parser.add_argument(
        "--remote-read-budget-gb",
        type=float,
        default=8.0,
        help="Safety cap for HRRR Zarr bytes read by the strict probe.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/raw/hrrr_point_forecast_cache",
        help="Cloud/local cache directory for stable HRRR grid metadata.",
    )
    parser.add_argument(
        "--backends",
        default=",".join(DEFAULT_CLOUD_BACKENDS),
        help="Comma-separated backend order. Defaults to zarr_chunk.",
    )
    parser.add_argument(
        "--output-parquet",
        default="data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_probe_strict.parquet",
        help="Strict probe forecast-weather parquet path.",
    )
    parser.add_argument(
        "--audit-json",
        default="reports/hrrr_point_forecast_probe_strict_audit.json",
        help="Strict probe audit JSON path.",
    )
    parser.add_argument(
        "--manifest-json",
        default="reports/hrrr_point_forecast_probe_strict_manifest.json",
        help="Strict probe manifest JSON output path.",
    )
    return parser.parse_args()


def main() -> None:
    """Write a strict seasonal probe manifest using the configured site."""

    args = parse_args()
    runtime = load_config(args.config)
    site = runtime.raw["site"]
    manifest = build_hrrr_strict_probe_manifest(
        year=int(args.year),
        latitude=float(site["latitude"]),
        longitude=float(site["longitude"]),
        lead_times=parse_lead_times(args.lead_times),
        bbox_deg=float(args.bbox_deg),
        local_output_budget_gb=float(args.local_output_budget_gb),
        remote_read_budget_gb=float(args.remote_read_budget_gb),
        cache_dir=args.cache_dir,
        backends=tuple(item.strip() for item in args.backends.split(",") if item.strip()),
        window_hours=int(args.window_hours),
        output_parquet=args.output_parquet,
        audit_json=args.audit_json,
    )
    write_hrrr_point_forecast_manifest(manifest, Path(args.manifest_json))

    print(f"Strict probe manifest: {Path(args.manifest_json)}")
    print(f"Windows: {len(manifest['collection']['windows'])}")
    print(f"Expected probe rows: {manifest['collection']['expected_rows']}")
    print(f"Output parquet: {manifest['outputs']['forecast_weather_parquet']}")
    print(f"Audit JSON: {manifest['outputs']['audit_json']}")


if __name__ == "__main__":
    main()
