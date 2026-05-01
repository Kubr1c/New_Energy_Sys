"""两级预报气象源升级准备模块。

模块设计原则：
- Level 1：Open-Meteo 历史预报，可执行的短期路径
- Level 2：NOAA HRRR，预留的高分辨率工程路径（暂不下载数据）
- 输出归一化预报气象 parquet、质量摘要、HRRR 合约清单及升级报告

本模块对应项目 Stage 7 前置的预报气象源升级准备功能。

入口命令: new-energy-sys prepare-forecast-weather --config <path>
"""

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
    """解析预报气象两级升级命令行参数。"""

    parser = argparse.ArgumentParser(
        description="准备更高质量的预报气象源以支持光伏预测升级。"
    )
    parser.add_argument("--config", required=True, help="预报气象升级 JSON 配置文件路径。")
    return parser.parse_args()


def _safe_time(value: Any) -> str | None:
    """将 pandas/numpy 时间戳转为报告友好的字符串。"""

    if pd.isna(value):
        return None
    return str(value)


def _quality_summary(frame: pd.DataFrame, *, expected_start: str, expected_end: str) -> dict[str, Any]:
    """为下载的预报气象表构建确定性质量指标。

    刻意对齐 Stage 2 质量风格：行数、小时覆盖率、缺失率及物理气象字段可用性。
    输出足够小便于人工检查，足够稳定便于跨数据源横向比较。
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
    """生成 Level 2 HRRR 采集合约清单（不下载 GRIB 数据）。

    HRRR 比 Open-Meteo 重得多：逐小时模型周期、lead-time 文件、
    GRIB2/Zarr 解析与站点提取。此清单提前定义生产级合约，
    后续实现具体提取器时无需修改下游特征名。
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
    """写入两级气象升级的简洁 Markdown 报告。"""

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
