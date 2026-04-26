from __future__ import annotations

from pathlib import Path
from typing import Iterable
import zipfile

import numpy as np
import pandas as pd


def _first_existing_column(columns: Iterable[str], candidates: list[str], label: str) -> str:
    """Return the first candidate column found in a source table."""

    column_set = set(columns)
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    raise ValueError(f"无法识别{label}字段，候选字段: {', '.join(candidates)}")


def _read_csv(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    """Read CSV with optional row cap for large public datasets."""

    return pd.read_csv(path, nrows=max_rows, low_memory=False)


def normalize_pv_power(path: Path, source_config: dict, capacity_kw: float) -> pd.DataFrame:
    """Normalize PV power data to timestamp + pv_power_kw.

    Public PV datasets are not schema-stable: timestamp and power columns differ
    across systems. The config therefore declares candidate column names, and
    this function picks the first available one.
    """

    frame = _read_csv(path, source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        source_config["timestamp_candidates"],
        "光伏时间戳",
    )
    power_col = _first_existing_column(
        frame.columns,
        source_config["power_candidates"],
        "光伏功率",
    )

    power = pd.to_numeric(frame[power_col], errors="coerce")
    power_unit = source_config.get("power_unit", "kW").lower()
    if power_unit in {"w", "watt", "watts"}:
        power = power / 1000.0
    elif power_unit not in {"kw", "kilowatt", "kilowatts"}:
        raise ValueError(f"Unsupported PV power unit: {source_config.get('power_unit')}")

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
            "pv_power_kw": power,
        }
    )
    output = output.dropna(subset=["timestamp", "pv_power_kw"])

    # Physical guardrail: PV output cannot be negative and should not exceed a
    # generous upper bound of installed capacity. The 1.2 factor keeps temporary
    # metadata mismatch from deleting useful samples during early exploration.
    output["pv_power_kw"] = output["pv_power_kw"].clip(lower=0, upper=capacity_kw * 1.2)
    return output.sort_values("timestamp")


def normalize_nrel_solar_zip(path: Path, source_config: dict) -> pd.DataFrame:
    """Normalize NREL Solar Integration actual/forecast files.

    The NREL state ZIP contains aligned files:
    - Actual_*_5_Min.csv: measured/simulated actual PV output;
    - DA_*_60_Min.csv: day-ahead forecast PV output;
    - HA4_*_60_Min.csv: four-hour-ahead forecast PV output.

    The actual 5-minute output is resampled to hourly mean, then joined with DA
    and HA4 forecasts. The result is a continuous hourly table suitable for the
    main forecasting experiment.
    """

    actual_member = source_config["actual_member"]
    da_member = source_config.get("day_ahead_member")
    ha4_member = source_config.get("hour_ahead_member")
    timezone = source_config.get("timezone", "UTC")

    def read_member(member: str, output_column: str) -> pd.DataFrame:
        with zipfile.ZipFile(path) as archive:
            with archive.open(member) as handle:
                frame = pd.read_csv(handle)
        timestamp = pd.to_datetime(frame["LocalTime"], format="%m/%d/%y %H:%M", errors="coerce")
        timestamp = timestamp.dt.tz_localize(timezone, nonexistent="shift_forward", ambiguous="NaT").dt.tz_convert("UTC")
        return pd.DataFrame(
            {
                "timestamp": timestamp,
                output_column: pd.to_numeric(frame["Power(MW)"], errors="coerce") * 1000.0,
            }
        ).dropna(subset=["timestamp", output_column])

    actual = read_member(actual_member, "pv_power_kw")
    actual_hourly = actual.set_index("timestamp").resample("1h").mean(numeric_only=True).reset_index()

    merged = actual_hourly
    if da_member:
        da = read_member(da_member, "pv_forecast_da_kw")
        merged = merged.merge(da, on="timestamp", how="left")
    if ha4_member:
        ha4 = read_member(ha4_member, "pv_forecast_ha4_kw")
        merged = merged.merge(ha4, on="timestamp", how="left")

    capacity_kw = float(source_config["capacity_mw"]) * 1000.0
    power_columns = [column for column in merged.columns if column.endswith("_kw")]
    for column in power_columns:
        merged[column] = merged[column].clip(lower=0, upper=capacity_kw * 1.05)

    return merged.sort_values("timestamp").reset_index(drop=True)


def normalize_weather(path: Path) -> pd.DataFrame:
    """Normalize weather fields to model-friendly names.

    Supports both Open-Meteo/ERA5 CSV files and NSRDB PSM CSV files. NSRDB
    files contain two metadata rows before the actual header; Open-Meteo files
    are regular one-header CSV files. The output schema is intentionally stable
    so downstream cleaning and feature engineering do not depend on provider
    naming conventions.
    """

    first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    if "Source" in first_line and "Location" in first_line:
        frame = pd.read_csv(path, skiprows=2, low_memory=False)
        base_date = pd.to_datetime(
            {
                "year": pd.to_numeric(frame["Year"], errors="coerce"),
                "month": pd.to_numeric(frame["Month"], errors="coerce"),
                "day": pd.to_numeric(frame["Day"], errors="coerce"),
            },
            errors="coerce",
            utc=True,
        )
        hour = pd.to_numeric(frame["Hour"], errors="coerce").fillna(0)
        minute = pd.to_numeric(frame["Minute"], errors="coerce").fillna(0)
        frame["timestamp"] = base_date + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m")
        output = frame.rename(
            columns={
                "GHI": "ghi_wm2",
                "DNI": "dni_wm2",
                "DHI": "dhi_wm2",
                "Clearsky GHI": "clearsky_ghi_wm2",
                "Clearsky DNI": "clearsky_dni_wm2",
                "Clearsky DHI": "clearsky_dhi_wm2",
                "Temperature": "temperature_c",
                "Air Temperature": "temperature_c",
                "Dew Point": "dew_point_c",
                "Relative Humidity": "relative_humidity_pct",
                "Pressure": "pressure_hpa",
                "Surface Pressure": "pressure_hpa",
                "Wind Speed": "wind_speed_ms",
                "Wind Direction": "wind_direction_deg",
                "Solar Zenith Angle": "solar_zenith_angle_deg",
                "Surface Albedo": "surface_albedo",
                "Cloud Type": "cloud_type",
                "Fill Flag": "weather_fill_flag",
                "Precipitable Water": "precipitable_water_cm",
            }
        )
        keep = [
            "timestamp",
            "ghi_wm2",
            "dni_wm2",
            "dhi_wm2",
            "clearsky_ghi_wm2",
            "clearsky_dni_wm2",
            "clearsky_dhi_wm2",
            "temperature_c",
            "dew_point_c",
            "relative_humidity_pct",
            "pressure_hpa",
            "wind_speed_ms",
            "wind_direction_deg",
            "solar_zenith_angle_deg",
            "surface_albedo",
            "cloud_type",
            "weather_fill_flag",
            "precipitable_water_cm",
        ]
        existing = [column for column in keep if column in output.columns]
        for column in existing:
            if column != "timestamp":
                output[column] = pd.to_numeric(output[column], errors="coerce")
        return output[existing].dropna(subset=["timestamp"]).sort_values("timestamp")

    frame = _read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    output = frame.rename(
        columns={
            "temperature_2m": "temperature_c",
            "relative_humidity_2m": "relative_humidity_pct",
            "dew_point_2m": "dew_point_c",
            "surface_pressure": "surface_pressure_hpa",
            "pressure_msl": "pressure_hpa",
            "precipitation": "precipitation_mm",
            "wind_speed_10m": "wind_speed_ms",
            "wind_direction_10m": "wind_direction_deg",
            "wind_gusts_10m": "wind_gusts_ms",
            "shortwave_radiation": "ghi_wm2",
            "direct_radiation": "dni_wm2",
            "direct_normal_irradiance": "dni_wm2",
            "diffuse_radiation": "dhi_wm2",
            "terrestrial_radiation": "toa_radiation_wm2",
            "cloud_cover": "cloud_cover_pct",
            "cloud_cover_low": "cloud_cover_low_pct",
            "cloud_cover_mid": "cloud_cover_mid_pct",
            "cloud_cover_high": "cloud_cover_high_pct",
            "weather_forecast_lead_time_hour": "weather_forecast_lead_time_hour",
        }
    )
    if output.columns.duplicated().any():
        output = output.loc[:, ~output.columns.duplicated()]
    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def derive_weather_from_pv(path: Path, pv_source_config: dict, weather_config: dict) -> pd.DataFrame:
    """Derive weather-like fields from a PVDAQ/DuraMAT PV source file.

    Some PVDAQ public subsets already contain irradiance, ambient temperature,
    and wind speed. Reusing these columns is more robust for the first project
    milestone than joining a separate weather source that may not overlap in
    time with the selected PV station.
    """

    frame = _read_csv(path, pv_source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        pv_source_config["timestamp_candidates"],
        "天气时间戳",
    )

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
        }
    )
    for source_col, target_col in weather_config.get("field_map", {}).items():
        if source_col in frame.columns:
            values = pd.to_numeric(frame[source_col], errors="coerce")
            if target_col in output.columns:
                output[target_col] = output[target_col].fillna(values)
            else:
                output[target_col] = values

    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def normalize_opsd(path: Path, source_config: dict) -> pd.DataFrame:
    """Normalize OPSD load and price fields.

    OPSD column availability changes across releases and countries. The project
    starts with Germany/Luxembourg fields because they are commonly available in
    the public package and are enough to drive an economic dispatch demo.
    """

    frame = _read_csv(path, source_config.get("max_rows"))
    timestamp_col = _first_existing_column(
        frame.columns,
        source_config["timestamp_candidates"],
        "OPSD时间戳",
    )
    load_col = _first_existing_column(frame.columns, source_config["load_candidates"], "负荷")

    price_col = None
    for candidate in source_config["price_candidates"]:
        if candidate in frame.columns:
            price_col = candidate
            break

    output = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True),
            "load_mw": pd.to_numeric(frame[load_col], errors="coerce"),
        }
    )
    if price_col:
        output["price_eur_mwh"] = pd.to_numeric(frame[price_col], errors="coerce")
    else:
        # Fallback price profile keeps the scheduling module runnable when OPSD
        # lacks day-ahead price fields in the selected release.
        hour = output["timestamp"].dt.hour
        output["price_eur_mwh"] = 50 + ((hour >= 17) & (hour <= 21)).astype(int) * 60

    return output.dropna(subset=["timestamp"]).sort_values("timestamp")


def map_opsd_profile_to_target_timeline(target: pd.DataFrame, opsd: pd.DataFrame) -> pd.DataFrame:
    """Map OPSD load/price profiles onto the target PV timeline.

    NREL Solar Integration PV data is for 2006, while OPSD load/price data often
    starts in 2015. To keep the dispatch module tied to real OPSD shapes without
    pretending timestamps overlap, this function builds day-of-week/hour average
    profiles from OPSD and projects them onto the NREL timestamps.
    """

    profile_source = opsd.dropna(subset=["load_mw", "price_eur_mwh"]).copy()
    profile_source["day_of_week"] = profile_source["timestamp"].dt.dayofweek
    profile_source["hour"] = profile_source["timestamp"].dt.hour
    profile = (
        profile_source.groupby(["day_of_week", "hour"], as_index=False)[["load_mw", "price_eur_mwh"]]
        .mean()
    )

    mapped = pd.DataFrame({"timestamp": target["timestamp"].dropna().drop_duplicates()})
    mapped["day_of_week"] = mapped["timestamp"].dt.dayofweek
    mapped["hour"] = mapped["timestamp"].dt.hour
    mapped = mapped.merge(profile, on=["day_of_week", "hour"], how="left")

    for column in ["load_mw", "price_eur_mwh"]:
        if mapped[column].isna().any():
            mapped[column] = mapped[column].fillna(profile_source[column].median())

    return mapped[["timestamp", "load_mw", "price_eur_mwh"]].sort_values("timestamp")


def build_synthetic_market(index_source: pd.DataFrame, source_config: dict) -> pd.DataFrame:
    """Create deterministic load and price curves aligned with the PV timeline.

    The first implementation needs an economically meaningful dispatch signal,
    not a perfect market model. This function provides a reproducible profile:
    valley prices at night, peak prices in the evening, and load following a
    smooth daily cycle.
    """

    timestamps = pd.DataFrame({"timestamp": index_source["timestamp"].dropna().drop_duplicates()})
    timestamps = timestamps.sort_values("timestamp").reset_index(drop=True)
    hour = timestamps["timestamp"].dt.hour

    base_load = float(source_config["base_load_mw"])
    amplitude = float(source_config["daily_load_amplitude_mw"])
    base_price = float(source_config["base_price_eur_mwh"])
    peak_price = float(source_config["peak_price_eur_mwh"])
    valley_price = float(source_config["valley_price_eur_mwh"])

    daily_phase = (hour - 18) / 24 * 2 * np.pi
    timestamps["load_mw"] = base_load + amplitude * (1 + np.cos(daily_phase))
    timestamps["price_eur_mwh"] = base_price
    timestamps.loc[(hour >= 0) & (hour <= 6), "price_eur_mwh"] = valley_price
    timestamps.loc[(hour >= 17) & (hour <= 21), "price_eur_mwh"] = peak_price
    return timestamps


def build_hourly_training_table(
    *,
    pv_power: pd.DataFrame,
    weather: pd.DataFrame,
    opsd: pd.DataFrame,
) -> pd.DataFrame:
    """Align all sources to one hourly UTC training table."""

    pv_hourly = (
        pv_power.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )
    weather_hourly = (
        weather.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )
    opsd_hourly = (
        opsd.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )

    merged = pv_hourly.merge(weather_hourly, on="timestamp", how="left")
    merged = merged.merge(opsd_hourly, on="timestamp", how="left")

    # The PV target is the supervised learning label. Rows without it are not
    # valid training samples and must be removed before feature filling.
    merged = merged.dropna(subset=["pv_power_kw"]).copy()

    # Weather and market fields can have short gaps after resampling or joining.
    # Limit filling to adjacent rows so long outages remain visible during QA.
    feature_columns = [column for column in merged.columns if column not in {"timestamp", "pv_power_kw"}]
    merged[feature_columns] = merged[feature_columns].ffill(limit=3).bfill(limit=3)
    for column in feature_columns:
        if pd.api.types.is_numeric_dtype(merged[column]) and merged[column].isna().any():
            merged[column] = merged[column].fillna(merged[column].median())

    # Time features are deterministic, leakage-free, and useful for both tree
    # baselines and sequence models.
    merged["hour"] = merged["timestamp"].dt.hour
    merged["day_of_week"] = merged["timestamp"].dt.dayofweek
    merged["month"] = merged["timestamp"].dt.month

    return merged.sort_values("timestamp").reset_index(drop=True)
