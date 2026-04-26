from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.storage import (
    _constraint_summary,
    _prepare_dispatch_input,
    _simulate_dispatch_scenario,
)


@dataclass(frozen=True)
class Stage11StrategySensitivityResult:
    """Stage11 储能策略敏感性分析产物容器。

    results 保存小时级回放明细，metrics 保存每个候选策略的聚合指标，report 保存
    质量门禁、推荐策略和产物路径。拆分三类产物的目的，是让后续展示、报告和自动化
    检查都可以读取结构化数据，而不是解析 Markdown 文本。
    """

    results: pd.DataFrame
    metrics: pd.DataFrame
    report: dict[str, Any]


def _quantile_value(series: pd.Series, quantile: float) -> float:
    """计算电价分位数，并返回可稳定写入 CSV/JSON 的普通 float。

    pandas 会在不同 dtype 下返回 numpy scalar。这里统一转成 Python float，避免
    JSON 序列化和 Markdown 格式化时出现类型差异。
    """

    return float(series.quantile(float(quantile)))


def _build_threshold_candidates(
    price_series: pd.Series,
    storage_config: dict[str, Any],
    *,
    charge_quantiles: list[float],
    discharge_quantiles: list[float],
) -> list[dict[str, Any]]:
    """生成覆盖当前电价分布的阈值候选策略。

    Stage10 的固定 `discharge_price_threshold=45.0` 高于当前样本最大电价，导致
    放电永远不会触发。Stage11 不手工猜阈值，而是用分位数从样本分布中生成候选：
    - 低分位电价触发充电；
    - 高分位电价触发放电；
    - 只保留 `charge < discharge` 的组合，避免同一区间同时满足充放电条件。

    同时保留 Stage10 固定阈值作为基线候选，便于在一张表中横向比较。
    """

    if price_series.empty:
        raise ValueError("price_series is empty; cannot build Stage11 threshold candidates.")

    candidates: list[dict[str, Any]] = [
        {
            "strategy_id": "stage10_fixed_threshold",
            "strategy_family": "fixed_threshold",
            "charge_quantile": np.nan,
            "discharge_quantile": np.nan,
            "charge_price_threshold": float(storage_config["charge_price_threshold"]),
            "discharge_price_threshold": float(storage_config["discharge_price_threshold"]),
        }
    ]

    seen_thresholds: set[tuple[float, float]] = {
        (
            round(float(storage_config["charge_price_threshold"]), 10),
            round(float(storage_config["discharge_price_threshold"]), 10),
        )
    }
    for charge_quantile in charge_quantiles:
        charge_threshold = _quantile_value(price_series, charge_quantile)
        for discharge_quantile in discharge_quantiles:
            discharge_threshold = _quantile_value(price_series, discharge_quantile)
            if charge_threshold >= discharge_threshold:
                # 价差方向错误的组合没有经济意义，也会让规则边界难以解释。
                continue

            threshold_key = (round(charge_threshold, 10), round(discharge_threshold, 10))
            if threshold_key in seen_thresholds:
                continue
            seen_thresholds.add(threshold_key)

            candidates.append(
                {
                    "strategy_id": f"q{int(charge_quantile * 100):02d}_q{int(discharge_quantile * 100):02d}",
                    "strategy_family": "price_quantile_threshold",
                    "charge_quantile": float(charge_quantile),
                    "discharge_quantile": float(discharge_quantile),
                    "charge_price_threshold": charge_threshold,
                    "discharge_price_threshold": discharge_threshold,
                }
            )

    if len(candidates) == 1:
        raise ValueError("Stage11 generated no valid quantile threshold candidates.")
    return candidates


def _candidate_storage_config(storage_config: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """复制储能配置并替换候选阈值。

    不直接修改原始 config，避免候选策略之间共享可变字典造成阈值串扰。
    """

    candidate_config = dict(storage_config)
    candidate_config["charge_price_threshold"] = float(candidate["charge_price_threshold"])
    candidate_config["discharge_price_threshold"] = float(candidate["discharge_price_threshold"])
    return candidate_config


def _metrics_for_candidate(
    scenario_rows: pd.DataFrame,
    storage_config: dict[str, Any],
    candidate: dict[str, Any],
    *,
    capacity_kw: float,
) -> dict[str, Any]:
    """汇总单个候选策略的经济性、动作强度和约束指标。"""

    constraints = _constraint_summary(scenario_rows, storage_config)
    total_storage_revenue = float(scenario_rows["storage_revenue_eur"].sum())
    total_no_storage_revenue = float(scenario_rows["no_storage_revenue_eur"].sum())
    total_charge_kwh = float(scenario_rows["actual_charge_kw"].sum())
    total_discharge_kwh = float(scenario_rows["actual_discharge_kw"].sum())

    return {
        **candidate,
        "scenario": "forecast_dispatch",
        "sample_count": int(len(scenario_rows)),
        "total_storage_revenue_eur": total_storage_revenue,
        "total_no_storage_revenue_eur": total_no_storage_revenue,
        "incremental_revenue_eur": total_storage_revenue - total_no_storage_revenue,
        "planned_revenue_eur": float(scenario_rows["planned_revenue_eur"].sum()),
        "total_charge_kwh": total_charge_kwh,
        "total_discharge_kwh": total_discharge_kwh,
        "cycle_equivalent_count": float(
            min(total_charge_kwh, total_discharge_kwh) / float(storage_config["capacity_kwh"])
        ),
        "total_curtailed_kwh": float(scenario_rows["curtailed_kw"].sum()),
        "total_shortfall_kwh": float(scenario_rows["shortfall_kw"].sum()),
        "total_surplus_kwh": float(scenario_rows["surplus_kw"].sum()),
        "mean_soc": float(scenario_rows["soc_end"].mean()),
        "min_soc": float(scenario_rows["soc_end"].min()),
        "max_soc": float(scenario_rows["soc_end"].max()),
        "capacity_kw": float(capacity_kw),
        **constraints,
    }


def _select_best_strategy(metrics: pd.DataFrame) -> pd.Series:
    """选择推荐策略。

    生产判断不能只看收益。这里先过滤掉无放电、物理约束失败的候选，再按增量收益
    排序；若所有候选都不合格，直接抛出问题，由报告暴露根因，而不是静默回退。
    """

    constraint_columns = [
        "soc_within_bounds",
        "charge_power_within_limit",
        "discharge_power_within_limit",
        "no_simultaneous_charge_discharge",
        "energy_balance_error_within_tolerance",
    ]
    eligible = metrics[
        (metrics["total_discharge_kwh"] > 0)
        & metrics[constraint_columns].all(axis=1)
    ].copy()
    if eligible.empty:
        raise ValueError(
            "Stage11 has no eligible strategy with discharge activity and passing constraints. "
            "Inspect price spread, storage size, and threshold grid before adding a fallback."
        )

    eligible = eligible.sort_values(
        by=["incremental_revenue_eur", "cycle_equivalent_count", "total_discharge_kwh"],
        ascending=[False, False, False],
    )
    return eligible.iloc[0]


def _json_safe(value: Any) -> Any:
    """递归转换 JSON 报告中的非标准数值。

    Python 的 `json.dumps` 默认会把 `float("nan")` 写成 `NaN`，这不是严格 JSON。
    Stage11 的固定阈值基线没有分位数，因此内部用 NaN 表示缺省；写报告前统一转成
    `None`，让产物能被前端、数据平台和严格 JSON 解析器稳定读取。
    """

    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def run_stage11_strategy_sensitivity(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    charge_quantiles: list[float] | None = None,
    discharge_quantiles: list[float] | None = None,
    output_paths: dict[str, Path] | None = None,
) -> Stage11StrategySensitivityResult:
    """运行 Stage11 储能策略敏感性分析。

    Stage11 沿用 Stage10 的预测消费和真实结算链路，只扫描价格阈值策略族。这样
    能把当前瓶颈限定在“储能策略是否覆盖电价分布”，而不会把模型预测、天气链路、
    市场映射三类问题混在一起。
    """

    capacity_kw = float(config["site"]["capacity_kw"])
    storage_config = dict(config["storage"])
    charge_quantiles = charge_quantiles or [0.05, 0.10, 0.20, 0.30, 0.40]
    discharge_quantiles = discharge_quantiles or [0.60, 0.70, 0.80, 0.90, 0.95]

    dispatch_input = _prepare_dispatch_input(
        predictions,
        feature_frame,
        horizon_hours=horizon_hours,
    )
    price_series = dispatch_input["price_eur_mwh"].astype(float)
    candidates = _build_threshold_candidates(
        price_series,
        storage_config,
        charge_quantiles=charge_quantiles,
        discharge_quantiles=discharge_quantiles,
    )

    result_frames: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_config = _candidate_storage_config(storage_config, candidate)
        scenario_rows = _simulate_dispatch_scenario(
            dispatch_input,
            candidate_config,
            capacity_kw=capacity_kw,
            scenario="forecast_dispatch",
            forecast_column="prediction_kw",
        )
        scenario_rows.insert(0, "strategy_id", candidate["strategy_id"])
        scenario_rows.insert(1, "strategy_family", candidate["strategy_family"])
        scenario_rows.insert(2, "charge_price_threshold", candidate["charge_price_threshold"])
        scenario_rows.insert(3, "discharge_price_threshold", candidate["discharge_price_threshold"])

        result_frames.append(scenario_rows)
        metric_rows.append(
            _metrics_for_candidate(
                scenario_rows,
                candidate_config,
                candidate,
                capacity_kw=capacity_kw,
            )
        )

    results = pd.concat(result_frames, ignore_index=True)
    metrics = pd.DataFrame(metric_rows).sort_values(
        by=["incremental_revenue_eur", "total_discharge_kwh"],
        ascending=[False, False],
    )

    best = _select_best_strategy(metrics)
    fixed = metrics.loc[metrics["strategy_id"] == "stage10_fixed_threshold"].iloc[0]
    no_storage_revenue = float(fixed["total_no_storage_revenue_eur"])
    quality_gates = {
        "input_non_empty": bool(len(dispatch_input) > 0),
        "candidate_count_positive": bool(len(candidates) > 0),
        "at_least_one_strategy_discharges": bool((metrics["total_discharge_kwh"] > 0).any()),
        "all_strategy_constraints_passed": bool(
            metrics[
                [
                    "soc_within_bounds",
                    "charge_power_within_limit",
                    "discharge_power_within_limit",
                    "no_simultaneous_charge_discharge",
                    "energy_balance_error_within_tolerance",
                ]
            ].all(axis=None)
        ),
        "best_strategy_discharges": bool(float(best["total_discharge_kwh"]) > 0),
        "market_signals_aligned": bool(dispatch_input[["price_eur_mwh", "load_mw"]].notna().all().all()),
    }

    report = {
        "stage": "stage11_storage_strategy_sensitivity",
        "strategy": "price_quantile_threshold_sensitivity",
        "horizon_hours": int(horizon_hours),
        "capacity_kw": capacity_kw,
        "storage_config_base": storage_config,
        "charge_quantiles": [float(value) for value in charge_quantiles],
        "discharge_quantiles": [float(value) for value in discharge_quantiles],
        "candidate_count": int(len(candidates)),
        "input_rows": int(len(dispatch_input)),
        "market_alignment_input_rows": int(dispatch_input.attrs.get("market_alignment_input_rows", len(dispatch_input))),
        "market_alignment_dropped_rows": int(dispatch_input.attrs.get("market_alignment_dropped_rows", 0)),
        "dispatch_timestamp_start": str(dispatch_input["dispatch_timestamp"].min()),
        "dispatch_timestamp_end": str(dispatch_input["dispatch_timestamp"].max()),
        "price_distribution": {
            "min": float(price_series.min()),
            "p05": _quantile_value(price_series, 0.05),
            "p25": _quantile_value(price_series, 0.25),
            "p50": _quantile_value(price_series, 0.50),
            "p75": _quantile_value(price_series, 0.75),
            "p95": _quantile_value(price_series, 0.95),
            "max": float(price_series.max()),
        },
        "baseline": {
            "no_storage_revenue_eur": no_storage_revenue,
            "stage10_fixed_threshold": _json_safe(fixed.to_dict()),
        },
        "best_strategy": _json_safe(best.to_dict()),
        "quality_gates": quality_gates,
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "pitfall": (
            "Stage11 是基于 OPSD 映射电价和 Stage9 history_only t+24h 预测的离线阈值扫描。"
            "最佳阈值只能说明当前样本分布下的策略敏感性，不能直接外推为真实市场收益。"
        ),
    }
    return Stage11StrategySensitivityResult(results=results, metrics=metrics, report=report)


def write_stage11_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage11 中文 Markdown 报告。"""

    best = report["best_strategy"]
    fixed = report["baseline"]["stage10_fixed_threshold"]
    price_distribution = report["price_distribution"]
    top_metrics = metrics.head(10)

    lines = [
        "# Stage11 储能策略敏感性分析报告",
        "",
        "## 范围",
        "",
        f"- 调度策略族: `{report['strategy']}`",
        f"- 预测 horizon: `{report['horizon_hours']}h`",
        f"- 候选策略数: `{report['candidate_count']}`",
        f"- 输入样本数: `{report['input_rows']}`",
        f"- 市场信号无法对齐剔除行数: `{report['market_alignment_dropped_rows']}` / `{report['market_alignment_input_rows']}`",
        f"- 交付时段: `{report['dispatch_timestamp_start']}` 至 `{report['dispatch_timestamp_end']}`",
        f"- 电价区间: `{price_distribution['min']:.4f}` 至 `{price_distribution['max']:.4f} EUR/MWh`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage9 标准预测"] --> B["Stage10 交付时刻对齐"]',
        '    C["Stage3 电价/负荷"] --> B',
        '    B --> D["电价分位数阈值生成"]',
        '    D --> E["逐候选策略回放"]',
        '    E --> F["收益 / 循环 / 约束门禁"]',
        '    F --> G["固定阈值 vs 最优敏感阈值"]',
        "```",
        "",
        "## 电价分布",
        "",
        "| min | p05 | p25 | p50 | p75 | p95 | max |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {price_distribution['min']:.4f} | {price_distribution['p05']:.4f} | "
            f"{price_distribution['p25']:.4f} | {price_distribution['p50']:.4f} | "
            f"{price_distribution['p75']:.4f} | {price_distribution['p95']:.4f} | "
            f"{price_distribution['max']:.4f} |"
        ),
        "",
        "## 基准与推荐策略",
        "",
        "| 策略 | 充电阈值 | 放电阈值 | 收益 EUR | 相对无储能 EUR | 充电 kWh | 放电 kWh | 等效循环 | SOC 区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| `no_storage` | - | - | {report['baseline']['no_storage_revenue_eur']:.4f} | "
            "0.0000 | 0.0000 | 0.0000 | 0.0000 | - |"
        ),
        (
            f"| `stage10_fixed_threshold` | {fixed['charge_price_threshold']:.4f} | "
            f"{fixed['discharge_price_threshold']:.4f} | {fixed['total_storage_revenue_eur']:.4f} | "
            f"{fixed['incremental_revenue_eur']:.4f} | {fixed['total_charge_kwh']:.4f} | "
            f"{fixed['total_discharge_kwh']:.4f} | {fixed['cycle_equivalent_count']:.4f} | "
            f"{fixed['min_soc']:.3f}-{fixed['max_soc']:.3f} |"
        ),
        (
            f"| `{best['strategy_id']}` | {best['charge_price_threshold']:.4f} | "
            f"{best['discharge_price_threshold']:.4f} | {best['total_storage_revenue_eur']:.4f} | "
            f"{best['incremental_revenue_eur']:.4f} | {best['total_charge_kwh']:.4f} | "
            f"{best['total_discharge_kwh']:.4f} | {best['cycle_equivalent_count']:.4f} | "
            f"{best['min_soc']:.3f}-{best['max_soc']:.3f} |"
        ),
        "",
        "## Top 10 候选策略",
        "",
        "| strategy_id | 充电阈值 | 放电阈值 | 相对无储能 EUR | 充电 kWh | 放电 kWh | 等效循环 | 短缺 kWh |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for _, row in top_metrics.iterrows():
        lines.append(
            f"| `{row['strategy_id']}` | {row['charge_price_threshold']:.4f} | "
            f"{row['discharge_price_threshold']:.4f} | {row['incremental_revenue_eur']:.4f} | "
            f"{row['total_charge_kwh']:.4f} | {row['total_discharge_kwh']:.4f} | "
            f"{row['cycle_equivalent_count']:.4f} | {row['total_shortfall_kwh']:.4f} |"
        )

    lines.extend(["", "## 质量门禁", ""])
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(
        [
            "",
            "## 关键结论",
            "",
            f"- Stage10 固定阈值放电量: `{fixed['total_discharge_kwh']:.4f} kWh`，问题根因是放电阈值高于样本最高电价。",
            f"- Stage11 推荐策略 `{best['strategy_id']}` 已形成完整充放电闭环，放电量 `{best['total_discharge_kwh']:.4f} kWh`。",
            f"- 推荐策略相对无储能收益 `{best['incremental_revenue_eur']:.4f} EUR`；若仍不为正，优先检查价差信号、效率损耗、储能容量和 PV 规模，而不是继续盲目调模型。",
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
            "- 工作内容: 读取 Stage10 固化链路，生成电价分位数阈值族，逐策略回放 SOC/功率/收益，输出敏感性指标和推荐策略。",
            "- 目标完成情况: Stage11 已验证固定阈值失败根因，并找到可产生放电动作的候选策略族。",
            "- 下一阶段可行性: 若推荐阈值收益仍弱，可进入 S12 做 24h look-ahead 线性规划；若收益转正且约束稳定，可先做报告展示和策略固化。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_stage11_json(report: dict[str, Any], path: Path) -> None:
    """写出 Stage11 JSON 报告。"""

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
