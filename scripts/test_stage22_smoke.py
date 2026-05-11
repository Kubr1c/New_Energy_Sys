"""Quick smoke tests for Stage22 module."""
from __future__ import annotations

from new_energy_sys.stage22_degradation_aware_config import (
    _build_stage22_grid,
    _fmt_mult,
)


def test_grid_size():
    """Default grid should produce 4*4*4*4*4 = 1024 configs."""
    base = {
        "capacity_kwh": 2000.0,
        "max_charge_kw": 1000.0,
        "max_discharge_kw": 1000.0,
        "soc_initial": 0.5,
        "soc_min": 0.1,
        "soc_max": 0.9,
        "cycle_cost_eur_per_kwh": 0.002,
    }
    grid = _build_stage22_grid(base)
    assert len(grid) == 1024, f"Expected 1024, got {len(grid)}"
    print(f"PASS test_grid_size: {len(grid)} configs")


def test_config_id_format():
    """Sample config_id should match the expected pattern."""
    base = {
        "capacity_kwh": 2000.0,
        "max_charge_kw": 1000.0,
        "max_discharge_kw": 1000.0,
        "soc_initial": 0.5,
        "soc_min": 0.1,
        "soc_max": 0.9,
        "cycle_cost_eur_per_kwh": 0.002,
    }
    grid = _build_stage22_grid(base)
    sample = grid[0]
    cid = sample["config_id"]
    # Pattern: capX_powX_socX_X_lambdaX_spreadX
    parts = cid.split("_")
    assert parts[0].startswith("cap"), f"Expected cap prefix, got {cid}"
    assert any(p.startswith("pow") for p in parts), f"Expected pow, got {cid}"
    assert any(p.startswith("soc") for p in parts), f"Expected soc, got {cid}"
    assert any(p.startswith("lambda") for p in parts), f"Expected lambda, got {cid}"
    assert any(p.startswith("spread") for p in parts), f"Expected spread, got {cid}"
    print(f"PASS test_config_id_format: {cid}")


def test_effective_cycle_cost():
    """Lambda=2.0 should double the effective cycle cost."""
    base = {
        "capacity_kwh": 2000.0,
        "max_charge_kw": 1000.0,
        "max_discharge_kw": 1000.0,
        "soc_initial": 0.5,
        "soc_min": 0.1,
        "soc_max": 0.9,
        "cycle_cost_eur_per_kwh": 0.002,
    }
    grid = _build_stage22_grid(base)
    for item in grid:
        expected = item["lambda"] * 0.002
        assert abs(item["effective_cycle_cost_eur_per_kwh"] - expected) < 1e-12, (
            f"lambda={item['lambda']}, expected={expected}, "
            f"got={item['effective_cycle_cost_eur_per_kwh']}"
        )
    print("PASS test_effective_cycle_cost")


def test_soc_clamping():
    """SOC initial should be clamped into the new range."""
    base = {
        "capacity_kwh": 2000.0,
        "max_charge_kw": 1000.0,
        "max_discharge_kw": 1000.0,
        "soc_initial": 0.5,
        "soc_min": 0.1,
        "soc_max": 0.9,
        "cycle_cost_eur_per_kwh": 0.002,
    }
    grid = _build_stage22_grid(base)
    for item in grid:
        sc = item["storage_config"]
        assert sc["soc_min"] <= sc["soc_initial"] <= sc["soc_max"], (
            f"SOC initial {sc['soc_initial']} not in "
            f"[{sc['soc_min']}, {sc['soc_max']}] for {item['config_id']}"
        )
    print("PASS test_soc_clamping")


def test_fmt_mult():
    """Format helper should produce expected tokens."""
    assert _fmt_mult(1.0) == "1p0"
    assert _fmt_mult(1.25) == "1p25"
    assert _fmt_mult(0.5) == "0p5"
    assert _fmt_mult(2.0) == "2p0"
    print("PASS test_fmt_mult")


if __name__ == "__main__":
    test_grid_size()
    test_config_id_format()
    test_effective_cycle_cost()
    test_soc_clamping()
    test_fmt_mult()
    print("\nAll smoke tests passed.")
