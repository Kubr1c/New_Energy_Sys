"""Unit tests for Stage23 scenario selection and reporting."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from new_energy_sys.stage23_scenario_dispatch_showcase import (
    OUTPUT_COLUMNS,
    _json_safe,
    build_stage23_markdown,
    build_stage23_report,
    select_representative_scenarios,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mini_economic_metrics() -> pd.DataFrame:
    """Construct a minimal economic metrics DataFrame for testing."""
    rows = []
    for cid, lam, cap, repl, life, fade, d_val, c_val, sub, gross, deg, add, net, soh, efc in [
        # Baseline zero-cycle
        ("cap1p0_pow0p5_soc0p2_0p8_lambda2p0_spread0p0", 2.0, 1.0, 150, 1.0, 0.015, 0, 0, 0, 0.0, 12970, 0, -12970, 0.957, 0),
        # Baseline active
        ("cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0", 1.0, 1.0, 150, 1.0, 0.015, 0, 0, 0, 3017, 17268, 0, -14252, 0.942, 171),
        # Aggressive
        ("cap1p5_pow1p0_soc0p1_0p9_lambda0p0_spread0p0", 0.0, 1.5, 150, 1.0, 0.015, 0, 0, 0, 10363, 46730, 0, -36367, 0.896, 693),
        # Capacity value 20 (two configs)
        ("cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0", 1.0, 1.0, 150, 1.0, 0.015, 0, 20, 0, 3017, 17268, 57600, 43348, 0.942, 171),
        ("cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0", 1.0, 1.0, 150, 1.0, 0.015, 0, 50, 0, 3017, 17268, 144000, 129748, 0.942, 171),
        # Low degradation
        ("cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0", 1.0, 1.0, 75, 2.0, 0.005, 0, 0, 0, 3017, 4317, 0, -1300, 0.985, 171),
        # Pure arbitrage best
        ("cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0", 1.0, 1.0, 50, 3.0, 0.0, 0, 0, 0, 3017, 2878, 0, 139, 0.991, 171),
    ]:
        rows.append({
            "config_id": cid,
            "lambda": lam,
            "capacity_multiplier": cap,
            "replacement_cost_eur_per_kwh": repl,
            "cycle_life_multiplier": life,
            "calendar_fade_rate": fade,
            "discharge_value_eur_per_mwh": d_val,
            "capacity_value_eur_per_kw_year": c_val,
            "fixed_subsidy_eur_per_kwh": sub,
            "gross_incremental_revenue_eur": gross,
            "degradation_cost_eur": deg,
            "additional_revenue_eur": add,
            "net_incremental_revenue_eur": net,
            "soh_end": soh,
            "equivalent_full_cycles": efc,
            "max_cycle_depth_dod": 0.0,
            "constraints_passed": True,
            "power_multiplier": 0.75,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSelectRepresentativeScenarios:
    """Tests for scenario selection logic."""

    def test_select_returns_expected_count(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        # Without spread metrics, expect 7 scenarios (all except #2)
        assert 6 <= len(selected) <= 8, f"Expected 6–8 scenarios, got {len(selected)}"

    def test_select_with_spread_returns_more(self):
        eco = _make_mini_economic_metrics()
        spread = pd.DataFrame([{
            "config_id": "best_active_config_amp3",
            "label": "best_active_config",
            "amplification_factor": 3.0,
            "gross_incremental_revenue_eur": 15000.0,
            "degradation_cost_eur": 11793.0,
            "net_incremental_revenue_eur": 3207.0,
            "soh_end": 0.898,
            "equivalent_full_cycles": 500.0,
            "constraints_passed": True,
        }])
        selected = select_representative_scenarios(eco, spread_metrics=spread)
        assert len(selected) >= 7

    def test_boundary_note_not_empty(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        for _, row in selected.iterrows():
            note = str(row.get("boundary_note", ""))
            assert note, f"Empty boundary_note for {row.get('scenario_name')}"
            assert "实测收益" not in note, f"Forbidden phrase in boundary_note: {note}"
            assert "真实结算" not in note, f"Forbidden phrase in boundary_note: {note}"

    def test_required_columns_present(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        required = [
            "gross_incremental_revenue_eur",
            "degradation_cost_eur",
            "net_incremental_revenue_eur",
            "soh_end",
            "equivalent_full_cycles",
        ]
        for col in required:
            assert col in selected.columns, f"Missing required column: {col}"

    def test_baseline_net_negative(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        baseline_rows = selected[selected["scenario_type"] == "baseline"]
        assert len(baseline_rows) > 0, "No baseline scenario found"
        for _, row in baseline_rows.iterrows():
            assert float(row["net_incremental_revenue_eur"]) < 0, (
                f"Baseline scenario {row['scenario_name']} should have net<0"
            )

    def test_positive_scenarios_have_non_baseline_params(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        pos = selected[selected["net_incremental_revenue_eur"] > 0]
        for _, row in pos.iterrows():
            is_non_baseline = (
                float(row.get("capacity_value_eur_per_kw_year", 0)) > 0
                or float(row.get("fixed_subsidy_eur_per_kwh", 0)) > 0
                or float(row.get("calendar_fade_rate", 0.015)) < 0.015
                or float(row.get("replacement_cost_eur_per_kwh", 150)) < 150
                or float(row.get("cycle_life_multiplier", 1.0)) > 1.0
            )
            assert is_non_baseline, (
                f"Positive scenario {row['scenario_name']} has no non-baseline parameter"
            )


class TestReportGeneration:
    """Tests for report generation."""

    def test_markdown_contains_required_sections(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        md = build_stage23_markdown(selected, report_date="2026-05-11")
        required = [
            "背景与边界",
            "情景设计",
            "核心结果表",
            "策略对比分析",
            "收益-退化权衡",
            "论文可用结论",
            "局限性",
        ]
        for section in required:
            assert section in md, f"Missing section: {section}"

    def test_main_table_not_too_large(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        assert len(selected) <= 10, f"Main table has {len(selected)} rows, > 10"

    def test_json_quality_gates(self):
        eco = _make_mini_economic_metrics()
        selected = select_representative_scenarios(eco, spread_metrics=None)
        with TemporaryDirectory() as td:
            p = Path(td)
            result = build_stage23_report(
                selected,
                output_paths={
                    "metrics_csv": p / "metrics.csv",
                    "report_json": p / "report.json",
                    "report_md": p / "report.md",
                },
                report_date="2026-05-11",
            )
            gates = result.report.get("quality_gates", {})
            assert gates.get("baseline_net_negative"), "baseline_net_negative should be True"
            assert gates.get("at_least_one_positive"), "at_least_one_positive should be True"
            assert gates.get("all_boundary_notes_non_empty"), "all_boundary_notes should be non-empty"
            assert (p / "metrics.csv").exists()
            assert (p / "report.json").exists()
            assert (p / "report.md").exists()
