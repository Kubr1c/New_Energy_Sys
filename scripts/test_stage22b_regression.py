"""Regression + monotonicity tests for Stage22B."""
from __future__ import annotations

import pandas as pd
from new_energy_sys.stage22_degradation_aware_config import run_stage22b_economic_sensitivity

# Load merged v2 metrics
base = pd.read_csv("data/processed/pvdaq_nsrdb_2020_2022/stage22_rawnhide_base_v2_metrics.csv")
high = pd.read_csv("data/processed/pvdaq_nsrdb_2020_2022/stage22_rawnhide_high_lambda_v2_metrics.csv")
merged = pd.concat([base, high], ignore_index=True)
merged = merged.drop_duplicates(subset="config_id", keep="last")
print(f"Merged: {len(merged)} unique configs")

# ---- Regression: default = original ----
result = run_stage22b_economic_sensitivity(
    merged,
    replacement_costs=[150.0],
    cycle_life_multipliers=[1.0],
    calendar_fade_rates=[0.015],
    discharge_value_eur_per_mwh=[0.0],
    capacity_value_eur_per_kw_year=[0.0],
    fixed_subsidy_eur_per_kwh=[0.0],
    min_soh=0.0,
)
m = result.metrics
mismatches = 0
for _, row in m.iterrows():
    orig_row = merged[merged["config_id"] == row["config_id"]].iloc[0]
    orig_net = float(orig_row["net_incremental_revenue_eur"])
    new_net = float(row["net_incremental_revenue_eur"])
    diff = abs(orig_net - new_net)
    if diff > 0.01:
        print(f"  MISMATCH {row['config_id']}: orig={orig_net:.4f} new={new_net:.4f}")
        mismatches += 1
print(f"Regression: {mismatches} mismatches (expect 0)")

# ---- Monotonicity: cheaper replacement → net should not drop ----
for cost_a, cost_b in [(150.0, 100.0), (100.0, 75.0), (75.0, 50.0)]:
    ra = run_stage22b_economic_sensitivity(
        merged, replacement_costs=[cost_a], cycle_life_multipliers=[1.0],
        calendar_fade_rates=[0.015], discharge_value_eur_per_mwh=[0.0],
        capacity_value_eur_per_kw_year=[0.0], fixed_subsidy_eur_per_kwh=[0.0],
        min_soh=0.0,
    )
    rb = run_stage22b_economic_sensitivity(
        merged, replacement_costs=[cost_b], cycle_life_multipliers=[1.0],
        calendar_fade_rates=[0.015], discharge_value_eur_per_mwh=[0.0],
        capacity_value_eur_per_kw_year=[0.0], fixed_subsidy_eur_per_kwh=[0.0],
        min_soh=0.0,
    )
    ma = ra.metrics.set_index("config_id")["net_incremental_revenue_eur"]
    mb = rb.metrics.set_index("config_id")["net_incremental_revenue_eur"]
    violations = (mb < ma).sum()
    print(f"Replacement cost {cost_a}→{cost_b}: {violations} monotonicity violations (expect 0)")

# ---- Monotonicity: longer life → deg_cost should not rise ----
for lm_a, lm_b in [(1.0, 2.0), (2.0, 3.0)]:
    ra = run_stage22b_economic_sensitivity(
        merged, replacement_costs=[150.0], cycle_life_multipliers=[lm_a],
        calendar_fade_rates=[0.015], discharge_value_eur_per_mwh=[0.0],
        capacity_value_eur_per_kw_year=[0.0], fixed_subsidy_eur_per_kwh=[0.0],
        min_soh=0.0,
    )
    rb = run_stage22b_economic_sensitivity(
        merged, replacement_costs=[150.0], cycle_life_multipliers=[lm_b],
        calendar_fade_rates=[0.015], discharge_value_eur_per_mwh=[0.0],
        capacity_value_eur_per_kw_year=[0.0], fixed_subsidy_eur_per_kwh=[0.0],
        min_soh=0.0,
    )
    ma = ra.metrics.set_index("config_id")["degradation_cost_eur"]
    mb = rb.metrics.set_index("config_id")["degradation_cost_eur"]
    violations = (mb > ma + 0.01).sum()
    print(f"Cycle life {lm_a}→{lm_b}: {violations} deg_cost violations (expect 0)")

# ---- SOH consistency ----
result_full = run_stage22b_economic_sensitivity(
    merged,
    replacement_costs=[150.0],
    cycle_life_multipliers=[1.0],
    calendar_fade_rates=[0.015],
    discharge_value_eur_per_mwh=[0.0],
    capacity_value_eur_per_kw_year=[0.0],
    fixed_subsidy_eur_per_kwh=[0.0],
    min_soh=0.0,
)
m = result_full.metrics
soh_diffs = abs(
    m.set_index("config_id")["soh_end"] - merged.set_index("config_id")["soh_end"]
)
max_diff = soh_diffs.max()
print(f"SOH max diff: {max_diff:.6f} (expect < 0.001)")

print("\nAll checks complete.")
