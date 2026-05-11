"""Realtime weather-driven storage dispatch experiment service.

This module backs the defense/demo experiment bench.  It intentionally keeps
the simulation transparent: Open-Meteo forecast weather is normalized, PV power
is estimated from GHI and public reference-site capacity, and storage dispatch
uses a lightweight deterministic policy.  The output is a reference simulation,
not measured plant generation or real settlement revenue.
"""

from __future__ import annotations

import math
import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_LATITUDE = 40.8606
DEFAULT_LONGITUDE = -105.0189
DEFAULT_CAPACITY_KW = 22000.0
DEFAULT_PERFORMANCE_RATIO = 0.82
CACHE_TTL_SECONDS = 12 * 60
RUN_LOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "processed"
    / "pvdaq_nsrdb_2020_2022"
    / "stage22_weather_experiment_runs.jsonl"
)

HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "surface_pressure",
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
]

WEATHER_PROFILES = {
    "realtime": {"ghi": 1.0, "cloud": 1.0, "temp": 0.0, "humidity": 0.0, "wind": 1.0},
    "clear": {"ghi": 1.18, "cloud": 0.45, "temp": 2.0, "humidity": -8.0, "wind": 0.9},
    "cloudy": {"ghi": 0.74, "cloud": 1.35, "temp": -1.0, "humidity": 5.0, "wind": 1.08},
    "overcast": {"ghi": 0.42, "cloud": 1.65, "temp": -3.0, "humidity": 12.0, "wind": 1.12},
    "custom": {"ghi": 1.0, "cloud": 1.0, "temp": 0.0, "humidity": 0.0, "wind": 1.0},
}

PRICE_SCENARIO_LABELS = {
    "solar_duck_curve": "光伏鸭形曲线场景",
    "tou_peak_valley": "峰谷分时电价",
    "flat_proxy_30": "固定电价",
    "high_volatility_stress": "高波动电价",
    "synthetic_scenario": "合成场景",
}

WEATHER_SCENARIO_LABELS = {
    "realtime": "实时天气",
    "clear": "晴天",
    "cloudy": "多云",
    "overcast": "阴天",
    "custom": "自定义",
}

OBJECTIVE_LABELS = {
    "economic": "经济性优先",
    "smooth": "平滑优先",
    "balanced": "综合优化",
}

ALGORITHM_LABELS = {
    "none": "无储能",
    "rule": "规则策略",
    "rolling": "滚动优化",
    "multi": "多目标优化",
}

_FORECAST_CACHE: dict[tuple[float, float, int], tuple[float, dict[str, Any]]] = {}


class WeatherExperimentError(RuntimeError):
    """Raised when realtime weather or experiment generation fails."""


def structured_error(exc: Exception) -> dict[str, Any]:
    """Build the API error contract consumed by the experiment bench UI."""
    return {
        "error_code": "WEATHER_EXPERIMENT_UNAVAILABLE",
        "message": str(exc),
        "provider": "open_meteo_forecast",
        "request_time": datetime.now(timezone.utc).isoformat(),
        "retryable": True,
        "suggested_action": "请稍后重试；若现场网络不稳定，可切换晴天、多云或阴天场景后再次运行。",
    }


@dataclass(frozen=True)
class ExperimentParams:
    dispatch_date: str | None = None
    horizon_hours: int = 24
    weather_scenario: str = "realtime"
    price_scenario: str = "solar_duck_curve"
    battery_energy_kwh: float = 2000.0
    battery_power_kw: float = 1000.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    initial_soc: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 0.9
    objective: str = "balanced"
    algorithm: str = "rolling"
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    capacity_kw: float = DEFAULT_CAPACITY_KW


def clear_forecast_cache() -> None:
    """Clear the in-memory forecast cache.  Intended for tests."""
    _FORECAST_CACHE.clear()


def fetch_open_meteo_forecast(
    *,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    forecast_hours: int = 72,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """Fetch and normalize Open-Meteo forecast weather with short-lived cache."""
    hours = int(_clamp(forecast_hours, 1, 96))
    key = (round(float(latitude), 4), round(float(longitude), 4), hours)
    now = time.time()
    cached = _FORECAST_CACHE.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        payload = dict(cached[1])
        payload["cache"] = {"hit": True, "ttl_seconds": CACHE_TTL_SECONDS}
        return payload

    try:
        raw_payload = _request_open_meteo_forecast(
            latitude=float(latitude),
            longitude=float(longitude),
            forecast_hours=hours,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # pragma: no cover - exact requests exception varies
        raise WeatherExperimentError(f"Realtime weather request failed: {exc}") from exc

    normalized = normalize_open_meteo_payload(raw_payload, forecast_hours=hours)
    normalized["cache"] = {"hit": False, "ttl_seconds": CACHE_TTL_SECONDS}
    _FORECAST_CACHE[key] = (now, normalized)
    return normalized


def _request_open_meteo_forecast(
    *,
    latitude: float,
    longitude: float,
    forecast_hours: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    response = requests.get(
        OPEN_METEO_FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
            "forecast_hours": forecast_hours,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def normalize_open_meteo_payload(payload: dict[str, Any], *, forecast_hours: int) -> dict[str, Any]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise WeatherExperimentError("Open-Meteo response missing hourly.time")

    required = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "shortwave_radiation", "cloud_cover"]
    missing = [field for field in required if field not in hourly]
    rows = []
    row_count = min(int(forecast_hours), len(times))
    fetched_at = datetime.now(timezone.utc).isoformat()
    for index in range(row_count):
        valid_time = _parse_open_meteo_time(str(times[index]))
        row = {
            "forecast_valid_time": valid_time.isoformat(),
            "request_time": fetched_at,
            "weather_provider": "open_meteo_forecast",
            "temperature_c": _series_value(hourly, "temperature_2m", index, 20.0),
            "relative_humidity_pct": _series_value(hourly, "relative_humidity_2m", index, 45.0),
            "dew_point_c": _series_value(hourly, "dew_point_2m", index, None),
            "surface_pressure_hpa": _series_value(hourly, "surface_pressure", index, None),
            "precipitation_mm": _series_value(hourly, "precipitation", index, 0.0),
            "wind_speed_ms": _series_value(hourly, "wind_speed_10m", index, 0.0),
            "wind_direction_deg": _series_value(hourly, "wind_direction_10m", index, None),
            "wind_gusts_ms": _series_value(hourly, "wind_gusts_10m", index, None),
            "ghi_wm2": _series_value(hourly, "shortwave_radiation", index, 0.0),
            "dni_wm2": _series_value(hourly, "direct_normal_irradiance", index, 0.0),
            "dhi_wm2": _series_value(hourly, "diffuse_radiation", index, 0.0),
            "cloud_cover_pct": _series_value(hourly, "cloud_cover", index, 0.0),
            "cloud_cover_low_pct": _series_value(hourly, "cloud_cover_low", index, None),
            "cloud_cover_mid_pct": _series_value(hourly, "cloud_cover_mid", index, None),
            "cloud_cover_high_pct": _series_value(hourly, "cloud_cover_high", index, None),
            "weather_forecast_issue_time": fetched_at,
            "weather_forecast_lead_time_hour": max((valid_time - datetime.now(timezone.utc)).total_seconds() / 3600, 0),
            "weather_forecast_issue_time_is_assumed": False,
        }
        rows.append(row)

    return {
        "provider": "open_meteo_forecast",
        "request_time": fetched_at,
        "grid": {
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "elevation_m": payload.get("elevation"),
            "timezone": payload.get("timezone", "UTC"),
        },
        "quality": {
            "requested_hours": int(forecast_hours),
            "returned_hours": len(rows),
            "required_fields_missing": missing,
            "all_required_fields_present": not missing,
            "forecast_valid_time_distinct_from_request_time": True,
        },
        "rows": rows,
    }


def run_weather_dispatch_experiment(params: ExperimentParams) -> dict[str, Any]:
    """Run the realtime weather-driven dispatch experiment."""
    started = time.perf_counter()
    clean = normalize_experiment_params(params)
    forecast = fetch_open_meteo_forecast(
        latitude=clean.latitude,
        longitude=clean.longitude,
        forecast_hours=max(clean.horizon_hours, 72),
    )
    rows = build_weather_rows(forecast["rows"], clean)
    result = simulate_dispatch(rows, clean)
    comparison = build_comparison_rows(rows, clean)
    sensitivity = build_sensitivity_rows(rows, clean)
    run_id = uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "run_id": run_id,
        "created_at": created_at,
        "status": "success",
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "source": {
            "mode": "realtime_weather_api",
            "provider": forecast["provider"],
            "request_time": forecast["request_time"],
            "cache": forecast.get("cache", {}),
            "grid": forecast["grid"],
            "quality": forecast["quality"],
        },
        "parameters": clean.__dict__,
        "weather": rows,
        "dispatch_rows": result["rows"],
        "kpis": result["kpis"],
        "comparison": comparison,
        "sensitivity": sensitivity,
        "recommendation": build_recommendation(comparison, result["kpis"]),
        "boundary": {
            "generation_kind": "weather_estimated_pv_not_measured_reference_station",
            "is_measured_generation": False,
            "is_real_settlement_revenue": False,
            "message": "参考仿真：天气估算 PV 出力与可配置电价场景，不代表实测电站发电或真实结算收益。",
        },
    }
    try:
        append_run_record(payload)
        payload["recorded"] = True
    except OSError as exc:
        payload["recorded"] = False
        payload["record_error"] = str(exc)
    return payload


def append_run_record(payload: dict[str, Any], *, path: Path | None = None) -> None:
    """Append one complete run payload to the JSONL history file."""
    target = path or RUN_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def list_run_records(*, limit: int = 20, path: Path | None = None) -> list[dict[str, Any]]:
    """Return newest run summaries without loading large time-series into the UI."""
    records = read_run_records(path=path)
    records.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return [summarize_run_record(row) for row in records[: int(_clamp(limit, 1, 100))]]


def get_run_record(run_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    """Find one complete run record by id."""
    for record in read_run_records(path=path):
        if record.get("run_id") == run_id:
            return record
    return None


def read_run_records(*, path: Path | None = None) -> list[dict[str, Any]]:
    """Read valid JSONL records and skip corrupt partial lines."""
    target = path or RUN_LOG_PATH
    if not target.exists():
        return []
    records = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def summarize_run_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build the compact history row used by the frontend run table."""
    params = record.get("parameters") or {}
    kpis = record.get("kpis") or {}
    return {
        "run_id": record.get("run_id"),
        "created_at": record.get("created_at"),
        "status": record.get("status", "success"),
        "duration_ms": record.get("duration_ms"),
        "recorded": record.get("recorded", True),
        "weather_scenario": params.get("weather_scenario"),
        "weather_scenario_label": WEATHER_SCENARIO_LABELS.get(params.get("weather_scenario"), params.get("weather_scenario")),
        "price_scenario": params.get("price_scenario"),
        "price_scenario_label": PRICE_SCENARIO_LABELS.get(params.get("price_scenario"), params.get("price_scenario")),
        "horizon_hours": params.get("horizon_hours"),
        "algorithm": params.get("algorithm"),
        "objective": params.get("objective"),
        "incremental_revenue_eur": kpis.get("incremental_revenue_eur"),
        "total_revenue_eur": kpis.get("total_revenue_eur"),
        "peak_shaving_ratio": kpis.get("peak_shaving_ratio"),
        "smoothing_ratio": kpis.get("smoothing_ratio"),
        "recommendation": record.get("recommendation"),
    }


def export_run_record(record: dict[str, Any], *, format_name: str) -> tuple[str, str, str]:
    """Return filename, media type, and content for JSON or dispatch-row CSV export."""
    run_id = str(record.get("run_id") or "unknown")
    if format_name == "csv":
        content = build_dispatch_csv(record.get("dispatch_rows") or [])
        return f"weather_experiment_{run_id}.csv", "text/csv; charset=utf-8", content
    content = json.dumps(record, ensure_ascii=False, indent=2)
    return f"weather_experiment_{run_id}.json", "application/json; charset=utf-8", content


def build_dispatch_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize hourly dispatch rows for spreadsheet inspection."""
    columns = [
        "time",
        "forecast_valid_time",
        "weather_scenario",
        "ghi_wm2",
        "temperature_c",
        "cloud_cover_pct",
        "pv_kw",
        "price_eur_mwh",
        "charge_kw",
        "discharge_kw",
        "grid_kw",
        "soc_pct",
        "revenue_eur",
        "incremental_revenue_eur",
        "curtailment_kw",
        "degradation_cost_eur",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def normalize_experiment_params(params: ExperimentParams) -> ExperimentParams:
    soc_min = _clamp(params.soc_min, 0.05, 0.8)
    soc_max = _clamp(max(params.soc_max, soc_min + 0.05), 0.2, 0.98)
    return ExperimentParams(
        dispatch_date=params.dispatch_date,
        horizon_hours=int(_clamp(params.horizon_hours, 24, 72)),
        weather_scenario=params.weather_scenario if params.weather_scenario in WEATHER_PROFILES else "realtime",
        price_scenario=params.price_scenario or "solar_duck_curve",
        battery_energy_kwh=_clamp(params.battery_energy_kwh, 500, 8000),
        battery_power_kw=_clamp(params.battery_power_kw, 200, 5000),
        charge_efficiency=_clamp(params.charge_efficiency, 0.7, 0.99),
        discharge_efficiency=_clamp(params.discharge_efficiency, 0.7, 0.99),
        initial_soc=_clamp(params.initial_soc, soc_min, soc_max),
        soc_min=soc_min,
        soc_max=soc_max,
        objective=params.objective if params.objective in OBJECTIVE_LABELS else "balanced",
        algorithm=params.algorithm if params.algorithm in ALGORITHM_LABELS else "rolling",
        latitude=_clamp(params.latitude, -90, 90),
        longitude=_clamp(params.longitude, -180, 180),
        capacity_kw=_clamp(params.capacity_kw, 1, 200000),
    )


def build_weather_rows(forecast_rows: list[dict[str, Any]], params: ExperimentParams) -> list[dict[str, Any]]:
    profile = WEATHER_PROFILES[params.weather_scenario]
    start = _parse_dispatch_start(params.dispatch_date)
    output = []
    for index in range(params.horizon_hours):
        source = forecast_rows[index % len(forecast_rows)]
        valid_time = start.isoformat() if index == 0 and start else source["forecast_valid_time"]
        if start:
            valid_time = datetime.fromtimestamp(start.timestamp() + index * 3600, tz=timezone.utc).isoformat()
        ghi = _clamp(_num(source.get("ghi_wm2"), 0.0) * profile["ghi"], 0, 1100)
        pv_kw = _clamp(params.capacity_kw * ghi / 1000 * DEFAULT_PERFORMANCE_RATIO, 0, params.capacity_kw)
        row = {
            "time": valid_time,
            "forecast_valid_time": valid_time,
            "source_forecast_valid_time": source["forecast_valid_time"],
            "request_time": source["request_time"],
            "weather_provider": source["weather_provider"],
            "weather_scenario": params.weather_scenario,
            "weather_scenario_label": WEATHER_SCENARIO_LABELS.get(params.weather_scenario, params.weather_scenario),
            "ghi_wm2": ghi,
            "dni_wm2": _clamp(_num(source.get("dni_wm2"), 0.0) * profile["ghi"], 0, 1200),
            "dhi_wm2": _clamp(_num(source.get("dhi_wm2"), 0.0) * profile["ghi"], 0, 1000),
            "temperature_c": _num(source.get("temperature_c"), 20.0) + profile["temp"],
            "relative_humidity_pct": _clamp(_num(source.get("relative_humidity_pct"), 45.0) + profile["humidity"], 0, 100),
            "wind_speed_ms": _clamp(_num(source.get("wind_speed_ms"), 0.0) * profile["wind"], 0, 35),
            "cloud_cover_pct": _clamp(_num(source.get("cloud_cover_pct"), 0.0) * profile["cloud"], 0, 100),
            "pv_kw": pv_kw,
            "price_eur_mwh": synthetic_price(index, params.price_scenario),
            "price_scenario_id": params.price_scenario,
            "price_scenario_label": PRICE_SCENARIO_LABELS.get(params.price_scenario, params.price_scenario),
            "is_measured_generation": False,
        }
        output.append(row)
    return output


def simulate_dispatch(rows: list[dict[str, Any]], params: ExperimentParams) -> dict[str, Any]:
    if not rows:
        return {"rows": [], "kpis": empty_kpis()}
    energy_kwh = params.battery_energy_kwh
    power_kw = params.battery_power_kw
    soc = _clamp(params.initial_soc, params.soc_min, params.soc_max)
    prices = sorted(_num(row["price_eur_mwh"], 0.0) for row in rows)
    price_low = quantile(prices, 0.28)
    price_high = quantile(prices, 0.72)
    price_mean = sum(prices) / len(prices)
    pv_values = [_num(row["pv_kw"], 0.0) for row in rows]
    pv_mean = sum(pv_values) / len(pv_values)
    grid_limit = max(pv_mean * 1.15, params.capacity_kw * 0.72)
    output = []
    total_revenue = 0.0
    no_storage_revenue = 0.0
    degradation_cost = 0.0
    throughput_kwh = 0.0
    pv_energy = 0.0
    curtailed = 0.0
    no_storage_curtailed = 0.0

    for index, row in enumerate(rows):
        decision = dispatch_decision(
            row,
            index,
            rows,
            params=params,
            price_low=price_low,
            price_high=price_high,
            price_mean=price_mean,
            pv_mean=pv_mean,
            grid_limit=grid_limit,
        )
        available_charge_kw = max(0.0, (params.soc_max - soc) * energy_kwh / params.charge_efficiency)
        charge_kw = _clamp(min(decision["charge_kw"], row["pv_kw"], available_charge_kw), 0, power_kw)
        soc = _clamp(soc + (charge_kw * params.charge_efficiency) / energy_kwh, params.soc_min, params.soc_max)
        available_discharge_kw = max(0.0, (soc - params.soc_min) * energy_kwh * params.discharge_efficiency)
        discharge_kw = _clamp(min(decision["discharge_kw"], available_discharge_kw), 0, power_kw)
        soc = _clamp(soc - (discharge_kw / params.discharge_efficiency) / energy_kwh, params.soc_min, params.soc_max)
        no_storage_grid_kw = min(row["pv_kw"], grid_limit)
        raw_grid_kw = row["pv_kw"] - charge_kw + discharge_kw
        grid_kw = _clamp(min(raw_grid_kw, grid_limit), 0, grid_limit)
        curtailment_kw = max(0.0, raw_grid_kw - grid_limit)
        revenue = grid_kw * row["price_eur_mwh"] / 1000
        baseline_revenue = no_storage_grid_kw * row["price_eur_mwh"] / 1000
        degradation = (charge_kw + discharge_kw) / 1000 * 6
        total_revenue += revenue
        no_storage_revenue += baseline_revenue
        degradation_cost += degradation
        throughput_kwh += charge_kw + discharge_kw
        pv_energy += row["pv_kw"]
        curtailed += curtailment_kw
        no_storage_curtailed += max(0.0, row["pv_kw"] - grid_limit)
        output.append({
            **row,
            "charge_kw": charge_kw,
            "discharge_kw": discharge_kw,
            "grid_kw": grid_kw,
            "soc": soc,
            "soc_pct": soc * 100,
            "soc_min_pct": params.soc_min * 100,
            "soc_max_pct": params.soc_max * 100,
            "revenue_eur": revenue,
            "no_storage_revenue_eur": baseline_revenue,
            "incremental_revenue_eur": revenue - baseline_revenue,
            "curtailment_kw": curtailment_kw,
            "degradation_cost_eur": degradation,
        })

    grid_values = [_num(row["grid_kw"], 0.0) for row in output]
    no_storage_peak = max(pv_values) if pv_values else 1.0
    grid_peak = max(grid_values) if grid_values else 0.0
    pv_ramp = average_ramp(pv_values)
    grid_ramp = average_ramp(grid_values)
    equivalent_cycles = throughput_kwh / max(energy_kwh * 2, 1)
    kpis = {
        "total_revenue_eur": total_revenue,
        "no_storage_revenue_eur": no_storage_revenue,
        "incremental_revenue_eur": total_revenue - no_storage_revenue - degradation_cost,
        "degradation_cost_eur": degradation_cost,
        "equivalent_cycles": equivalent_cycles,
        "soh_impact": equivalent_cycles * 0.00008,
        "curtailment_rate": curtailed / pv_energy if pv_energy else 0.0,
        "curtailment_reduction_ratio": _clamp((no_storage_curtailed - curtailed) / no_storage_curtailed, 0, 1) if no_storage_curtailed else 0.0,
        "peak_shaving_ratio": _clamp((no_storage_peak - grid_peak) / no_storage_peak, 0, 1) if no_storage_peak else 0.0,
        "smoothing_ratio": _clamp((pv_ramp - grid_ramp) / pv_ramp, 0, 1) if pv_ramp else 0.0,
    }
    return {"rows": output, "kpis": kpis}


def dispatch_decision(
    row: dict[str, Any],
    index: int,
    rows: list[dict[str, Any]],
    *,
    params: ExperimentParams,
    price_low: float,
    price_high: float,
    price_mean: float,
    pv_mean: float,
    grid_limit: float,
) -> dict[str, float]:
    if params.algorithm == "none":
        return {"charge_kw": 0.0, "discharge_kw": 0.0}
    next_price = _num(rows[index + 1]["price_eur_mwh"], row["price_eur_mwh"]) if index + 1 < len(rows) else row["price_eur_mwh"]
    low_price = row["price_eur_mwh"] <= price_low or (row["price_eur_mwh"] < price_mean and next_price >= row["price_eur_mwh"])
    high_price = row["price_eur_mwh"] >= price_high
    pv_above_mean = row["pv_kw"] > pv_mean * 1.08
    if params.algorithm == "rule":
        charge_kw = params.battery_power_kw * 0.62 if low_price else 0.0
        discharge_kw = params.battery_power_kw * 0.62 if high_price else 0.0
    elif params.algorithm == "multi":
        charge_kw = params.battery_power_kw * 0.76 if low_price or pv_above_mean else 0.0
        discharge_kw = params.battery_power_kw * 0.68 if high_price and row["pv_kw"] < pv_mean * 0.9 else 0.0
    else:
        charge_kw = params.battery_power_kw * 0.72 if low_price or pv_above_mean else 0.0
        discharge_kw = params.battery_power_kw * 0.72 if high_price else 0.0

    if params.objective == "economic":
        discharge_kw *= 1.18
        charge_kw *= 1.08 if low_price else 0.78
    elif params.objective == "smooth":
        charge_kw = max(charge_kw * 0.92, params.battery_power_kw * 0.7 if row["pv_kw"] > grid_limit * 0.82 else 0.0)
        discharge_kw *= 0.85 if row["pv_kw"] < pv_mean * 0.55 else 0.55
    else:
        discharge_kw *= 0.95
    return {"charge_kw": charge_kw, "discharge_kw": discharge_kw}


def build_comparison_rows(rows: list[dict[str, Any]], params: ExperimentParams) -> list[dict[str, Any]]:
    variants = [
        ("无储能", "none", "balanced"),
        ("固定阈值", "rule", "economic"),
        ("滚动优化", "rolling", "economic"),
        ("多目标优化", "multi", "balanced"),
    ]
    output = []
    for label, algorithm, objective in variants:
        result = simulate_dispatch(rows, ExperimentParams(**{**params.__dict__, "algorithm": algorithm, "objective": objective}))
        output.append({"label": label, **result["kpis"]})
    return output


def build_sensitivity_rows(rows: list[dict[str, Any]], params: ExperimentParams) -> list[dict[str, Any]]:
    output = []
    for factor in [0.6, 0.8, 1.0, 1.2, 1.4]:
        capacity = simulate_dispatch(rows, ExperimentParams(**{**params.__dict__, "battery_energy_kwh": params.battery_energy_kwh * factor}))["kpis"]["incremental_revenue_eur"]
        power = simulate_dispatch(rows, ExperimentParams(**{**params.__dict__, "battery_power_kw": params.battery_power_kw * factor}))["kpis"]["incremental_revenue_eur"]
        output.append({"label": f"{round(factor * 100)}%", "capacity_revenue_eur": capacity, "power_revenue_eur": power})
    return output


def build_recommendation(comparison: list[dict[str, Any]], kpis: dict[str, float]) -> str:
    best = max(comparison, key=lambda row: row.get("incremental_revenue_eur", float("-inf"))) if comparison else {}
    risk = "但当前储能吞吐量偏高，答辩中应说明退化成本口径。" if kpis.get("soh_impact", 0.0) > 0.001 else "且退化影响处于较低水平。"
    return (
        f"建议采用“{best.get('label', '滚动优化')}”作为展示主方案；"
        f"当前场景下其增量收益为 EUR {best.get('incremental_revenue_eur', 0.0):.2f}，"
        f"削峰效果为 {best.get('peak_shaving_ratio', 0.0) * 100:.1f}%，{risk}"
    )


def synthetic_price(index: int, scenario: str) -> float:
    hour = index % 24
    if scenario == "flat_proxy_30":
        return 30.0
    if scenario == "high_volatility_stress":
        return 140.0 if 18 <= hour <= 21 else -20.0 if 10 <= hour <= 15 else 35.0
    if scenario == "tou_peak_valley":
        return 90.0 if 18 <= hour <= 22 else 18.0 if hour <= 6 else 42.0
    return 105.0 if 17 <= hour <= 22 else 5.0 if 10 <= hour <= 15 else 38.0


def empty_kpis() -> dict[str, float]:
    return {
        "total_revenue_eur": 0.0,
        "no_storage_revenue_eur": 0.0,
        "incremental_revenue_eur": 0.0,
        "degradation_cost_eur": 0.0,
        "equivalent_cycles": 0.0,
        "soh_impact": 0.0,
        "curtailment_rate": 0.0,
        "curtailment_reduction_ratio": 0.0,
        "peak_shaving_ratio": 0.0,
        "smoothing_ratio": 0.0,
    }


def _parse_open_meteo_time(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    if "+" not in text:
        text = f"{text}+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _parse_dispatch_start(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(f"{value}T00:00:00+00:00")
    except ValueError:
        return None


def _series_value(hourly: dict[str, list[Any]], field: str, index: int, fallback: float | None) -> float | None:
    values = hourly.get(field) or []
    if index >= len(values):
        return fallback
    return _num(values[index], fallback)


def _num(value: Any, fallback: float | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), minimum), maximum)


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * q)))
    return values[index]


def average_ramp(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return sum(abs(values[i] - values[i - 1]) for i in range(1, len(values))) / (len(values) - 1)
