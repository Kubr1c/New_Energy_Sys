"""Run an HRRR point forecast batch from a manifest.

The intended production placement for this command is a near-source cloud
environment, for example an AWS job that can read public HRRR Zarr chunks
without sending all chunk bytes back to the local workstation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from new_energy_sys.hrrr_point_forecast import (
    HrrrPointForecastResult,
    collect_hrrr_point_forecast,
    load_hrrr_point_forecast_manifest,
    write_hrrr_point_forecast_batch_outputs,
)
import pandas as pd


def _collect_manifest_windows(
    *,
    collection: dict,
    execution: dict,
    cache_dir: Path,
    timeout_seconds: int,
    max_required_field_failures: int | None,
) -> HrrrPointForecastResult:
    """Collect every non-contiguous probe window and combine one batch audit.

    Strict probe manifests intentionally sample several seasons instead of one
    continuous date range.  Keeping this logic in the batch runner preserves the
    existing collector API while making the manifest itself the single source of
    truth for expected timestamps.
    """

    windows = collection["windows"]
    per_window_budget_gb = float(execution["remote_read_budget_gb"]) / max(len(windows), 1)
    frames = []
    audits = []
    for window in windows:
        result = collect_hrrr_point_forecast(
            start=window["start"],
            end=window["end"],
            latitude=float(collection["latitude"]),
            longitude=float(collection["longitude"]),
            lead_times=[int(value) for value in collection["lead_times"]],
            lead_times_as_candidates=bool(collection.get("lead_times_as_candidates", False)),
            bbox_deg=float(collection["bbox_deg"]),
            budget_gb=per_window_budget_gb,
            cache_dir=cache_dir,
            backends=tuple(execution["backends"]),
            timeout_seconds=timeout_seconds,
            stop_on_projected_budget=False,
            max_required_field_failures=max_required_field_failures,
        )
        if not result.forecast_weather.empty:
            frames.append(result.forecast_weather)
        audits.append(result.audit)

    forecast_weather = (
        pd.concat(frames, ignore_index=True)
        .sort_values(["timestamp", "weather_forecast_lead_time_hour"])
        .reset_index(drop=True)
        if frames
        else pd.DataFrame()
    )
    missing_timestamps = [
        timestamp
        for audit in audits
        for timestamp in audit.get("missing_timestamps", [])
    ]
    warnings = [
        warning
        for audit in audits
        for warning in audit.get("warnings", [])
    ]
    statuses = {str(audit.get("status")) for audit in audits}
    if not len(forecast_weather):
        status = "failed_no_rows"
    elif statuses == {"completed"} and not missing_timestamps:
        status = "completed"
    elif statuses.intersection({"budget_exceeded", "estimated_budget_exceeded", "failed_no_rows"}):
        status = sorted(statuses)[0]
    else:
        status = "completed_with_missing"

    audit = {
        "status": status,
        "start": collection["start"],
        "end": collection["end"],
        "windows": collection["windows"],
        "latitude": float(collection["latitude"]),
        "longitude": float(collection["longitude"]),
        "lead_times": [int(value) for value in collection["lead_times"]],
        "lead_times_as_candidates": bool(collection.get("lead_times_as_candidates", False)),
        "bbox_deg": float(collection["bbox_deg"]),
        "backends": list(execution["backends"]),
        "budget_bytes": int(sum(int(audit.get("budget_bytes", 0)) for audit in audits)),
        "downloaded_bytes": int(sum(int(audit.get("downloaded_bytes", 0)) for audit in audits)),
        "warning_threshold_bytes": int(sum(int(audit.get("warning_threshold_bytes", 0)) for audit in audits)),
        "warnings": warnings,
        "expected_rows": int(collection["expected_rows"]),
        "output_rows": int(len(forecast_weather)),
        "projected_downloaded_bytes": None,
        "missing_timestamps": missing_timestamps,
        "attempts": [
            attempt
            for audit in audits
            for attempt in audit.get("attempts", [])
        ],
        "window_audits": audits,
    }
    return HrrrPointForecastResult(forecast_weather=forecast_weather, audit=audit)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an HRRR point forecast batch manifest.")
    parser.add_argument("--manifest", required=True, help="Manifest JSON created by prepare_hrrr_point_forecast_manifest.")
    parser.add_argument("--output-parquet", help="Optional override for the forecast-weather parquet path.")
    parser.add_argument("--audit-json", help="Optional override for the audit JSON path.")
    parser.add_argument("--cache-dir", help="Optional override for the HRRR metadata/cache directory.")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="HTTP timeout per request.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_hrrr_point_forecast_manifest(Path(args.manifest))
    collection = manifest["collection"]
    execution = manifest["execution"]
    outputs = manifest["outputs"]

    output_parquet = Path(args.output_parquet or outputs["forecast_weather_parquet"])
    audit_json = Path(args.audit_json or outputs["audit_json"])
    cache_dir = Path(args.cache_dir or execution["cache_dir"])

    # In cloud-near-source mode, the 10GB target applies to final artifacts, not
    # to internal S3 chunk reads. The remote read budget is still enforced as a
    # safety cap, but the local-only projected-budget gate must stay disabled.
    if collection.get("windows"):
        result = _collect_manifest_windows(
            collection=collection,
            execution=execution,
            cache_dir=cache_dir,
            timeout_seconds=int(args.timeout_seconds),
            max_required_field_failures=3 if manifest.get("kind") == "hrrr_strict_probe_batch" else None,
        )
    else:
        result = collect_hrrr_point_forecast(
            start=collection["start"],
            end=collection["end"],
            latitude=float(collection["latitude"]),
            longitude=float(collection["longitude"]),
            lead_times=[int(value) for value in collection["lead_times"]],
            lead_times_as_candidates=bool(collection.get("lead_times_as_candidates", False)),
            bbox_deg=float(collection["bbox_deg"]),
            budget_gb=float(execution["remote_read_budget_gb"]),
            cache_dir=cache_dir,
            backends=tuple(execution["backends"]),
            timeout_seconds=int(args.timeout_seconds),
            stop_on_projected_budget=False,
        )
    final_audit = write_hrrr_point_forecast_batch_outputs(
        result,
        output_parquet=output_parquet,
        audit_json=audit_json,
        manifest=manifest,
    )

    print(f"HRRR batch rows: {len(result.forecast_weather)}")
    print(f"Remote read bytes: {final_audit['remote_read_bytes']}/{final_audit['remote_read_budget_bytes']}")
    print(f"Local output bytes: {final_audit['local_output_bytes']}/{final_audit['local_output_budget_bytes']}")
    print(f"Audit status: {final_audit['status']}")
    print(f"Forecast parquet: {output_parquet}")
    print(f"Audit JSON: {audit_json}")

    if final_audit["local_output_budget_exceeded"] or final_audit["status"] in {"budget_exceeded", "failed_no_rows"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
