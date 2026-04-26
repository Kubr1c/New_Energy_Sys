from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from new_energy_sys.config import load_config
from new_energy_sys.data_sources import fetch_open_meteo_historical_forecast_range
from new_energy_sys.io_utils import ensure_dir
from new_energy_sys.standardize import normalize_weather


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the two-level forecast-weather upgrade."""

    parser = argparse.ArgumentParser(
        description="Prepare stronger forecast-weather sources for PV forecasting upgrades."
    )
    parser.add_argument("--config", required=True, help="Path to forecast-weather upgrade JSON config.")
    return parser.parse_args()


def _safe_time(value: Any) -> str | None:
    """Convert pandas/numpy timestamps into report-friendly strings."""

    if pd.isna(value):
        return None
    return str(value)


def _quality_summary(frame: pd.DataFrame, *, expected_start: str, expected_end: str) -> dict[str, Any]:
    """Build deterministic QA metrics for the downloaded forecast-weather table.

    This deliberately mirrors the Stage2 quality style: row count, hourly
    coverage, missingness, and physical weather-field availability. The output
    is small enough to be inspected by humans and stable enough to compare
    across later provider swaps.
    """

    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    start = pd.Timestamp(expected_start, tz="UTC")
    end = pd.Timestamp(expected_end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(hours=1)
    expected_hours = int(((end - start) / pd.Timedelta(hours=1)) + 1)
    observed_hours = int(working["timestamp"].nunique())

    numeric_columns = working.select_dtypes(include="number").columns.tolist()
    missing_by_column = {
        column: int(working[column].isna().sum())
        for column in working.columns
        if int(working[column].isna().sum()) > 0
    }

    return {
        "rows": int(len(working)),
        "columns": int(len(working.columns)),
        "numeric_columns": numeric_columns,
        "min_timestamp": _safe_time(working["timestamp"].min()),
        "max_timestamp": _safe_time(working["timestamp"].max()),
        "expected_hours": expected_hours,
        "observed_hours": observed_hours,
        "hourly_coverage": round(observed_hours / expected_hours, 6) if expected_hours else 0.0,
        "missing_by_column": missing_by_column,
        "quality_gates": {
            "non_empty": bool(len(working) > 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "hourly_coverage_at_least_95pct": bool(expected_hours > 0 and observed_hours / expected_hours >= 0.95),
            "has_solar_radiation_fields": bool(
                {"ghi_wm2", "dni_wm2", "dhi_wm2"}.intersection(set(working.columns))
            ),
            "has_cloud_and_wind_fields": bool(
                {"cloud_cover_pct", "wind_speed_ms"}.issubset(set(working.columns))
            ),
            "has_forecast_lead_time": bool("weather_forecast_lead_time_hour" in working.columns),
        },
    }


def _hrrr_manifest(config: dict[str, Any]) -> dict[str, Any]:
    """Return the level-2 HRRR acquisition contract without downloading GRIB data.

    HRRR is materially heavier than Open-Meteo: hourly model cycles, lead-time
    files, GRIB2/Zarr parsing, and point extraction. This manifest defines the
    production-grade contract now, so the next implementation can add a concrete
    extractor without changing downstream feature names.
    """

    hrrr = config["sources"].get("level_2_weather", {})
    return {
        "provider": "NOAA HRRR Archive",
        "status": "engineering_reserved_not_downloaded",
        "reason": "HRRR needs forecast-cycle extraction and GRIB/Zarr tooling; Open-Meteo is the executable short-term path.",
        "archive": hrrr.get("archive", "https://registry.opendata.aws/noaa-hrrr-pds/"),
        "bucket": hrrr.get("bucket", "noaa-hrrr-bdp-pds"),
        "preferred_product": hrrr.get("preferred_product", "wrfsfcf"),
        "lead_times_hour": hrrr.get("lead_times_hour", [1, 6, 24]),
        "required_output_columns": [
            "timestamp",
            "weather_forecast_issue_time",
            "weather_forecast_lead_time_hour",
            "ghi_wm2",
            "dni_wm2",
            "dhi_wm2",
            "temperature_c",
            "relative_humidity_pct",
            "wind_speed_ms",
            "wind_direction_deg",
            "cloud_cover_pct",
            "pressure_hpa",
            "precipitation_mm",
        ],
        "pitfall": "HRRR is stronger but much heavier; if PV target timestamps and HRRR valid-time windows are not aligned by forecast issue time, the model will silently learn unavailable future weather.",
    }


def _write_markdown_report(report: dict[str, Any], path: Path) -> None:
    """Write a concise markdown report for the two-level weather upgrade."""

    quality = report["level_1_open_meteo"]["quality"]
    gates = quality["quality_gates"]
    lines = [
        "# Forecast Weather Upgrade Report",
        "",
        "## Scope",
        "",
        "- Level 1: `Open-Meteo Historical Forecast` executable short-term route",
        "- Level 2: `NOAA HRRR` reserved high-resolution route",
        f"- Site: `{report['site']['name']}` (`{report['site']['latitude']}`, `{report['site']['longitude']}`)",
        f"- Date range: `{report['date_range']['start']}` to `{report['date_range']['end']}`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Aligned PV target source"] --> B["Stage1 hourly table"]',
        '    C["Open-Meteo Historical Forecast"] --> B',
        '    D["HRRR forecast-cycle extractor"] --> B',
        '    B --> E["Stage2 cleaning"]',
        '    E --> F["Stage3 forecast-weather features"]',
        '    F --> G["Stage4/5 modeling and ablation"]',
        "```",
        "",
        "## Level 1 Result",
        "",
        f"- Raw file: `{report['level_1_open_meteo']['raw_path']}`",
        f"- Normalized parquet: `{report['level_1_open_meteo']['normalized_parquet']}`",
        f"- Rows: `{quality['rows']}`",
        f"- Columns: `{quality['columns']}`",
        f"- Time range: `{quality['min_timestamp']}` to `{quality['max_timestamp']}`",
        f"- Hourly coverage: `{quality['hourly_coverage']}`",
        "",
        "## Quality Gates",
        "",
    ]
    for gate, passed in gates.items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(
        [
            "",
            "## Level 2 HRRR Contract",
            "",
            f"- Archive: `{report['level_2_hrrr']['archive']}`",
            f"- Bucket: `{report['level_2_hrrr']['bucket']}`",
            f"- Product: `{report['level_2_hrrr']['preferred_product']}`",
            f"- Lead times: `{report['level_2_hrrr']['lead_times_hour']}`",
            "",
            "## Current Blocking Point",
            "",
            report["pv_alignment_assessment"],
            "",
            "## Pitfall",
            "",
            report["level_2_hrrr"]["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    runtime = load_config(args.config)
    config = runtime.raw

    ensure_dir(runtime.raw_dir)
    ensure_dir(runtime.processed_dir)

    site = config["site"]
    date_range = config["date_range"]
    level_1_source = config["sources"]["level_1_weather"]

    result = fetch_open_meteo_historical_forecast_range(
        latitude=float(site["latitude"]),
        longitude=float(site["longitude"]),
        start=date_range["start"],
        end=date_range["end"],
        source=level_1_source,
        raw_dir=runtime.raw_dir,
    )

    normalized = normalize_weather(result.path)
    normalized = normalized.sort_values("timestamp").reset_index(drop=True)

    normalized_parquet = runtime.processed_dir / "level1_open_meteo_historical_forecast.parquet"
    normalized_csv = runtime.processed_dir / "level1_open_meteo_historical_forecast_preview.csv"
    normalized.to_parquet(normalized_parquet, index=False)
    normalized.head(200).to_csv(normalized_csv, index=False)

    hrrr_manifest = _hrrr_manifest(config)
    (runtime.processed_dir / "level2_hrrr_manifest.json").write_text(
        json.dumps(hrrr_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pv_alignment = (
        "当前已完成强天气预报源接入验证，但尚未发现同时间窗、同坐标可直接拼接的 PV 功率目标源。"
        "因此下一步不能把该天气表硬接到 2006 NREL 光伏数据；必须先接入 2021+ 的 PV 目标源，"
        "或改用 HRRR 覆盖期内的 PVDAQ/PVFleet/OEDI 站点数据。"
    )
    report = {
        "stage": "forecast_weather_upgrade_two_level",
        "site": site,
        "date_range": date_range,
        "level_1_open_meteo": {
            "source_url": result.source_url,
            "raw_path": str(result.path),
            "normalized_parquet": str(normalized_parquet),
            "preview_csv": str(normalized_csv),
            "quality": _quality_summary(
                normalized,
                expected_start=date_range["start"],
                expected_end=date_range["end"],
            ),
        },
        "level_2_hrrr": hrrr_manifest,
        "pv_alignment_assessment": pv_alignment,
    }

    report_json = runtime.processed_dir / "forecast_weather_upgrade_report.json"
    report_md = runtime.processed_dir / "forecast_weather_upgrade_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(report, report_md)

    print(f"forecast weather parquet: {normalized_parquet}")
    print(f"forecast weather report: {report_md}")


if __name__ == "__main__":
    main()
