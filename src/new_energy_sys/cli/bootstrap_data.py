from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.config import load_config
from new_energy_sys.data_sources import fetch_declared_sources
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.standardize import (
    build_hourly_training_table,
    build_synthetic_market,
    derive_weather_from_pv,
    map_opsd_profile_to_target_timeline,
    normalize_nrel_solar_zip,
    normalize_opsd,
    normalize_pv_power,
    normalize_weather,
)
from new_energy_sys.storage import simulate_rule_based_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and prepare first-stage energy dataset.")
    parser.add_argument("--config", required=True, help="Path to JSON data source configuration.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime = load_config(args.config)

    ensure_dir(runtime.raw_dir)
    ensure_dir(runtime.processed_dir)

    results = fetch_declared_sources(runtime.raw, runtime.raw_dir, runtime.root_dir)
    sources = runtime.raw["sources"]
    site = runtime.raw["site"]

    if sources["pv_power"]["kind"] == "nrel_solar_zip":
        pv_power = normalize_nrel_solar_zip(results["pv_power"].path, sources["pv_power"])
    else:
        pv_power = normalize_pv_power(
            results["pv_power"].path,
            sources["pv_power"],
            capacity_kw=float(site["capacity_kw"]),
        )
    if sources["weather"]["kind"] == "from_pv_power":
        weather = derive_weather_from_pv(
            results["pv_power"].path,
            sources["pv_power"],
            sources["weather"],
        )
    elif sources["weather"]["kind"] == "disabled":
        weather = pv_power[["timestamp"]].copy()
    else:
        weather = normalize_weather(results["weather"].path)

    if sources["opsd"]["kind"] == "synthetic_market":
        opsd = build_synthetic_market(pv_power, sources["opsd"])
    else:
        opsd_raw = normalize_opsd(results["opsd"].path, sources["opsd"])
        if sources["opsd"].get("align") == "profile_to_pv_timeline":
            opsd = map_opsd_profile_to_target_timeline(pv_power, opsd_raw)
        else:
            opsd = opsd_raw

    training = build_hourly_training_table(
        pv_power=pv_power,
        weather=weather,
        opsd=opsd,
    )
    training_with_storage = simulate_rule_based_storage(training, runtime.raw["storage"])

    output_path = runtime.processed_dir / "hourly_training_with_storage.parquet"
    training_with_storage.to_parquet(output_path, index=False)

    preview_path = runtime.processed_dir / "hourly_training_with_storage_preview.csv"
    training_with_storage.head(200).to_csv(preview_path, index=False)

    print(f"输出训练表: {output_path}")
    print(f"输出预览表: {preview_path}")
    print(f"样本行数: {len(training_with_storage)}")


if __name__ == "__main__":
    main()
