"""Budget-aware HRRR point forecast collection for Stage7.

The older HRRR path downloads GRIB records that still contain full CONUS grids.
This module uses the official NOMADS grib filter first, requesting only a small
lat/lon bounding box around the site.  The output is a compact forecast-valid-time
table with native issue_time and lead_time metadata that Stage7 can consume
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
import json
import math

import numpy as np
import pandas as pd
import requests

from new_energy_sys.io_utils import ensure_dir


NOMADS_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
DEFAULT_BACKENDS = ("nomads_bbox", "zarr_chunk")
DEFAULT_CLOUD_BACKENDS = ("zarr_chunk",)
DEFAULT_STRICT_PROBE_DATES = ("2022-01-15", "2022-04-15", "2022-07-15", "2022-10-15")
HTTP_HEADERS = {"User-Agent": "new-energy-sys-hrrr-point-forecast/0.1"}
STAGE7_CORE_WEATHER_COLUMNS = (
    "ghi_wm2",
    "temperature_c",
    "relative_humidity_pct",
    "surface_pressure_hpa",
    "wind_speed_ms",
    "wind_direction_deg",
    "cloud_cover_pct",
    "precipitation_mm",
)
REQUIRED_NOMADS_VARIABLES = ("TMP", "RH", "UGRD", "VGRD", "APCP", "TCDC", "DSWRF", "PRES")
REQUIRED_NOMADS_LEVELS = (
    "2_m_above_ground",
    "10_m_above_ground",
    "surface",
    "entire_atmosphere",
)
ZARR_VARIABLES = {
    "TMP": (("TMP", "2m_above_ground"),),
    "RH": (("RH", "2m_above_ground"),),
    "UGRD": (("UGRD", "10m_above_ground"),),
    "VGRD": (("VGRD", "10m_above_ground"),),
    "APCP": (("APCP_acc_fcst", "surface"), ("APCP", "surface")),
    "TCDC": (("TCDC", "entire_atmosphere"),),
    "DSWRF": (("DSWRF", "surface"),),
    "PRES": (("PRES", "surface"),),
}
ZARR_REQUIRED_KEYS = {"TMP", "RH", "UGRD", "VGRD", "APCP", "TCDC", "PRES", "DSWRF"}


def _portable_manifest_path(value: str | Path) -> str:
    """Return a manifest path that works on Windows authors and Linux runners."""

    return str(value).replace("\\", "/")


@dataclass(frozen=True)
class HrrrPointForecastResult:
    """Collected HRRR forecast table and its reproducibility audit."""

    forecast_weather: pd.DataFrame
    audit: dict[str, Any]


class DownloadBudget:
    """Track the hard download budget and emit a warning at 80% usage."""

    def __init__(self, budget_gb: float) -> None:
        if budget_gb <= 0:
            raise ValueError("budget_gb must be positive.")
        self.budget_bytes = int(float(budget_gb) * 1024**3)
        self.downloaded_bytes = 0
        self.warned_80pct = False

    def add(self, byte_count: int) -> list[str]:
        """Add downloaded bytes and return newly triggered warning messages."""

        if byte_count < 0:
            raise ValueError("byte_count must be non-negative.")
        self.downloaded_bytes += int(byte_count)
        warnings: list[str] = []
        if not self.warned_80pct and self.downloaded_bytes >= self.budget_bytes * 0.8:
            self.warned_80pct = True
            warnings.append(
                f"downloaded bytes reached 80% of budget: {self.downloaded_bytes}/{self.budget_bytes}"
            )
        return warnings

    @property
    def exceeded(self) -> bool:
        return self.downloaded_bytes > self.budget_bytes


def _project_download_bytes(
    *,
    successful_downloaded_bytes: int,
    successful_rows: int,
    expected_rows: int,
) -> int | None:
    """Estimate full-run download volume from completed successful samples.

    HRRR public Zarr objects are much coarser than one station-hour point. A
    single successful row can therefore prove that a 24-hour or full-year run is
    over budget even before the hard counter crosses the limit. This estimate is
    used only as a stop gate; the audit keeps the observed and projected bytes
    separate so the decision remains reproducible.
    """

    if successful_rows <= 0 or expected_rows <= 0:
        return None
    return int(math.ceil(successful_downloaded_bytes / successful_rows * expected_rows))


class BackendDownloadError(RuntimeError):
    """Backend failure that still carries audit information."""

    def __init__(self, message: str, *, source_url: str | None = None, downloaded_bytes: int = 0) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.downloaded_bytes = int(downloaded_bytes)


def parse_lead_times(value: str) -> list[int]:
    """Parse a comma-separated lead-time list such as ``24`` or ``6,24``."""

    lead_times = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not lead_times:
        raise ValueError("at least one lead time is required.")
    if any(lead < 0 or lead > 48 for lead in lead_times):
        raise ValueError("HRRR lead times must be between 0 and 48 hours.")
    return sorted(set(lead_times))


def build_valid_times(start: str, end: str) -> pd.DatetimeIndex:
    """Build an inclusive hourly UTC valid-time range from date strings."""

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    if end_ts < start_ts:
        raise ValueError(f"invalid HRRR range: {start} > {end}")
    if str(end).count(":") == 0:
        end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(hours=1)
    return pd.date_range(start=start_ts, end=end_ts, freq="1h", tz="UTC")


def build_stratified_probe_windows(
    *,
    year: int,
    start_dates: tuple[str, ...] | None = None,
    window_hours: int = 48,
) -> list[dict[str, str]]:
    """Build fixed seasonal probe windows before the expensive yearly run.

    The default windows sample winter, spring, summer, and autumn.  Each window
    is represented as an inclusive hourly range so the manifest can be checked
    exactly against the downloaded parquet.
    """

    if window_hours <= 0:
        raise ValueError("window_hours must be positive.")
    dates = start_dates or tuple(value.replace("2022", str(year), 1) for value in DEFAULT_STRICT_PROBE_DATES)
    windows: list[dict[str, str]] = []
    for value in dates:
        start = pd.Timestamp(value, tz="UTC")
        end = start + pd.Timedelta(hours=window_hours - 1)
        windows.append(
            {
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    return windows


def build_hrrr_point_forecast_manifest(
    *,
    start: str,
    end: str,
    latitude: float,
    longitude: float,
    lead_times: list[int],
    output_parquet: str,
    audit_json: str,
    local_output_budget_gb: float,
    remote_read_budget_gb: float,
    lead_times_as_candidates: bool = False,
    bbox_deg: float = 0.05,
    cache_dir: str = "data/raw/hrrr_point_forecast_cache",
    backends: tuple[str, ...] = DEFAULT_CLOUD_BACKENDS,
) -> dict[str, Any]:
    """Build a reproducible HRRR point-forecast batch manifest.

    The manifest is intentionally explicit because it is the contract between
    the local project and a near-source cloud runner. The cloud runner may read
    tens of gigabytes of public HRRR chunks, but the project budget is enforced
    against the final parquet/audit artifacts that come back to the local
    workspace.
    """

    if local_output_budget_gb <= 0:
        raise ValueError("local_output_budget_gb must be positive.")
    if remote_read_budget_gb <= 0:
        raise ValueError("remote_read_budget_gb must be positive.")
    valid_times = build_valid_times(start, end)
    expected_rows = int(len(valid_times) if lead_times_as_candidates else len(valid_times) * len(lead_times))
    return {
        "schema_version": 1,
        "kind": "hrrr_point_forecast_batch",
        "collection": {
            "start": start,
            "end": end,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "lead_times": [int(value) for value in lead_times],
            "lead_times_as_candidates": bool(lead_times_as_candidates),
            "bbox_deg": float(bbox_deg),
            "expected_rows": expected_rows,
            "expected_valid_time_start": str(valid_times[0]) if len(valid_times) else None,
            "expected_valid_time_end": str(valid_times[-1]) if len(valid_times) else None,
        },
        "execution": {
            "mode": "cloud_near_source",
            "backends": list(backends),
            "cache_dir": cache_dir,
            "remote_read_budget_gb": float(remote_read_budget_gb),
            "remote_read_budget_bytes": int(float(remote_read_budget_gb) * 1024**3),
            "local_output_budget_gb": float(local_output_budget_gb),
            "local_output_budget_bytes": int(float(local_output_budget_gb) * 1024**3),
        },
        "outputs": {
            "forecast_weather_parquet": _portable_manifest_path(output_parquet),
            "audit_json": _portable_manifest_path(audit_json),
        },
        "hrrr_source": {
            "archive": "s3://hrrrzarr/sfc",
            "grid_index": "s3://hrrrzarr/grid/HRRR_chunk_index.zarr",
            "product": "sfc",
            "forecast_cycle_rule": "issue_time = valid_time - lead_time_hour; candidate lead-times keep the first available row per valid_time",
            "backend_policy": "near-source runner reads HRRR Zarr chunks; local workspace receives parquet/audit only",
        },
        "stage7_contract": {
            "required_audit_columns": [
                "timestamp",
                "weather_forecast_issue_time",
                "weather_forecast_lead_time_hour",
                "source_url",
                "backend",
                "downloaded_bytes",
                "grid_latitude",
                "grid_longitude",
            ],
            "core_weather_columns": list(STAGE7_CORE_WEATHER_COLUMNS),
            "leakage_gate": "weather_forecast_issue_time <= prediction_time",
        },
    }


def build_hrrr_strict_probe_manifest(
    *,
    year: int,
    latitude: float,
    longitude: float,
    output_parquet: str,
    audit_json: str,
    local_output_budget_gb: float = 0.05,
    remote_read_budget_gb: float = 8.0,
    lead_times: list[int] | None = None,
    bbox_deg: float = 0.05,
    cache_dir: str = "data/raw/hrrr_point_forecast_cache",
    backends: tuple[str, ...] = DEFAULT_CLOUD_BACKENDS,
    window_hours: int = 48,
) -> dict[str, Any]:
    """Build the mandatory strict probe manifest for HRRR release gating.

    Unlike a monthly manifest, this manifest is intentionally non-contiguous:
    it samples four seasonal windows and must pass validation plus visual review
    before the remote runner is allowed to start the full-year extraction.
    """

    lead_times = lead_times or list(range(24, 49))
    windows = build_stratified_probe_windows(year=year, window_hours=window_hours)
    expected_times = pd.DatetimeIndex(
        np.concatenate([build_valid_times(window["start"], window["end"]).values for window in windows])
    ).tz_localize("UTC")
    manifest = build_hrrr_point_forecast_manifest(
        start=windows[0]["start"],
        end=windows[-1]["end"],
        latitude=latitude,
        longitude=longitude,
        lead_times=lead_times,
        lead_times_as_candidates=True,
        bbox_deg=bbox_deg,
        local_output_budget_gb=local_output_budget_gb,
        remote_read_budget_gb=remote_read_budget_gb,
        cache_dir=cache_dir,
        backends=backends,
        output_parquet=output_parquet,
        audit_json=audit_json,
    )
    manifest["kind"] = "hrrr_strict_probe_batch"
    manifest["collection"].update(
        {
            "year": int(year),
            "windows": windows,
            "window_hours": int(window_hours),
            "expected_rows": int(len(expected_times)),
            "expected_valid_timestamps": [str(value) for value in expected_times],
            "expected_valid_time_start": str(expected_times[0]) if len(expected_times) else None,
            "expected_valid_time_end": str(expected_times[-1]) if len(expected_times) else None,
        }
    )
    manifest["stage7_contract"]["probe_gate"] = (
        "strict machine validation and human visual review are required before yearly extraction"
    )
    return manifest


def build_hrrr_monthly_point_forecast_manifests(
    *,
    year: int,
    latitude: float,
    longitude: float,
    lead_times: list[int],
    output_dir: str,
    audit_dir: str,
    manifest_dir: str,
    local_output_budget_gb: float,
    remote_read_budget_gb: float,
    lead_times_as_candidates: bool = False,
    bbox_deg: float = 0.05,
    cache_dir: str = "data/raw/hrrr_point_forecast_cache",
    backends: tuple[str, ...] = DEFAULT_CLOUD_BACKENDS,
) -> list[tuple[Path, dict[str, Any]]]:
    """Build one cloud manifest per month for a resumable yearly extraction.

    HRRR cloud reads can fail on individual cycles or because a temporary EC2
    session is interrupted. Monthly manifests keep the work small enough that a
    failed run can be repeated without discarding completed months.
    """

    manifests: list[tuple[Path, dict[str, Any]]] = []
    for month in range(1, 13):
        start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        end = start + pd.offsets.MonthEnd(0)
        suffix = f"{year}_{month:02d}_f{lead_times[0]:02d}"
        manifest = build_hrrr_point_forecast_manifest(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            latitude=latitude,
            longitude=longitude,
            lead_times=lead_times,
            lead_times_as_candidates=lead_times_as_candidates,
            bbox_deg=bbox_deg,
            local_output_budget_gb=local_output_budget_gb,
            remote_read_budget_gb=remote_read_budget_gb,
            cache_dir=cache_dir,
            backends=backends,
            output_parquet=_portable_manifest_path(Path(output_dir) / f"stage7_hrrr_forecast_weather_{suffix}.parquet"),
            audit_json=_portable_manifest_path(Path(audit_dir) / f"hrrr_point_forecast_{suffix}_audit.json"),
        )
        manifest_path = Path(manifest_dir) / f"hrrr_point_forecast_{suffix}_manifest.json"
        manifests.append((manifest_path, manifest))
    return manifests


def load_hrrr_point_forecast_manifest(path: Path) -> dict[str, Any]:
    """Load and validate the minimal manifest contract for a batch run."""

    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported HRRR point forecast manifest schema_version.")
    if manifest.get("kind") not in {"hrrr_point_forecast_batch", "hrrr_strict_probe_batch"}:
        raise ValueError("manifest kind must be hrrr_point_forecast_batch or hrrr_strict_probe_batch.")
    for section in ("collection", "execution", "outputs"):
        if section not in manifest or not isinstance(manifest[section], dict):
            raise ValueError(f"manifest is missing section: {section}")
    return manifest


def build_nomads_bbox_url(
    *,
    valid_time: pd.Timestamp,
    lead_time_hour: int,
    latitude: float,
    longitude: float,
    bbox_deg: float,
) -> str:
    """Build the official NOMADS grib-filter URL for one HRRR forecast sample."""

    if valid_time.tzinfo is None:
        valid_time = valid_time.tz_localize("UTC")
    else:
        valid_time = valid_time.tz_convert("UTC")

    issue_time = (valid_time - pd.Timedelta(hours=lead_time_hour)).floor("h")
    params: dict[str, str] = {
        "file": f"hrrr.t{issue_time:%H}z.wrfsfcf{lead_time_hour:02d}.grib2",
        "subregion": "",
        "leftlon": f"{longitude - bbox_deg:.4f}",
        "rightlon": f"{longitude + bbox_deg:.4f}",
        "toplat": f"{latitude + bbox_deg:.4f}",
        "bottomlat": f"{latitude - bbox_deg:.4f}",
        "dir": f"/hrrr.{issue_time:%Y%m%d}/conus",
    }
    for variable in REQUIRED_NOMADS_VARIABLES:
        params[f"var_{variable}"] = "on"
    for level in REQUIRED_NOMADS_LEVELS:
        params[f"lev_{level}"] = "on"
    return f"{NOMADS_FILTER_URL}?{urlencode(params)}"


def _is_probably_grib(content: bytes) -> bool:
    """Reject HTML or plaintext error pages before cfgrib tries to parse them."""

    prefix = content[:128].lstrip().lower()
    return bool(content) and not (
        prefix.startswith(b"<")
        or prefix.startswith(b"<!doctype")
        or prefix.startswith(b"data file is not present")
        or b"invalid file request" in prefix
    )


def _download_nomads_bbox_grib(
    *,
    url: str,
    target: Path,
    timeout_seconds: int,
) -> tuple[Path, int]:
    """Download one NOMADS bbox-filtered GRIB file and return its byte count."""

    if target.exists():
        return target, int(target.stat().st_size)

    ensure_dir(target.parent)
    response = requests.get(url, timeout=timeout_seconds, headers=HTTP_HEADERS)
    downloaded_bytes = len(response.content)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise BackendDownloadError(
            f"{response.status_code} {response.reason} from NOMADS",
            source_url=url,
            downloaded_bytes=downloaded_bytes,
        ) from exc
    if not _is_probably_grib(response.content):
        raise BackendDownloadError(
            "NOMADS did not return a GRIB payload",
            source_url=url,
            downloaded_bytes=downloaded_bytes,
        )

    temp_target = target.with_suffix(target.suffix + ".tmp")
    temp_target.write_bytes(response.content)
    temp_target.replace(target)
    return target, downloaded_bytes


def _wind_direction_deg(u_ms: float | None, v_ms: float | None) -> float | None:
    """Convert U/V wind components to meteorological direction degrees."""

    if u_ms is None or v_ms is None or math.isnan(u_ms) or math.isnan(v_ms):
        return None
    return float((270.0 - math.degrees(math.atan2(v_ms, u_ms))) % 360.0)


def _dew_point_c(temperature_c: float | None, relative_humidity_pct: float | None) -> float | None:
    """Estimate dew point from temperature and relative humidity."""

    if (
        temperature_c is None
        or relative_humidity_pct is None
        or math.isnan(temperature_c)
        or math.isnan(relative_humidity_pct)
        or relative_humidity_pct <= 0
    ):
        return None
    bounded_rh = min(max(relative_humidity_pct, 1e-6), 100.0)
    a = 17.625
    b = 243.04
    gamma = math.log(bounded_rh / 100.0) + (a * temperature_c) / (b + temperature_c)
    return float((b * gamma) / (a - gamma))


def _normalize_hrrr_sample(
    sample: pd.DataFrame,
    *,
    backend: str,
    source_url: str,
    downloaded_bytes: int,
) -> pd.DataFrame:
    """Add Stage7 audit columns and derived weather fields to one HRRR sample."""

    row = sample.copy()
    row["source_url"] = source_url
    row["backend"] = backend
    row["downloaded_bytes"] = int(downloaded_bytes)
    row["pressure_hpa"] = row.get("surface_pressure_hpa")
    if {"wind_u10_ms", "wind_v10_ms"}.issubset(row.columns):
        row["wind_direction_deg"] = [
            _wind_direction_deg(u, v) for u, v in zip(row["wind_u10_ms"], row["wind_v10_ms"])
        ]
    if {"temperature_c", "relative_humidity_pct"}.issubset(row.columns):
        row["dew_point_c"] = [
            _dew_point_c(temp, rh)
            for temp, rh in zip(row["temperature_c"], row["relative_humidity_pct"])
        ]
    return row


def _collect_nomads_sample(
    *,
    valid_time: pd.Timestamp,
    lead_time_hour: int,
    latitude: float,
    longitude: float,
    bbox_deg: float,
    cache_dir: Path,
    timeout_seconds: int,
) -> tuple[pd.DataFrame, dict[str, Any], int]:
    """Collect one forecast-valid-time sample through NOMADS bbox filtering."""

    url = build_nomads_bbox_url(
        valid_time=valid_time,
        lead_time_hour=lead_time_hour,
        latitude=latitude,
        longitude=longitude,
        bbox_deg=bbox_deg,
    )
    cache_name = f"nomads_hrrr_t{valid_time:%Y%m%d%H}_f{lead_time_hour:02d}_bbox{bbox_deg:.3f}.grib2"
    grib_path, downloaded_bytes = _download_nomads_bbox_grib(
        url=url,
        target=cache_dir / cache_name,
        timeout_seconds=timeout_seconds,
    )
    # Keep the legacy GRIB dependency lazy. The cloud zarr path deliberately
    # installs a minimal dependency set and should not need cfgrib/eccodes just
    # to import this module or run zarr-only tests.
    from new_energy_sys.hrrr import extract_hrrr_point_sample

    sample = extract_hrrr_point_sample(
        grib_path=grib_path,
        latitude=latitude,
        longitude=longitude,
    ).frame
    frame = _normalize_hrrr_sample(
        sample,
        backend="nomads_bbox",
        source_url=url,
        downloaded_bytes=downloaded_bytes,
    )
    audit = {
        "timestamp": str(valid_time),
        "lead_time_hour": int(lead_time_hour),
        "backend": "nomads_bbox",
        "source_url": url,
        "downloaded_bytes": int(downloaded_bytes),
        "status": "ok",
        "error": None,
    }
    return frame, audit, downloaded_bytes


def _load_hrrrzarr_context(latitude: float, longitude: float, *, cache_dir: Path) -> dict[str, Any]:
    """Load the HRRR Zarr chunk index and locate the nearest grid point.

    The chunk-index table is stable for the HRRR grid but slow to reopen from
    remote S3 under weak network conditions. We cache only the derived station
    mapping, not weather values, so repeated probes keep the same audit trail
    while avoiding a non-weather metadata download on every run.
    """

    try:
        import s3fs
        import xarray as xr
    except Exception as exc:
        raise RuntimeError("zarr_chunk backend requires `s3fs` and `xarray`.") from exc

    fs = s3fs.S3FileSystem(anon=True)
    ensure_dir(cache_dir)
    context_cache = cache_dir / (
        "hrrrzarr_context_"
        f"lat{latitude:.5f}_lon{longitude:.5f}".replace("-", "m").replace(".", "p")
        + ".json"
    )
    if context_cache.exists():
        cached = json.loads(context_cache.read_text(encoding="utf-8"))
        cached["fs"] = fs
        return cached

    chunk_index = xr.open_zarr(s3fs.S3Map("s3://hrrrzarr/grid/HRRR_chunk_index.zarr", s3=fs))
    lat_grid = chunk_index["latitude"].values
    lon_grid = chunk_index["longitude"].values
    if np.nanmax(lon_grid) > 180.0 and longitude < 0.0:
        target_lon = longitude + 360.0
    else:
        target_lon = longitude
    distance = (lat_grid - latitude) ** 2 + (lon_grid - target_lon) ** 2
    row_index, col_index = np.unravel_index(int(np.nanargmin(distance)), distance.shape)
    nearest = chunk_index.isel(y=row_index, x=col_index)
    chunk_id = str(nearest["chunk_id"].values)
    grid_lon = float(nearest["longitude"].values)
    if grid_lon > 180.0:
        grid_lon -= 360.0
    context = {
        "fs": fs,
        "chunk_id": chunk_id,
        "in_chunk_y": int(nearest["in_chunk_y"].values),
        "in_chunk_x": int(nearest["in_chunk_x"].values),
        "grid_latitude": float(nearest["latitude"].values),
        "grid_longitude": grid_lon,
    }
    context_cache.write_text(
        json.dumps({key: value for key, value in context.items() if key != "fs"}, indent=2),
        encoding="utf-8",
    )
    return context


def _hrrrzarr_chunk_path(*, issue_time: pd.Timestamp, variable: str, level: str, chunk_id: str) -> str:
    """Build the public hrrrzarr S3 object path for one variable chunk."""

    return (
        issue_time.strftime(f"hrrrzarr/sfc/%Y%m%d/%Y%m%d_%Hz_fcst.zarr/")
        + f"{level}/{variable}/{level}/{variable}/0.{chunk_id}"
    )


def _zarr_dtype(variable: str, level: str) -> str:
    """Return the documented hrrrzarr chunk dtype for supported variables."""

    if variable == "PRES" and level == "surface":
        return "<f4"
    return "<f2"


def _read_hrrrzarr_values(
    *,
    fs: Any,
    issue_time: pd.Timestamp,
    lead_time_hours: list[int],
    variable: str,
    level: str,
    chunk_id: str,
    in_chunk_y: int,
    in_chunk_x: int,
) -> tuple[dict[int, float | None], int, str]:
    """Read one point for one variable at multiple leads from a single chunk.

    HRRR Zarr stores all forecast leads for a spatial tile in one compressed
    object.  Reading current and previous APCP leads separately would download
    the same object twice.  This helper decompresses the object once and returns
    the requested lead values, keeping full-year extraction costs bounded while
    still allowing cumulative fields to be converted safely.
    """

    try:
        import numcodecs as ncd
    except Exception as exc:
        raise RuntimeError("zarr_chunk backend requires the optional `numcodecs` package.") from exc

    path = _hrrrzarr_chunk_path(
        issue_time=issue_time,
        variable=variable,
        level=level,
        chunk_id=chunk_id,
    )
    with fs.open(path, "rb") as handle:
        compressed = handle.read()
    buffer = ncd.blosc.decompress(compressed)
    chunk = np.frombuffer(buffer, dtype=_zarr_dtype(variable, level))
    entry_size = 150 * 150
    if len(chunk) < entry_size:
        raise RuntimeError(f"hrrrzarr chunk is smaller than one spatial tile: {path}")
    data = np.reshape(chunk, (len(chunk) // entry_size, 150, 150))
    max_lead = max(lead_time_hours)
    if max_lead >= data.shape[0]:
        raise RuntimeError(
            f"hrrrzarr chunk {path} has {data.shape[0]} lead entries; cannot read f{max_lead:02d}"
        )

    values: dict[int, float | None] = {}
    for lead_time_hour in lead_time_hours:
        value = float(data[lead_time_hour, in_chunk_y, in_chunk_x])
        values[int(lead_time_hour)] = None if np.isnan(value) else value
    return values, len(compressed), path


def _read_hrrrzarr_value(
    *,
    fs: Any,
    issue_time: pd.Timestamp,
    lead_time_hour: int,
    variable: str,
    level: str,
    chunk_id: str,
    in_chunk_y: int,
    in_chunk_x: int,
) -> tuple[float | None, int, str]:
    """Read one point value from one compressed HRRR Zarr chunk."""

    values, byte_count, path = _read_hrrrzarr_values(
        fs=fs,
        issue_time=issue_time,
        lead_time_hours=[lead_time_hour],
        variable=variable,
        level=level,
        chunk_id=chunk_id,
        in_chunk_y=in_chunk_y,
        in_chunk_x=in_chunk_x,
    )
    return values[int(lead_time_hour)], byte_count, path


def _read_hrrrzarr_hourly_precipitation(
    *,
    fs: Any,
    issue_time: pd.Timestamp,
    lead_time_hour: int,
    candidates: tuple[tuple[str, str], ...],
    chunk_id: str,
    in_chunk_y: int,
    in_chunk_x: int,
) -> tuple[float | None, int, str | None, list[str], dict[str, Any]]:
    """Convert HRRR accumulated precipitation to an hourly increment.

    HRRR surface forecast precipitation is usually exposed as an accumulated
    field (`APCP_acc_fcst`).  Stage7 expects hourly weather features, so the
    correct value is the difference between the current and previous lead within
    the same forecast cycle.  Cross-cycle differencing is intentionally avoided
    because accumulated precipitation resets at the cycle boundary.
    """

    errors: list[str] = []
    for variable, level in candidates:
        try:
            if lead_time_hour <= 0:
                values, byte_count, source_path = _read_hrrrzarr_values(
                    fs=fs,
                    issue_time=issue_time,
                    lead_time_hours=[lead_time_hour],
                    variable=variable,
                    level=level,
                    chunk_id=chunk_id,
                    in_chunk_y=in_chunk_y,
                    in_chunk_x=in_chunk_x,
                )
                current = values.get(int(lead_time_hour))
                if current is None:
                    raise RuntimeError("current accumulated precipitation is NaN")
                hourly = max(float(current), 0.0)
                return hourly, byte_count, source_path, errors, {
                    "precipitation_transform": "accumulated_to_hourly_diff",
                    "precipitation_current_lead_hour": int(lead_time_hour),
                    "precipitation_previous_lead_hour": None,
                    "precipitation_accumulated_current_mm": float(current),
                    "precipitation_accumulated_previous_mm": None,
                    "precipitation_negative_diff_clipped": False,
                }

            values, byte_count, source_path = _read_hrrrzarr_values(
                fs=fs,
                issue_time=issue_time,
                lead_time_hours=[lead_time_hour - 1, lead_time_hour],
                variable=variable,
                level=level,
                chunk_id=chunk_id,
                in_chunk_y=in_chunk_y,
                in_chunk_x=in_chunk_x,
            )
            current = values.get(int(lead_time_hour))
            previous = values.get(int(lead_time_hour - 1))
            if current is None or previous is None:
                raise RuntimeError("current or previous accumulated precipitation is NaN")
            diff = float(current) - float(previous)
            negative_diff_clipped = False
            if diff < -1e-6:
                raise RuntimeError(
                    "accumulated precipitation decreased within one forecast cycle: "
                    f"f{lead_time_hour - 1:02d}={previous}, f{lead_time_hour:02d}={current}"
                )
            if diff < 0.0:
                diff = 0.0
                negative_diff_clipped = True
            return float(diff), byte_count, source_path, errors, {
                "precipitation_transform": "accumulated_to_hourly_diff",
                "precipitation_current_lead_hour": int(lead_time_hour),
                "precipitation_previous_lead_hour": int(lead_time_hour - 1),
                "precipitation_accumulated_current_mm": float(current),
                "precipitation_accumulated_previous_mm": float(previous),
                "precipitation_negative_diff_clipped": negative_diff_clipped,
            }
        except FileNotFoundError as exc:
            errors.append(f"{variable}@{level}: {exc}")
        except Exception as exc:
            errors.append(f"{variable}@{level}: {exc}")
    return None, 0, None, errors, {"precipitation_transform": "failed_accumulated_to_hourly_diff"}


def _read_hrrrzarr_first_available(
    *,
    fs: Any,
    issue_time: pd.Timestamp,
    lead_time_hour: int,
    candidates: tuple[tuple[str, str], ...],
    chunk_id: str,
    in_chunk_y: int,
    in_chunk_x: int,
) -> tuple[float | None, int, str | None, list[str]]:
    """Read the first available candidate for a logical weather field."""

    errors: list[str] = []
    for variable, level in candidates:
        try:
            value, byte_count, source_path = _read_hrrrzarr_value(
                fs=fs,
                issue_time=issue_time,
                lead_time_hour=lead_time_hour,
                variable=variable,
                level=level,
                chunk_id=chunk_id,
                in_chunk_y=in_chunk_y,
                in_chunk_x=in_chunk_x,
            )
            return value, byte_count, source_path, errors
        except FileNotFoundError as exc:
            errors.append(f"{variable}@{level}: {exc}")
        except Exception as exc:
            errors.append(f"{variable}@{level}: {exc}")
    return None, 0, None, errors


def _read_grib_dswrf_value(
    *,
    valid_time: pd.Timestamp,
    lead_time_hour: int,
    latitude: float,
    longitude: float,
    cache_dir: Path,
    timeout_seconds: int,
) -> tuple[float | None, int, str]:
    """Read DSWRF from NOAA HRRR GRIB by downloading only its byte range.

    The public `hrrrzarr` forecast archive contains DSWRF Zarr metadata for the
    tested 2022 cycles, but the actual forecast chunk objects are absent.  DSWRF
    is a hard Stage7 input, so the production path reads this one variable from
    the NOAA HRRR GRIB archive instead of silently filling zeros.  The downloaded
    subset contains only the DSWRF GRIB message selected from the `.idx` file.
    """

    from new_energy_sys.hrrr import (
        DSWRF_REQUIRED_PATTERNS,
        _download_hrrr_subset,
        build_hrrr_cycle_urls,
        extract_hrrr_dswrf_point_sample,
    )

    grib_url, idx_url, cache_stem = build_hrrr_cycle_urls(
        valid_time=valid_time,
        lead_time_hour=lead_time_hour,
    )
    subset_target = cache_dir / "grib_dswrf" / f"{cache_stem}_dswrf.grib2"
    subset_path = _download_hrrr_subset(
        grib_url=grib_url,
        idx_url=idx_url,
        subset_target=subset_target,
        timeout_seconds=timeout_seconds,
        required_patterns=DSWRF_REQUIRED_PATTERNS,
    )
    sample = extract_hrrr_dswrf_point_sample(
        grib_path=subset_path,
        latitude=latitude,
        longitude=longitude,
    )
    value = sample.frame["ghi_wm2"].iloc[0]
    if value is None or pd.isna(value):
        return None, int(subset_path.stat().st_size), f"{grib_url}#idx=DSWRF@surface"
    return float(value), int(subset_path.stat().st_size), f"{grib_url}#idx=DSWRF@surface"


def _missing_required_fields_from_error(message: str) -> set[str]:
    """Parse missing-field names from the collector's required-field error.

    Candidate lead-time mode is expected to encounter unavailable HRRR cycles:
    for example an hourly 03z cycle may not publish f24+ products while the 00z
    cycle can still satisfy the same valid timestamp with f27.  Those multi-field
    failures must not trip the DSWRF fast stop.  A pure `DSWRF` miss, however,
    means the radiation source itself is broken and should still stop quickly.
    """

    prefix = "hrrrzarr missing required fields:"
    if not message.startswith(prefix):
        return set()
    return {field.strip() for field in message[len(prefix) :].split(",") if field.strip()}


def _collect_zarr_sample(
    *,
    valid_time: pd.Timestamp,
    lead_time_hour: int,
    zarr_context: dict[str, Any],
    latitude: float,
    longitude: float,
    cache_dir: Path,
    timeout_seconds: int,
) -> tuple[pd.DataFrame, dict[str, Any], int]:
    """Collect one forecast-valid-time sample through Zarr plus GRIB DSWRF.

    Zarr remains the near-source path for temperature, humidity, wind, pressure,
    cloud cover, and precipitation.  Surface downward shortwave radiation is
    read from a single-message GRIB subset because the forecast Zarr archive
    does not currently expose usable DSWRF chunk data for the target year.
    """

    if valid_time.tzinfo is None:
        valid_time = valid_time.tz_localize("UTC")
    else:
        valid_time = valid_time.tz_convert("UTC")
    issue_time = (valid_time - pd.Timedelta(hours=lead_time_hour)).floor("h")

    raw_values: dict[str, float | None] = {}
    source_paths: list[str] = []
    missing_fields: dict[str, list[str]] = {}
    field_audit: dict[str, Any] = {}
    downloaded_bytes = 0
    for logical_name, candidates in ZARR_VARIABLES.items():
        if logical_name == "DSWRF":
            try:
                value, byte_count, source_path = _read_grib_dswrf_value(
                    valid_time=valid_time,
                    lead_time_hour=lead_time_hour,
                    latitude=latitude,
                    longitude=longitude,
                    cache_dir=cache_dir,
                    timeout_seconds=timeout_seconds,
                )
                errors: list[str] = []
            except Exception as exc:
                value = None
                byte_count = 0
                source_path = None
                errors = [f"DSWRF@surface_grib_subset: {exc}"]
        elif logical_name == "APCP":
            value, byte_count, source_path, errors, precip_audit = _read_hrrrzarr_hourly_precipitation(
                fs=zarr_context["fs"],
                issue_time=issue_time,
                lead_time_hour=lead_time_hour,
                candidates=candidates,
                chunk_id=zarr_context["chunk_id"],
                in_chunk_y=zarr_context["in_chunk_y"],
                in_chunk_x=zarr_context["in_chunk_x"],
            )
            field_audit.update(precip_audit)
        else:
            value, byte_count, source_path, errors = _read_hrrrzarr_first_available(
                fs=zarr_context["fs"],
                issue_time=issue_time,
                lead_time_hour=lead_time_hour,
                candidates=candidates,
                chunk_id=zarr_context["chunk_id"],
                in_chunk_y=zarr_context["in_chunk_y"],
                in_chunk_x=zarr_context["in_chunk_x"],
            )
        if source_path:
            source_paths.append(source_path)
        if errors:
            missing_fields[logical_name] = errors
        raw_values[logical_name] = value
        downloaded_bytes += byte_count

    missing_required = sorted(key for key in ZARR_REQUIRED_KEYS if raw_values.get(key) is None)
    if missing_required:
        raise BackendDownloadError(
            f"hrrrzarr missing required fields: {', '.join(missing_required)}",
            source_url=";".join(source_paths) if source_paths else None,
            downloaded_bytes=downloaded_bytes,
        )

    u10 = raw_values["UGRD"]
    v10 = raw_values["VGRD"]
    wind_speed_ms = None
    if u10 is not None and v10 is not None:
        wind_speed_ms = math.sqrt(u10**2 + v10**2)
    temperature_c = None if raw_values["TMP"] is None else raw_values["TMP"] - 273.15
    pressure_hpa = None if raw_values["PRES"] is None else raw_values["PRES"] / 100.0
    frame = pd.DataFrame(
        [
            {
                "timestamp": valid_time,
                "weather_forecast_issue_time": issue_time,
                "weather_forecast_lead_time_hour": float(lead_time_hour),
                "grid_latitude": zarr_context["grid_latitude"],
                "grid_longitude": zarr_context["grid_longitude"],
                "temperature_c": temperature_c,
                "relative_humidity_pct": raw_values["RH"],
                "wind_u10_ms": u10,
                "wind_v10_ms": v10,
                "wind_speed_ms": wind_speed_ms,
                "wind_direction_deg": _wind_direction_deg(u10, v10),
                "precipitation_mm": raw_values["APCP"],
                "cloud_cover_pct": raw_values["TCDC"],
                "ghi_wm2": raw_values["DSWRF"],
                "surface_pressure_hpa": pressure_hpa,
                "pressure_hpa": pressure_hpa,
                "dew_point_c": _dew_point_c(temperature_c, raw_values["RH"]),
                "source_url": ";".join(source_paths),
                "backend": "zarr_chunk",
                "downloaded_bytes": int(downloaded_bytes),
            }
        ]
    )
    audit = {
        "timestamp": str(valid_time),
        "lead_time_hour": int(lead_time_hour),
        "backend": "zarr_chunk",
        "source_url": ";".join(source_paths),
        "downloaded_bytes": int(downloaded_bytes),
        "status": "ok",
        "error": None,
        "missing_optional_fields": missing_fields,
        **field_audit,
    }
    return frame, audit, downloaded_bytes


def collect_hrrr_point_forecast(
    *,
    start: str,
    end: str,
    latitude: float,
    longitude: float,
    lead_times: list[int],
    lead_times_as_candidates: bool = False,
    bbox_deg: float,
    budget_gb: float,
    cache_dir: Path,
    backends: tuple[str, ...] = DEFAULT_BACKENDS,
    timeout_seconds: int = 120,
    stop_on_projected_budget: bool = True,
    max_required_field_failures: int | None = None,
) -> HrrrPointForecastResult:
    """Collect a budget-limited HRRR point forecast table."""

    ensure_dir(cache_dir)
    budget = DownloadBudget(budget_gb)
    valid_times = build_valid_times(start, end)
    expected_rows = int(len(valid_times) if lead_times_as_candidates else len(valid_times) * len(lead_times))
    zarr_context: dict[str, Any] | None = None
    frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    status = "completed"
    successful_downloaded_bytes = 0
    projected_downloaded_bytes: int | None = None
    required_field_failure_count = 0

    def attempt_sample(valid_time: pd.Timestamp, lead_time_hour: int) -> bool:
        """Try all configured backends for one timestamp/lead pair.

        Returns True after the first successful backend. Failed attempts remain
        in the audit so missing HRRR cycles are visible, but in candidate mode a
        later lead can still satisfy the same forecast-valid timestamp.
        """

        nonlocal zarr_context
        nonlocal successful_downloaded_bytes
        nonlocal projected_downloaded_bytes
        nonlocal required_field_failure_count
        nonlocal status

        for backend in backends:
            try:
                if backend == "nomads_bbox":
                    frame, audit, downloaded_bytes = _collect_nomads_sample(
                        valid_time=valid_time,
                        lead_time_hour=lead_time_hour,
                        latitude=latitude,
                        longitude=longitude,
                        bbox_deg=bbox_deg,
                        cache_dir=cache_dir,
                        timeout_seconds=timeout_seconds,
                    )
                elif backend == "zarr_chunk":
                    if zarr_context is None:
                        zarr_context = _load_hrrrzarr_context(latitude, longitude, cache_dir=cache_dir)
                    frame, audit, downloaded_bytes = _collect_zarr_sample(
                        valid_time=valid_time,
                        lead_time_hour=lead_time_hour,
                        zarr_context=zarr_context,
                        latitude=latitude,
                        longitude=longitude,
                        cache_dir=cache_dir,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    raise ValueError(f"unsupported HRRR point forecast backend: {backend}")

                warnings.extend(budget.add(downloaded_bytes))
                audit_rows.append(audit)
                frames.append(frame)
                successful_downloaded_bytes += int(downloaded_bytes)
                required_field_failure_count = 0
                projected_downloaded_bytes = _project_download_bytes(
                    successful_downloaded_bytes=successful_downloaded_bytes,
                    successful_rows=len(frames),
                    expected_rows=expected_rows,
                )
                return True
            except Exception as exc:
                failed_source_url = getattr(exc, "source_url", None)
                failed_downloaded_bytes = int(getattr(exc, "downloaded_bytes", 0))
                failed_error = str(exc)
                warnings.extend(budget.add(failed_downloaded_bytes))
                audit_rows.append(
                    {
                        "timestamp": str(valid_time),
                        "lead_time_hour": int(lead_time_hour),
                        "backend": backend,
                        "source_url": failed_source_url,
                        "downloaded_bytes": failed_downloaded_bytes,
                        "status": "failed",
                        "error": failed_error,
                    }
                )
                missing_required_fields = _missing_required_fields_from_error(failed_error)
                if missing_required_fields == {"DSWRF"}:
                    required_field_failure_count += 1
                    if (
                        max_required_field_failures is not None
                        and required_field_failure_count >= max_required_field_failures
                    ):
                        status = "failed_required_fields"
                        warnings.append(
                            "stopped after repeated required-field failures: "
                            f"{required_field_failure_count}/{max_required_field_failures}"
                        )
                        return False
                elif missing_required_fields:
                    required_field_failure_count = 0
                if budget.exceeded:
                    return False
        return False

    if lead_times_as_candidates:
        for valid_time in valid_times:
            sample_collected = False
            for lead_time_hour in lead_times:
                if attempt_sample(valid_time, lead_time_hour):
                    sample_collected = True
                    break
                if budget.exceeded or status == "failed_required_fields":
                    break
            if not sample_collected and status != "failed_required_fields":
                status = "completed_with_missing"
            if status == "failed_required_fields":
                break
            if budget.exceeded:
                status = "budget_exceeded"
                warnings.append(f"download budget exceeded: {budget.downloaded_bytes}/{budget.budget_bytes}")
                break
            if (
                stop_on_projected_budget
                and status in {"completed", "completed_with_missing"}
                and projected_downloaded_bytes is not None
                and projected_downloaded_bytes > budget.budget_bytes
            ):
                status = "estimated_budget_exceeded"
                warnings.append(
                    "projected download bytes exceed budget: "
                    f"{projected_downloaded_bytes}/{budget.budget_bytes}"
                )
                break
    else:
        for lead_time_hour in lead_times:
            for valid_time in valid_times:
                sample_collected = attempt_sample(valid_time, lead_time_hour)
                if not sample_collected and status != "failed_required_fields":
                    status = "completed_with_missing"
                if status == "failed_required_fields":
                    break
                if budget.exceeded:
                    status = "budget_exceeded"
                    warnings.append(f"download budget exceeded: {budget.downloaded_bytes}/{budget.budget_bytes}")
                    break
                if (
                    stop_on_projected_budget
                    and status in {"completed", "completed_with_missing"}
                    and projected_downloaded_bytes is not None
                    and projected_downloaded_bytes > budget.budget_bytes
                ):
                    status = "estimated_budget_exceeded"
                    warnings.append(
                        "projected download bytes exceed budget: "
                        f"{projected_downloaded_bytes}/{budget.budget_bytes}"
                    )
                    break
            if budget.exceeded or status in {"estimated_budget_exceeded", "failed_required_fields"}:
                break

    if frames:
        forecast_weather = (
            pd.concat(frames, ignore_index=True)
            .sort_values(["timestamp", "weather_forecast_lead_time_hour"])
            .reset_index(drop=True)
        )
    else:
        forecast_weather = pd.DataFrame()
        if status == "completed":
            status = "failed_no_rows"

    ok_timestamps = {
        pd.Timestamp(row["timestamp"]).tz_convert("UTC")
        for row in audit_rows
        if row["status"] == "ok"
    }
    missing_timestamps = [str(value) for value in valid_times if value not in ok_timestamps]
    audit = {
        "status": status,
        "start": start,
        "end": end,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "lead_times": [int(value) for value in lead_times],
        "lead_times_as_candidates": bool(lead_times_as_candidates),
        "bbox_deg": float(bbox_deg),
        "backends": list(backends),
        "budget_bytes": int(budget.budget_bytes),
        "downloaded_bytes": int(budget.downloaded_bytes),
        "warning_threshold_bytes": int(budget.budget_bytes * 0.8),
        "warnings": warnings,
        "expected_rows": expected_rows,
        "output_rows": int(len(forecast_weather)),
        "projected_downloaded_bytes": projected_downloaded_bytes,
        "missing_timestamps": missing_timestamps,
        "attempts": audit_rows,
    }
    return HrrrPointForecastResult(forecast_weather=forecast_weather, audit=audit)


def write_hrrr_point_forecast_outputs(
    result: HrrrPointForecastResult,
    *,
    output_parquet: Path,
    audit_json: Path,
) -> None:
    """Write forecast-weather parquet and JSON audit artifacts."""

    ensure_dir(output_parquet.parent)
    ensure_dir(audit_json.parent)
    if not result.forecast_weather.empty:
        result.forecast_weather.to_parquet(output_parquet, index=False)
    audit_json.write_text(
        json.dumps(result.audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_hrrr_point_forecast_manifest(manifest: dict[str, Any], manifest_json: Path) -> None:
    """Persist a batch manifest with stable JSON formatting."""

    ensure_dir(manifest_json.parent)
    manifest_json.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_hrrr_point_forecast_batch_outputs(
    result: HrrrPointForecastResult,
    *,
    output_parquet: Path,
    audit_json: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Write cloud-batch artifacts and return the final augmented audit.

    The batch runner separates two quantities that were conflated in the local
    probe: `remote_read_bytes` describes how much HRRR Zarr data the near-source
    job read, while `local_output_bytes` describes the parquet/audit payload
    that would be transferred back to this project.
    """

    ensure_dir(output_parquet.parent)
    ensure_dir(audit_json.parent)
    if not result.forecast_weather.empty:
        result.forecast_weather.to_parquet(output_parquet, index=False)

    output_parquet_bytes = int(output_parquet.stat().st_size) if output_parquet.exists() else 0
    local_budget_bytes = int(manifest["execution"]["local_output_budget_bytes"])
    final_audit = {
        **result.audit,
        "execution_mode": manifest["execution"]["mode"],
        "manifest": manifest,
        "remote_read_bytes": int(result.audit["downloaded_bytes"]),
        "remote_read_budget_bytes": int(manifest["execution"]["remote_read_budget_bytes"]),
        "local_output_budget_bytes": local_budget_bytes,
        "artifact_bytes": {
            "forecast_weather_parquet": output_parquet_bytes,
            "audit_json": None,
        },
    }
    final_audit["local_output_bytes"] = output_parquet_bytes
    final_audit["local_output_budget_exceeded"] = final_audit["local_output_bytes"] > local_budget_bytes
    audit_json.write_text(json.dumps(final_audit, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_json_bytes = int(audit_json.stat().st_size)
    final_audit["artifact_bytes"]["audit_json"] = audit_json_bytes
    final_audit["local_output_bytes"] = output_parquet_bytes + audit_json_bytes
    final_audit["local_output_budget_exceeded"] = final_audit["local_output_bytes"] > local_budget_bytes
    audit_json.write_text(json.dumps(final_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return final_audit


def merge_hrrr_point_forecast_batches(
    *,
    input_dir: Path,
    audit_dir: Path,
    output_parquet: Path,
    audit_json: Path,
    expected_start: str,
    expected_end: str,
    lead_time_hour: int,
    min_lead_time_hour: int | None = None,
    max_lead_time_hour: int | None = None,
) -> dict[str, Any]:
    """Merge monthly HRRR point-forecast parquet files with strict validation.

    The merge step is the final gate before Stage7. It intentionally fails on
    duplicated valid timestamps, wrong lead times, or leakage where the forecast
    issue time is later than the Stage7 prediction time (`valid_time - horizon`).
    Missing hours are allowed, but they are written to the audit instead of
    being filled silently.
    """

    parquet_paths = sorted(input_dir.glob("*.parquet"))
    audit_paths = sorted(audit_dir.glob("*.json"))
    expected_times = build_valid_times(expected_start, expected_end)
    min_lead = int(lead_time_hour if min_lead_time_hour is None else min_lead_time_hour)
    max_lead = int(lead_time_hour if max_lead_time_hour is None else max_lead_time_hour)
    if min_lead > max_lead:
        raise ValueError("min_lead_time_hour must be <= max_lead_time_hour.")
    ensure_dir(output_parquet.parent)
    ensure_dir(audit_json.parent)

    audit: dict[str, Any] = {
        "status": "completed",
        "input_dir": str(input_dir),
        "audit_dir": str(audit_dir),
        "output_parquet": str(output_parquet),
        "expected_start": expected_start,
        "expected_end": expected_end,
        "horizon_hour": int(lead_time_hour),
        "min_lead_time_hour": min_lead,
        "max_lead_time_hour": max_lead,
        "expected_rows": int(len(expected_times)),
        "missing_timestamps": [str(value) for value in expected_times],
        "input_parquet_files": [str(path) for path in parquet_paths],
        "input_audit_files": [str(path) for path in audit_paths],
        "errors": [],
    }

    if not parquet_paths:
        audit["status"] = "failed_validation"
        audit["errors"].append(f"no parquet files found in {input_dir}")
        audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return audit

    frames = [pd.read_parquet(path) for path in parquet_paths]
    merged = pd.concat(frames, ignore_index=True)
    required_columns = {
        "timestamp",
        "weather_forecast_issue_time",
        "weather_forecast_lead_time_hour",
        *STAGE7_CORE_WEATHER_COLUMNS,
    }
    missing_columns = sorted(required_columns.difference(merged.columns))
    if missing_columns:
        audit["status"] = "failed_validation"
        audit["errors"].append(f"merged parquet missing required columns: {', '.join(missing_columns)}")
        audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return audit

    merged["timestamp"] = pd.to_datetime(merged["timestamp"], errors="coerce", utc=True)
    merged["weather_forecast_issue_time"] = pd.to_datetime(
        merged["weather_forecast_issue_time"],
        errors="coerce",
        utc=True,
    )
    merged["weather_forecast_lead_time_hour"] = pd.to_numeric(
        merged["weather_forecast_lead_time_hour"],
        errors="coerce",
    )
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    duplicate_timestamps = (
        merged.loc[merged["timestamp"].duplicated(keep=False), "timestamp"]
        .dropna()
        .dt.strftime("%Y-%m-%d %H:%M:%S%z")
        .drop_duplicates()
        .tolist()
    )
    invalid_lead_rows = merged[
        (merged["weather_forecast_lead_time_hour"] < float(min_lead))
        | (merged["weather_forecast_lead_time_hour"] > float(max_lead))
    ]
    prediction_time = merged["timestamp"] - pd.to_timedelta(int(lead_time_hour), unit="h")
    leakage_rows = merged[merged["weather_forecast_issue_time"] > prediction_time]
    null_key_rows = merged[
        merged[["timestamp", "weather_forecast_issue_time", "weather_forecast_lead_time_hour"]]
        .isna()
        .any(axis=1)
    ]

    observed_times = pd.DatetimeIndex(merged["timestamp"].dropna().drop_duplicates()).tz_convert("UTC")
    missing_timestamps = [str(value) for value in expected_times if value not in observed_times]
    unexpected_timestamps = [
        str(value)
        for value in observed_times
        if value < expected_times[0] or value > expected_times[-1]
    ]

    monthly_audits: list[dict[str, Any]] = []
    for path in audit_paths:
        try:
            monthly_audits.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            audit["errors"].append(f"invalid JSON audit {path}: {exc}")
    remote_read_bytes = sum(int(item.get("remote_read_bytes", item.get("downloaded_bytes", 0))) for item in monthly_audits)
    local_output_bytes = sum(int(item.get("local_output_bytes", 0)) for item in monthly_audits)
    ok_attempts = [
        attempt
        for monthly_audit in monthly_audits
        for attempt in monthly_audit.get("attempts", [])
        if attempt.get("status") == "ok"
    ]
    missing_precipitation_transform = [
        attempt.get("timestamp")
        for attempt in ok_attempts
        if attempt.get("precipitation_transform") != "accumulated_to_hourly_diff"
    ]
    negative_precipitation_clipped = [
        attempt.get("timestamp")
        for attempt in ok_attempts
        if bool(attempt.get("precipitation_negative_diff_clipped"))
    ]

    if duplicate_timestamps:
        audit["errors"].append(f"duplicate timestamps found: {duplicate_timestamps[:10]}")
    if len(invalid_lead_rows):
        audit["errors"].append(f"rows outside lead_time range {min_lead}..{max_lead}: {len(invalid_lead_rows)}")
    if len(leakage_rows):
        audit["errors"].append(f"rows failing issue_time <= prediction_time: {len(leakage_rows)}")
    if len(null_key_rows):
        audit["errors"].append(f"rows with null key audit fields: {len(null_key_rows)}")
    if unexpected_timestamps:
        audit["errors"].append(f"timestamps outside expected range: {unexpected_timestamps[:10]}")
    if "ghi_wm2" in merged.columns and pd.to_numeric(merged["ghi_wm2"], errors="coerce").max() <= 0.0:
        audit["errors"].append("ghi_wm2 is all zero; DSWRF extraction is not valid for Stage7")

    audit.update(
        {
            "input_rows": int(len(merged)),
            "output_rows": int(merged["timestamp"].nunique()),
            "expected_rows": int(len(expected_times)),
            "missing_timestamps": missing_timestamps,
            "unexpected_timestamps": unexpected_timestamps,
            "duplicate_timestamps": duplicate_timestamps,
            "remote_read_bytes": int(remote_read_bytes),
            "local_output_bytes_from_monthly_audits": int(local_output_bytes),
            "monthly_audit_count": int(len(monthly_audits)),
            "precipitation_semantics": {
                "transform": "accumulated_to_hourly_diff",
                "ok_attempts": int(len(ok_attempts)),
                "missing_transform_count": int(len(missing_precipitation_transform)),
                "negative_clipped_count": int(len(negative_precipitation_clipped)),
                "missing_transform_examples": missing_precipitation_transform[:10],
                "negative_clipped_examples": negative_precipitation_clipped[:10],
            },
            "min_timestamp": str(merged["timestamp"].min()) if len(merged) else None,
            "max_timestamp": str(merged["timestamp"].max()) if len(merged) else None,
        }
    )
    if missing_timestamps and audit["status"] == "completed":
        audit["status"] = "completed_with_missing"
    if audit["errors"]:
        audit["status"] = "failed_validation"
        audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return audit

    merged.to_parquet(output_parquet, index=False)
    output_bytes = int(output_parquet.stat().st_size)
    audit["artifact_bytes"] = {
        "forecast_weather_parquet": output_bytes,
        "audit_json": None,
    }
    audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    audit["artifact_bytes"]["audit_json"] = int(audit_json.stat().st_size)
    audit["local_output_bytes"] = output_bytes + audit["artifact_bytes"]["audit_json"]
    audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit
