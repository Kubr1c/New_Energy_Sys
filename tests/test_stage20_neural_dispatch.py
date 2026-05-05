from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from new_energy_sys.stage20_neural_dispatch import (  # noqa: E402
    _build_policy_dataset,
    _direction_metrics,
    _postprocess_actions,
    _replay_policy,
    _restrict_candidates_to_common_dispatch_window,
)


def _feature_frame(start: str, periods: int) -> pd.DataFrame:
    """Build the minimal Stage3-like market frame needed by Stage12 alignment."""
    ts = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "load_mw": np.linspace(1.0, 1.2, periods),
            "price_eur_mwh": np.linspace(20.0, 40.0, periods),
        }
    )


def _prediction_frame(start: str, periods: int) -> pd.DataFrame:
    """Build a Stage9-like t+24h prediction frame."""
    ts = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "target": "target_pv_power_t_plus_24h",
            "prediction_kw": np.full(periods, 0.4),
            "prediction_capacity_ratio": np.full(periods, 0.4 / 1.12),
            "actual_kw": np.full(periods, 0.35),
        }
    )


def _storage_config() -> dict:
    """Return the smallest storage/site config used by replay tests."""
    return {
        "site": {"capacity_kw": 1.12},
        "storage": {
            "capacity_kwh": 2.0,
            "max_charge_kw": 1.0,
            "max_discharge_kw": 1.0,
            "charge_efficiency": 0.95,
            "discharge_efficiency": 0.95,
            "soc_min": 0.1,
            "soc_max": 0.9,
            "soc_initial": 0.5,
        },
    }


def _stage12_policy_rows(start: str, periods: int) -> pd.DataFrame:
    """Build minimal Stage12 rolling rows with continuous hourly look-ahead."""
    ts = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "scenario": ["rolling_optimization"] * periods,
            "forecast_timestamp": ts - pd.Timedelta(hours=24),
            "dispatch_timestamp": ts,
            "target": ["target_pv_power_t_plus_24h"] * periods,
            "price_eur_mwh": np.linspace(20.0, 50.0, periods),
            "load_mw": np.full(periods, 1.0),
            "forecast_pv_kw": np.linspace(0.1, 0.9, periods),
            "actual_pv_kw": np.linspace(0.1, 0.8, periods),
            "soc_start": np.full(periods, 0.5),
            "soc_end": np.full(periods, 0.5),
            "planned_charge_kw": np.where(np.arange(periods) % 3 == 0, 0.2, 0.0),
            "planned_discharge_kw": np.where(np.arange(periods) % 5 == 0, 0.15, 0.0),
            "actual_charge_kw": np.zeros(periods),
            "actual_discharge_kw": np.zeros(periods),
            "planned_net_export_kw": np.zeros(periods),
            "actual_net_export_kw": np.zeros(periods),
            "no_storage_export_kw": np.zeros(periods),
            "curtailed_kw": np.zeros(periods),
            "shortfall_kw": np.zeros(periods),
            "surplus_kw": np.zeros(periods),
            "planned_revenue_eur": np.zeros(periods),
            "storage_revenue_eur": np.zeros(periods),
            "no_storage_revenue_eur": np.zeros(periods),
            "incremental_revenue_eur": np.zeros(periods),
        }
    )


def test_common_dispatch_window_restricts_all_prediction_sources() -> None:
    """Every dispatch source must be evaluated on the same settled timestamps."""
    feature_frame = _feature_frame("2022-01-02", periods=60)
    first = _prediction_frame("2022-01-01 00:00", periods=10)
    second = _prediction_frame("2022-01-01 03:00", periods=10)

    trimmed, metadata = _restrict_candidates_to_common_dispatch_window(
        [
            {"label": "first", "predictions": first},
            {"label": "second", "predictions": second},
        ],
        feature_frame,
        horizon_hours=24,
    )

    assert metadata["common_window_rows"] == 7
    assert {len(item["predictions"]) for item in trimmed} == {7}
    dispatch_sets = {
        tuple(item["predictions"]["timestamp"] + pd.Timedelta(hours=24))
        for item in trimmed
    }
    assert len(dispatch_sets) == 1


def test_policy_dataset_filters_requested_window_and_reports_gap_drops() -> None:
    """Stage20B policy rows must stay inside the requested distillation window."""
    stage12 = _stage12_policy_rows("2021-01-01", periods=60)

    X, y, names, policy_frame = _build_policy_dataset(
        stage12,
        _feature_frame("2021-01-01", periods=60),
        policy_start="2021-01-01",
        policy_end="2021-01-02",
        require_full_policy_window=True,
    )

    assert X.shape[1] == 77
    assert y.shape[1] == 2
    assert names[0] == "soc_start"
    assert policy_frame["dispatch_ts"].min() >= pd.Timestamp("2021-01-01", tz="UTC")
    assert policy_frame["dispatch_ts"].max() <= pd.Timestamp("2021-01-02 23:00", tz="UTC")
    assert policy_frame.attrs["dropped_gap_count"] == 0


def test_policy_dataset_strict_window_rejects_missing_stage12_rows() -> None:
    """Stage20B must fail loudly instead of silently shortening the full-year window."""
    stage12 = _stage12_policy_rows("2021-01-01", periods=30)

    with pytest.raises(ValueError, match="end before the requested policy window"):
        _build_policy_dataset(
            stage12,
            _feature_frame("2021-01-01", periods=30),
            policy_start="2021-01-01",
            policy_end="2021-01-03",
            require_full_policy_window=True,
        )


def test_postprocess_actions_uses_forecast_and_soc_bounds() -> None:
    """Planned actions must respect forecast PV, export headroom and SOC limits."""
    charge, discharge = _postprocess_actions(
        np.array([2.0, 0.1]),
        np.array([0.5, 2.0]),
        np.array([0.89, 0.11]),
        np.array([0.2, 1.0]),
        capacity_kw=1.12,
        max_charge_kw=1.0,
        max_discharge_kw=1.0,
        capacity_kwh=2.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
        soc_min=0.1,
        soc_max=0.9,
    )

    assert charge[0] <= 0.2
    assert discharge[0] == 0.0
    assert charge[1] == 0.0
    assert discharge[1] <= (0.11 - 0.1) * 2.0 * 0.95 + 1e-12


def test_direction_metrics_include_majority_baseline() -> None:
    """Imbalanced dispatch labels require a majority-class baseline."""
    labels = np.array([1, 1, 1, 1, 0, 0, 2])
    preds = np.array([1, 0, 0, 1, 0, 2, 2])

    metrics = _direction_metrics(preds, labels)

    assert metrics["direction_random_baseline"] == 1 / 3
    assert metrics["direction_majority_baseline"] == 4 / 7
    assert metrics["direction_confusion_matrix"][1][1] == 2


def test_replay_policy_enforces_stage12_physical_settlement() -> None:
    """Strict replay must not charge more than actual PV or exceed SOC/export limits."""

    class ChargeOnlyPolicy(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return torch.tensor([[2.0, 0.0]] * len(x), dtype=torch.float32)

    policy_frame = pd.DataFrame(
        {
            "forecast_timestamp": pd.date_range("2022-01-01", periods=2, freq="h", tz="UTC"),
            "dispatch_timestamp": pd.date_range("2022-01-02", periods=2, freq="h", tz="UTC"),
            "target": ["target_pv_power_t_plus_24h"] * 2,
            "price_eur_mwh": [30.0, 35.0],
            "load_mw": [1.0, 1.0],
            "forecast_pv_kw": [1.0, 1.0],
            "actual_pv_kw": [0.2, 0.1],
            "soc_start": [0.5, 0.6],
            "soc_end": [0.6, 0.7],
            "planned_charge_kw": [0.5, 0.5],
            "planned_discharge_kw": [0.0, 0.0],
            "actual_charge_kw": [0.2, 0.1],
            "actual_discharge_kw": [0.0, 0.0],
            "planned_net_export_kw": [0.5, 0.5],
            "actual_net_export_kw": [0.0, 0.0],
            "no_storage_export_kw": [0.2, 0.1],
            "curtailed_kw": [0.0, 0.0],
            "shortfall_kw": [0.5, 0.5],
            "surplus_kw": [0.0, 0.0],
            "planned_revenue_eur": [0.0, 0.0],
            "storage_revenue_eur": [0.0, 0.0],
            "no_storage_revenue_eur": [0.0, 0.0],
            "incremental_revenue_eur": [0.0, 0.0],
        }
    )

    replay, metrics = _replay_policy(
        ChargeOnlyPolicy(),
        policy_frame,
        np.array([[0.5], [0.6]], dtype=np.float32),
        _storage_config(),
        x_mean=np.array([0.0], dtype=np.float32),
        x_std=np.array([1.0], dtype=np.float32),
        feature_names=["soc_start"],
        test_start_index=0,
        capacity_kw=1.12,
    )

    assert (replay["actual_charge_kw"] <= replay["actual_pv_kw"] + 1e-12).all()
    assert (replay["actual_net_export_kw"] <= 1.12 + 1e-12).all()
    assert replay["soc_end"].between(0.1 - 1e-12, 0.9 + 1e-12).all()
    assert bool(metrics.iloc[0]["energy_balance_error_within_tolerance"])


def test_two_stage_replay_gates_power_by_predicted_direction() -> None:
    """Two-stage policy must not emit charge power when it predicts discharge."""

    class DischargeOnlyPolicy(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            logits = torch.tensor([[0.0, -5.0, 5.0]] * len(x), dtype=torch.float32)
            charge_norm = torch.full((len(x),), 0.9, dtype=torch.float32)
            discharge_norm = torch.full((len(x),), 0.8, dtype=torch.float32)
            return logits, charge_norm, discharge_norm

    policy_frame = pd.DataFrame(
        {
            "forecast_timestamp": pd.date_range("2022-01-01", periods=2, freq="h", tz="UTC"),
            "dispatch_timestamp": pd.date_range("2022-01-02", periods=2, freq="h", tz="UTC"),
            "target": ["target_pv_power_t_plus_24h"] * 2,
            "price_eur_mwh": [80.0, 85.0],
            "load_mw": [1.0, 1.0],
            "forecast_pv_kw": [0.2, 0.2],
            "actual_pv_kw": [0.2, 0.2],
            "soc_start": [0.5, 0.5],
            "soc_end": [0.45, 0.4],
            "planned_charge_kw": [0.0, 0.0],
            "planned_discharge_kw": [0.5, 0.5],
            "actual_charge_kw": [0.0, 0.0],
            "actual_discharge_kw": [0.5, 0.5],
            "planned_net_export_kw": [0.7, 0.7],
            "actual_net_export_kw": [0.7, 0.7],
            "no_storage_export_kw": [0.2, 0.2],
            "curtailed_kw": [0.0, 0.0],
            "shortfall_kw": [0.0, 0.0],
            "surplus_kw": [0.0, 0.0],
            "planned_revenue_eur": [0.0, 0.0],
            "storage_revenue_eur": [0.0, 0.0],
            "no_storage_revenue_eur": [0.0, 0.0],
            "incremental_revenue_eur": [0.0, 0.0],
        }
    )

    replay, metrics = _replay_policy(
        DischargeOnlyPolicy(),
        policy_frame,
        np.array([[0.5], [0.5]], dtype=np.float32),
        _storage_config(),
        x_mean=np.array([0.0], dtype=np.float32),
        x_std=np.array([1.0], dtype=np.float32),
        feature_names=["soc_start"],
        test_start_index=0,
        capacity_kw=1.12,
        policy_mode="two-stage",
    )

    assert (replay["planned_charge_kw"] == 0.0).all()
    assert replay["planned_discharge_kw"].iloc[0] > 0.0
    assert (replay["predicted_direction"] == 2).all()
    assert bool(metrics.iloc[0]["no_simultaneous_charge_discharge"])
