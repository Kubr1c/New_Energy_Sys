from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_energy_sys.hrrr import extract_hrrr_point_sample
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """Parse arguments for the minimal single-file HRRR extraction test."""

    parser = argparse.ArgumentParser(description="Extract one station sample from one HRRR GRIB2 file.")
    parser.add_argument("--grib", required=True, help="Path to one local HRRR GRIB2 file.")
    parser.add_argument("--latitude", required=True, type=float, help="Station latitude.")
    parser.add_argument("--longitude", required=True, type=float, help="Station longitude.")
    parser.add_argument("--output-csv", required=True, help="CSV path for the extracted sample.")
    parser.add_argument("--output-json", required=True, help="JSON path for extraction metadata.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_csv = Path(args.output_csv)
    output_json = Path(args.output_json)
    ensure_dir(output_csv.parent)
    ensure_dir(output_json.parent)

    result = extract_hrrr_point_sample(
        grib_path=args.grib,
        latitude=args.latitude,
        longitude=args.longitude,
    )
    result.frame.to_csv(output_csv, index=False)
    with output_json.open("w", encoding="utf-8") as handle:
        json.dump(result.metadata, handle, ensure_ascii=False, indent=2)

    print(f"HRRR sample CSV: {output_csv}")
    print(f"HRRR sample metadata: {output_json}")
    print(result.frame.to_string(index=False))


if __name__ == "__main__":
    main()
