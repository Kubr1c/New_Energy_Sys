from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import math
import re
import warnings

import cfgrib
import pandas as pd
import requests


@dataclass(frozen=True)
class HrrrPointSample:
    """One HRRR point-forecast sample extracted from a single GRIB2 file.

    The stage goal is not full production ingestion yet. This container keeps
    the extraction output explicit and auditable so later multi-file ingestion
    can reuse the same weather schema without rewriting the downstream stages.
    """

    frame: pd.DataFrame
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HrrrIndexRecord:
    """One byte-range addressable record from an HRRR `.idx` sidecar file."""

    line_number: int
    start_byte: int
    short_name: str
    level: str
    descriptor: str


def _open_hrrr_datasets(grib_path: Path) -> list[Any]:
    """Open all logical xarray datasets inside one HRRR GRIB2 file.

    HRRR surface products contain many variable groups with different vertical
    coordinates and cfgrib exposes them as multiple xarray datasets. The code
    disables on-disk index reuse because stale `.idx` files frequently produce
    noisy compatibility warnings after file replacement or partial retries.
    """

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=FutureWarning)
        return cfgrib.open_datasets(grib_path, backend_kwargs={"indexpath": ""})


def _parse_idx_records(idx_text: str) -> list[HrrrIndexRecord]:
    """Parse an HRRR `.idx` file into byte-addressable record metadata.

    Example line:
    `71:49695389:d=2022010100:TMP:2 m above ground:24 hour fcst:`
    """

    records: list[HrrrIndexRecord] = []
    for raw_line in idx_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 6:
            continue
        records.append(
            HrrrIndexRecord(
                line_number=int(parts[0]),
                start_byte=int(parts[1]),
                short_name=parts[3],
                level=parts[4],
                descriptor=":".join(parts[5:]).rstrip(":"),
            )
        )
    return records


def _selected_hrrr_records(records: list[HrrrIndexRecord]) -> list[HrrrIndexRecord]:
    """Select the minimum variable set needed by the strict-weather pilot."""

    required_patterns = [
        ("TMP", "2 m above ground"),
        ("RH", "2 m above ground"),
        ("UGRD", "10 m above ground"),
        ("VGRD", "10 m above ground"),
        ("APCP", "surface"),
        ("TCDC", "entire atmosphere"),
        ("DSWRF", "surface"),
        ("PRES", "surface"),
    ]

    selected: list[HrrrIndexRecord] = []
    missing: list[str] = []
    for short_name, level in required_patterns:
        match = next(
            (
                record
                for record in records
                if record.short_name == short_name and record.level == level
            ),
            None,
        )
        if match is None:
            missing.append(f"{short_name}@{level}")
        else:
            selected.append(match)

    if missing:
        raise ValueError(f"HRRR idx missing required records: {', '.join(missing)}")
    return sorted(selected, key=lambda record: record.start_byte)


def _download_hrrr_subset(
    *,
    grib_url: str,
    idx_url: str,
    subset_target: Path,
    timeout_seconds: int = 120,
) -> Path:
    """Download only the GRIB messages needed for strict weather extraction.

    Monthly HRRR extraction is only tractable if each large source GRIB2 file is
    sliced down to the byte ranges needed for the project variables. This keeps
    disk usage and decode time bounded without introducing a separate GRIB CLI.
    """

    if subset_target.exists():
        return subset_target

    idx_response = requests.get(idx_url, timeout=timeout_seconds)
    idx_response.raise_for_status()
    records = _parse_idx_records(idx_response.text)
    selected = _selected_hrrr_records(records)

    head_response = requests.head(grib_url, timeout=timeout_seconds)
    head_response.raise_for_status()
    content_length = int(head_response.headers["Content-Length"])

    subset_target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = subset_target.with_suffix(subset_target.suffix + ".tmp")
    with temp_target.open("wb") as handle:
        for index, record in enumerate(selected):
            if index + 1 < len(selected):
                end_byte = selected[index + 1].start_byte - 1
            else:
                end_byte = content_length - 1
            response = requests.get(
                grib_url,
                headers={"Range": f"bytes={record.start_byte}-{end_byte}"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            handle.write(response.content)

    temp_target.replace(subset_target)
    return subset_target


def _first_data_array(datasets: list[Any], variable_name: str) -> Any | None:
    """Return the first xarray DataArray matching the requested variable name."""

    for dataset in datasets:
        if variable_name in dataset.data_vars:
            return dataset[variable_name]
    return None


def _select_point(data_array: Any, latitude: float, longitude: float) -> tuple[float, float, float]:
    """Select the nearest HRRR grid point and return value + grid coordinates.

    HRRR latitude/longitude are 2D coordinates over the native Lambert grid.
    Using xarray's standard `.sel(..., method="nearest")` is not reliable on
    2D coordinates, so this function computes the nearest grid cell manually.
    The distance metric is intentionally simple because the target use case is
    single-station nearest-neighbor extraction, not exact geodesic remapping.
    """

    lat_grid = data_array.latitude.values
    lon_grid = data_array.longitude.values
    if lon_grid.max() > 180.0 and longitude < 0.0:
        normalized_longitude = longitude + 360.0
    else:
        normalized_longitude = longitude

    distance = (lat_grid - latitude) ** 2 + (lon_grid - normalized_longitude) ** 2
    flat_index = int(distance.argmin())
    row_index, col_index = divmod(flat_index, distance.shape[1])

    point = data_array.isel(y=row_index, x=col_index)
    value = float(point.values.squeeze())
    grid_lat = float(lat_grid[row_index, col_index])
    grid_lon = float(lon_grid[row_index, col_index])
    if grid_lon > 180.0:
        grid_lon -= 360.0
    return value, grid_lat, grid_lon


def _scalar_coord(data_array: Any, coord_name: str) -> str | None:
    """Return a scalar coordinate as an ISO-like string when available."""

    if coord_name not in data_array.coords:
        return None
    value = pd.to_datetime(data_array.coords[coord_name].values).tz_localize(None)
    return value.isoformat()


def _convert_temperature_k_to_c(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else value - 273.15


def _convert_pressure_pa_to_hpa(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else value / 100.0


def _convert_precipitation_to_mm(value: float | None, units: str | None) -> float | None:
    """Convert HRRR precipitation to millimetres using the declared GRIB units.

    HRRR commonly exposes accumulated precipitation as `kg m**-2`, which is
    numerically equivalent to millimetres of water depth. Some products use
    metres instead. Unit-aware conversion is mandatory here because a naive
    `*1000` would silently inflate rainfall totals by three orders of magnitude
    when the GRIB payload is already in `kg m**-2`.
    """

    if value is None or math.isnan(value):
        return None
    normalized_units = (units or "").strip().lower()
    if normalized_units in {"kg m**-2", "kg m^-2", "mm"}:
        return value
    if normalized_units in {"m", "metre", "meter", "metres", "meters"}:
        return value * 1000.0
    return value


def _convert_cloud_cover_to_pct(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return value * 100.0 if value <= 1.5 else value


def extract_hrrr_point_sample(
    *,
    grib_path: Path,
    latitude: float,
    longitude: float,
) -> HrrrPointSample:
    """Extract a strict point-weather sample from one HRRR GRIB2 forecast file.

    Variables are selected conservatively:
    - `t2m`, `r2`, `u10`, `v10`, `tp`, `tcc`, `sdswrf`, `sp`
    - wind speed is derived from `u10` and `v10`
    - issue/valid time are preserved from the GRIB coordinates

    This is enough to prove the strict-weather chain can reach the same core
    weather features already used by the project, while keeping the first HRRR
    implementation small and testable.
    """

    datasets = _open_hrrr_datasets(grib_path)

    selected = {
        "t2m": _first_data_array(datasets, "t2m"),
        "r2": _first_data_array(datasets, "r2"),
        "u10": _first_data_array(datasets, "u10"),
        "v10": _first_data_array(datasets, "v10"),
        "tp": _first_data_array(datasets, "tp"),
        "tcc": _first_data_array(datasets, "tcc"),
        "sdswrf": _first_data_array(datasets, "sdswrf"),
        "sp": _first_data_array(datasets, "sp"),
    }
    missing = [name for name, data_array in selected.items() if data_array is None]
    if missing:
        raise ValueError(f"HRRR sample is missing required variables: {', '.join(missing)}")

    extracted: dict[str, float | None] = {}
    grid_lat: float | None = None
    grid_lon: float | None = None
    for name, data_array in selected.items():
        value, current_lat, current_lon = _select_point(data_array, latitude, longitude)
        extracted[name] = value
        if grid_lat is None:
            grid_lat = current_lat
            grid_lon = current_lon

    issue_time = _scalar_coord(selected["t2m"], "time")
    valid_time = _scalar_coord(selected["t2m"], "valid_time")
    step_value = selected["t2m"].coords.get("step")
    if step_value is None:
        lead_time_hour = None
    else:
        lead_time_hour = float(pd.to_timedelta(step_value.values).total_seconds() / 3600.0)

    u10 = extracted["u10"]
    v10 = extracted["v10"]
    wind_speed_ms = None
    if u10 is not None and v10 is not None and not math.isnan(u10) and not math.isnan(v10):
        wind_speed_ms = math.sqrt(u10**2 + v10**2)

    frame = pd.DataFrame(
        [
            {
                "timestamp": valid_time,
                "weather_forecast_issue_time": issue_time,
                "weather_forecast_lead_time_hour": lead_time_hour,
                "grid_latitude": grid_lat,
                "grid_longitude": grid_lon,
                "temperature_c": _convert_temperature_k_to_c(extracted["t2m"]),
                "relative_humidity_pct": extracted["r2"],
                "wind_u10_ms": u10,
                "wind_v10_ms": v10,
                "wind_speed_ms": wind_speed_ms,
                "precipitation_mm": _convert_precipitation_to_mm(
                    extracted["tp"],
                    selected["tp"].attrs.get("units"),
                ),
                "cloud_cover_pct": _convert_cloud_cover_to_pct(extracted["tcc"]),
                "ghi_wm2": extracted["sdswrf"],
                "surface_pressure_hpa": _convert_pressure_pa_to_hpa(extracted["sp"]),
            }
        ]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["weather_forecast_issue_time"] = pd.to_datetime(frame["weather_forecast_issue_time"], utc=True)

    metadata = {
        "grib_path": str(grib_path),
        "requested_latitude": latitude,
        "requested_longitude": longitude,
        "grid_latitude": grid_lat,
        "grid_longitude": grid_lon,
        "dataset_count": len(datasets),
        "available_variables": sorted({str(name) for dataset in datasets for name in dataset.data_vars}),
    }
    return HrrrPointSample(frame=frame, metadata=metadata)


def build_hrrr_cycle_urls(*, valid_time: pd.Timestamp, lead_time_hour: int) -> tuple[str, str, str]:
    """Build the GRIB2, idx, and cache stem for one strict HRRR forecast sample."""

    if valid_time.tzinfo is None:
        valid_time = valid_time.tz_localize("UTC")
    else:
        valid_time = valid_time.tz_convert("UTC")

    issue_time = valid_time - pd.Timedelta(hours=lead_time_hour)
    issue_time = issue_time.floor("h")
    cycle_hour = issue_time.strftime("%H")
    date_folder = issue_time.strftime("%Y%m%d")
    lead_text = f"{lead_time_hour:02d}"
    relative_path = f"hrrr.{date_folder}/conus/hrrr.t{cycle_hour}z.wrfsfcf{lead_text}.grib2"
    base_url = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
    return (
        f"{base_url}/{relative_path}",
        f"{base_url}/{relative_path}.idx",
        f"hrrr_t{cycle_hour}z_f{lead_text}_{valid_time.strftime('%Y%m%d%H')}",
    )


def build_hrrr_monthly_point_table(
    *,
    start: str,
    end: str,
    latitude: float,
    longitude: float,
    lead_time_hour: int,
    cache_dir: Path,
    timeout_seconds: int = 120,
) -> pd.DataFrame:
    """Build a strict HRRR point-weather table for a fixed lead time.

    The pilot intentionally keeps lead time fixed across the whole month. That
    yields one auditable weather table and is enough to validate alignment with
    Stage1-3 before adding mixed `f01/f06/f24` logic.
    """

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(hours=1)
    valid_times = pd.date_range(start=start_ts, end=end_ts, freq="1h", tz="UTC")

    frames: list[pd.DataFrame] = []
    for index, valid_time in enumerate(valid_times, start=1):
        grib_url, idx_url, cache_stem = build_hrrr_cycle_urls(
            valid_time=valid_time,
            lead_time_hour=lead_time_hour,
        )
        subset_target = cache_dir / f"{cache_stem}.grib2"
        subset_path = _download_hrrr_subset(
            grib_url=grib_url,
            idx_url=idx_url,
            subset_target=subset_target,
            timeout_seconds=timeout_seconds,
        )
        sample = extract_hrrr_point_sample(
            grib_path=subset_path,
            latitude=latitude,
            longitude=longitude,
        )
        frames.append(sample.frame)
        if index % 24 == 0:
            print(f"HRRR extracted {index}/{len(valid_times)} hourly samples for fixed f{lead_time_hour:02d}")

    table = pd.concat(frames, ignore_index=True)
    table = table.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)
    return table
