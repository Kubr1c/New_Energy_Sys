"""Analyze critical boundary for positive net in Stage22B results."""
import pandas as pd

m = pd.read_csv("data/processed/pvdaq_nsrdb_2020_2022/stage22b_economic_sensitivity_metrics.csv")

# Without subsidy
no_sub = m[m["fixed_subsidy_eur_per_kwh"] == 0]
print("=== Without subsidy ===")
print(f"Total rows: {len(no_sub)}")
pos = no_sub[no_sub["net_incremental_revenue_eur"] > 0]
print(f"Rows with net>0: {len(pos)}")
if len(pos) > 0:
    best = pos.nlargest(3, "net_incremental_revenue_eur")
    for _, r in best.iterrows():
        print(f"  repl={r['replacement_cost_eur_per_kwh']:.0f} life={r['cycle_life_multiplier']:.0f}x "
              f"fade={r['calendar_fade_rate']:.3f} d_val={r['discharge_value_eur_per_mwh']:.0f} "
              f"c_val={r['capacity_value_eur_per_kw_year']:.0f} net={r['net_incremental_revenue_eur']:.2f}")

# No subsidy, no extra revenue
baseline = m[(m["fixed_subsidy_eur_per_kwh"] == 0) & (m["discharge_value_eur_per_mwh"] == 0) & (m["capacity_value_eur_per_kw_year"] == 0)]
print(f"\n=== Pure arbitrage (no subsidy, no extra revenue) ===")
print(f"Total rows: {len(baseline)}")
pos2 = baseline[baseline["net_incremental_revenue_eur"] > 0]
print(f"Rows with net>0: {len(pos2)}")
if len(pos2) > 0:
    best2 = pos2.nlargest(5, "net_incremental_revenue_eur")
    for _, r in best2.iterrows():
        print(f"  repl={r['replacement_cost_eur_per_kwh']:.0f} life={r['cycle_life_multiplier']:.0f}x "
              f"fade={r['calendar_fade_rate']:.3f} net={r['net_incremental_revenue_eur']:.2f}")
else:
    closest = baseline.nlargest(5, "net_incremental_revenue_eur")
    print("Top 5 closest to zero:")
    for _, r in closest.iterrows():
        print(f"  repl={r['replacement_cost_eur_per_kwh']:.0f} life={r['cycle_life_multiplier']:.0f}x "
              f"fade={r['calendar_fade_rate']:.3f} net={r['net_incremental_revenue_eur']:.2f}")

# Minimal conditions: find the cheapest config that first gets positive
print("\n=== Minimal conditions for net>0 (no subsidy) ===")
for repl in [150, 100, 75, 50]:
    for life in [1.0, 2.0, 3.0]:
        for fade in [0.015, 0.01, 0.005, 0.0]:
            for d_val in [0, 10, 20]:
                for c_val in [0, 20, 50]:
                    subset = m[(m["replacement_cost_eur_per_kwh"] == repl) &
                               (m["cycle_life_multiplier"] == life) &
                               (m["calendar_fade_rate"] == fade) &
                               (m["discharge_value_eur_per_mwh"] == d_val) &
                               (m["capacity_value_eur_per_kw_year"] == c_val) &
                               (m["fixed_subsidy_eur_per_kwh"] == 0)]
                    if len(subset) == 0:
                        continue
                    if (subset["net_incremental_revenue_eur"] > 0).any():
                        best_row = subset.nlargest(1, "net_incremental_revenue_eur").iloc[0]
                        print(f"  repl={repl} life={life}x fade={fade} d_val={d_val} c_val={c_val} "
                              f"net={best_row['net_incremental_revenue_eur']:.2f} "
                              f"phys={best_row['config_id']}")
                        break  # stop at first fade rate that works
                else:
                    continue
                break  # stop at first d_val that works
            else:
                continue
            break  # stop at first c_val that works
        else:
            continue
        break
    else:
        continue
    break
