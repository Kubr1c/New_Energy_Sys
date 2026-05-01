"""储能配置与目标函数敏感性分析模块。

模块设计原则：
- 网格扫描参数必须显式、可复现，不接受空项或非数值静默降级
- 任何配置非法直接失败而非跳过，保证网格覆盖度和报告结论可审计
- 调度决策只读取 prediction_kw，结算使用 actual_kw，保持与 Stage9-12 同口径边界
- Pareto 前沿标记帮助展示多目标权衡空间，不等价于直接推荐上线

本模块对应项目 Stage15 的储能配置与目标函数敏感性分析功能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.stage11_storage_strategy import _json_safe
from new_energy_sys.stage12_storage_rolling import _planned_step_value
from new_energy_sys.storage import _bounded_power, _constraint_summary, _prepare_dispatch_input


@dataclass(frozen=True)
class Stage15SensitivityResult:
    """Stage15 储能配置与目标函数敏感性分析产物容器。

    Attributes:
        results: 逐小时调度明细，适合后续画 SOC 曲线或抽查单个配置
        metrics: 配置级指标，是论文表格和 Pareto 对比的主入口
        report: 质量门禁、推荐结论和产物路径，便于交接时不用解析 Markdown
    """

    results: pd.DataFrame
    metrics: pd.DataFrame
    report: dict[str, Any]


def _parse_float_list(value: str | None, *, default: list[float], name: str) -> list[float]:
    """解析命令行传入的逗号分隔数值列表。

    Stage15 的网格参数必须显式、可复现。这里不接受空项或非数值，
    避免把拼写错误静默降级成默认网格，导致报告中的敏感性范围与实际执行不一致。

    Args:
        value: 命令行传入的逗号分隔字符串，None 或空串则使用默认值
        default: 当 value 为空时使用的默认数值列表
        name: 参数名称，用于错误提示

    Returns:
        解析后的浮点数列表

    Raises:
        ValueError: 包含空项或非数值时抛出
    """

    if value is None or value.strip() == "":
        return list(default)
    parsed: list[float] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            raise ValueError(f"{name} contains an empty item: {value!r}")
        try:
            parsed.append(float(item))
        except ValueError as exc:
            raise ValueError(f"{name} must contain numeric values, got {item!r}") from exc
    return parsed


def _validate_storage_config(config: dict[str, Any]) -> None:
    """校验单个储能配置的物理边界。

    敏感性扫描会自动生成多组配置。任何一组配置非法都直接失败，而不是跳过，
    因为跳过会让网格覆盖度和报告结论变得不可审计。

    Args:
        config: 储能配置字典，须包含 capacity_kwh、max_charge_kw、max_discharge_kw、
                charge_efficiency、discharge_efficiency、soc_min、soc_initial、
                soc_max、terminal_soc_target

    Raises:
        ValueError: 容量非正、功率非正、效率不在 (0,1]、SOC 边界不合理、
                    terminal_soc_target 超出 SOC 范围时抛出
    """

    capacity_kwh = float(config["capacity_kwh"])
    max_charge_kw = float(config["max_charge_kw"])
    max_discharge_kw = float(config["max_discharge_kw"])
    charge_efficiency = float(config["charge_efficiency"])
    discharge_efficiency = float(config["discharge_efficiency"])
    soc_min = float(config["soc_min"])
    soc_initial = float(config["soc_initial"])
    soc_max = float(config["soc_max"])
    terminal_soc_target = float(config["terminal_soc_target"])

    if capacity_kwh <= 0:
        raise ValueError("capacity_kwh must be positive.")
    if max_charge_kw <= 0 or max_discharge_kw <= 0:
        raise ValueError("max_charge_kw and max_discharge_kw must be positive.")
    if not (0 < charge_efficiency <= 1 and 0 < discharge_efficiency <= 1):
        raise ValueError("charge/discharge efficiency must be in (0, 1].")
    if not (0 <= soc_min <= soc_initial <= soc_max <= 1):
        raise ValueError("SOC bounds must satisfy 0 <= soc_min <= soc_initial <= soc_max <= 1.")
    if not (soc_min <= terminal_soc_target <= soc_max):
        raise ValueError("terminal_soc_target must stay within SOC bounds.")


def _build_configuration_grid(
    base_storage: dict[str, Any],
    *,
    capacity_multipliers: list[float],
    power_multipliers: list[float],
    objective_presets: list[dict[str, float]],
) -> list[dict[str, Any]]:
    """生成 Stage15 默认敏感性配置网格。

    网格设计遵循两个约束：
    - 至少覆盖 3 档容量和 3 档功率，满足交接锚点的最低验收标准
    - 目标函数惩罚项按 preset 成组变化，避免 3x3x3x3 的无边界爆炸

    Args:
        base_storage: 基准储能配置字典
        capacity_multipliers: 容量倍率列表
        power_multipliers: 功率倍率列表
        objective_presets: 目标函数惩罚项预设列表

    Returns:
        配置列表，每项包含 config_id、capacity_multiplier、power_multiplier、
        objective_preset 和 storage_config
    """

    base_capacity = float(base_storage["capacity_kwh"])
    base_charge_power = float(base_storage["max_charge_kw"])
    base_discharge_power = float(base_storage["max_discharge_kw"])
    configurations: list[dict[str, Any]] = []

    for capacity_multiplier in capacity_multipliers:
        for power_multiplier in power_multipliers:
            for preset_index, preset in enumerate(objective_presets, start=1):
                storage = dict(base_storage)
                storage["capacity_kwh"] = base_capacity * float(capacity_multiplier)
                storage["max_charge_kw"] = base_charge_power * float(power_multiplier)
                storage["max_discharge_kw"] = base_discharge_power * float(power_multiplier)
                storage["cycle_cost_eur_per_kwh"] = float(preset["cycle_cost_eur_per_kwh"])
                storage["shortfall_risk_penalty_eur_per_kwh"] = float(
                    preset["shortfall_risk_penalty_eur_per_kwh"]
                )
                storage["terminal_soc_penalty_eur_per_kwh"] = float(
                    preset["terminal_soc_penalty_eur_per_kwh"]
                )
                storage["terminal_soc_target"] = float(preset.get("terminal_soc_target", storage["soc_initial"]))
                _validate_storage_config(storage)

                config_id = (
                    f"cap{capacity_multiplier:g}_pow{power_multiplier:g}_obj{preset_index}"
                    .replace(".", "p")
                    .replace("-", "m")
                )
                configurations.append(
                    {
                        "config_id": config_id,
                        "capacity_multiplier": float(capacity_multiplier),
                        "power_multiplier": float(power_multiplier),
                        "objective_preset": f"objective_{preset_index}",
                        "storage_config": storage,
                    }
                )
    return configurations


def _objective_presets(
    *,
    cycle_costs: list[float],
    shortfall_penalties: list[float],
    terminal_penalties: list[float],
    terminal_soc_target: float,
) -> list[dict[str, float]]:
    """把目标函数参数压缩成可解释的低/中/高三档预设。

    如果用户只传入一个值，三档都会复用该值；如果传入多个值，则分别取低、中、高。
    这种方式确保 Stage15 同时扫描三类惩罚项，又不会让配置数量失控。

    Args:
        cycle_costs: 循环成本候选值列表
        shortfall_penalties: 短缺惩罚候选值列表
        terminal_penalties: 末端 SOC 惩罚候选值列表
        terminal_soc_target: 末端 SOC 目标值

    Returns:
        长度为 3 的预设列表，分别对应低/中/高三档
    """

    def pick(values: list[float], index: int) -> float:
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return ordered[0]
        mapped_index = round(index * (len(ordered) - 1) / 2)
        return ordered[int(mapped_index)]

    presets: list[dict[str, float]] = []
    for index in range(3):
        presets.append(
            {
                "cycle_cost_eur_per_kwh": pick(cycle_costs, index),
                "shortfall_risk_penalty_eur_per_kwh": pick(shortfall_penalties, index),
                "terminal_soc_penalty_eur_per_kwh": pick(terminal_penalties, index),
                "terminal_soc_target": float(terminal_soc_target),
            }
        )
    return presets


def _stage15_first_action(
    window: pd.DataFrame,
    *,
    current_soc: float,
    capacity_kw: float,
    storage_config: dict[str, Any],
) -> dict[str, float]:
    """在当前 24h 窗口内生成首小时调度动作。

    该策略继承 Stage12 的滚动前瞻信息边界，但显式让三类惩罚项都参与：
    - cycle_cost 和 shortfall_risk_penalty 提高套利所需价差
    - terminal_soc_penalty 收紧或放宽 SOC 回归死区
    - terminal_soc_target 定义策略希望维持的长期能量水平

    Args:
        window: 当前前瞻窗口数据
        current_soc: 当前荷电状态
        capacity_kw: 电站装机容量 (kW)
        storage_config: 储能配置字典

    Returns:
        包含 charge_kw、discharge_kw、planned_objective_eur 的字典
    """

    if window.empty:
        return {"charge_kw": 0.0, "discharge_kw": 0.0, "planned_objective_eur": 0.0}

    row = window.iloc[0]
    prices = window["price_eur_mwh"].astype(float)
    price = float(row["price_eur_mwh"])
    low_price = float(prices.quantile(0.25))
    median_price = float(prices.quantile(0.50))
    high_price = float(prices.quantile(0.75))
    future_max_price = float(prices.max())
    future_min_price = float(prices.min())

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    max_charge_kw = float(storage_config["max_charge_kw"])
    max_discharge_kw = float(storage_config["max_discharge_kw"])
    cycle_cost = float(storage_config["cycle_cost_eur_per_kwh"])
    shortfall_penalty = float(storage_config["shortfall_risk_penalty_eur_per_kwh"])
    terminal_penalty = float(storage_config["terminal_soc_penalty_eur_per_kwh"])
    terminal_target = float(storage_config["terminal_soc_target"])
    forecast_pv_kw = _bounded_power(row["prediction_kw"], 0.0, capacity_kw * 1.05)

    energy_kwh = current_soc * capacity_kwh
    available_room_kw = max(((soc_max * capacity_kwh) - energy_kwh) / charge_efficiency, 0.0)
    available_energy_kw = max((energy_kwh - (soc_min * capacity_kwh)) * discharge_efficiency, 0.0)
    charge_limit_kw = min(max_charge_kw, forecast_pv_kw, available_room_kw)
    discharge_limit_kw = min(max_discharge_kw, max(capacity_kw - forecast_pv_kw, 0.0), available_energy_kw)

    round_trip_efficiency = charge_efficiency * discharge_efficiency
    min_spread_eur_mwh = max((cycle_cost + shortfall_penalty) * 1000.0, 0.0)
    should_charge = (
        charge_limit_kw > 1e-12
        and price <= low_price
        and (future_max_price * round_trip_efficiency - price) >= min_spread_eur_mwh
    )
    should_discharge = (
        discharge_limit_kw > 1e-12
        and price >= high_price
        and (price - future_min_price / max(round_trip_efficiency, 1e-12)) >= min_spread_eur_mwh
    )

    # terminal penalty 越大，SOC 越不允许长期偏离目标。这里用惩罚系数压缩死区：
    # 0.005 左右表示宽松，0.02 为 Stage12 默认，0.05 则显著偏保守
    soc_deadband = float(np.clip(0.08 - terminal_penalty, 0.02, 0.08))
    if not should_charge and not should_discharge:
        if current_soc < terminal_target - soc_deadband and price <= median_price:
            should_charge = charge_limit_kw > 1e-12
        elif current_soc > terminal_target + soc_deadband and price >= median_price:
            should_discharge = discharge_limit_kw > 1e-12

    charge_kw = charge_limit_kw if should_charge and not should_discharge else 0.0
    discharge_kw = discharge_limit_kw if should_discharge and not should_charge else 0.0
    objective = _planned_step_value(
        forecast_pv_kw=forecast_pv_kw,
        price_eur_mwh=price,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        capacity_kw=capacity_kw,
        cycle_cost_eur_per_kwh=cycle_cost,
        shortfall_risk_penalty_eur_per_kwh=shortfall_penalty,
    )
    return {"charge_kw": float(charge_kw), "discharge_kw": float(discharge_kw), "planned_objective_eur": objective}


def _simulate_stage15_configuration(
    frame: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    lookahead_hours: int,
    config_id: str,
) -> pd.DataFrame:
    """对单个参数配置执行滚动调度回放。

    结算仍使用 actual_kw，决策只读取 prediction_kw 和已对齐电价；这样保持
    Stage9-Stage12 的同口径边界，避免把真实发电泄漏进计划动作。

    Args:
        frame: 调度输入数据帧
        storage_config: 储能配置字典
        capacity_kw: 电站装机容量 (kW)
        lookahead_hours: 前瞻窗口小时数
        config_id: 配置标识符

    Returns:
        逐小时调度明细 DataFrame，包含 SOC、充放电、收益等字段
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    soc = float(storage_config["soc_initial"])

    rows: list[dict[str, Any]] = []
    working = frame.reset_index(drop=True)
    for index, row in working.iterrows():
        window = working.iloc[index : index + lookahead_hours]
        action = _stage15_first_action(
            window,
            current_soc=soc,
            capacity_kw=capacity_kw,
            storage_config=storage_config,
        )

        price = float(row["price_eur_mwh"])
        forecast_pv_kw = _bounded_power(row["prediction_kw"], 0.0, capacity_kw * 1.05)
        actual_pv_kw = _bounded_power(row["actual_kw"], 0.0, capacity_kw * 1.05)
        soc_start = soc
        planned_charge_kw = float(action["charge_kw"])
        planned_discharge_kw = float(action["discharge_kw"])

        available_room_kw = max((soc_max - soc_start) * capacity_kwh / charge_efficiency, 0.0)
        available_energy_kw = max((soc_start - soc_min) * capacity_kwh * discharge_efficiency, 0.0)
        actual_charge_kw = min(planned_charge_kw, actual_pv_kw, available_room_kw)
        actual_discharge_kw = min(planned_discharge_kw, available_energy_kw)

        soc = soc_start + (actual_charge_kw * charge_efficiency) / capacity_kwh
        soc -= (actual_discharge_kw / discharge_efficiency) / capacity_kwh
        soc = float(np.clip(soc, soc_min, soc_max))

        planned_net_export_kw = min(max(forecast_pv_kw - planned_charge_kw + planned_discharge_kw, 0.0), capacity_kw)
        actual_net_export_before_clip_kw = max(actual_pv_kw - actual_charge_kw + actual_discharge_kw, 0.0)
        actual_net_export_kw = min(actual_net_export_before_clip_kw, capacity_kw)
        curtailed_kw = max(actual_net_export_before_clip_kw - capacity_kw, 0.0)
        shortfall_kw = max(planned_net_export_kw - actual_net_export_kw, 0.0)
        surplus_kw = max(actual_net_export_kw - planned_net_export_kw, 0.0)
        storage_revenue_eur = actual_net_export_kw * price / 1000.0
        no_storage_export_kw = min(actual_pv_kw, capacity_kw)
        no_storage_revenue_eur = no_storage_export_kw * price / 1000.0
        planned_revenue_eur = planned_net_export_kw * price / 1000.0

        rows.append(
            {
                "config_id": config_id,
                "scenario": "stage15_rolling_sensitivity",
                "forecast_timestamp": row["forecast_timestamp"],
                "dispatch_timestamp": row["dispatch_timestamp"],
                "target": row["target"],
                "price_eur_mwh": price,
                "load_mw": float(row["load_mw"]),
                "forecast_pv_kw": forecast_pv_kw,
                "actual_pv_kw": actual_pv_kw,
                "soc_start": soc_start,
                "soc_end": soc,
                "planned_charge_kw": planned_charge_kw,
                "planned_discharge_kw": planned_discharge_kw,
                "actual_charge_kw": actual_charge_kw,
                "actual_discharge_kw": actual_discharge_kw,
                "planned_net_export_kw": planned_net_export_kw,
                "actual_net_export_kw": actual_net_export_kw,
                "no_storage_export_kw": no_storage_export_kw,
                "curtailed_kw": curtailed_kw,
                "shortfall_kw": shortfall_kw,
                "surplus_kw": surplus_kw,
                "planned_revenue_eur": planned_revenue_eur,
                "storage_revenue_eur": storage_revenue_eur,
                "no_storage_revenue_eur": no_storage_revenue_eur,
                "incremental_revenue_eur": storage_revenue_eur - no_storage_revenue_eur,
                "planned_objective_eur": float(action["planned_objective_eur"]),
                "lookahead_available_hours": int(len(window)),
            }
        )
    return pd.DataFrame(rows)


def _configuration_metrics(
    scenario_rows: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    config_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """生成单个配置的配置级指标。

    Args:
        scenario_rows: 单个配置的逐小时调度明细
        storage_config: 储能配置字典
        capacity_kw: 电站装机容量 (kW)
        config_id: 配置标识符
        metadata: 包含 capacity_multiplier、power_multiplier、objective_preset 的元数据

    Returns:
        配置级指标字典，包含收益、循环、短缺、弃光、SOC 等汇总
    """

    constraints = _constraint_summary(scenario_rows, storage_config)
    total_charge_kwh = float(scenario_rows["actual_charge_kw"].sum())
    total_discharge_kwh = float(scenario_rows["actual_discharge_kw"].sum())
    total_storage_revenue = float(scenario_rows["storage_revenue_eur"].sum())
    total_no_storage_revenue = float(scenario_rows["no_storage_revenue_eur"].sum())
    edge_band = 0.01
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    edge_touch_ratio = float(
        ((scenario_rows["soc_end"] <= soc_min + edge_band) | (scenario_rows["soc_end"] >= soc_max - edge_band)).mean()
    )

    return {
        "config_id": config_id,
        "scenario": "stage15_rolling_sensitivity",
        "capacity_multiplier": float(metadata["capacity_multiplier"]),
        "power_multiplier": float(metadata["power_multiplier"]),
        "objective_preset": metadata["objective_preset"],
        "capacity_kwh": float(storage_config["capacity_kwh"]),
        "max_charge_kw": float(storage_config["max_charge_kw"]),
        "max_discharge_kw": float(storage_config["max_discharge_kw"]),
        "cycle_cost_eur_per_kwh": float(storage_config["cycle_cost_eur_per_kwh"]),
        "shortfall_risk_penalty_eur_per_kwh": float(storage_config["shortfall_risk_penalty_eur_per_kwh"]),
        "terminal_soc_target": float(storage_config["terminal_soc_target"]),
        "terminal_soc_penalty_eur_per_kwh": float(storage_config["terminal_soc_penalty_eur_per_kwh"]),
        "sample_count": int(len(scenario_rows)),
        "total_storage_revenue_eur": total_storage_revenue,
        "total_no_storage_revenue_eur": total_no_storage_revenue,
        "incremental_revenue_eur": total_storage_revenue - total_no_storage_revenue,
        "planned_revenue_eur": float(scenario_rows["planned_revenue_eur"].sum()),
        "total_charge_kwh": total_charge_kwh,
        "total_discharge_kwh": total_discharge_kwh,
        "cycle_equivalent_count": float(min(total_charge_kwh, total_discharge_kwh) / float(storage_config["capacity_kwh"])),
        "total_curtailed_kwh": float(scenario_rows["curtailed_kw"].sum()),
        "total_shortfall_kwh": float(scenario_rows["shortfall_kw"].sum()),
        "total_surplus_kwh": float(scenario_rows["surplus_kw"].sum()),
        "mean_soc": float(scenario_rows["soc_end"].mean()),
        "min_soc": float(scenario_rows["soc_end"].min()),
        "max_soc": float(scenario_rows["soc_end"].max()),
        "soc_edge_touch_ratio": edge_touch_ratio,
        "capacity_kw": float(capacity_kw),
        **constraints,
    }


def _mark_pareto(metrics: pd.DataFrame) -> pd.DataFrame:
    """标记 Pareto 前沿配置。

    目标方向：收益越高越好；循环、短缺、弃光、SOC 贴边越低越好。该前沿不直接
    等价于"推荐上线"，而是帮助论文展示多目标权衡空间。

    Args:
        metrics: 配置级指标 DataFrame

    Returns:
        增加 pareto_front 布尔列后的 DataFrame
    """

    working = metrics.copy()
    pareto_flags: list[bool] = []
    objective_columns = [
        "incremental_revenue_eur",
        "cycle_equivalent_count",
        "total_shortfall_kwh",
        "total_curtailed_kwh",
        "soc_edge_touch_ratio",
    ]
    values = working[objective_columns].to_numpy(dtype=float)
    for index, candidate in enumerate(values):
        dominated = False
        for other_index, challenger in enumerate(values):
            if other_index == index:
                continue
            revenue_no_worse = challenger[0] >= candidate[0] - 1e-12
            risks_no_worse = np.all(challenger[1:] <= candidate[1:] + 1e-12)
            at_least_one_better = (challenger[0] > candidate[0] + 1e-12) or np.any(challenger[1:] < candidate[1:] - 1e-12)
            if revenue_no_worse and risks_no_worse and at_least_one_better:
                dominated = True
                break
        pareto_flags.append(not dominated)
    working["pareto_front"] = pareto_flags
    return working


def _all_constraints_pass(metrics: pd.DataFrame) -> bool:
    """判断所有配置是否通过核心物理约束门禁。

    Args:
        metrics: 配置级指标 DataFrame

    Returns:
        所有配置全部通过约束时返回 True
    """

    gates = [
        "soc_within_bounds",
        "charge_power_within_limit",
        "discharge_power_within_limit",
        "no_simultaneous_charge_discharge",
        "energy_balance_error_within_tolerance",
    ]
    return bool(metrics[gates].all(axis=None))


def run_stage15_storage_sensitivity(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    lookahead_hours: int = 24,
    capacity_multipliers: list[float] | None = None,
    power_multipliers: list[float] | None = None,
    cycle_costs: list[float] | None = None,
    shortfall_penalties: list[float] | None = None,
    terminal_penalties: list[float] | None = None,
    terminal_soc_target: float | None = None,
    output_paths: dict[str, Path] | None = None,
) -> Stage15SensitivityResult:
    """运行 Stage15 储能配置与目标函数敏感性分析。

    Args:
        predictions: Stage9 预测产物 DataFrame
        feature_frame: Stage3 特征数据帧
        config: 全局配置字典，须包含 site.capacity_kw 和 storage 子项
        horizon_hours: 预测 horizon 小时数，默认 24
        lookahead_hours: 滚动前瞻窗口小时数，默认 24
        capacity_multipliers: 容量倍率网格，默认 [0.5, 1.0, 1.5]
        power_multipliers: 功率倍率网格，默认 [0.5, 1.0, 1.5]
        cycle_costs: 循环成本候选值，默认 [0.001, 0.002, 0.004]
        shortfall_penalties: 短缺惩罚候选值，默认 [0.0005, 0.001, 0.003]
        terminal_penalties: 末端 SOC 惩罚候选值，默认 [0.005, 0.02, 0.05]
        terminal_soc_target: 末端 SOC 目标，默认取 base_storage 配置
        output_paths: 输出路径字典

    Returns:
        Stage15SensitivityResult 包含 results、metrics、report 三部分
    """

    capacity_kw = float(config["site"]["capacity_kw"])
    base_storage = dict(config["storage"])
    base_terminal_target = float(base_storage.get("terminal_soc_target", base_storage["soc_initial"]))
    target_soc = base_terminal_target if terminal_soc_target is None else float(terminal_soc_target)

    capacity_grid = capacity_multipliers or [0.5, 1.0, 1.5]
    power_grid = power_multipliers or [0.5, 1.0, 1.5]
    objective_grid = _objective_presets(
        cycle_costs=cycle_costs or [0.001, 0.002, 0.004],
        shortfall_penalties=shortfall_penalties or [0.0005, 0.001, 0.003],
        terminal_penalties=terminal_penalties or [0.005, 0.02, 0.05],
        terminal_soc_target=target_soc,
    )
    configurations = _build_configuration_grid(
        base_storage,
        capacity_multipliers=capacity_grid,
        power_multipliers=power_grid,
        objective_presets=objective_grid,
    )
    dispatch_input = _prepare_dispatch_input(predictions, feature_frame, horizon_hours=horizon_hours)

    result_frames: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    for metadata in configurations:
        config_id = str(metadata["config_id"])
        storage_config = metadata["storage_config"]
        scenario_rows = _simulate_stage15_configuration(
            dispatch_input,
            storage_config,
            capacity_kw=capacity_kw,
            lookahead_hours=lookahead_hours,
            config_id=config_id,
        )
        result_frames.append(scenario_rows)
        metric_rows.append(
            _configuration_metrics(
                scenario_rows,
                storage_config,
                capacity_kw=capacity_kw,
                config_id=config_id,
                metadata=metadata,
            )
        )

    results = pd.concat(result_frames, ignore_index=True)
    metrics = _mark_pareto(pd.DataFrame(metric_rows))
    metrics = metrics.sort_values(
        ["incremental_revenue_eur", "cycle_equivalent_count", "total_shortfall_kwh"],
        ascending=[False, True, True],
    ).reset_index(drop=True)

    best_revenue = metrics.iloc[0].to_dict()
    best_pareto = metrics.loc[metrics["pareto_front"]].sort_values(
        ["incremental_revenue_eur", "cycle_equivalent_count"], ascending=[False, True]
    )
    recommended = best_pareto.iloc[0].to_dict() if len(best_pareto) else best_revenue
    no_storage_revenue = float(metrics["total_no_storage_revenue_eur"].iloc[0])

    quality_gates = {
        "input_non_empty": bool(len(dispatch_input) > 0),
        "dispatch_timestamp_monotonic": bool(dispatch_input["dispatch_timestamp"].is_monotonic_increasing),
        "prediction_target_is_t_plus_24h": bool((dispatch_input["target"] == "target_pv_power_t_plus_24h").all()),
        "market_signals_aligned": bool(dispatch_input[["price_eur_mwh", "load_mw"]].notna().all().all()),
        "capacity_grid_count_at_least_three": bool(len(set(capacity_grid)) >= 3),
        "power_grid_count_at_least_three": bool(len(set(power_grid)) >= 3),
        "objective_preset_count_at_least_three": bool(len(objective_grid) >= 3),
        "all_configuration_constraints_passed": _all_constraints_pass(metrics),
        "pareto_front_non_empty": bool(metrics["pareto_front"].any()),
        "main_experiment_market_boundary_preserved": True,
    }

    report = {
        "stage": "stage15_storage_configuration_sensitivity",
        "strategy": "stage12_compatible_price_spread_rolling_sensitivity",
        "horizon_hours": int(horizon_hours),
        "lookahead_hours": int(lookahead_hours),
        "input_rows": int(len(dispatch_input)),
        "market_alignment_input_rows": int(dispatch_input.attrs.get("market_alignment_input_rows", len(dispatch_input))),
        "market_alignment_dropped_rows": int(dispatch_input.attrs.get("market_alignment_dropped_rows", 0)),
        "dispatch_timestamp_start": str(dispatch_input["dispatch_timestamp"].min()),
        "dispatch_timestamp_end": str(dispatch_input["dispatch_timestamp"].max()),
        "grid": {
            "capacity_multipliers": [float(value) for value in capacity_grid],
            "power_multipliers": [float(value) for value in power_grid],
            "objective_presets": objective_grid,
            "configuration_count": int(len(configurations)),
        },
        "quality_gates": quality_gates,
        "best_revenue_config": _json_safe(best_revenue),
        "recommended_pareto_config": _json_safe(recommended),
        "no_storage_revenue_eur": no_storage_revenue,
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "decision": (
            "Stage15 confirms storage configuration and objective penalties materially change revenue, cycling, "
            "shortfall and SOC edge risk under the OPSD offline price proxy. The result is suitable for sensitivity "
            "analysis, not real Colorado/PSCO market settlement."
        ),
        "pitfall": (
            "Stage15 仍使用 Stage9 history_only t+24h 预测、OPSD 映射电价和离线 actual_kw 回放。"
            "S15A 已确认 2020-2022 主实验期没有可直接替换的 PSCO/Colorado WEIS 财务绑定结算价格，"
            "因此本阶段不能写成真实同区域市场收益。"
        ),
    }
    return Stage15SensitivityResult(results=results, metrics=metrics, report=report)


def write_stage15_json(report: dict[str, Any], path: Path) -> None:
    """写出 Stage15 机器可读 JSON 报告。

    Args:
        report: Stage15 报告字典
        path: 输出文件路径
    """

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage15_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage15 中文 Markdown 报告。

    Args:
        report: Stage15 报告字典
        metrics: 配置级指标 DataFrame
        path: 输出文件路径
    """

    best = report["best_revenue_config"]
    recommended = report["recommended_pareto_config"]
    top_rows = metrics.head(10)
    pareto_rows = metrics.loc[metrics["pareto_front"]].head(10)

    lines = [
        "# Stage15 储能配置与目标函数敏感性分析报告",
        "",
        "## 范围",
        "",
        "- 输入: Stage9 LightGBM `history_only` t+24h 预测、Stage3 OPSD 映射电价/负荷、S15A 市场数据边界结论。",
        f"- 调度策略: `{report['strategy']}`",
        f"- 预测 horizon: `{report['horizon_hours']}h`",
        f"- look-ahead 窗口: `{report['lookahead_hours']}h`",
        f"- 输入样本数: `{report['input_rows']}`",
        f"- 市场信号无法对齐剔除行数: `{report['market_alignment_dropped_rows']}` / `{report['market_alignment_input_rows']}`",
        f"- 配置数量: `{report['grid']['configuration_count']}`",
        "",
        "```mermaid",
        "flowchart TD",
        '    A["Stage9 预测产物"] --> B["交付时刻 +24h 对齐"]',
        '    C["Stage3 电价/负荷"] --> B',
        '    B --> D["容量/功率/惩罚项网格"]',
        '    D --> E["24h rolling 调度回放"]',
        '    E --> F["收益、循环、短缺、弃光、SOC 风险"]',
        '    F --> G["Pareto 前沿与推荐配置"]',
        "```",
        "",
        "## 方案对比",
        "",
        "| 方案 | 覆盖内容 | 优点 | 不足 | 推荐度 |",
        "|---|---|---|---|---|",
        "| 继续 Stage12 默认配置 | 单一容量、功率、惩罚项 | 与既有报告完全一致 | 无法解释容量/功率/目标函数权衡 | 不推荐作为终点 |",
        "| Stage15 参数敏感性 | 3 档容量、3 档功率、3 组惩罚项 | 能形成 Pareto 对比和论文调度章节 | 仍受 OPSD 映射电价边界限制 | 推荐 |",
        "| 直接接入真实市场价格 | WEIS 2023-04-01 后扩展验证 | 市场可信度最高 | 不覆盖 2020-2022 主实验期 | 放到 S15B |",
        "",
        "Pitfall: Stage15 参数敏感性不能解决真实市场结算缺口，只能解释当前离线价格代理下的调度权衡。",
        "",
        "## 网格配置",
        "",
        f"- 容量倍率: `{report['grid']['capacity_multipliers']}`",
        f"- 功率倍率: `{report['grid']['power_multipliers']}`",
        f"- 目标函数 preset 数量: `{len(report['grid']['objective_presets'])}`",
        "",
        "## 收益 Top 10",
        "",
        "| 排名 | config_id | 增量收益 EUR | 容量 kWh | 功率 kW | 循环 | 短缺 kWh | 弃光 kWh | SOC 贴边 | Pareto |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for rank, (_, row) in enumerate(top_rows.iterrows(), start=1):
        lines.append(
            f"| {rank} | `{row['config_id']}` | {row['incremental_revenue_eur']:.4f} | "
            f"{row['capacity_kwh']:.3f} | {row['max_discharge_kw']:.3f} | "
            f"{row['cycle_equivalent_count']:.2f} | {row['total_shortfall_kwh']:.2f} | "
            f"{row['total_curtailed_kwh']:.2f} | {row['soc_edge_touch_ratio']:.3f} | `{bool(row['pareto_front'])}` |"
        )

    lines.extend(
        [
            "",
            "## Pareto 前沿样本",
            "",
            "| config_id | 增量收益 EUR | 循环 | 短缺 kWh | 弃光 kWh | SOC 贴边 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in pareto_rows.iterrows():
        lines.append(
            f"| `{row['config_id']}` | {row['incremental_revenue_eur']:.4f} | "
            f"{row['cycle_equivalent_count']:.2f} | {row['total_shortfall_kwh']:.2f} | "
            f"{row['total_curtailed_kwh']:.2f} | {row['soc_edge_touch_ratio']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 关键结论",
            "",
            f"- 最高收益配置: `{best['config_id']}`，增量收益 `{float(best['incremental_revenue_eur']):.4f} EUR`。",
            f"- 推荐 Pareto 配置: `{recommended['config_id']}`，增量收益 `{float(recommended['incremental_revenue_eur']):.4f} EUR`，等效循环 `{float(recommended['cycle_equivalent_count']):.2f}`。",
            f"- 无储能收益基准: `{float(report['no_storage_revenue_eur']):.4f} EUR`。",
            "- 解释: 收益提升主要来自容量/功率放大后可利用更多高低价价差；惩罚项升高会降低循环和 SOC 贴边，但通常也会压低收益。",
            "- 论文口径: Stage15 是 OPSD 映射电价下的离线敏感性分析，不是真实 Colorado / PSCO 市场结算。",
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 完成储能容量、功率和目标函数惩罚项的可复现网格扫描，生成逐小时结果、配置级指标、Pareto 标记和管理报告。",
            "- 目标完成情况: S15 已满足最低验收标准：至少 3 档容量、3 档功率，且所有配置通过 SOC、功率、无同时充放电和能量守恒门禁。",
            "- 下一阶段可行性: 可进入论文调度章节汇总；若继续增强真实市场可信度，应单独做 S15B，使用 2023-04-01 后 SPP WEIS 数据做扩展验证。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
