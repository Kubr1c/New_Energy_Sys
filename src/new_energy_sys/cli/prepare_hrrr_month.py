from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_energy_sys.hrrr import build_hrrr_monthly_point_table
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """Parse arguments for fixed-lead monthly HRRR extraction."""

    parser = argparse.ArgumentParser(description="Prepare one month of strict HRRR point-forecast weather.")
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD.")
    parser.add_argument("--latitude", required=True, type=float, help="Station latitude.")
    parser.add_argument("--longitude", required=True, type=float, help="Station longitude.")
    parser.add_argument("--lead-time-hour", required=True, type=int, help="Fixed HRRR forecast lead time.")
    parser.add_argument("--cache-dir", required=True, help="Directory for subset GRIB2 cache files.")
    parser.add_argument("--output-csv", required=True, help="Output CSV path for normalized strict weather.")
    parser.add_argument("--output-json", required=True, help="Output JSON path for summary metadata.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_csv = Path(args.output_csv)
    output_json = Path(args.output_json)
    cache_dir = ensure_dir(Path(args.cache_dir))
    ensure_dir(output_csv.parent)
    ensure_dir(output_json.parent)

    table = build_hrrr_monthly_point_table(
        start=args.start,
        end=args.end,
        latitude=args.latitude,
        longitude=args.longitude,
        lead_time_hour=args.lead_time_hour,
        cache_dir=cache_dir,
    )
    table.to_csv(output_csv, index=False)

    report = {
        "start": args.start,
        "end": args.end,
        "latitude": args.latitude,
        "longitude": args.longitude,
        "lead_time_hour": args.lead_time_hour,
        "rows": int(len(table)),
        "min_timestamp": str(table["timestamp"].min()) if not table.empty else None,
        "max_timestamp": str(table["timestamp"].max()) if not table.empty else None,
        "cache_dir": str(cache_dir),
        "output_csv": str(output_csv),
    }
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"HRRR month CSV: {output_csv}")
    print(f"HRRR month metadata: {output_json}")
    print(table.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
