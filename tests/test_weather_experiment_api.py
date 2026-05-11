from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import weather_experiment
from backend.app.main import app


def _fake_open_meteo_payload(hours: int = 96) -> dict:
    """Return a compact Open-Meteo-shaped payload with a realistic day profile."""
    start = datetime(2026, 5, 7, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M") for index in range(hours)]
    ghi = []
    cloud = []
    for index in range(hours):
        hour = index % 24
        daylight = max(0.0, 1.0 - abs(hour - 12) / 7)
        ghi.append(round(820 * daylight, 2))
        cloud.append(25 if 8 <= hour <= 16 else 55)
    return {
        "latitude": 40.86,
        "longitude": -105.02,
        "elevation": 1550,
        "timezone": "UTC",
        "hourly": {
            "time": times,
            "temperature_2m": [12 + (index % 24) * 0.35 for index in range(hours)],
            "relative_humidity_2m": [62 - min(index % 24, 12) for index in range(hours)],
            "dew_point_2m": [4.0 for _ in range(hours)],
            "surface_pressure": [842.0 for _ in range(hours)],
            "precipitation": [0.0 for _ in range(hours)],
            "wind_speed_10m": [3.2 + (index % 5) * 0.1 for index in range(hours)],
            "wind_direction_10m": [240.0 for _ in range(hours)],
            "wind_gusts_10m": [6.5 for _ in range(hours)],
            "shortwave_radiation": ghi,
            "direct_normal_irradiance": ghi,
            "diffuse_radiation": [value * 0.28 for value in ghi],
            "cloud_cover": cloud,
            "cloud_cover_low": [value * 0.45 for value in cloud],
            "cloud_cover_mid": [value * 0.35 for value in cloud],
            "cloud_cover_high": [value * 0.2 for value in cloud],
        },
    }


@pytest.fixture
def run_log_path(monkeypatch):
    path = Path(".stage22_weather_runs_test.jsonl")
    if path.exists():
        path.unlink()
    monkeypatch.setattr(weather_experiment, "RUN_LOG_PATH", path)
    yield path
    if path.exists():
        path.unlink()


def _patch_forecast(monkeypatch) -> None:
    weather_experiment.clear_forecast_cache()
    monkeypatch.setattr(
        weather_experiment,
        "_request_open_meteo_forecast",
        lambda **_: _fake_open_meteo_payload(),
    )


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_open_meteo_payload_normalization_keeps_forecast_time_auditable() -> None:
    payload = weather_experiment.normalize_open_meteo_payload(_fake_open_meteo_payload(), forecast_hours=48)

    assert payload["provider"] == "open_meteo_forecast"
    assert payload["quality"]["returned_hours"] == 48
    assert payload["quality"]["all_required_fields_present"] is True
    assert payload["rows"][0]["forecast_valid_time"] != payload["rows"][0]["request_time"]
    assert payload["rows"][0]["weather_forecast_issue_time_is_assumed"] is False


def test_weather_scenario_adjustment_changes_pv_direction(monkeypatch, run_log_path) -> None:
    _patch_forecast(monkeypatch)
    realtime = weather_experiment.run_weather_dispatch_experiment(
        weather_experiment.ExperimentParams(horizon_hours=24, weather_scenario="realtime")
    )
    overcast = weather_experiment.run_weather_dispatch_experiment(
        weather_experiment.ExperimentParams(horizon_hours=24, weather_scenario="overcast")
    )

    realtime_pv = sum(row["pv_kw"] for row in realtime["weather"])
    overcast_pv = sum(row["pv_kw"] for row in overcast["weather"])
    assert overcast_pv < realtime_pv
    assert overcast["weather"][12]["cloud_cover_pct"] > realtime["weather"][12]["cloud_cover_pct"]


def test_dispatch_run_respects_requested_horizon_and_soc_bounds(monkeypatch, run_log_path) -> None:
    _patch_forecast(monkeypatch)
    result = weather_experiment.run_weather_dispatch_experiment(
        weather_experiment.ExperimentParams(
            horizon_hours=72,
            battery_energy_kwh=1800,
            battery_power_kw=900,
            soc_min=0.2,
            soc_max=0.82,
            initial_soc=0.5,
            algorithm="rolling",
        )
    )

    assert len(result["dispatch_rows"]) == 72
    assert result["boundary"]["is_measured_generation"] is False
    assert result["boundary"]["is_real_settlement_revenue"] is False
    assert all(20 <= row["soc_pct"] <= 82 for row in result["dispatch_rows"])
    assert result["kpis"]["equivalent_cycles"] >= 0
    assert result["run_id"]
    assert result["recorded"] is True
    assert weather_experiment.get_run_record(result["run_id"]) is not None


def test_weather_forecast_endpoint_requires_auth_and_returns_rows(monkeypatch, run_log_path) -> None:
    _patch_forecast(monkeypatch)
    client = TestClient(app)
    headers = _auth_headers(client)

    response = client.get("/api/weather/forecast", params={"forecast_hours": 24}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["quality"]["returned_hours"] == 24
    assert body["quality"]["forecast_valid_time_distinct_from_request_time"] is True


def test_weather_dispatch_endpoint_returns_chart_ready_contract(monkeypatch, run_log_path) -> None:
    _patch_forecast(monkeypatch)
    client = TestClient(app)
    headers = _auth_headers(client)

    response = client.post(
        "/api/dispatch/weather-experiment/run",
        headers=headers,
        json={
            "dispatchDate": "2026-05-07",
            "horizonHours": 24,
            "weatherScenario": "clear",
            "priceScenario": "tou_peak_valley",
            "batteryEnergyKwh": 2200,
            "batteryPowerKw": 900,
            "chargeEfficiency": 0.94,
            "dischargeEfficiency": 0.93,
            "initialSoc": 0.45,
            "socMin": 0.15,
            "socMax": 0.88,
            "objective": "balanced",
            "algorithm": "multi",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["weather"]) == 24
    assert len(body["dispatch_rows"]) == 24
    assert {"total_revenue_eur", "incremental_revenue_eur", "soh_impact"} <= set(body["kpis"])
    assert {row["label"] for row in body["comparison"]} == {"无储能", "固定阈值", "滚动优化", "多目标优化"}
    assert body["run_id"]
    assert body["recommendation"]


def test_weather_dispatch_history_and_export_endpoints(monkeypatch, run_log_path) -> None:
    _patch_forecast(monkeypatch)
    client = TestClient(app)
    headers = _auth_headers(client)
    run_response = client.post(
        "/api/dispatch/weather-experiment/run",
        headers=headers,
        json={"horizonHours": 24, "weatherScenario": "cloudy", "priceScenario": "flat_proxy_30"},
    )
    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]

    list_response = client.get("/api/dispatch/weather-experiment/runs", params={"limit": 5}, headers=headers)
    assert list_response.status_code == 200
    summaries = list_response.json()
    assert summaries[0]["run_id"] == run_id
    assert summaries[0]["horizon_hours"] == 24

    detail_response = client.get(f"/api/dispatch/weather-experiment/runs/{run_id}", headers=headers)
    assert detail_response.status_code == 200
    assert len(detail_response.json()["dispatch_rows"]) == 24

    json_export = client.get(f"/api/dispatch/weather-experiment/runs/{run_id}/export", params={"format": "json"}, headers=headers)
    assert json_export.status_code == 200
    assert json_export.headers["content-type"].startswith("application/json")
    assert json_export.json()["run_id"] == run_id

    csv_export = client.get(f"/api/dispatch/weather-experiment/runs/{run_id}/export", params={"format": "csv"}, headers=headers)
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    assert "soc_pct" in csv_export.text.splitlines()[0]


def test_weather_dispatch_failure_returns_structured_error(monkeypatch, run_log_path) -> None:
    weather_experiment.clear_forecast_cache()
    monkeypatch.setattr(
        weather_experiment,
        "_request_open_meteo_forecast",
        lambda **_: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )
    client = TestClient(app)
    headers = _auth_headers(client)

    response = client.post("/api/dispatch/weather-experiment/run", headers=headers, json={"horizonHours": 24})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_code"] == "WEATHER_EXPERIMENT_UNAVAILABLE"
    assert detail["retryable"] is True
    assert detail["suggested_action"]
    assert weather_experiment.list_run_records() == []
