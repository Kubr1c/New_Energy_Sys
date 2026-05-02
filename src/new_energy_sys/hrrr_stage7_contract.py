"""HRRR Stage7 forecast-weather contract validation.

This module is intentionally separate from Stage7 training.  Its job is to
fail fast when HRRR forecast weather is structurally valid but physically
unsafe for PV modeling, for example when irradiance was silently written as
zero after a DSWRF extraction failure.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_HRRR_COLUMNS = {
    "timestamp",
    "weather_forecast_issue_time",
    "weather_forecast_lead_time_hour",
    "grid_latitude",
    "grid_longitude",
    "source_url",
    "ghi_wm2",
    "temperature_c",
    "relative_humidity_pct",
    "wind_speed_ms",
    "wind_direction_deg",
    "pressure_hpa",
    "surface_pressure_hpa",
    "cloud_cover_pct",
    "precipitation_mm",
}

PHYSICAL_RANGES = {
    "ghi_wm2": (0.0, 1400.0),
    "temperature_c": (-80.0, 60.0),
    "relative_humidity_pct": (0.0, 100.0),
    "wind_speed_ms": (0.0, 75.0),
    "wind_direction_deg": (0.0, 360.0),
    "pressure_hpa": (500.0, 1100.0),
    "surface_pressure_hpa": (500.0, 1100.0),
    "cloud_cover_pct": (0.0, 100.0),
    "precipitation_mm": (0.0, 500.0),
}

MIN_REASONABLE_ROW_RATE = 0.95


@dataclass(frozen=True)
class HrrrStage7ContractResult:
    """Result of the HRRR contract gate used before Stage7 reruns."""

    passed: bool
    report: dict[str, Any]


def _utc_series(values: pd.Series) -> pd.Series:
    """Parse a timestamp column to UTC while preserving invalid rows as NaT."""

    return pd.to_datetime(values, errors="coerce", utc=True)


def _utc_timestamp(value: Any) -> pd.Timestamp:
    """Parse one timestamp-like value as a timezone-aware UTC Timestamp."""

    return pd.to_datetime(value, errors="raise", utc=True)


def _expected_times_from_audit_or_data(hrrr: pd.DataFrame, audit: dict[str, Any] | None) -> pd.DatetimeIndex:
    """Build the expected hourly HRRR valid-time index.

    The yearly merge audit is the preferred source because it records the exact
    intended range.  If a caller validates an ad-hoc sample without an audit,
    fall back to the min/max timestamps in the HRRR table.
    """

    if audit and audit.get("expected_start") and audit.get("expected_end"):
        start = _utc_timestamp(audit["expected_start"])
        end = _utc_timestamp(audit["expected_end"])
    else:
        timestamps = _utc_series(hrrr["timestamp"]).dropna()
        start = timestamps.min().floor("h")
        end = timestamps.max().floor("h")
    if start.hour == 0 and start.minute == 0 and start.second == 0 and str(end).endswith("00:00+00:00"):
        # Date-only audit values should mean the inclusive full day, matching
        # the HRRR manifest builder and avoiding off-by-one coverage checks.
        if audit and isinstance(audit.get("expected_end"), str) and len(audit["expected_end"]) == 10:
            end = end + pd.Timedelta(hours=23)
    return pd.date_range(start=start, end=end, freq="1h", tz="UTC")


def _gate(passed: bool, **details: Any) -> dict[str, Any]:
    """Create one stable gate payload for JSON and Markdown reports."""

    return {"passed": bool(passed), **details}


def _validate_required_columns(hrrr: pd.DataFrame) -> dict[str, Any]:
    missing = sorted(REQUIRED_HRRR_COLUMNS.difference(hrrr.columns))
    return _gate(not missing, missing_columns=missing)


def _validate_timestamps(hrrr: pd.DataFrame, expected_times: pd.DatetimeIndex) -> dict[str, Any]:
    timestamps = _utc_series(hrrr["timestamp"])
    observed = pd.DatetimeIndex(timestamps.dropna().drop_duplicates()).sort_values()
    missing = [str(value) for value in expected_times if value not in observed]
    duplicate_count = int(timestamps.duplicated().sum())
    coverage_ratio = float(len(observed.intersection(expected_times)) / len(expected_times)) if len(expected_times) else 0.0
    return _gate(
        bool(
            timestamps.notna().all()
            and hrrr["timestamp"].is_monotonic_increasing
            and duplicate_count == 0
            and coverage_ratio >= 0.995
        ),
        coverage_ratio=coverage_ratio,
        expected_rows=int(len(expected_times)),
        observed_rows=int(len(observed.intersection(expected_times))),
        duplicate_count=duplicate_count,
        missing_timestamps=missing,
    )


def _validate_audit_missing_timestamps(
    expected_times: pd.DatetimeIndex,
    hrrr: pd.DataFrame,
    audit: dict[str, Any] | None,
) -> dict[str, Any]:
    observed = pd.DatetimeIndex(_utc_series(hrrr["timestamp"]).dropna().drop_duplicates()).sort_values()
    calculated_missing = [str(value) for value in expected_times if value not in observed]
    if audit is None:
        return _gate(False, reason="hrrr audit JSON is required for explicit missing-hour traceability")
    audit_missing = [str(pd.Timestamp(value).tz_convert("UTC")) for value in audit.get("missing_timestamps", [])]
    return _gate(
        sorted(audit_missing) == sorted(calculated_missing),
        audit_status=audit.get("status"),
        audit_missing_count=len(audit_missing),
        calculated_missing_count=len(calculated_missing),
        calculated_missing_timestamps=calculated_missing,
    )


def _validate_numeric_ranges(hrrr: pd.DataFrame) -> dict[str, Any]:
    violations: dict[str, Any] = {}
    numeric = hrrr[list(PHYSICAL_RANGES)].apply(pd.to_numeric, errors="coerce")
    for column, (lower, upper) in PHYSICAL_RANGES.items():
        values = numeric[column]
        invalid = values.isna() | ~np.isfinite(values) | (values < lower) | (values > upper)
        if invalid.any():
            violations[column] = {
                "invalid_rows": int(invalid.sum()),
                "min": None if values.dropna().empty else float(values.min()),
                "max": None if values.dropna().empty else float(values.max()),
                "allowed_min": lower,
                "allowed_max": upper,
            }
    return _gate(not violations, violations=violations)


def _validate_dswrf_trace(hrrr: pd.DataFrame) -> dict[str, Any]:
    """Require DSWRF provenance for every emitted row.

    A numeric range check cannot distinguish a real nighttime zero from a
    silently zero-filled irradiance column.  The extractor writes every field
    path into `source_url`; the Stage7 gate therefore requires each row to carry
    a real DSWRF trace before the dataset is allowed to influence model metrics.
    """

    source = hrrr["source_url"].fillna("").astype(str)
    has_source = source.str.len() > 0
    has_dswrf = source.str.contains("DSWRF", case=False, regex=False)
    ghi = pd.to_numeric(hrrr["ghi_wm2"], errors="coerce")
    return _gate(
        bool(has_source.all() and has_dswrf.all() and ghi.notna().all() and ghi.max() > 0.0),
        empty_source_rows=int((~has_source).sum()),
        missing_dswrf_source_rows=int((~has_dswrf).sum()),
        ghi_max_wm2=None if ghi.dropna().empty else float(ghi.max()),
        source_path_count=int(source.str.split(";").explode().replace("", np.nan).dropna().nunique()),
    )


def _precipitation_audit_summary(audit: dict[str, Any] | None) -> dict[str, Any]:
    """Return precipitation transform counts from detailed or merged audits."""

    if audit is None:
        return {
            "available": False,
            "ok_attempts": 0,
            "missing_transform_count": 0,
            "negative_clipped_count": 0,
            "missing_transform_examples": [],
            "negative_clipped_examples": [],
        }

    if isinstance(audit.get("precipitation_semantics"), dict):
        summary = audit["precipitation_semantics"]
        return {
            "available": True,
            "ok_attempts": int(summary.get("ok_attempts", 0)),
            "missing_transform_count": int(summary.get("missing_transform_count", 0)),
            "negative_clipped_count": int(summary.get("negative_clipped_count", 0)),
            "missing_transform_examples": list(summary.get("missing_transform_examples", [])),
            "negative_clipped_examples": list(summary.get("negative_clipped_examples", [])),
        }

    attempts = [item for item in audit.get("attempts", []) if item.get("status") == "ok"]
    missing_transform = [
        item.get("timestamp")
        for item in attempts
        if item.get("precipitation_transform") != "accumulated_to_hourly_diff"
    ]
    negative_clipped = [
        item.get("timestamp")
        for item in attempts
        if bool(item.get("precipitation_negative_diff_clipped"))
    ]
    return {
        "available": bool(attempts),
        "ok_attempts": len(attempts),
        "missing_transform_count": len(missing_transform),
        "negative_clipped_count": len(negative_clipped),
        "missing_transform_examples": missing_transform[:10],
        "negative_clipped_examples": negative_clipped[:10],
    }


def _validate_precipitation_semantics(hrrr: pd.DataFrame, audit: dict[str, Any] | None) -> dict[str, Any]:
    """Require `precipitation_mm` to mean hourly precipitation, not APCP total."""

    summary = _precipitation_audit_summary(audit)
    errors: list[str] = []
    if not summary["available"]:
        errors.append("precipitation transform audit is missing")
    if summary["ok_attempts"] != len(hrrr):
        errors.append(f"ok_attempts={summary['ok_attempts']} != parquet_rows={len(hrrr)}")
    if summary["missing_transform_count"]:
        errors.append(f"missing_hourly_precipitation_transform={summary['missing_transform_count']}")
    if summary["negative_clipped_count"]:
        errors.append(f"negative_precipitation_diff_clipped={summary['negative_clipped_count']}")

    precipitation = pd.to_numeric(hrrr["precipitation_mm"], errors="coerce")
    invalid = precipitation.isna() | ~np.isfinite(precipitation) | (precipitation < 0.0)
    if invalid.any():
        errors.append(f"invalid_precipitation_rows={int(invalid.sum())}")

    return _gate(
        not errors,
        errors=errors,
        ok_attempts=summary["ok_attempts"],
        parquet_rows=len(hrrr),
        transform="accumulated_to_hourly_diff",
        missing_transform_examples=summary["missing_transform_examples"],
        negative_clipped_examples=summary["negative_clipped_examples"],
        max_hourly_precipitation_mm=None if precipitation.dropna().empty else float(precipitation.max()),
    )


def _validate_reasonable_anomaly_rate(stage2: pd.DataFrame, hrrr: pd.DataFrame) -> dict[str, Any]:
    """Require at least 95% of rows to be free of high-confidence anomalies."""

    frame = hrrr.copy()
    frame["timestamp"] = _utc_series(frame["timestamp"])
    stage2_weather = stage2[["timestamp", "ghi_wm2"]].copy()
    stage2_weather["timestamp"] = _utc_series(stage2_weather["timestamp"])
    stage2_weather["nsrdb_ghi_wm2"] = pd.to_numeric(stage2_weather["ghi_wm2"], errors="coerce")
    frame = (
        frame.merge(stage2_weather[["timestamp", "nsrdb_ghi_wm2"]], on="timestamp", how="left")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    numeric_columns = [
        "ghi_wm2",
        "temperature_c",
        "relative_humidity_pct",
        "surface_pressure_hpa",
        "wind_speed_ms",
        "wind_direction_deg",
        "cloud_cover_pct",
        "precipitation_mm",
    ]
    anomaly_flags: list[list[str]] = [[] for _ in range(len(frame))]
    for row_index, row in frame.iterrows():
        for column in numeric_columns:
            value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
            if pd.isna(value) or not np.isfinite(value):
                anomaly_flags[row_index].append(f"{column}:non_finite")

        nsrdb_ghi = pd.to_numeric(pd.Series([row.get("nsrdb_ghi_wm2")]), errors="coerce").iloc[0]
        hrrr_ghi = pd.to_numeric(pd.Series([row.get("ghi_wm2")]), errors="coerce").iloc[0]
        if pd.notna(nsrdb_ghi) and pd.notna(hrrr_ghi) and nsrdb_ghi <= 1.0 and hrrr_ghi > 50.0:
            anomaly_flags[row_index].append("ghi_wm2:nighttime_gt_50")

        precipitation = pd.to_numeric(pd.Series([row.get("precipitation_mm")]), errors="coerce").iloc[0]
        if pd.notna(precipitation) and precipitation > 50.0:
            anomaly_flags[row_index].append("precipitation_mm:gt_50_per_hour")

        wind_speed = pd.to_numeric(pd.Series([row.get("wind_speed_ms")]), errors="coerce").iloc[0]
        if pd.notna(wind_speed) and wind_speed > 35.0:
            anomaly_flags[row_index].append("wind_speed_ms:gt_35")

    timestamps = _utc_series(frame["timestamp"])
    adjacent_hour = timestamps.diff().eq(pd.Timedelta(hours=1))
    for column, threshold in {
        "temperature_c": 15.0,
        "surface_pressure_hpa": 8.0,
    }.items():
        values = pd.to_numeric(frame[column], errors="coerce")
        deltas = values.diff().abs()
        for row_index in deltas[(deltas > threshold) & adjacent_hour].index:
            anomaly_flags[int(row_index)].append(f"{column}:hourly_jump_gt_{threshold:g}")

    flagged = [(idx, flags) for idx, flags in enumerate(anomaly_flags) if flags]
    reasonable_rate = 1.0 - (len(flagged) / len(frame) if len(frame) else 1.0)
    return _gate(
        reasonable_rate >= MIN_REASONABLE_ROW_RATE,
        reasonable_row_rate=reasonable_rate,
        min_required_reasonable_row_rate=MIN_REASONABLE_ROW_RATE,
        flagged_rows=len(flagged),
        total_rows=len(frame),
        examples=[
            {
                "timestamp": str(frame.iloc[idx]["timestamp"]),
                "flags": flags,
            }
            for idx, flags in flagged[:20]
        ],
    )


def _validate_grid_distance(hrrr: pd.DataFrame, config: dict[str, Any], max_delta_deg: float = 0.05) -> dict[str, Any]:
    site_latitude = float(config["site"]["latitude"])
    site_longitude = float(config["site"]["longitude"])
    lat_delta = (pd.to_numeric(hrrr["grid_latitude"], errors="coerce") - site_latitude).abs()
    lon_delta = (pd.to_numeric(hrrr["grid_longitude"], errors="coerce") - site_longitude).abs()
    max_lat_delta = float(lat_delta.max())
    max_lon_delta = float(lon_delta.max())
    return _gate(
        max_lat_delta <= max_delta_deg and max_lon_delta <= max_delta_deg,
        site_latitude=site_latitude,
        site_longitude=site_longitude,
        max_latitude_delta=max_lat_delta,
        max_longitude_delta=max_lon_delta,
        max_allowed_delta=max_delta_deg,
    )


def _validate_ghi_distribution(stage2: pd.DataFrame, hrrr: pd.DataFrame) -> dict[str, Any]:
    stage2_weather = stage2[["timestamp", "ghi_wm2"]].copy()
    stage2_weather["timestamp"] = _utc_series(stage2_weather["timestamp"])
    stage2_weather["nsrdb_ghi_wm2"] = pd.to_numeric(stage2_weather["ghi_wm2"], errors="coerce")

    hrrr_weather = hrrr[["timestamp", "ghi_wm2"]].copy()
    hrrr_weather["timestamp"] = _utc_series(hrrr_weather["timestamp"])
    hrrr_weather["hrrr_ghi_wm2"] = pd.to_numeric(hrrr_weather["ghi_wm2"], errors="coerce")

    overlap = stage2_weather[["timestamp", "nsrdb_ghi_wm2"]].merge(
        hrrr_weather[["timestamp", "hrrr_ghi_wm2"]],
        on="timestamp",
        how="inner",
    )
    daytime = overlap[overlap["nsrdb_ghi_wm2"] > 50.0]
    hrrr_nonzero_rate = float((daytime["hrrr_ghi_wm2"] > 1.0).mean()) if len(daytime) else 0.0
    hrrr_max = float(hrrr_weather["hrrr_ghi_wm2"].max()) if len(hrrr_weather) else 0.0
    return _gate(
        len(daytime) > 0 and hrrr_nonzero_rate >= 0.85 and hrrr_max > 500.0,
        overlap_rows=int(len(overlap)),
        nsrdb_daytime_rows=int(len(daytime)),
        hrrr_daytime_nonzero_rate=hrrr_nonzero_rate,
        hrrr_ghi_max_wm2=hrrr_max,
        min_required_nonzero_rate=0.85,
        min_required_max_wm2=500.0,
    )


def _validate_issue_time_for_stage7_horizons(stage3: pd.DataFrame, hrrr: pd.DataFrame) -> dict[str, Any]:
    stage3_times = pd.DataFrame({"prediction_time": _utc_series(stage3["timestamp"]).dropna()})
    weather = hrrr[["timestamp", "weather_forecast_issue_time", "weather_forecast_lead_time_hour"]].copy()
    weather["forecast_valid_time"] = _utc_series(weather["timestamp"])
    weather["weather_forecast_issue_time"] = _utc_series(weather["weather_forecast_issue_time"])
    weather["weather_forecast_lead_time_hour"] = pd.to_numeric(
        weather["weather_forecast_lead_time_hour"],
        errors="coerce",
    )

    horizon_reports: dict[str, Any] = {}
    passed = True
    for horizon in (6, 24):
        joined = pd.DataFrame(
            {
                "prediction_time": stage3_times["prediction_time"],
                "forecast_valid_time": stage3_times["prediction_time"] + pd.Timedelta(hours=horizon),
            }
        ).merge(
            weather[["forecast_valid_time", "weather_forecast_issue_time", "weather_forecast_lead_time_hour"]],
            on="forecast_valid_time",
            how="inner",
        )
        if joined.empty:
            horizon_reports[f"target_plus_{horizon}h"] = {
                "joined_rows": 0,
                "leakage_rows": 0,
                "lead_time_missing_rows": 0,
                "passed": False,
            }
            passed = False
            continue
        leakage = joined["weather_forecast_issue_time"] > joined["prediction_time"]
        lead_missing = joined["weather_forecast_lead_time_hour"].isna()
        horizon_passed = bool(not leakage.any() and not lead_missing.any())
        passed = passed and horizon_passed
        horizon_reports[f"target_plus_{horizon}h"] = {
            "joined_rows": int(len(joined)),
            "leakage_rows": int(leakage.sum()),
            "lead_time_missing_rows": int(lead_missing.sum()),
            "passed": horizon_passed,
        }
    return _gate(passed, horizons=horizon_reports)


def validate_hrrr_stage7_contract(
    *,
    config: dict[str, Any],
    stage2: pd.DataFrame,
    stage3: pd.DataFrame,
    hrrr_weather: pd.DataFrame,
    hrrr_audit: dict[str, Any] | None,
) -> HrrrStage7ContractResult:
    """Validate HRRR forecast weather before it is allowed into Stage7."""

    hrrr = hrrr_weather.copy()
    if "timestamp" in hrrr.columns:
        hrrr["timestamp"] = _utc_series(hrrr["timestamp"])
        hrrr = hrrr.sort_values("timestamp").reset_index(drop=True)

    gates: dict[str, dict[str, Any]] = {}
    gates["required_columns"] = _validate_required_columns(hrrr)
    if not gates["required_columns"]["passed"]:
        report = {"stage": "hrrr_stage7_contract", "passed": False, "gates": gates}
        return HrrrStage7ContractResult(passed=False, report=report)

    expected_times = _expected_times_from_audit_or_data(hrrr, hrrr_audit)
    gates["timestamp_coverage"] = _validate_timestamps(hrrr, expected_times)
    gates["audit_missing_timestamps"] = _validate_audit_missing_timestamps(expected_times, hrrr, hrrr_audit)
    gates["numeric_physical_ranges"] = _validate_numeric_ranges(hrrr)
    gates["grid_distance"] = _validate_grid_distance(hrrr, config)
    gates["dswrf_source_trace"] = _validate_dswrf_trace(hrrr)
    gates["precipitation_semantics"] = _validate_precipitation_semantics(hrrr, hrrr_audit)
    gates["ghi_distribution"] = _validate_ghi_distribution(stage2, hrrr)
    gates["feature_reasonableness_rate"] = _validate_reasonable_anomaly_rate(stage2, hrrr)
    gates["stage7_issue_time_alignment"] = _validate_issue_time_for_stage7_horizons(stage3, hrrr)

    passed = bool(all(gate["passed"] for gate in gates.values()))
    report = {
        "stage": "hrrr_stage7_contract",
        "passed": passed,
        "decision": "allow_stage7_rerun" if passed else "block_stage7_rerun",
        "reason": (
            "HRRR forecast weather satisfies schema, time alignment, physical range, and irradiance gates."
            if passed
            else "HRRR forecast weather failed at least one hard data-contract gate; do not run Stage7."
        ),
        "gates": gates,
    }
    return HrrrStage7ContractResult(passed=passed, report=report)


def validate_hrrr_stage7_contract_paths(
    *,
    config: dict[str, Any],
    stage2_path: Path,
    stage3_path: Path,
    hrrr_weather_path: Path,
    hrrr_audit_path: Path | None,
) -> HrrrStage7ContractResult:
    """Load contract inputs from disk and run the HRRR gate."""

    stage2 = pd.read_parquet(stage2_path)
    stage3 = pd.read_parquet(stage3_path)
    hrrr_weather = pd.read_parquet(hrrr_weather_path)
    hrrr_audit = None
    if hrrr_audit_path is not None:
        hrrr_audit = json.loads(hrrr_audit_path.read_text(encoding="utf-8"))
    return validate_hrrr_stage7_contract(
        config=config,
        stage2=stage2,
        stage3=stage3,
        hrrr_weather=hrrr_weather,
        hrrr_audit=hrrr_audit,
    )


def write_hrrr_stage7_contract_report(report: dict[str, Any], *, json_path: Path, markdown_path: Path) -> None:
    """Write machine-readable and concise human-readable contract reports."""

    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# HRRR Stage7 数据契约校验报告",
        "",
        f"- 判定: `{report['decision']}`",
        f"- 原因: {report['reason']}",
        "",
        "## 门禁",
        "",
        "| 门禁 | 结果 | 关键细节 |",
        "|---|---:|---|",
    ]
    for name, gate in report["gates"].items():
        details = {key: value for key, value in gate.items() if key != "passed"}
        detail_text = json.dumps(details, ensure_ascii=False, default=str)
        if len(detail_text) > 220:
            detail_text = detail_text[:217] + "..."
        lines.append(f"| `{name}` | `{gate['passed']}` | `{detail_text}` |")
    lines.extend(
        [
            "",
            "## Pitfall",
            "",
            "GHI/DSWRF 是光伏预测的关键输入，不能用 0 值静默替代缺失；该门禁失败时必须重新抽取 HRRR。",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
