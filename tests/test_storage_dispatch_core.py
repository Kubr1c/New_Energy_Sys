from __future__ import annotations

import math

import pandas as pd
import pytest

from new_energy_sys.stage12_storage_rolling import run_stage12_rolling_optimization
from new_energy_sys.storage import (
    _constraint_summary,
    _prepare_dispatch_input,
    _simulate_dispatch_scenario,
)


def _storage_config() -> dict:
    return {
        "capacity_kwh": 10.0,
        "max_charge_kw": 3.0,
        "max_discharge_kw": 4.0,
        "charge_efficiency": 0.95,
        "discharge_efficiency": 0.9,
        "soc_initial": 0.5,
        "soc_min": 0.1,
        "soc_max": 0.9,
        "charge_price_threshold": 20.0,
        "discharge_price_threshold": 60.0,
    }


def _predictions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="h"),
            "target": ["target_pv_power_t_plus_24h"] * 3,
            "prediction_kw": [6.0, 5.0, 2.0],
            "prediction_capacity_ratio": [0.6, 0.5, 0.2],
            "actual_kw": [5.5, 3.0, 2.0],
        }
    )


def _market() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-02T00:00:00Z", periods=3, freq="h"),
            "load_mw": [100.0, 101.0, 102.0],
            "price_eur_mwh": [10.0, 75.0, 35.0],
        }
    )


def _stage12_config() -> dict:
    return {
        "site": {"capacity_kw": 10.0},
        "storage": _storage_config(),
    }


def test_prepare_dispatch_input_aligns_market_by_delivery_timestamp() -> None:
    dispatch_input = _prepare_dispatch_input(_predictions(), _market(), horizon_hours=24)

    assert list(dispatch_input["forecast_timestamp"]) == list(_predictions()["timestamp"])
    assert list(dispatch_input["dispatch_timestamp"]) == list(_market()["timestamp"])
    assert list(dispatch_input["price_eur_mwh"]) == [10.0, 75.0, 35.0]
    assert dispatch_input.attrs["market_alignment_input_rows"] == 3
    assert dispatch_input.attrs["market_alignment_dropped_rows"] == 0


def test_prepare_dispatch_input_drops_rows_without_market_signal() -> None:
    market = _market().iloc[:2].copy()

    dispatch_input = _prepare_dispatch_input(_predictions(), market, horizon_hours=24)

    assert len(dispatch_input) == 2
    assert dispatch_input.attrs["market_alignment_input_rows"] == 3
    assert dispatch_input.attrs["market_alignment_dropped_rows"] == 1


def test_prepare_dispatch_input_fails_fast_on_missing_prediction_column() -> None:
    predictions = _predictions().drop(columns=["actual_kw"])

    with pytest.raises(ValueError, match="predictions missing required columns: actual_kw"):
        _prepare_dispatch_input(predictions, _market(), horizon_hours=24)


def test_simulate_dispatch_respects_soc_power_and_energy_constraints() -> None:
    dispatch_input = _prepare_dispatch_input(_predictions(), _market(), horizon_hours=24)

    results = _simulate_dispatch_scenario(
        dispatch_input,
        _storage_config(),
        capacity_kw=10.0,
        scenario="unit_test",
        forecast_column="prediction_kw",
    )
    constraints = _constraint_summary(results, _storage_config())

    assert constraints["soc_within_bounds"] is True
    assert constraints["charge_power_within_limit"] is True
    assert constraints["discharge_power_within_limit"] is True
    assert constraints["no_simultaneous_charge_discharge"] is True
    assert constraints["energy_balance_error_within_tolerance"] is True
    assert constraints["simultaneous_charge_discharge_rows"] == 0
    assert constraints["max_energy_balance_error"] <= 1e-9


def test_simulate_dispatch_charges_on_low_price_and_discharges_on_high_price() -> None:
    dispatch_input = _prepare_dispatch_input(_predictions(), _market(), horizon_hours=24)

    results = _simulate_dispatch_scenario(
        dispatch_input,
        _storage_config(),
        capacity_kw=10.0,
        scenario="unit_test",
        forecast_column="prediction_kw",
    )

    low_price = results.iloc[0]
    high_price = results.iloc[1]
    assert low_price["actual_charge_kw"] > 0.0
    assert low_price["actual_discharge_kw"] == 0.0
    assert high_price["actual_discharge_kw"] > 0.0
    assert high_price["actual_charge_kw"] == 0.0
    expected_soc_after_low_price = 0.5 + (low_price["actual_charge_kw"] * 0.95) / 10.0
    assert math.isclose(low_price["soc_end"], expected_soc_after_low_price, abs_tol=1e-12)


def test_stage12_economic_mode_keeps_full_power_switch_behavior() -> None:
    result = run_stage12_rolling_optimization(
        _predictions(),
        _market(),
        _stage12_config(),
        lookahead_hours=3,
        dispatch_mode="economic",
    )

    rolling = result.results[result.results["scenario"] == "rolling_optimization"].reset_index(drop=True)
    assert result.report["dispatch_mode"] == "economic"
    assert rolling.loc[0, "dispatch_mode_label"] == "经济优先调度"
    assert math.isclose(rolling.loc[0, "actual_charge_kw"], 3.0, abs_tol=1e-12)


def test_stage12_smooth_mode_limits_storage_power_ramp() -> None:
    result = run_stage12_rolling_optimization(
        _predictions(),
        _market(),
        _stage12_config(),
        lookahead_hours=3,
        dispatch_mode="smooth",
        smooth_power_ramp_limit_kw=1.0,
        smooth_action_step_kw=1.0,
    )

    rolling = result.results[result.results["scenario"] == "rolling_optimization"].reset_index(drop=True)
    assert result.report["dispatch_mode"] == "smooth"
    assert rolling.loc[0, "dispatch_mode_label"] == "平滑运行调度"
    assert rolling["ramp_constraint_satisfied"].all()
    assert rolling["actual_storage_power_delta_kw"].max() <= 1.0 + 1e-12
    assert rolling["actual_charge_kw"].max() <= _storage_config()["max_charge_kw"] + 1e-12
    assert rolling["actual_discharge_kw"].max() <= _storage_config()["max_discharge_kw"] + 1e-12
