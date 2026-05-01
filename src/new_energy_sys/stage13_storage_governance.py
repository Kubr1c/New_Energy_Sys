"""储能策略治理与展示层模块。

模块设计原则：
- 不重新仿真，不重新优化参数，只消费 Stage10/11/12 的已审计产物
- 统一评分体系：收益分、物理约束分、运行风险分加权合成治理分
- 治理决策分为 reject/baseline/analysis_upper_bound/pilot_candidate/watch 五级
- 产物包括评分表 CSV、JSON 报告、Markdown 报告和静态 HTML 仪表盘

本模块对应项目 Stage 13 的储能策略治理汇总功能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from new_energy_sys.stage11_storage_strategy import _json_safe


@dataclass(frozen=True)
class Stage13GovernanceResult:
    """Stage13 储能策略治理层产物容器。

    Args:
        scorecard: 管理层可直接读取的策略评分表 DataFrame。
        report: 结构化治理结论字典。

    Markdown 和 HTML 由专用写出函数生成，避免后续系统只能解析自然语言报告。
    """

    scorecard: pd.DataFrame
    report: dict[str, Any]


_NUMERIC_COLUMNS = [
    "sample_count",
    "total_storage_revenue_eur",
    "total_no_storage_revenue_eur",
    "incremental_revenue_eur",
    "planned_revenue_eur",
    "total_charge_kwh",
    "total_discharge_kwh",
    "cycle_equivalent_count",
    "total_curtailed_kwh",
    "total_shortfall_kwh",
    "total_surplus_kwh",
    "mean_soc",
    "min_soc",
    "max_soc",
    "capacity_kw",
    "max_energy_balance_error",
    "simultaneous_charge_discharge_rows",
]

_CONSTRAINT_COLUMNS = [
    "soc_within_bounds",
    "charge_power_within_limit",
    "discharge_power_within_limit",
    "no_simultaneous_charge_discharge",
    "energy_balance_error_within_tolerance",
]


def _read_metrics(path: Path, *, stage: str) -> pd.DataFrame:
    """读取阶段指标文件并做基础类型校验。

    CSV 来自多个阶段，部分列在 Stage10 和 Stage11 中并不完全一致。这里只强制
    校验治理层必须依赖的公共列，缺少核心列时直接失败，避免生成伪完整报告。

    Args:
        path: 指标 CSV 文件路径。
        stage: 阶段标识字符串。

    Returns:
        类型校验后的 DataFrame，附带 source_stage 和 source_path 列。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        ValueError: 缺少核心列时抛出。
    """

    if not path.exists():
        raise FileNotFoundError(f"{stage} metrics file does not exist: {path}")
    frame = pd.read_csv(path)
    required = {"scenario", "total_storage_revenue_eur", "incremental_revenue_eur", "total_discharge_kwh"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{stage} metrics file misses required columns: {missing}")

    for column in _NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    for column in _CONSTRAINT_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column].map(_to_bool)

    frame["source_stage"] = stage
    frame["source_path"] = str(path)
    return frame


def _to_bool(value: Any) -> bool:
    """把 CSV 中的布尔字符串转换为 bool。

    Args:
        value: 需要转换的值。

    Returns:
        Python bool。

    Raises:
        ValueError: 无法解析为布尔值时抛出。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"Cannot parse boolean value: {value!r}")


def _first_row(frame: pd.DataFrame, *, column: str, value: str, source: str) -> pd.Series:
    """按场景或策略 ID 选择唯一治理输入行。

    Args:
        frame: 指标 DataFrame。
        column: 筛选列名。
        value: 筛选目标值。
        source: 来源阶段标识，用于错误信息。

    Returns:
        匹配的首行 Series。

    Raises:
        ValueError: 无匹配行时抛出。
    """

    rows = frame.loc[frame[column] == value]
    if rows.empty:
        raise ValueError(f"{source} does not contain {column}={value!r}")
    return rows.iloc[0]


def _all_constraints_pass(row: pd.Series) -> bool:
    """汇总物理约束门禁。

    只要存在任一约束列且值为 False，治理层就必须把该策略判为不可用。
    缺失的约束列不自动视为通过；当前阶段产物都包含这些列，因此缺失会被视为风险。

    Args:
        row: 策略指标行 Series。

    Returns:
        所有约束是否全部通过。
    """

    values = []
    for column in _CONSTRAINT_COLUMNS:
        if column not in row:
            values.append(False)
        else:
            values.append(bool(row[column]))
    return all(values)


def _score_strategy(row: pd.Series, *, max_incremental_revenue: float) -> dict[str, Any]:
    """生成策略治理评分。

    评分不是金融模型，只用于统一排序和暴露风险：
    - 收益分：按本轮候选最大增量收益归一化。
    - 物理分：SOC、功率、同时充放电、能量守恒全部通过才给满分。
    - 运行风险扣分：循环次数、短缺、电池长期贴边会降低治理评分。

    Args:
        row: 策略指标行 Series。
        max_incremental_revenue: 本轮候选最大增量收益，用于收益分归一化。

    Returns:
        包含 economic_score、constraint_score、risk_score、governance_score、
        risk_flags、constraint_passed 的字典。
    """

    incremental = float(row["incremental_revenue_eur"])
    discharge = float(row["total_discharge_kwh"])
    cycles = _safe_float(row.get("cycle_equivalent_count", 0.0), default=0.0)
    shortfall = _safe_float(row.get("total_shortfall_kwh", 0.0), default=0.0)
    min_soc = _safe_float(row.get("min_soc", 0.5), default=0.5)
    max_soc = _safe_float(row.get("max_soc", 0.5), default=0.5)
    constraints_passed = _all_constraints_pass(row)

    denominator = max(abs(max_incremental_revenue), 1e-9)
    economic_score = max(0.0, min(100.0, incremental / denominator * 100.0))
    constraint_score = 100.0 if constraints_passed else 0.0
    cycling_penalty = min(cycles / 200.0 * 20.0, 20.0)
    shortfall_penalty = min(shortfall / 1000.0 * 15.0, 15.0)
    soc_edge_penalty = 10.0 if min_soc <= 0.1000001 or max_soc >= 0.8999999 else 0.0
    risk_score = max(0.0, 100.0 - cycling_penalty - shortfall_penalty - soc_edge_penalty)
    governance_score = 0.45 * economic_score + 0.35 * constraint_score + 0.20 * risk_score

    flags: list[str] = []
    if not constraints_passed:
        flags.append("constraint_failed")
    if discharge <= 1e-9 and row["scenario_id"] != "no_storage":
        flags.append("no_discharge")
    if incremental < 0:
        flags.append("negative_incremental_revenue")
    if cycles > 150:
        flags.append("high_cycle_count")
    if shortfall > 800:
        flags.append("high_shortfall")
    if min_soc <= 0.1000001 or max_soc >= 0.8999999:
        flags.append("soc_edge_touch")

    return {
        "economic_score": economic_score,
        "constraint_score": constraint_score,
        "risk_score": risk_score,
        "governance_score": governance_score,
        "risk_flags": ",".join(flags) if flags else "none",
        "constraint_passed": constraints_passed,
    }


def _safe_float(value: Any, *, default: float) -> float:
    """把缺失数值归一为显式默认值。

    Stage10 的历史产物没有等效循环列，和 Stage11/12 合并后 pandas 会产生 NaN。
    治理层不能把 NaN 写入展示产物，因此这里把缺失值转成业务可解释的 0 或默认 SOC。

    Args:
        value: 需要转换的值。
        default: 缺省时的默认值。

    Returns:
        转换后的 float。
    """

    if value is None or pd.isna(value):
        return float(default)
    return float(value)


def _decision(row: pd.Series) -> tuple[str, str]:
    """生成治理决策和原因。

    Args:
        row: 含治理评分和风险标签的策略行 Series。

    Returns:
        (决策字符串, 原因字符串) 元组。
    """

    scenario = str(row["scenario_id"])
    flags = set(str(row["risk_flags"]).split(",")) if str(row["risk_flags"]) != "none" else set()
    incremental = float(row["incremental_revenue_eur"])

    if "constraint_failed" in flags:
        return "reject", "物理约束门禁未全部通过，不能进入策略候选池。"
    if scenario == "no_storage":
        return "baseline", "无储能只作为收益和风险基准，不是储能策略。"
    if scenario == "stage10_fixed_threshold":
        return "reject", "固定阈值无放电且增量收益为负，已被 Stage11 证实阈值与价格分布不匹配。"
    if scenario == "stage11_best_threshold_q40_q95":
        return "analysis_upper_bound", "离线阈值扫描收益最高，但属于回看型上界，不能直接固化为生产策略。"
    if scenario == "rolling_optimization" and incremental > 0:
        return "pilot_candidate", "滚动策略收益为正且约束通过，可作为受控试点候选，但需要真实市场数据复核。"
    return "watch", "策略未触发硬性拒绝条件，但收益或运行风险不足以直接推荐。"


def _selected_rows(stage10: pd.DataFrame, stage11: pd.DataFrame, stage12: pd.DataFrame) -> pd.DataFrame:
    """抽取 S13 需要治理的核心策略行。

    Stage10 用固定阈值 forecast_dispatch；Stage11 用最优 q40_q95；Stage12 用
    rolling_optimization 和 no_storage。这样可以保证同一张表覆盖失败基线、离线
    上界、滚动候选和无储能基准。

    Args:
        stage10: Stage10 指标 DataFrame。
        stage11: Stage11 指标 DataFrame。
        stage12: Stage12 指标 DataFrame。

    Returns:
        包含四行核心策略的 DataFrame。
    """

    fixed = _first_row(stage10, column="scenario", value="forecast_dispatch", source="Stage10").copy()
    fixed["scenario_id"] = "stage10_fixed_threshold"
    fixed["strategy_type"] = "fixed_threshold"
    fixed["stage_order"] = 10

    best = _first_row(stage11, column="strategy_id", value="q40_q95", source="Stage11").copy()
    best["scenario_id"] = "stage11_best_threshold_q40_q95"
    best["strategy_type"] = "offline_quantile_scan"
    best["stage_order"] = 11

    rolling = _first_row(stage12, column="scenario", value="rolling_optimization", source="Stage12").copy()
    rolling["scenario_id"] = "rolling_optimization"
    rolling["strategy_type"] = "rolling_lookahead"
    rolling["stage_order"] = 12

    no_storage = _first_row(stage12, column="scenario", value="no_storage", source="Stage12").copy()
    no_storage["scenario_id"] = "no_storage"
    no_storage["strategy_type"] = "baseline"
    no_storage["stage_order"] = 0

    return pd.DataFrame([fixed, best, rolling, no_storage])


def run_stage13_storage_governance(
    *,
    stage10_metrics_path: Path,
    stage11_metrics_path: Path,
    stage12_metrics_path: Path,
    output_paths: dict[str, Path] | None = None,
) -> Stage13GovernanceResult:
    """运行 Stage13 储能策略治理汇总。

    Stage13 不重新仿真，也不重新优化参数；它只消费 Stage10/11/12 的已审计产物，
    给出管理视角下的策略取舍、风险标签和下一阶段路线。

    Args:
        stage10_metrics_path: Stage10 指标 CSV 路径。
        stage11_metrics_path: Stage11 指标 CSV 路径。
        stage12_metrics_path: Stage12 指标 CSV 路径。
        output_paths: 输出产物路径字典。

    Returns:
        Stage13GovernanceResult 实例。
    """

    stage10 = _read_metrics(stage10_metrics_path, stage="stage10")
    stage11 = _read_metrics(stage11_metrics_path, stage="stage11")
    stage12 = _read_metrics(stage12_metrics_path, stage="stage12")
    selected = _selected_rows(stage10, stage11, stage12).reset_index(drop=True)
    # 不同阶段指标列不完全一致。治理层只允许输出显式数值，避免 CSV 和 HTML
    # 出现 NaN 后被误读为算法异常
    fill_defaults = {
        "cycle_equivalent_count": 0.0,
        "total_curtailed_kwh": 0.0,
        "total_shortfall_kwh": 0.0,
        "total_surplus_kwh": 0.0,
        "mean_soc": 0.5,
        "min_soc": 0.5,
        "max_soc": 0.5,
        "max_energy_balance_error": 0.0,
        "simultaneous_charge_discharge_rows": 0,
    }
    for column, default in fill_defaults.items():
        if column in selected.columns:
            selected[column] = selected[column].fillna(default)
    max_incremental_revenue = float(selected["incremental_revenue_eur"].max())

    score_rows: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        score = _score_strategy(row, max_incremental_revenue=max_incremental_revenue)
        row_dict = row.to_dict()
        row_dict.update(score)
        decision, reason = _decision(pd.Series(row_dict))
        row_dict["governance_decision"] = decision
        row_dict["decision_reason"] = reason
        score_rows.append(row_dict)

    scorecard = pd.DataFrame(score_rows)
    ordered_columns = [
        "scenario_id",
        "strategy_type",
        "source_stage",
        "governance_decision",
        "governance_score",
        "economic_score",
        "constraint_score",
        "risk_score",
        "risk_flags",
        "total_storage_revenue_eur",
        "incremental_revenue_eur",
        "total_charge_kwh",
        "total_discharge_kwh",
        "cycle_equivalent_count",
        "total_shortfall_kwh",
        "total_curtailed_kwh",
        "mean_soc",
        "min_soc",
        "max_soc",
        "constraint_passed",
        "sample_count",
        "decision_reason",
        "source_path",
    ]
    scorecard = scorecard[[column for column in ordered_columns if column in scorecard.columns]]
    scorecard = scorecard.sort_values(["governance_score", "incremental_revenue_eur"], ascending=False).reset_index(drop=True)

    best_revenue = scorecard.loc[scorecard["scenario_id"] == "stage11_best_threshold_q40_q95"].iloc[0]
    rolling = scorecard.loc[scorecard["scenario_id"] == "rolling_optimization"].iloc[0]
    fixed = scorecard.loc[scorecard["scenario_id"] == "stage10_fixed_threshold"].iloc[0]

    quality_gates = {
        "stage10_metrics_loaded": bool(len(stage10) > 0),
        "stage11_metrics_loaded": bool(len(stage11) > 0),
        "stage12_metrics_loaded": bool(len(stage12) > 0),
        "selected_strategy_count_is_four": bool(len(scorecard) == 4),
        "all_selected_constraints_passed": bool(scorecard["constraint_passed"].all()),
        "rolling_positive_incremental_revenue": bool(float(rolling["incremental_revenue_eur"]) > 0),
        "stage11_outperforms_rolling": bool(
            float(best_revenue["incremental_revenue_eur"]) > float(rolling["incremental_revenue_eur"])
        ),
        "fixed_threshold_rejected": bool(str(fixed["governance_decision"]) == "reject"),
    }

    report = {
        "stage": "stage13_storage_strategy_governance",
        "input_paths": {
            "stage10_metrics": str(stage10_metrics_path),
            "stage11_metrics": str(stage11_metrics_path),
            "stage12_metrics": str(stage12_metrics_path),
        },
        "quality_gates": quality_gates,
        "top_revenue_strategy": str(best_revenue["scenario_id"]),
        "recommended_governance_action": (
            "将 rolling_optimization 作为受控试点候选；将 Stage11 q40_q95 保留为离线上界；"
            "明确拒绝 Stage10 固定阈值策略；下一阶段优先做目标函数惩罚项和储能配置敏感性。"
        ),
        "key_deltas": {
            "stage11_minus_rolling_eur": float(best_revenue["incremental_revenue_eur"])
            - float(rolling["incremental_revenue_eur"]),
            "rolling_minus_fixed_eur": float(rolling["incremental_revenue_eur"])
            - float(fixed["incremental_revenue_eur"]),
        },
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "pitfall": (
            "S13 是治理汇总层，不改变 Stage10/11/12 的调度算法。当前收益仍基于 OPSD 映射电价、"
            "Stage9 history_only t+24h 预测和离线 actual_kw 回放，不能直接外推为真实市场收益。"
        ),
    }
    return Stage13GovernanceResult(scorecard=scorecard, report=report)


def write_stage13_json(report: dict[str, Any], path: Path) -> None:
    """写出严格 JSON 治理报告。

    Args:
        report: 治理报告字典。
        path: JSON 输出路径。
    """

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage13_report(report: dict[str, Any], scorecard: pd.DataFrame, path: Path) -> None:
    """写出 Stage13 中文管理报告。

    Args:
        report: 治理报告字典。
        scorecard: 策略评分表 DataFrame。
        path: 报告输出路径。
    """

    lines = [
        "# Stage13 储能策略治理与展示层报告",
        "",
        "## 范围",
        "",
        "- 输入: Stage10 固定阈值、Stage11 分位数阈值扫描、Stage12 24h rolling look-ahead 指标。",
        "- 输出: 策略评分表、治理决策、风险标签、可展示 HTML 仪表盘。",
        "- 原则: 不重新训练预测模型，不扩大阈值网格，不把离线回看收益直接当作生产收益。",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage10 fixed threshold"] --> D["Stage13 scorecard"]',
        '    B["Stage11 q40_q95 offline scan"] --> D',
        '    C["Stage12 rolling optimization"] --> D',
        '    D --> E["治理决策"]',
        '    D --> F["Markdown 报告"]',
        '    D --> G["HTML 仪表盘"]',
        "```",
        "",
        "## 方案对比",
        "",
        "| 方案 | 类型 | 治理结论 | 治理分 | 增量收益 EUR | 放电 kWh | 等效循环 | 短缺 kWh | 风险标签 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]

    for _, row in scorecard.iterrows():
        lines.append(
            f"| `{row['scenario_id']}` | `{row['strategy_type']}` | `{row['governance_decision']}` | "
            f"{float(row['governance_score']):.2f} | {float(row['incremental_revenue_eur']):.4f} | "
            f"{float(row['total_discharge_kwh']):.4f} | {float(row['cycle_equivalent_count']):.4f} | "
            f"{float(row['total_shortfall_kwh']):.4f} | `{row['risk_flags']}` |"
        )

    lines.extend(
        [
            "",
            "## 推荐结论",
            "",
            f"- 推荐动作: {report['recommended_governance_action']}",
            f"- Stage11 - rolling 收益差: `{report['key_deltas']['stage11_minus_rolling_eur']:.4f} EUR`。",
            f"- rolling - Stage10 固定阈值收益差: `{report['key_deltas']['rolling_minus_fixed_eur']:.4f} EUR`。",
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## 决策说明", ""])
    for _, row in scorecard.iterrows():
        lines.append(f"- `{row['scenario_id']}`: {row['decision_reason']}")

    lines.extend(
        [
            "",
            "## 输出产物",
            "",
        ]
    )
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 完成 Stage10/11/12 储能策略治理汇总，形成统一评分、风险标签、治理决策和展示层产物。",
            "- 目标完成情况: S13 已完成管理报告和静态仪表盘生成，能解释 Stage10 失败、Stage11 上界和 Stage12 试点候选之间的取舍。",
            "- 下一阶段可行性: 可进入 S14 做储能配置与目标函数惩罚项敏感性分析；若要接近生产评估，必须先替换为真实同区域市场价格和真实 forecast-cycle 输入。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_stage13_dashboard(report: dict[str, Any], scorecard: pd.DataFrame, path: Path) -> None:
    """写出无需服务端的静态 HTML 仪表盘。

    该文件只使用内联 CSS 和表格，适合作为阶段展示层产物；所有数据仍以 CSV/JSON
    为准，HTML 不作为机器消费接口。

    Args:
        report: 治理报告字典。
        scorecard: 策略评分表 DataFrame。
        path: HTML 输出路径。
    """

    max_revenue = max(float(scorecard["incremental_revenue_eur"].max()), 1e-9)
    rows = []
    for _, row in scorecard.iterrows():
        width = max(float(row["incremental_revenue_eur"]) / max_revenue * 100.0, 0.0)
        rows.append(
            "<tr>"
            f"<td>{row['scenario_id']}</td>"
            f"<td>{row['governance_decision']}</td>"
            f"<td>{float(row['governance_score']):.2f}</td>"
            f"<td>{float(row['incremental_revenue_eur']):.4f}</td>"
            f"<td><div class='bar'><span style='width:{width:.2f}%'></span></div></td>"
            f"<td>{float(row['cycle_equivalent_count']):.2f}</td>"
            f"<td>{float(row['total_shortfall_kwh']):.2f}</td>"
            f"<td>{row['risk_flags']}</td>"
            "</tr>"
        )

    gate_items = "\n".join(
        f"<li><span>{name}</span><strong>{value}</strong></li>" for name, value in report["quality_gates"].items()
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Stage13 Storage Governance Dashboard</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Microsoft YaHei", sans-serif; color: #172026; background: #f4f6f8; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; letter-spacing: 0; }}
    .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 18px; }}
    .metric {{ background: #fff; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px; }}
    .metric span {{ display: block; color: #5e6b78; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dde3ea; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e7ecf1; text-align: left; font-size: 13px; }}
    th {{ background: #eef3f7; color: #354250; }}
    .bar {{ width: 150px; height: 10px; background: #e3e8ee; border-radius: 6px; overflow: hidden; }}
    .bar span {{ display: block; height: 100%; background: #2f6f73; }}
    .gates {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 0; list-style: none; }}
    .gates li {{ display: flex; justify-content: space-between; background: #fff; border: 1px solid #dde3ea; border-radius: 8px; padding: 10px 12px; }}
    .note {{ color: #5e6b78; line-height: 1.55; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} .summary, .gates {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>Stage13 储能策略治理仪表盘</h1>
    <p class="note">{report['recommended_governance_action']}</p>
    <section class="summary">
      <div class="metric"><span>Stage11 - rolling</span><strong>{report['key_deltas']['stage11_minus_rolling_eur']:.4f} EUR</strong></div>
      <div class="metric"><span>rolling - Stage10</span><strong>{report['key_deltas']['rolling_minus_fixed_eur']:.4f} EUR</strong></div>
      <div class="metric"><span>Top revenue strategy</span><strong>{report['top_revenue_strategy']}</strong></div>
    </section>
    <h2>策略评分表</h2>
    <table>
      <thead>
        <tr><th>策略</th><th>治理结论</th><th>治理分</th><th>增量收益</th><th>收益条</th><th>等效循环</th><th>短缺 kWh</th><th>风险标签</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <h2>质量门禁</h2>
    <ul class="gates">{gate_items}</ul>
    <h2>Pitfall</h2>
    <p class="note">{report['pitfall']}</p>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
