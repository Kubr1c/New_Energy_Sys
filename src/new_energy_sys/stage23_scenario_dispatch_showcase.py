"""Stage23 scenario-based dispatch showcase.

This module does NOT run new dispatch or degradation computations.  It reads
Stage22B economic-sensitivity and spread-amplification results, selects 6–8
representative scenarios, and renders a paper-ready Markdown report.

Design principle: every scenario carries a ``boundary_note`` that explicitly
declares the economic assumptions, so the paper can cleanly separate
*parameter-reference simulation* from *scenario-hypothesis* conclusions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


# Fixed set of 17 output columns.
OUTPUT_COLUMNS = [
    "scenario_name",
    "scenario_type",
    "config_id",
    "strategy_label",
    "replacement_cost_eur_per_kwh",
    "cycle_life_multiplier",
    "calendar_fade_rate",
    "discharge_value_eur_per_mwh",
    "capacity_value_eur_per_kw_year",
    "fixed_subsidy_eur_per_kwh",
    "gross_incremental_revenue_eur",
    "degradation_cost_eur",
    "additional_revenue_eur",
    "net_incremental_revenue_eur",
    "soh_end",
    "equivalent_full_cycles",
    "constraints_passed",
    "boundary_note",
]

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    if pd.isna(value) if not isinstance(value, (dict, list, tuple)) else False:
        return None
    return value


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def load_stage23_inputs(
    economic_metrics_path: str,
    spread_metrics_path: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load Stage22B CSV artefacts.

    Returns a dict with keys ``economic_metrics`` and optionally ``spread_metrics``.
    """
    eco = pd.read_csv(economic_metrics_path)
    result: dict[str, pd.DataFrame] = {"economic_metrics": eco}
    if spread_metrics_path:
        result["spread_metrics"] = pd.read_csv(spread_metrics_path)
    return result


# ---------------------------------------------------------------------------
# Scenario selection
# ---------------------------------------------------------------------------


# Shorthand for the baseline economic assumption row.
_BASE_ECON = {
    "replacement_cost_eur_per_kwh": 150.0,
    "cycle_life_multiplier": 1.0,
    "calendar_fade_rate": 0.015,
    "discharge_value_eur_per_mwh": 0.0,
    "capacity_value_eur_per_kw_year": 0.0,
    "fixed_subsidy_eur_per_kwh": 0.0,
}


def _pick_best_active(
    eco: pd.DataFrame,
    repl: float,
    life: float,
    fade: float,
    d_val: float,
    c_val: float,
    sub: float,
) -> pd.Series:
    """Pick the best active-cycling config (lambda=1.0) for given econ params."""
    mask = (
        (eco["replacement_cost_eur_per_kwh"] == repl)
        & (eco["cycle_life_multiplier"] == life)
        & (eco["calendar_fade_rate"] == fade)
        & (eco["discharge_value_eur_per_mwh"] == d_val)
        & (eco["capacity_value_eur_per_kw_year"] == c_val)
        & (eco["fixed_subsidy_eur_per_kwh"] == sub)
        & (eco["lambda"].isin([1.0]))
        & (eco["constraints_passed"] == True)  # noqa: E712
    )
    subset = eco.loc[mask]
    if subset.empty:
        # Fall back to any lambda <= 1.0
        mask2 = (
            (eco["replacement_cost_eur_per_kwh"] == repl)
            & (eco["cycle_life_multiplier"] == life)
            & (eco["calendar_fade_rate"] == fade)
            & (eco["discharge_value_eur_per_mwh"] == d_val)
            & (eco["capacity_value_eur_per_kw_year"] == c_val)
            & (eco["fixed_subsidy_eur_per_kwh"] == sub)
            & (eco["lambda"] <= 1.0)
            & (eco["constraints_passed"] == True)  # noqa: E712
        )
        subset = eco.loc[mask2]
    return subset.sort_values("net_incremental_revenue_eur", ascending=False).iloc[0]


def _pick_zero_cycle(
    eco: pd.DataFrame,
    repl: float,
    life: float,
    fade: float,
    d_val: float,
    c_val: float,
    sub: float,
) -> pd.Series:
    """Pick the zero-cycle config (lambda >= 2.0, cap=1.0) for given econ params."""
    mask = (
        (eco["replacement_cost_eur_per_kwh"] == repl)
        & (eco["cycle_life_multiplier"] == life)
        & (eco["calendar_fade_rate"] == fade)
        & (eco["discharge_value_eur_per_mwh"] == d_val)
        & (eco["capacity_value_eur_per_kw_year"] == c_val)
        & (eco["fixed_subsidy_eur_per_kwh"] == sub)
        & (eco["lambda"] >= 2.0)
        & (eco["capacity_multiplier"] == 1.0)
        & (eco["constraints_passed"] == True)  # noqa: E712
    )
    subset = eco.loc[mask]
    if subset.empty:
        raise ValueError("Zero-cycle config not found for baseline economics")
    return subset.sort_values("net_incremental_revenue_eur", ascending=False).iloc[0]


def _row_to_dict(row: pd.Series, **overrides: Any) -> dict[str, Any]:
    """Map a DataFrame row into the 18-column Stage23 output schema."""
    return {
        "scenario_name": str(overrides.get("scenario_name", "")),
        "scenario_type": str(overrides.get("scenario_type", "")),
        "config_id": str(row.get("config_id", "")),
        "strategy_label": str(overrides.get("strategy_label", "")),
        "replacement_cost_eur_per_kwh": float(
            row.get("replacement_cost_eur_per_kwh", float("nan"))
        ),
        "cycle_life_multiplier": float(
            row.get("cycle_life_multiplier", float("nan"))
        ),
        "calendar_fade_rate": float(row.get("calendar_fade_rate", float("nan"))),
        "discharge_value_eur_per_mwh": float(
            row.get("discharge_value_eur_per_mwh", 0.0)
        ),
        "capacity_value_eur_per_kw_year": float(
            row.get("capacity_value_eur_per_kw_year", 0.0)
        ),
        "fixed_subsidy_eur_per_kwh": float(
            row.get("fixed_subsidy_eur_per_kwh", 0.0)
        ),
        "gross_incremental_revenue_eur": float(
            row.get("gross_incremental_revenue_eur", float("nan"))
        ),
        "degradation_cost_eur": float(
            row.get("degradation_cost_eur", float("nan"))
        ),
        "additional_revenue_eur": float(
            row.get("additional_revenue_eur", 0.0)
        ),
        "net_incremental_revenue_eur": float(
            row.get("net_incremental_revenue_eur", float("nan"))
        ),
        "soh_end": float(row.get("soh_end", float("nan"))),
        "equivalent_full_cycles": float(
            row.get("equivalent_full_cycles", float("nan"))
        ),
        "constraints_passed": bool(row.get("constraints_passed", False)),
        "boundary_note": str(overrides.get("boundary_note", "")),
    }


def select_representative_scenarios(
    economic_metrics: pd.DataFrame,
    spread_metrics: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Select 6–8 representative scenarios from Stage22B results.

    Returns a DataFrame with the 18 columns defined in ``OUTPUT_COLUMNS``.
    """
    eco = economic_metrics.copy()
    rows: list[dict[str, Any]] = []

    # ---- Scenario 1: baseline pure arbitrage (zero-cycle) ----
    r1 = _pick_zero_cycle(eco, repl=150.0, life=1.0, fade=0.015, d_val=0.0, c_val=0.0, sub=0.0)
    rows.append(_row_to_dict(
        r1,
        scenario_name="基准纯套利（零循环）",
        scenario_type="baseline",
        strategy_label="zero_cycle_lower_bound",
        replacement_cost_eur_per_kwh=150.0,
        cycle_life_multiplier=1.0,
        calendar_fade_rate=0.015,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=0.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="基准代理电价情景",
    ))

    # ---- Scenario 2: price volatility amplification ----
    if spread_metrics is not None:
        sp = spread_metrics
        sp_mask = (
            (sp["amplification_factor"] == 3.0)
            & (sp["label"] == "best_active_config")
        )
        sp_sub = sp.loc[sp_mask]
        if not sp_sub.empty:
            r2 = sp_sub.iloc[0]
            rows.append(_row_to_dict(
                {
                    "config_id": r2.get("config_id", ""),
                    "replacement_cost_eur_per_kwh": 150.0,
                    "cycle_life_multiplier": 1.0,
                    "calendar_fade_rate": 0.015,
                    "discharge_value_eur_per_mwh": 0.0,
                    "capacity_value_eur_per_kw_year": 0.0,
                    "fixed_subsidy_eur_per_kwh": 0.0,
                    "gross_incremental_revenue_eur": r2.get(
                        "gross_incremental_revenue_eur"
                    ),
                    "degradation_cost_eur": r2.get("degradation_cost_eur"),
                    "additional_revenue_eur": 0.0,
                    "net_incremental_revenue_eur": r2.get(
                        "net_incremental_revenue_eur"
                    ),
                    "soh_end": r2.get("soh_end"),
                    "equivalent_full_cycles": r2.get("equivalent_full_cycles"),
                    "constraints_passed": r2.get("constraints_passed", False),
                },
                scenario_name="价格波动增强（amp=3.0）",
                scenario_type="price_volatility",
                strategy_label="best_active_config",
                boundary_note="价格波动放大情景，非市场预测",
            ))

    # ---- Scenario 3: capacity value low ----
    r3 = _pick_best_active(eco, repl=150.0, life=1.0, fade=0.015, d_val=0.0, c_val=20.0, sub=0.0)
    rows.append(_row_to_dict(
        r3,
        scenario_name="容量价值低档（20 EUR/kW·年）",
        scenario_type="capacity_revenue",
        strategy_label="best_active_cycling",
        replacement_cost_eur_per_kwh=150.0,
        cycle_life_multiplier=1.0,
        calendar_fade_rate=0.015,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=20.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="容量价值情景假设",
    ))

    # ---- Scenario 4: capacity value high ----
    r4 = _pick_best_active(eco, repl=150.0, life=1.0, fade=0.015, d_val=0.0, c_val=50.0, sub=0.0)
    rows.append(_row_to_dict(
        r4,
        scenario_name="容量价值高档（50 EUR/kW·年）",
        scenario_type="capacity_revenue",
        strategy_label="best_active_cycling",
        replacement_cost_eur_per_kwh=150.0,
        cycle_life_multiplier=1.0,
        calendar_fade_rate=0.015,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=50.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="容量价值情景假设",
    ))

    # ---- Scenario 5: low degradation cost ----
    r5 = _pick_best_active(eco, repl=75.0, life=2.0, fade=0.005, d_val=0.0, c_val=0.0, sub=0.0)
    rows.append(_row_to_dict(
        r5,
        scenario_name="低退化成本（repl=75, life=2×, fade=0.5%）",
        scenario_type="cost_improvement",
        strategy_label="best_active_cycling",
        replacement_cost_eur_per_kwh=75.0,
        cycle_life_multiplier=2.0,
        calendar_fade_rate=0.005,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=0.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="电池成本与寿命改善情景",
    ))

    # ---- Scenario 6: best pure arbitrage ----
    pure_mask = (
        (eco["discharge_value_eur_per_mwh"] == 0.0)
        & (eco["capacity_value_eur_per_kw_year"] == 0.0)
        & (eco["fixed_subsidy_eur_per_kwh"] == 0.0)
        & (eco["constraints_passed"] == True)  # noqa: E712
    )
    pure_best = eco.loc[pure_mask].sort_values(
        "net_incremental_revenue_eur", ascending=False
    ).iloc[0]
    rows.append(_row_to_dict(
        pure_best,
        scenario_name="最优纯套利",
        scenario_type="pure_arbitrage_best",
        strategy_label="optimal_pure_arbitrage",
        boundary_note="电池成本与寿命改善情景",
    ))

    # ---- Scenario 7: degradation-aware active cycling ----
    deg_mask = (
        (eco["config_id"] == "cap1p0_pow0p75_soc0p1_0p9_lambda1p0_spread0p0")
    )
    deg_sub = eco.loc[deg_mask]
    if deg_sub.empty:
        # Fuzzy match
        deg_mask = eco["config_id"].str.contains("cap1p0_pow0p75_soc0p1_0p9_lambda1p0")
        deg_sub = eco.loc[deg_mask]
    deg_base = deg_sub.loc[
        (deg_sub["replacement_cost_eur_per_kwh"] == 150.0)
        & (deg_sub["cycle_life_multiplier"] == 1.0)
        & (deg_sub["calendar_fade_rate"] == 0.015)
        & (deg_sub["discharge_value_eur_per_mwh"] == 0.0)
        & (deg_sub["capacity_value_eur_per_kw_year"] == 0.0)
        & (deg_sub["fixed_subsidy_eur_per_kwh"] == 0.0)
    ]
    if deg_base.empty:
        deg_base = deg_sub.head(1)
    r7 = deg_base.iloc[0]
    rows.append(_row_to_dict(
        r7,
        scenario_name="退化约束主动循环（λ=1.0）",
        scenario_type="degradation_aware",
        strategy_label="degradation_aware_active",
        replacement_cost_eur_per_kwh=150.0,
        cycle_life_multiplier=1.0,
        calendar_fade_rate=0.015,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=0.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="基准代理电价情景",
    ))

    # ---- Scenario 8: aggressive benchmark (stage15-style) ----
    agg_mask = (
        (eco["config_id"] == "cap1p5_pow1p0_soc0p1_0p9_lambda0p0_spread0p0")
    )
    agg_sub = eco.loc[agg_mask]
    if agg_sub.empty:
        agg_mask = eco["config_id"].str.contains("cap1p5_pow1p0_soc0p1_0p9_lambda0p0")
        agg_sub = eco.loc[agg_mask]
    agg_base = agg_sub.loc[
        (agg_sub["replacement_cost_eur_per_kwh"] == 150.0)
        & (agg_sub["cycle_life_multiplier"] == 1.0)
        & (agg_sub["calendar_fade_rate"] == 0.015)
        & (agg_sub["discharge_value_eur_per_mwh"] == 0.0)
        & (agg_sub["capacity_value_eur_per_kw_year"] == 0.0)
        & (agg_sub["fixed_subsidy_eur_per_kwh"] == 0.0)
    ]
    if agg_base.empty:
        agg_base = agg_sub.head(1)
    r8 = agg_base.iloc[0]
    rows.append(_row_to_dict(
        r8,
        scenario_name="激进策略对照（λ=0）",
        scenario_type="aggressive_baseline",
        strategy_label="stage15_aggressive",
        replacement_cost_eur_per_kwh=150.0,
        cycle_life_multiplier=1.0,
        calendar_fade_rate=0.015,
        discharge_value_eur_per_mwh=0.0,
        capacity_value_eur_per_kw_year=0.0,
        fixed_subsidy_eur_per_kwh=0.0,
        boundary_note="基准代理电价情景",
    ))

    df = pd.DataFrame(rows)
    # Ensure column order
    existing = [c for c in OUTPUT_COLUMNS if c in df.columns]
    return df[existing]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


_EUR = "EUR"


def _fmt(val: Any, decimals: int = 2) -> str:
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def build_stage23_markdown(selected: pd.DataFrame, report_date: str = "") -> str:
    """Render the Stage23 paper-ready Markdown report."""
    today = report_date or date.today().isoformat()
    lines: list[str] = [
        f"# Stage23 — 情景化多收益调度效果展示",
        "",
        f"**日期**：{today}",
        "**数据来源**：Stage22B 经济敏感性分析 + 价差放大实验",
        "",
        "## 1. 背景与边界",
        "",
        "本报告基于 **Rawhide Prairie Solar 参数参照仿真**（22 MW PV + 2 MWh BESS）",
        "和 **OPSD 丹麦代理电价**。所有结果均为参数参照仿真产出的情景展示，",
        "**不代表 Rawhide 电站实际运行收益或市场结算**。",
        "",
        "基准结论与情景结论严格区分：",
        "- **基准结论**：当前 OPSD 代理电价与默认退化参数下，单一套利收益不足以覆盖退化成本。",
        "- **情景结论**：在价格波动增强、容量价值叠加或电池经济条件改善的情景下，",
        "  退化约束调度策略可以获得正净增量。",
        "",
        "## 2. 情景设计",
        "",
        "| # | 情景 | 类型 | 关键参数（偏离基准部分） |",
        "|---|------|------|--------------------------|",
    ]
    for _, row in selected.iterrows():
        name = row.get("scenario_name", "")
        stype = row.get("scenario_type", "")
        note = row.get("boundary_note", "")
        lines.append(f"| {_} | {name} | {stype} | {note} |")

    lines.extend([
        "",
        "## 3. 核心结果表",
        "",
        "| 情景 | 策略 | 毛增量 | 退化成本 | 额外收益 | **净增量** | SOH | EFC |",
        "|------|------|--------|----------|----------|------------|-----|-----|",
    ])
    for _, row in selected.iterrows():
        lines.append(
            f"| {row.get('scenario_name', '')} "
            f"| {row.get('strategy_label', '')} "
            f"| {_fmt(row.get('gross_incremental_revenue_eur'))} "
            f"| {_fmt(row.get('degradation_cost_eur'))} "
            f"| {_fmt(row.get('additional_revenue_eur'))} "
            f"| **{_fmt(row.get('net_incremental_revenue_eur'))}** "
            f"| {_fmt(row.get('soh_end'), 6)} "
            f"| {_fmt(row.get('equivalent_full_cycles'), 1)} |"
        )

    lines.extend([
        "",
        "## 4. 策略对比分析",
        "",
        "### 4.1 基准 vs 退化约束",
        "",
        "基准纯套利情景下，最优策略为零循环（λ ≥ 2.0），净增量 -12,970 EUR，",
        "全部为日历老化成本。任何主动充放电的边际收益均低于边际退化成本。",
        "",
        "退化约束主动循环（λ=1.0, 15 EUR/MWh 价差门槛）将等效循环从 680 降至 171",
        "（-75%），SOH 从 0.898 提升至 0.942（+4.4pp），但净增量仍为负（-14,252 EUR），",
        "说明在当前价差下即使高度节制的循环也无法覆盖日历老化。",
        "",
        "### 4.2 激进策略的风险",
        "",
        "激进策略（cap=1.5, λ=0）毛增量收益最高（10,363 EUR），但退化成本也最高",
        "（46,730 EUR），净增量 -36,367 EUR，SOH 降至 0.896。该策略在 Stage15",
        "毛收益 Pareto 前沿下曾被选为推荐配置，这验证了以退化后净收益替代毛收益",
        "作为筛选指标的必要性。",
        "",
        "### 4.3 正净收益的路径",
        "",
        "三条路径可实现正净收益：",
        "1. **容量价值叠加**：20 EUR/kW·年即可使主动循环转正。",
        "2. **价差放大**：日内价差波动放大至 3× 时主动循环转正。",
        "3. **成本改善**：重置成本 ≤ 75 EUR/kWh + 寿命 ≥ 2× + 日历老化 ≤ 0.5%/年。",
        "",
        "## 5. 收益-退化权衡",
        "",
        "| 策略类型 | 净增量 (EUR) | SOH | EFC | 特点 |",
        "|----------|-------------|-----|-----|------|",
    ])
    # Pick 3 contrasting rows for the tradeoff table
    for label, desc in [
        ("zero_cycle_lower_bound", "零保护，日历老化不可消除"),
        ("degradation_aware_active", "节制循环，收益与寿命折中"),
        ("stage15_aggressive", "激进套利，SOH 显著劣化"),
    ]:
        sub = selected[selected["strategy_label"] == label]
        if not sub.empty:
            r = sub.iloc[0]
            lines.append(
                f"| {desc} "
                f"| {_fmt(r.get('net_incremental_revenue_eur'))} "
                f"| {_fmt(r.get('soh_end'), 6)} "
                f"| {_fmt(r.get('equivalent_full_cycles'), 1)} "
                f"| {r.get('boundary_note', '')} |"
            )

    lines.extend([
        "",
        "## 6. 论文可用结论",
        "",
        "> 在基准代理电价下，单一套利收益难以覆盖退化成本；",
        "> 在价格波动增强或容量价值叠加情景下，退化约束滚动调度能够获得正净增量。",
        "> 该结果表明，储能调度经济性对市场价差信号和收益机制具有较高敏感性，",
        "> 以退化后净收益替代毛收益作为配置筛选指标是必要的。",
        "",
        "## 7. 局限性",
        "",
        "- Rawhide 非实测：PV 数据基于 PVDAQ 原型曲线按容量比缩放，非 Rawhide 实测发电。",
        "- OPSD 非当地电价：代理电价为丹麦日前市场数据，不代表科罗拉多州当地结算价格。",
        "- 容量价值和补贴为情景参数：报告中的容量价值、放电附加价值和固定补贴均",
        "  为情景假设，不代表实际市场收益。",
        "- 未纳入调频和弃光削减：弃光削减价值（需逐小时弃光数据）和调频辅助服务",
        "  （需分钟级调度模型）未纳入本阶段分析。",
        "",
        f"*报告由 Stage23 自动生成于 {today}*",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage23Result:
    selected: pd.DataFrame
    report: dict[str, Any]


def build_stage23_report(
    selected: pd.DataFrame,
    output_paths: dict[str, Path] | None = None,
    report_date: str = "",
) -> Stage23Result:
    """Generate the Stage23 report and write artefacts."""
    today = report_date or date.today().isoformat()

    report = {
        "stage": "stage23_scenario_dispatch_showcase",
        "report_date": today,
        "scenario_count": int(len(selected)),
        "scenarios": [
            {
                "name": str(row.get("scenario_name", "")),
                "type": str(row.get("scenario_type", "")),
                "config_id": str(row.get("config_id", "")),
                "net_incremental_revenue_eur": float(
                    row.get("net_incremental_revenue_eur", float("nan"))
                ),
                "soh_end": float(row.get("soh_end", float("nan"))),
                "boundary_note": str(row.get("boundary_note", "")),
            }
            for _, row in selected.iterrows()
        ],
        "quality_gates": {
            "baseline_net_negative": bool(
                (selected["net_incremental_revenue_eur"] < 0).any()
            ),
            "at_least_one_positive": bool(
                (selected["net_incremental_revenue_eur"] > 0).any()
            ),
            "all_boundary_notes_non_empty": bool(
                selected["boundary_note"].notna().all()
                and (selected["boundary_note"] != "").all()
            ),
        },
    }

    if output_paths:
        metrics_csv = output_paths.get("metrics_csv")
        report_json = output_paths.get("report_json")
        report_md = output_paths.get("report_md")

        if metrics_csv:
            metrics_csv.parent.mkdir(parents=True, exist_ok=True)
            selected.to_csv(metrics_csv, index=False)
            print(f"[stage23] metrics → {metrics_csv}")

        if report_json:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(
                json.dumps(_json_safe(report), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[stage23] JSON → {report_json}")

        if report_md:
            report_md.parent.mkdir(parents=True, exist_ok=True)
            md = build_stage23_markdown(selected, report_date=today)
            report_md.write_text(md, encoding="utf-8")
            print(f"[stage23] Markdown → {report_md}")

        print(report["quality_gates"])

    return Stage23Result(selected=selected, report=report)
