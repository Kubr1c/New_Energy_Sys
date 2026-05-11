from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from new_energy_sys.stage21_rawhide_weather_dispatch import validate_market_price_scenarios


SCENARIO_PATH = Path("configs/market_price_scenarios.stage21a.json")


def _load_config() -> dict:
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def test_stage21a_market_price_scenario_config_is_valid() -> None:
    report = validate_market_price_scenarios(_load_config())

    assert report["synthetic_scenario_count"] >= 4
    assert report["real_market_candidate_count"] >= 1
    assert report["all_scenarios_declare_settlement_truth"] is True


def test_stage21a_rejects_short_hourly_synthetic_curve() -> None:
    config = _load_config()
    broken = copy.deepcopy(config)
    broken["synthetic_scenarios"][0]["values_eur_mwh_by_local_hour"] = [30.0] * 23

    with pytest.raises(ValueError, match="must contain 24 hourly prices"):
        validate_market_price_scenarios(broken)


def test_stage21a_rejects_out_of_range_price() -> None:
    config = _load_config()
    broken = copy.deepcopy(config)
    broken["synthetic_scenarios"][0]["values_eur_mwh_by_local_hour"] = [999.0] * 24

    with pytest.raises(ValueError, match="outside configured range"):
        validate_market_price_scenarios(broken)


def test_stage21a_rejects_real_market_candidate_without_node_mapping_flag() -> None:
    config = _load_config()
    broken = copy.deepcopy(config)
    broken["real_market_candidates"][0].pop("requires_node_mapping", None)

    with pytest.raises(ValueError, match="requires_node_mapping"):
        validate_market_price_scenarios(broken)
