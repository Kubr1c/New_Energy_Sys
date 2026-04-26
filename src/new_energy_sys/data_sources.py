from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .io_utils import download_file, ensure_dir


@dataclass(frozen=True)
class DownloadResult:
    """Metadata for a downloaded or generated raw source file."""

    name: str
    path: Path
    source_url: str | None


def _safe_filename(url: str, fallback: str) -> str:
    """Build a stable local filename from a remote URL."""

    filename = Path(urlparse(url).path).name
    return filename if filename else fallback


def download_csv_url(name: str, source: dict, raw_dir: Path) -> DownloadResult:
    """Download a generic public CSV source declared in the config.

    This provider deliberately stays generic because PVDAQ/OEDI/DuraMAT expose
    many CSV resources with different path conventions. The normalization layer
    handles schema differences after the file is cached.
    """

    url = source["url"]
    filename = _safe_filename(url, f"{name}.csv")
    target = raw_dir / name / filename

    if not target.exists():
        download_file(url=url, target=target)

    return DownloadResult(name=name, path=target, source_url=url)


def use_local_file(name: str, source: dict, root_dir: Path) -> DownloadResult:
    """Register a local raw file as a data source.

    This is useful when a public provider closes a chunked response early but a
    valid partial CSV is already available for profile extraction. The path is
    still declared in config so the pipeline remains reproducible.
    """

    path = (root_dir / source["path"]).resolve()
    if not path.exists():
        raise FileNotFoundError(f"本地数据源不存在: {path}")
    return DownloadResult(name=name, path=path, source_url=None)


def download_nrel_solar_zip(source: dict, raw_dir: Path) -> DownloadResult:
    """Download a NREL Solar Power Data for Integration Studies state ZIP."""

    url = source["url"]
    filename = _safe_filename(url, "nrel_solar.zip")
    target = raw_dir / "nrel_solar" / filename
    if not target.exists():
        download_file(url=url, target=target)
    return DownloadResult(name="nrel_solar", path=target, source_url=url)


def download_pvdaq_s3_year(source: dict, raw_dir: Path) -> DownloadResult:
    """Download and merge one PVDAQ system-year from OEDI's public S3 layout.

    PVDAQ stores measurements as one CSV per day:
    ``pvdaq/csv/pvdata/system_id=<id>/year=<year>/month=<m>/day=<d>/...``.
    Merging locally gives the rest of the pipeline a stable single-file input
    without depending on a database client or AWS credentials.
    """

    bucket_url = source.get("bucket_url", "https://oedi-data-lake.s3.amazonaws.com")
    system_id = int(source["system_id"])
    year = int(source["year"])
    ensure_dir(raw_dir / "pvdaq")

    target = raw_dir / "pvdaq" / source.get("target_name", f"pvdaq_system_{system_id}_{year}.csv")
    if target.exists():
        return DownloadResult(name="pv_power", path=target, source_url=None)

    list_url = (
        f"{bucket_url}/?list-type=2&prefix=pvdaq/csv/pvdata/system_id={system_id}/year={year}/"
        "&max-keys=1000"
    )
    response = requests.get(list_url, timeout=int(source.get("timeout_seconds", 60)))
    response.raise_for_status()

    namespace = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    root = ET.fromstring(response.text)
    keys = [
        node.text
        for node in root.findall("s:Contents/s:Key", namespace)
        if node.text and node.text.endswith(".csv")
    ]
    if not keys:
        raise ValueError(f"PVDAQ system {system_id} has no CSV files for year {year}")

    def read_day(key: str) -> pd.DataFrame:
        """Read one PVDAQ day file.

        Network latency dominates this workload. Reading day files concurrently
        keeps the total wall-clock time bounded while preserving deterministic
        ordering during the final timestamp sort.
        """

        file_url = f"{bucket_url}/{key}"
        return pd.read_csv(file_url, low_memory=False)

    frames: list[pd.DataFrame] = []
    max_workers = int(source.get("download_workers", 12))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {executor.submit(read_day, key): key for key in sorted(keys)}
        for index, future in enumerate(as_completed(future_to_key), start=1):
            key = future_to_key[future]
            try:
                frames.append(future.result())
            except Exception as exc:
                raise RuntimeError(f"Failed to read PVDAQ day file {key}") from exc
            if index % 50 == 0:
                print(f"PVDAQ downloaded {index}/{len(keys)} day files for system {system_id} {year}")

    merged = pd.concat(frames, ignore_index=True)
    merged.to_csv(target, index=False)
    return DownloadResult(name="pv_power", path=target, source_url=list_url)


def download_pvdaq_s3_years(source: dict, raw_dir: Path) -> DownloadResult:
    """Download and merge multiple PVDAQ system-years into one raw CSV.

    Stage-5 sequence models need more than a single year if the project should
    evaluate seasonal generalization instead of just proving the pipeline runs.
    PVDAQ still exposes files by single system-year, so this wrapper delegates
    to the production-tested yearly downloader, then creates one deterministic
    multi-year cache file for the standardization layer.

    The function intentionally keeps the source schema untouched. Timestamp and
    power-column selection remain config-driven in ``normalize_pv_power`` so a
    future station with different PVDAQ channel names can reuse this adapter
    without a code change.
    """

    system_id = int(source["system_id"])
    years = source.get("years")
    if not years:
        raise ValueError("PVDAQ multi-year source requires a non-empty `years` list.")

    parsed_years = sorted({int(year) for year in years})
    ensure_dir(raw_dir / "pvdaq")
    target = raw_dir / "pvdaq" / source.get(
        "target_name",
        f"pvdaq_system_{system_id}_{parsed_years[0]}_{parsed_years[-1]}.csv",
    )
    if target.exists():
        return DownloadResult(name="pv_power", path=target, source_url=None)

    frames: list[pd.DataFrame] = []
    source_urls: list[str] = []
    for year in parsed_years:
        yearly_source = dict(source)
        yearly_source["year"] = year
        yearly_source["target_name"] = f"pvdaq_system_{system_id}_{year}.csv"
        yearly_result = download_pvdaq_s3_year(yearly_source, raw_dir)
        frames.append(pd.read_csv(yearly_result.path, low_memory=False))
        if yearly_result.source_url:
            source_urls.append(yearly_result.source_url)

    merged = pd.concat(frames, ignore_index=True)
    merged.to_csv(target, index=False)
    return DownloadResult(
        name="pv_power",
        path=target,
        source_url=source_urls[0] if source_urls else None,
    )


def fetch_open_meteo_archive(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    raw_dir: Path,
    target_name: str | None = None,
) -> DownloadResult:
    """Fetch hourly weather data from Open-Meteo Archive API.

    Open-Meteo is used as the default first-stage weather source because it
    requires no API key and returns analysis-ready hourly meteorological fields.
    """

    ensure_dir(raw_dir / "weather")
    target = raw_dir / "weather" / (target_name or f"open_meteo_{start}_{end}.csv")
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    hourly_fields = [
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "surface_pressure",
        "pressure_msl",
        "precipitation",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
        "shortwave_radiation",
        "direct_radiation",
        "direct_normal_irradiance",
        "diffuse_radiation",
        "terrestrial_radiation",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
    ]
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(hourly_fields),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }
    url = "https://archive-api.open-meteo.com/v1/archive"
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()

    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("Open-Meteo 返回结构异常：缺少 hourly.time")

    frame = pd.DataFrame(hourly).rename(columns={"time": "timestamp"})
    frame.to_csv(target, index=False)
    return DownloadResult(name="weather", path=target, source_url=response.url)


def fetch_open_meteo_historical_forecast(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """Fetch forecast-time weather fields from Open-Meteo Historical Forecast.

    This provider is separated from the archive/reanalysis adapter. The archive
    endpoint is useful for weather enrichment, but historical forecast data is
    the stricter input for production-like PV forecasting because it better
    represents information available at prediction time.
    """

    ensure_dir(raw_dir / "weather_forecast")

    provider = "open_meteo_historical_forecast"
    assumed_lead_time_hour = int(source.get("assumed_lead_time_hour", 24))
    target = raw_dir / "weather_forecast" / (
        source.get("target_name")
        or f"{provider}_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv"
    )
    if target.exists():
        return DownloadResult(name="weather_forecast", path=target, source_url=None)

    hourly_fields = source.get(
        "hourly_fields",
        [
            "temperature_2m",
            "relative_humidity_2m",
            "dew_point_2m",
            "surface_pressure",
            "pressure_msl",
            "precipitation",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "shortwave_radiation",
            "direct_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
        ],
    )
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(hourly_fields),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }
    url = source.get("url", "https://historical-forecast-api.open-meteo.com/v1/forecast")
    response = requests.get(url, params=params, timeout=int(source.get("timeout_seconds", 120)))
    response.raise_for_status()
    payload = response.json()

    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("Open-Meteo Historical Forecast response missing hourly.time")

    frame = pd.DataFrame(hourly).rename(columns={"time": "timestamp"})
    valid_time = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)

    # The endpoint returns valid-time hourly forecasts. It does not expose a
    # full issue-cycle x lead-time matrix in the simple CSV workflow, so the
    # configured lead-time assumption is stored explicitly for auditability.
    frame["weather_provider"] = provider
    frame["weather_forecast_lead_time_hour"] = assumed_lead_time_hour
    frame["weather_forecast_issue_time"] = (
        valid_time - pd.to_timedelta(assumed_lead_time_hour, unit="h")
    ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    frame["weather_forecast_issue_time_is_assumed"] = True

    frame.to_csv(target, index=False)
    return DownloadResult(name="weather_forecast", path=target, source_url=response.url)


def fetch_open_meteo_historical_forecast_range(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """Fetch a long Open-Meteo Historical Forecast range in calendar chunks.

    Public weather APIs are more reliable with bounded requests. This wrapper
    fetches month-sized chunks, keeps every raw chunk cached, and writes one
    deduplicated merged CSV for downstream normalization.
    """

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise ValueError(f"Invalid weather forecast range: {start} > {end}")

    ensure_dir(raw_dir / "weather_forecast")
    merged_target = raw_dir / "weather_forecast" / (
        source.get("merged_target_name")
        or f"open_meteo_historical_forecast_merged_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv"
    )
    if merged_target.exists():
        return DownloadResult(name="weather_forecast", path=merged_target, source_url=None)

    chunk_frames: list[pd.DataFrame] = []
    source_urls: list[str] = []
    cursor = start_ts
    while cursor <= end_ts:
        chunk_end = min(cursor + pd.offsets.MonthEnd(0), end_ts)
        chunk_source = dict(source)
        chunk_source["target_name"] = (
            f"open_meteo_historical_forecast_{cursor.date()}_{chunk_end.date()}_"
            f"{latitude:.4f}_{longitude:.4f}.csv"
        )
        chunk = fetch_open_meteo_historical_forecast(
            latitude=latitude,
            longitude=longitude,
            start=str(cursor.date()),
            end=str(chunk_end.date()),
            source=chunk_source,
            raw_dir=raw_dir,
        )
        chunk_frames.append(pd.read_csv(chunk.path, low_memory=False))
        if chunk.source_url:
            source_urls.append(chunk.source_url)
        cursor = chunk_end + pd.Timedelta(days=1)

    merged = pd.concat(chunk_frames, ignore_index=True)
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], errors="coerce", utc=True)
    merged = (
        merged.dropna(subset=["timestamp"])
        .drop_duplicates(subset=["timestamp"], keep="first")
        .sort_values("timestamp")
    )
    merged["timestamp"] = merged["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged.to_csv(merged_target, index=False)
    return DownloadResult(
        name="weather_forecast",
        path=merged_target,
        source_url=source_urls[0] if source_urls else None,
    )


def fetch_nsrdb_weather(
    *,
    latitude: float,
    longitude: float,
    year: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """Fetch NSRDB PSM solar resource data for one point-year.

    NSRDB direct CSV streaming is restricted to a single point and single year,
    which matches the current NREL 2006 single-site experiment. The API returns
    a SAM-style CSV with two metadata rows followed by hourly records.
    """

    ensure_dir(raw_dir / "weather")
    target = raw_dir / "weather" / source.get(
        "target_name",
        f"nsrdb_{year}_{latitude:.4f}_{longitude:.4f}.csv",
    )
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    api_key = source.get("api_key")
    api_key_env = source.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(str(api_key_env), api_key)
    if not api_key:
        raise ValueError(
            "NSRDB requires an API key. Set the configured environment variable "
            f"`{api_key_env or 'NREL_API_KEY'}` or provide `api_key` in the config."
        )

    email = source.get("email")
    email_env = source.get("email_env")
    if email_env:
        email = os.environ.get(str(email_env), email)
    if not email:
        raise ValueError(
            "NSRDB requires a contact email. Set the configured environment variable "
            f"`{email_env or 'NREL_API_EMAIL'}` or provide `email` in the config."
        )

    attributes = source.get(
        "attributes",
        [
            "dhi",
            "dni",
            "ghi",
            "clearsky_dhi",
            "clearsky_dni",
            "clearsky_ghi",
            "cloud_type",
            "dew_point",
            "air_temperature",
            "surface_pressure",
            "relative_humidity",
            "solar_zenith_angle",
            "surface_albedo",
            "total_precipitable_water",
            "wind_direction",
            "wind_speed",
            "fill_flag",
        ],
    )
    params = {
        "api_key": api_key,
        "full_name": source.get("full_name", "New Energy Sys"),
        "email": email,
        "affiliation": source.get("affiliation", "Academic"),
        "reason": source.get("reason", "research"),
        "mailing_list": str(source.get("mailing_list", "false")).lower(),
        "wkt": f"POINT({longitude} {latitude})",
        "names": year,
        "attributes": ",".join(attributes),
        "leap_day": str(source.get("leap_day", "false")).lower(),
        "utc": "true",
        "interval": int(source.get("interval", 60)),
    }
    url = source.get(
        "url",
        "https://developer.nlr.gov/api/nsrdb/v2/solar/nsrdb-GOES-aggregated-v4-0-0-download.csv",
    )
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()

    text = response.text
    header = text[:500].lower()
    if "errors" in header or "api key" in header or text.lstrip().startswith("{"):
        raise ValueError(f"NSRDB returned an error response: {text[:500]}")

    target.write_text(text, encoding="utf-8")
    return DownloadResult(name="weather", path=target, source_url=response.url)


def fetch_nsrdb_weather_years(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """Fetch and merge NSRDB point-year CSV files for a PVDAQ time span.

    NSRDB direct CSV downloads are point-year requests. PVDAQ system 10 spans
    full 2022 plus partial 2023, so this wrapper downloads every required year
    independently and creates one SAM-style CSV with a single metadata/header
    pair. The downstream normalizer can then keep one stable parsing path.
    """

    start_year = int(pd.Timestamp(start).year)
    end_year = int(pd.Timestamp(end).year)
    years = list(range(start_year, end_year + 1))

    ensure_dir(raw_dir / "weather")
    target = raw_dir / "weather" / source.get(
        "merged_target_name",
        f"nsrdb_merged_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv",
    )
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    source_urls: list[str] = []
    merged_lines: list[str] = []
    for index, year in enumerate(years):
        year_source = dict(source)
        year_source["target_name"] = f"nsrdb_{year}_{latitude:.4f}_{longitude:.4f}.csv"
        result = fetch_nsrdb_weather(
            latitude=latitude,
            longitude=longitude,
            year=str(year),
            source=year_source,
            raw_dir=raw_dir,
        )
        lines = result.path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) < 3:
            raise ValueError(f"NSRDB CSV for {year} is too short: {result.path}")

        # Keep exactly one metadata row and one column-header row. Repeated
        # headers inside the body would later be coerced to NaN and look like
        # unexplained weather gaps.
        if index == 0:
            merged_lines.extend(lines)
        else:
            merged_lines.extend(lines[2:])
        if result.source_url:
            source_urls.append(result.source_url)

    target.write_text("\n".join(merged_lines) + "\n", encoding="utf-8")
    return DownloadResult(name="weather", path=target, source_url=source_urls[0] if source_urls else None)


def fetch_weather_with_fallback(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """Fetch weather with NSRDB first and Open-Meteo as the deterministic fallback."""

    year = str(pd.Timestamp(start).year)
    fallback_name = f"open_meteo_era5_fallback_{start}_{end}.csv"
    try:
        return fetch_nsrdb_weather(
            latitude=latitude,
            longitude=longitude,
            year=year,
            source=source.get("nsrdb", {}),
            raw_dir=raw_dir,
        )
    except Exception as exc:
        print(f"NSRDB 获取失败，切换 Open-Meteo/ERA5: {exc}")
        return fetch_open_meteo_archive(
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
            raw_dir=raw_dir,
            target_name=fallback_name,
        )


def fetch_declared_sources(config: dict, raw_dir: Path, root_dir: Path | None = None) -> dict[str, DownloadResult]:
    """Fetch all enabled first-stage data sources."""

    site = config["site"]
    date_range = config["date_range"]
    sources = config["sources"]
    results: dict[str, DownloadResult] = {}

    pv_source = sources.get("pv_power")
    if pv_source and pv_source.get("kind") == "csv_url":
        results["pv_power"] = download_csv_url("pv_power", pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "nrel_solar_zip":
        results["pv_power"] = download_nrel_solar_zip(pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "pvdaq_s3_year":
        results["pv_power"] = download_pvdaq_s3_year(pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "pvdaq_s3_years":
        results["pv_power"] = download_pvdaq_s3_years(pv_source, raw_dir)

    weather_source = sources.get("weather")
    if weather_source and weather_source.get("kind") == "open_meteo_archive":
        results["weather"] = fetch_open_meteo_archive(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "open_meteo_historical_forecast":
        results["weather"] = fetch_open_meteo_historical_forecast_range(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "nsrdb":
        results["weather"] = fetch_nsrdb_weather_years(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "nsrdb_then_open_meteo":
        results["weather"] = fetch_weather_with_fallback(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "local_csv":
        if root_dir is None:
            raise ValueError("local_csv 数据源需要 root_dir")
        results["weather"] = use_local_file("weather", weather_source, root_dir)

    opsd_source = sources.get("opsd")
    if opsd_source and opsd_source.get("kind") == "csv_url":
        results["opsd"] = download_csv_url("opsd", opsd_source, raw_dir)
    elif opsd_source and opsd_source.get("kind") == "local_csv":
        if root_dir is None:
            raise ValueError("local_csv 数据源需要 root_dir")
        results["opsd"] = use_local_file("opsd", opsd_source, root_dir)

    return results
