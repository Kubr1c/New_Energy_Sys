"""Decompose HRRR GHI into DHI + DNI using Erbs or DISC model."""

from __future__ import annotations

import argparse

from new_energy_sys.irradiance_decomposition import decompose_hrrr_parquet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decompose HRRR GHI into DHI and DNI."
    )
    parser.add_argument("--input", required=True, help="HRRR parquet with ghi_wm2 column.")
    parser.add_argument("--output", required=True, help="Output parquet path.")
    parser.add_argument("--latitude", type=float, default=39.74)
    parser.add_argument("--longitude", type=float, default=-105.18)
    parser.add_argument("--altitude", type=float, default=1730.0)
    parser.add_argument(
        "--model", choices=["erbs", "disc"], default="disc",
        help="Decomposition model (default: disc).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = decompose_hrrr_parquet(
        input_path=args.input,
        output_path=args.output,
        latitude=args.latitude,
        longitude=args.longitude,
        altitude=args.altitude,
        model=args.model,
    )
    print(f"Model:     {args.model}")
    print(f"Rows:      {len(result)}")
    print(f"Columns:   solar_zenith_deg, kt, dhi_wm2, dni_wm2")
    print(f"GHI range: {result['ghi_wm2'].min():.1f} – {result['ghi_wm2'].max():.1f} W/m2")
    print(f"DHI range: {result['dhi_wm2'].min():.1f} – {result['dhi_wm2'].max():.1f} W/m2")
    print(f"DNI range: {result['dni_wm2'].min():.1f} – {result['dni_wm2'].max():.1f} W/m2")
    daytime = result["ghi_wm2"] > 10
    if daytime.any():
        print(f"DHI/GHI (daytime): {(result['dhi_wm2'][daytime] / result['ghi_wm2'][daytime]).mean():.3f}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
