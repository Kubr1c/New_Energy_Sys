"""Strict HRRR probe validation before expensive yearly extraction.

The yearly HRRR run is intentionally blocked until a small stratified probe is
proven usable.  This module validates the downloaded probe parquet, checks that
each weather feature is present and physically plausible, and writes a compact
visual review artifact for human approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.hrrr_point_forecast import STAGE7_CORE_WEATHER_COLUMNS, build_valid_times
from new_energy_sys.hrrr_stage7_contract import PHYSICAL_RANGES


PROBE_REQUIRED_COLUMNS = {
    "timestamp",
    "weather_forecast_issue_time",
    "weather_forecast_lead_time_hour",
    "grid_latitude",
    "grid_longitude",
    "source_url",
    "backend",
    "downloaded_bytes",
    "pressure_hpa",
    *STAGE7_CORE_WEATHER_COLUMNS,
}
MIN_REASONABLE_ROW_RATE = 0.95


@dataclass(frozen=True)
class HrrrProbeContractResult:
    """Probe validation result used as the full-run release gate."""

    passed: bool
    report: dict[str, Any]


def _utc_series(values: pd.Series) -> pd.Series:
    """Parse a timestamp column as timezone-aware UTC."""

    return pd.to_datetime(values, errors="coerce", utc=True)


def _gate(passed: bool, **details: Any) -> dict[str, Any]:
    """Create one stable report section for JSON/Markdown output."""

    return {"passed": bool(passed), **details}


def expected_probe_times_from_manifest(manifest: dict[str, Any]) -> pd.DatetimeIndex:
    """Return the exact valid timestamps promised by a strict probe manifest."""

    collection = manifest["collection"]
    if collection.get("expected_valid_timestamps"):
        return pd.DatetimeIndex(pd.to_datetime(collection["expected_valid_timestamps"], utc=True)).sort_values()
    if collection.get("windows"):
        windows = [
            build_valid_times(str(window["start"]), str(window["end"]))
            for window in collection["windows"]
        ]
        if not windows:
            return pd.DatetimeIndex([], tz="UTC")
        return pd.DatetimeIndex(np.concatenate([window.values for window in windows])).tz_localize("UTC")
    return build_valid_times(str(collection["start"]), str(collection["end"]))


def _validate_required_columns(hrrr: pd.DataFrame) -> dict[str, Any]:
    """Ensure every Stage7 weather and audit column exists in the probe table."""

    missing = sorted(PROBE_REQUIRED_COLUMNS.difference(hrrr.columns))
    return _gate(not missing, missing_columns=missing)


def _validate_timestamp_alignment(hrrr: pd.DataFrame, expected_times: pd.DatetimeIndex) -> dict[str, Any]:
    """Require an exact UTC, sorted, duplicate-free match to the probe manifest."""

    timestamps = _utc_series(hrrr["timestamp"])
    observed = pd.DatetimeIndex(timestamps.dropna().drop_duplicates()).sort_values()
    missing = [str(value) for value in expected_times if value not in observed]
    unexpected = [str(value) for value in observed if value not in expected_times]
    duplicate_count = int(timestamps.duplicated().sum())
    passed = bool(
        timestamps.notna().all()
        and hrrr["timestamp"].is_monotonic_increasing
        and duplicate_count == 0
        and not missing
        and not unexpected
        and len(hrrr) == len(expected_times)
    )
    return _gate(
        passed,
        expected_rows=int(len(expected_times)),
        observed_rows=int(len(hrrr)),
        duplicate_count=duplicate_count,
        missing_timestamps=missing,
        unexpected_timestamps=unexpected,
    )


def _validate_audit_traceability(
    hrrr: pd.DataFrame,
    audit: dict[str, Any],
    expected_times: pd.DatetimeIndex,
) -> dict[str, Any]:
    """Check that the batch audit agrees with the probe table and has no gaps."""

    audit_missing = [str(pd.Timestamp(value).tz_convert("UTC")) for value in audit.get("missing_timestamps", [])]
    errors: list[str] = []
    if audit.get("status") != "completed":
        errors.append(f"status={audit.get('status')}")
    if int(audit.get("output_rows", -1)) != len(hrrr):
        errors.append(f"output_rows={audit.get('output_rows')} != parquet_rows={len(hrrr)}")
    if int(audit.get("expected_rows", -1)) != len(expected_times):
        errors.append(f"expected_rows={audit.get('expected_rows')} != manifest_rows={len(expected_times)}")
    if audit_missing:
        errors.append(f"missing_timestamps={len(audit_missing)}")
    if audit.get("local_output_budget_exceeded"):
        errors.append("local_output_budget_exceeded=true")
    return _gate(not errors, errors=errors, audit_status=audit.get("status"), audit_missing_timestamps=audit_missing)


def _validate_numeric_fields(hrrr: pd.DataFrame) -> dict[str, Any]:
    """Validate physical ranges, finite values, and feature-level summary stats."""

    violations: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    for column in sorted(PHYSICAL_RANGES):
        if column not in hrrr.columns:
            continue
        lower, upper = PHYSICAL_RANGES[column]
        values = pd.to_numeric(hrrr[column], errors="coerce")
        finite = values.notna() & np.isfinite(values)
        invalid = ~finite | (values < lower) | (values > upper)
        non_null_values = values[finite]
        summaries[column] = {
            "null_count": int((~finite).sum()),
            "min": None if non_null_values.empty else float(non_null_values.min()),
            "max": None if non_null_values.empty else float(non_null_values.max()),
            "nonzero_rate": None if non_null_values.empty else float((non_null_values.abs() > 1e-9).mean()),
        }
        if invalid.any():
            violations[column] = {
                "invalid_rows": int(invalid.sum()),
                "allowed_min": lower,
                "allowed_max": upper,
                "min": summaries[column]["min"],
                "max": summaries[column]["max"],
            }

    empty_features = [
        column
        for column in STAGE7_CORE_WEATHER_COLUMNS
        if pd.to_numeric(hrrr[column], errors="coerce").notna().sum() == 0
    ]
    if empty_features:
        violations["empty_features"] = empty_features

    return _gate(not violations, violations=violations, summaries=summaries)


def _validate_precipitation_semantics(hrrr: pd.DataFrame, audit: dict[str, Any]) -> dict[str, Any]:
    """Require precipitation to be hourly, not raw accumulated APCP.

    The visual audit showed that raw `APCP_acc_fcst` creates plateau and step
    patterns because it is cumulative within a forecast cycle.  For Stage7 the
    column named `precipitation_mm` must mean an hourly increment.  The extractor
    records the transform per successful attempt; this gate prevents old or
    partially converted artifacts from being approved by range checks alone.
    """

    attempts = [item for item in audit.get("attempts", []) if item.get("status") == "ok"]
    transform_errors: list[str] = []
    if len(attempts) != len(hrrr):
        transform_errors.append(f"ok_attempts={len(attempts)} != parquet_rows={len(hrrr)}")
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
    if missing_transform:
        transform_errors.append(f"missing_hourly_precipitation_transform={len(missing_transform)}")
    if negative_clipped:
        transform_errors.append(f"negative_precipitation_diff_clipped={len(negative_clipped)}")

    precipitation = pd.to_numeric(hrrr["precipitation_mm"], errors="coerce")
    invalid = precipitation.isna() | ~np.isfinite(precipitation) | (precipitation < 0.0)
    if invalid.any():
        transform_errors.append(f"invalid_precipitation_rows={int(invalid.sum())}")

    return _gate(
        not transform_errors,
        errors=transform_errors,
        ok_attempts=len(attempts),
        parquet_rows=len(hrrr),
        transform="accumulated_to_hourly_diff",
        missing_transform_examples=missing_transform[:10],
        negative_clipped_examples=negative_clipped[:10],
        max_hourly_precipitation_mm=None if precipitation.dropna().empty else float(precipitation.max()),
    )


def _validate_reasonable_anomaly_rate(stage2: pd.DataFrame, hrrr: pd.DataFrame) -> dict[str, Any]:
    """Require at least 95% of probe rows to be free of high-confidence anomalies.

    This gate deliberately avoids subjective model-quality judgements such as
    "cloud cover is jumpy"; HRRR single-grid forecasts can legitimately move in
    steps.  It only counts rows with strong data-contract symptoms: impossible
    finite values, nighttime GHI under a NSRDB-night proxy, extreme hourly
    precipitation, or implausibly abrupt continuous-variable jumps inside the
    same 48-hour probe window.
    """

    frame = hrrr.copy()
    frame["timestamp"] = _utc_series(frame["timestamp"])
    stage2_weather = stage2[["timestamp", "ghi_wm2"]].copy()
    stage2_weather["timestamp"] = _utc_series(stage2_weather["timestamp"])
    stage2_weather["nsrdb_ghi_wm2"] = pd.to_numeric(stage2_weather["ghi_wm2"], errors="coerce")
    frame = frame.merge(stage2_weather[["timestamp", "nsrdb_ghi_wm2"]], on="timestamp", how="left")
    frame = _windowed_probe_frame(frame)

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
    for row_index, row in frame.reset_index(drop=True).iterrows():
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

    sorted_frame = frame.reset_index(drop=True)
    for column, threshold in {
        "temperature_c": 15.0,
        "surface_pressure_hpa": 8.0,
    }.items():
        values = pd.to_numeric(sorted_frame[column], errors="coerce")
        deltas = values.groupby(sorted_frame["probe_window_index"]).diff().abs()
        for row_index in deltas[deltas > threshold].index:
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
                "window_hour": float(frame.iloc[idx]["window_hour"]),
                "flags": flags,
            }
            for idx, flags in flagged[:20]
        ],
    )


def _validate_grid_distance(hrrr: pd.DataFrame, config: dict[str, Any], max_delta_deg: float = 0.05) -> dict[str, Any]:
    """Ensure the HRRR grid point is close enough to the configured PV site."""

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


def _validate_dswrf_trace(hrrr: pd.DataFrame) -> dict[str, Any]:
    """Require real DSWRF source paths so irradiance cannot be silently zero-filled."""

    source = hrrr["source_url"].fillna("").astype(str)
    has_source = source.str.len() > 0
    has_dswrf = source.str.contains("DSWRF", case=False, regex=False)
    ghi = pd.to_numeric(hrrr["ghi_wm2"], errors="coerce")
    passed = bool(has_source.all() and has_dswrf.all() and ghi.notna().all() and ghi.max() > 0.0)
    return _gate(
        passed,
        empty_source_rows=int((~has_source).sum()),
        missing_dswrf_source_rows=int((~has_dswrf).sum()),
        ghi_max_wm2=None if ghi.dropna().empty else float(ghi.max()),
        source_path_count=int(source.str.split(";").explode().replace("", np.nan).dropna().nunique()),
    )


def _validate_issue_time_and_lead(hrrr: pd.DataFrame) -> dict[str, Any]:
    """Check forecast issue time, valid time, and lead-time metadata agree."""

    valid_time = _utc_series(hrrr["timestamp"])
    issue_time = _utc_series(hrrr["weather_forecast_issue_time"])
    lead = pd.to_numeric(hrrr["weather_forecast_lead_time_hour"], errors="coerce")
    calculated_lead = (valid_time - issue_time).dt.total_seconds() / 3600.0
    lead_mismatch = (calculated_lead - lead).abs() > 1e-6
    leakage = issue_time > valid_time
    invalid = valid_time.isna() | issue_time.isna() | lead.isna() | lead_mismatch | leakage
    return _gate(
        not invalid.any(),
        invalid_rows=int(invalid.sum()),
        leakage_rows=int(leakage.sum()),
        lead_mismatch_rows=int(lead_mismatch.sum()),
        min_lead_hour=None if lead.dropna().empty else float(lead.min()),
        max_lead_hour=None if lead.dropna().empty else float(lead.max()),
    )


def _validate_probe_ghi_distribution(stage2: pd.DataFrame, hrrr: pd.DataFrame) -> dict[str, Any]:
    """Compare probe GHI against NSRDB daytime samples and summer intensity."""

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
    summer = hrrr_weather[hrrr_weather["timestamp"].dt.month.isin([6, 7, 8])]
    nonzero_rate = float((daytime["hrrr_ghi_wm2"] > 1.0).mean()) if len(daytime) else 0.0
    summer_max = float(summer["hrrr_ghi_wm2"].max()) if len(summer) else 0.0
    return _gate(
        len(daytime) > 0 and nonzero_rate >= 0.85 and summer_max > 500.0,
        overlap_rows=int(len(overlap)),
        nsrdb_daytime_rows=int(len(daytime)),
        hrrr_daytime_nonzero_rate=nonzero_rate,
        summer_hrrr_ghi_max_wm2=summer_max,
        min_required_nonzero_rate=0.85,
        min_required_summer_max_wm2=500.0,
    )


def validate_hrrr_probe_contract(
    *,
    config: dict[str, Any],
    stage2: pd.DataFrame,
    hrrr_weather: pd.DataFrame,
    hrrr_audit: dict[str, Any],
    manifest: dict[str, Any],
) -> HrrrProbeContractResult:
    """Validate strict probe artifacts before any yearly HRRR extraction."""

    hrrr = hrrr_weather.copy()
    if "timestamp" in hrrr.columns:
        hrrr["timestamp"] = _utc_series(hrrr["timestamp"])
        hrrr = hrrr.sort_values("timestamp").reset_index(drop=True)

    gates: dict[str, dict[str, Any]] = {}
    gates["required_columns"] = _validate_required_columns(hrrr)
    if not gates["required_columns"]["passed"]:
        report = {
            "stage": "hrrr_strict_probe_contract",
            "passed": False,
            "decision": "block_full_hrrr_extraction",
            "reason": "HRRR probe is missing required weather or audit columns.",
            "gates": gates,
        }
        return HrrrProbeContractResult(passed=False, report=report)

    expected_times = expected_probe_times_from_manifest(manifest)
    gates["timestamp_alignment"] = _validate_timestamp_alignment(hrrr, expected_times)
    gates["audit_traceability"] = _validate_audit_traceability(hrrr, hrrr_audit, expected_times)
    gates["numeric_physical_ranges"] = _validate_numeric_fields(hrrr)
    gates["precipitation_semantics"] = _validate_precipitation_semantics(hrrr, hrrr_audit)
    gates["grid_distance"] = _validate_grid_distance(hrrr, config)
    gates["dswrf_source_trace"] = _validate_dswrf_trace(hrrr)
    gates["issue_time_and_lead"] = _validate_issue_time_and_lead(hrrr)
    gates["ghi_distribution"] = _validate_probe_ghi_distribution(stage2, hrrr)
    gates["feature_reasonableness_rate"] = _validate_reasonable_anomaly_rate(stage2, hrrr)

    passed = bool(all(gate["passed"] for gate in gates.values()))
    report = {
        "stage": "hrrr_strict_probe_contract",
        "passed": passed,
        "decision": "allow_human_review" if passed else "block_full_hrrr_extraction",
        "reason": (
            "HRRR probe passed strict machine gates; review the weather feature plot before approving full extraction."
            if passed
            else "HRRR probe failed at least one hard gate; do not start the yearly extraction."
        ),
        "expected_probe_rows": int(len(expected_times)),
        "gates": gates,
    }
    return HrrrProbeContractResult(passed=passed, report=report)


def validate_hrrr_probe_contract_paths(
    *,
    config: dict[str, Any],
    stage2_path: Path,
    hrrr_weather_path: Path,
    hrrr_audit_path: Path,
    manifest_path: Path,
) -> HrrrProbeContractResult:
    """Load strict probe inputs from disk and run the validation gate."""

    return validate_hrrr_probe_contract(
        config=config,
        stage2=pd.read_parquet(stage2_path),
        hrrr_weather=pd.read_parquet(hrrr_weather_path),
        hrrr_audit=json.loads(hrrr_audit_path.read_text(encoding="utf-8")),
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
    )


def _windowed_probe_frame(plot_frame: pd.DataFrame) -> pd.DataFrame:
    """Add human-review plotting coordinates for discontinuous probe windows.

    Strict probe samples four short seasonal windows.  Plotting those samples
    directly on a calendar-year x-axis compresses every 48-hour window into a
    nearly vertical line and hides hourly variation.  This helper infers each
    contiguous hourly block from timestamp gaps, then creates a local
    `window_hour` coordinate so every subplot uses an easy-to-read 0..47 hour
    axis while still preserving the original UTC timestamp in the data table.
    """

    frame = plot_frame.sort_values("timestamp").reset_index(drop=True).copy()
    timestamps = _utc_series(frame["timestamp"])
    gaps = timestamps.diff().gt(pd.Timedelta(hours=1))
    frame["probe_window_index"] = gaps.fillna(False).cumsum().astype(int)
    frame["probe_window_label"] = [
        f"{pd.Timestamp(start).strftime('%Y-%m-%d')} UTC"
        for start in frame.groupby("probe_window_index")["timestamp"].transform("min")
    ]
    window_start = frame.groupby("probe_window_index")["timestamp"].transform("min")
    frame["window_hour"] = ((timestamps - _utc_series(window_start)).dt.total_seconds() / 3600.0).astype(float)
    return frame


def _plot_hourly_metric(
    *,
    axis: Any,
    frame: pd.DataFrame,
    column: str,
    label: str,
    color: str,
    marker: str = "o",
    linewidth: float = 1.35,
) -> None:
    """Draw one metric as both a line and points for hourly visual inspection."""

    if column not in frame.columns:
        return
    values = pd.to_numeric(frame[column], errors="coerce")
    axis.plot(
        frame["window_hour"],
        values,
        linewidth=linewidth,
        color=color,
        label=label,
    )
    axis.scatter(
        frame["window_hour"],
        values,
        s=12,
        color=color,
        marker=marker,
        alpha=0.85,
    )


def _draw_hourly_probe_feature_figure(*, plot_frame: pd.DataFrame, figure_path: Path) -> list[Path]:
    """Write a window-by-window hourly figure that is readable by eye.

    Rows are weather features and columns are the four seasonal probe windows.
    Every subplot uses local hour-of-window coordinates instead of calendar
    dates.  This keeps all 192 hourly samples visually separable and makes
    radiation, cloud, precipitation, and lead-time anomalies obvious before the
    expensive full-year extraction is approved.
    """

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hourly = _windowed_probe_frame(plot_frame)
    window_ids = list(hourly["probe_window_index"].drop_duplicates())
    if not window_ids:
        window_ids = [0]
        hourly["probe_window_index"] = 0
        hourly["probe_window_label"] = "no valid timestamps"
        hourly["window_hour"] = np.arange(len(hourly), dtype=float)

    metrics = [
        ("GHI: HRRR vs NSRDB", "W/m2", [("ghi_wm2", "HRRR GHI", "#1f77b4"), ("nsrdb_ghi_wm2", "NSRDB GHI", "#ff7f0e")]),
        ("Temperature", "C", [("temperature_c", "temperature_c", "#d62728")]),
        ("Relative Humidity", "%", [("relative_humidity_pct", "relative_humidity_pct", "#17becf")]),
        ("Wind Speed", "m/s", [("wind_speed_ms", "wind_speed_ms", "#2ca02c")]),
        ("Wind Direction", "deg", [("wind_direction_deg", "wind_direction_deg", "#9467bd")]),
        ("Surface Pressure", "hPa", [("surface_pressure_hpa", "surface_pressure_hpa", "#8c564b")]),
        ("Cloud Cover", "%", [("cloud_cover_pct", "cloud_cover_pct", "#7f7f7f")]),
        ("Precipitation", "mm", [("precipitation_mm", "precipitation_mm", "#1f77b4")]),
        ("Forecast Lead Time", "hours", [("weather_forecast_lead_time_hour", "lead_time_hour", "#bcbd22")]),
    ]
    nrows = len(metrics)
    ncols = len(window_ids)
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(6.2 * ncols, 2.55 * nrows),
        sharex=False,
        squeeze=False,
    )

    for col_index, window_id in enumerate(window_ids):
        window = hourly[hourly["probe_window_index"] == window_id].copy()
        title = str(window["probe_window_label"].iloc[0]) if len(window) else f"window {window_id + 1}"
        for row_index, (metric_title, ylabel, series) in enumerate(metrics):
            axis = axes[row_index][col_index]
            for column, label, color in series:
                _plot_hourly_metric(axis=axis, frame=window, column=column, label=label, color=color)
            if row_index == 0:
                axis.set_title(title, fontsize=10)
            if col_index == 0:
                axis.set_ylabel(f"{metric_title}\n{ylabel}", fontsize=9)
            else:
                axis.set_ylabel(ylabel, fontsize=8)
            axis.set_xlim(-1, 48)
            axis.set_xticks([0, 6, 12, 18, 24, 30, 36, 42, 47])
            axis.grid(alpha=0.25)
            if row_index == nrows - 1:
                axis.set_xlabel("hour in 48h probe window", fontsize=8)
            if len(series) > 1:
                axis.legend(loc="upper right", fontsize=7, frameon=True)

    fig.suptitle("HRRR Strict Probe Hourly Weather Feature Review", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(figure_path, dpi=180)
    plt.close(fig)

    companion_paths: list[Path] = []
    for companion_index, window_id in enumerate(window_ids, start=1):
        window = hourly[hourly["probe_window_index"] == window_id].copy()
        label = str(window["probe_window_label"].iloc[0]) if len(window) else f"window {window_id + 1}"
        date_token = label.split()[0].replace("-", "")
        companion_path = figure_path.with_name(
            f"{figure_path.stem}_window_{companion_index:02d}_{date_token}{figure_path.suffix}"
        )
        companion_paths.append(companion_path)

        window_fig, window_axes = plt.subplots(
            len(metrics),
            1,
            figsize=(11, 2.35 * len(metrics)),
            sharex=False,
            squeeze=False,
        )
        for row_index, (metric_title, ylabel, series) in enumerate(metrics):
            axis = window_axes[row_index][0]
            for column, label_text, color in series:
                _plot_hourly_metric(axis=axis, frame=window, column=column, label=label_text, color=color)
            axis.set_title(metric_title, fontsize=10)
            axis.set_ylabel(ylabel, fontsize=9)
            axis.set_xlim(-1, 48)
            axis.set_xticks([0, 6, 12, 18, 24, 30, 36, 42, 47])
            axis.grid(alpha=0.25)
            if len(series) > 1:
                axis.legend(loc="upper right", fontsize=8, frameon=True)
            if row_index == len(metrics) - 1:
                axis.set_xlabel("hour in 48h probe window", fontsize=9)
        window_fig.suptitle(f"HRRR Strict Probe Hourly Review - {label}", fontsize=14)
        window_fig.tight_layout(rect=(0, 0, 1, 0.985))
        window_fig.savefig(companion_path, dpi=180)
        plt.close(window_fig)

    return companion_paths


def write_hrrr_probe_review(
    *,
    report: dict[str, Any],
    stage2: pd.DataFrame,
    hrrr_weather: pd.DataFrame,
    json_path: Path,
    markdown_path: Path,
    figure_path: Path,
) -> None:
    """Write JSON, Markdown, and PNG artifacts for manual probe review."""

    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    hrrr = hrrr_weather.copy()
    for column in {
        "timestamp",
        "source_url",
        "weather_forecast_lead_time_hour",
        "pressure_hpa",
        "surface_pressure_hpa",
        *STAGE7_CORE_WEATHER_COLUMNS,
    }:
        if column not in hrrr.columns:
            # Failed probes should still produce a readable review report.  A
            # placeholder keeps plotting/report writing alive while the JSON
            # gate records the real blocker.
            hrrr[column] = pd.NaT if column == "timestamp" else np.nan
    hrrr["timestamp"] = _utc_series(hrrr["timestamp"])
    stage2_weather = stage2[["timestamp", "ghi_wm2"]].copy()
    stage2_weather["timestamp"] = _utc_series(stage2_weather["timestamp"])
    stage2_weather["nsrdb_ghi_wm2"] = pd.to_numeric(stage2_weather["ghi_wm2"], errors="coerce")
    plot_frame = hrrr.merge(stage2_weather[["timestamp", "nsrdb_ghi_wm2"]], on="timestamp", how="left")
    plot_frame = plot_frame.sort_values("timestamp")

    try:
        hourly_figure_paths = _draw_hourly_probe_feature_figure(plot_frame=plot_frame, figure_path=figure_path)
    except Exception as exc:  # pragma: no cover - exercised only when matplotlib is absent.
        raise RuntimeError("matplotlib is required to generate the HRRR probe review figure.") from exc

    lines = [
        "# HRRR 严格 Probe 气象数据审查报告",
        "",
        f"- 判定: `{report['decision']}`",
        f"- 机器校验通过: `{report['passed']}`",
        f"- 原因: {report['reason']}",
        f"- 可视化图表: `{figure_path}`",
        f"- 小时级分窗口图表: {', '.join(f'`{path}`' for path in hourly_figure_paths)}",
        "",
        "## 门禁结果",
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
            "Probe 通过只代表抽取链路和字段物理形态可信，不能替代全量年度合约；全量抽取后仍必须运行 Stage7 年度合约。",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
