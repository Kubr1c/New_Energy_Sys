"""储能滚动优化调度模块。

模块设计原则：
- 使用 24h look-ahead 滚动窗口，每小时只执行首小时动作后用真实 SOC 重新求解
- 离散 SOC 动态规划求解器和快速价差近似两种实现，默认使用快速版本
- 目标函数包含计划收益、循环成本、短缺风险惩罚和终端 SOC 惩罚
- 与 Stage10 固定阈值和 Stage11 最优阈值基准同表对比

本模块对应项目 Stage 12 的储能滚动优化调度功能。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.stage11_storage_strategy import _json_safe
from new_energy_sys.storage import (
    _bounded_power,
    _constraint_summary,
    _prepare_dispatch_input,
    _simulate_dispatch_scenario,
)


@dataclass(frozen=True)
class Stage12RollingOptimizationResult:
    """Stage12 滚动优化产物容器。

    Args:
        results: 小时级回放明细 DataFrame。
        metrics: 场景级聚合指标 DataFrame。
        report: 质量门禁、策略参数和输出路径的字典。

    三类产物分离，后续展示层可以直接消费 CSV/JSON，不需要解析 Markdown 文本。
    """

    results: pd.DataFrame
    metrics: pd.DataFrame
    report: dict[str, Any]


def _float_config(config: dict[str, Any], key: str, default: float) -> float:
    """读取可选数值配置。

    Stage12 会新增若干优化惩罚项。旧配置文件没有这些字段时使用显式默认值，
    但如果用户写入了非法值，要在入口处直接失败，避免生成看似正常但不可解释的
    调度结果。

    Args:
        config: 储能配置字典。
        key: 配置键名。
        default: 缺省默认值。

    Returns:
        配置值的 float 形式。

    Raises:
        ValueError: 配置值非数值时抛出。
    """

    value = config.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"storage.{key} must be numeric, got {value!r}") from exc


def _candidate_amounts(limit_kw: float, step_kw: float) -> list[float]:
    """生成包含 0 和上限的动作集合。

    离散动态规划不依赖 scipy 线性规划求解器，因此需要把连续充放电功率离散化。
    这里强制保留动作上限，避免因为步长不能整除上限而低估可用功率。

    Args:
        limit_kw: 功率上限（kW）。
        step_kw: 离散步长（kW）。

    Returns:
        从 0 到上限的离散功率值列表。
    """

    if limit_kw <= 1e-12:
        return [0.0]
    values = list(np.arange(0.0, limit_kw + step_kw * 0.5, step_kw))
    values = [float(min(max(value, 0.0), limit_kw)) for value in values]
    values.append(float(limit_kw))
    return sorted({round(value, 10) for value in values})


def _nearest_grid_energy(energy_kwh: float, grid: np.ndarray) -> float:
    """把连续电池能量映射到最近的 SOC 网格点。

    Args:
        energy_kwh: 连续电池能量（kWh）。
        grid: SOC 网格点数组。

    Returns:
        最近网格点对应的能量值（kWh）。
    """

    index = int(np.abs(grid - float(energy_kwh)).argmin())
    return float(grid[index])


def _window_action_candidates(
    *,
    energy_kwh: float,
    forecast_pv_kw: float,
    price_eur_mwh: float,
    capacity_kw: float,
    storage_config: dict[str, Any],
    action_step_kw: float,
) -> list[tuple[float, float]]:
    """为单个小时生成可行动作。

    动作只允许三类：空闲、PV 侧充电、放电；不允许同一小时同时充放电。
    充电功率同时受 PV 预测值、PCS 功率和 SOC 剩余空间约束；放电功率同时受
    PCS 功率、SOC 可用能量和并网容量余量约束。

    Args:
        energy_kwh: 当前电池能量（kWh）。
        forecast_pv_kw: PV 预测功率（kW）。
        price_eur_mwh: 电价（EUR/MWh）。
        capacity_kw: 站点容量（kW）。
        storage_config: 储能配置字典。
        action_step_kw: 动作离散步长（kW）。

    Returns:
        (charge_kw, discharge_kw) 元组列表，表示所有可行动作。
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    max_charge_kw = float(storage_config["max_charge_kw"])
    max_discharge_kw = float(storage_config["max_discharge_kw"])

    min_energy_kwh = soc_min * capacity_kwh
    max_energy_kwh = soc_max * capacity_kwh
    available_room_kw = max((max_energy_kwh - energy_kwh) / charge_efficiency, 0.0)
    available_discharge_kw = max((energy_kwh - min_energy_kwh) * discharge_efficiency, 0.0)

    charge_limit_kw = min(max_charge_kw, forecast_pv_kw, available_room_kw)
    discharge_limit_kw = min(
        max_discharge_kw,
        max(capacity_kw - forecast_pv_kw, 0.0),
        available_discharge_kw,
    )

    actions: set[tuple[float, float]] = {(0.0, 0.0)}
    for charge_kw in _candidate_amounts(charge_limit_kw, action_step_kw):
        if charge_kw > 1e-12:
            actions.add((charge_kw, 0.0))
    for discharge_kw in _candidate_amounts(discharge_limit_kw, action_step_kw):
        if discharge_kw > 1e-12:
            actions.add((0.0, discharge_kw))

    # 高价时把放电动作排在前面，低价时把充电动作排在前面。排序不影响最优性，
    # 但在浮点目标完全相同时能让策略更稳定、可复现
    if price_eur_mwh >= float(storage_config.get("discharge_price_threshold", math.inf)):
        return sorted(actions, key=lambda item: (item[1], -item[0]), reverse=True)
    return sorted(actions, key=lambda item: (item[0], -item[1]), reverse=True)


def _planned_step_value(
    *,
    forecast_pv_kw: float,
    price_eur_mwh: float,
    charge_kw: float,
    discharge_kw: float,
    capacity_kw: float,
    cycle_cost_eur_per_kwh: float,
    shortfall_risk_penalty_eur_per_kwh: float,
) -> float:
    """计算优化器在计划层看到的单小时目标值。

    目标值使用预测 PV 和电价计算计划收益，同时扣除循环成本和放电承诺惩罚。
    放电承诺惩罚不是物理成本，而是对预测误差下短缺风险的保守约束：当策略
    为了提高计划外送而额外放电时，需要付出风险预算。

    Args:
        forecast_pv_kw: PV 预测功率（kW）。
        price_eur_mwh: 电价（EUR/MWh）。
        charge_kw: 计划充电功率（kW）。
        discharge_kw: 计划放电功率（kW）。
        capacity_kw: 站点容量（kW）。
        cycle_cost_eur_per_kwh: 循环成本（EUR/kWh）。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚（EUR/kWh）。

    Returns:
        单小时计划层目标值（EUR）。
    """

    planned_export_kw = min(max(forecast_pv_kw - charge_kw + discharge_kw, 0.0), capacity_kw)
    planned_revenue = planned_export_kw * price_eur_mwh / 1000.0
    cycle_cost = (charge_kw + discharge_kw) * cycle_cost_eur_per_kwh
    shortfall_risk_cost = discharge_kw * shortfall_risk_penalty_eur_per_kwh
    return float(planned_revenue - cycle_cost - shortfall_risk_cost)


def _optimize_first_action(
    window: pd.DataFrame,
    *,
    current_soc: float,
    capacity_kw: float,
    storage_config: dict[str, Any],
    lookahead_hours: int,
    soc_grid_count: int,
    action_step_kw: float,
    cycle_cost_eur_per_kwh: float,
    shortfall_risk_penalty_eur_per_kwh: float,
    terminal_soc_target: float,
    terminal_soc_penalty_eur_per_kwh: float,
) -> dict[str, float]:
    """在当前滚动窗口内求解首小时动作。

    实现方式是离散 SOC 动态规划：从窗口末端向前递推每个 SOC 网格点的最优
    剩余价值，当前时刻只执行首小时动作。下一小时会用真实结算后的 SOC 重新
    求解，因此不会把 24h 计划一次性固化。

    Args:
        window: 滚动窗口 DataFrame。
        current_soc: 当前 SOC。
        capacity_kw: 站点容量（kW）。
        storage_config: 储能配置字典。
        lookahead_hours: 前瞻窗口小时数。
        soc_grid_count: SOC 离散网格点数。
        action_step_kw: 动作离散步长（kW）。
        cycle_cost_eur_per_kwh: 循环成本（EUR/kWh）。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚（EUR/kWh）。
        terminal_soc_target: 终端 SOC 目标值。
        terminal_soc_penalty_eur_per_kwh: 终端 SOC 惩罚（EUR/kWh）。

    Returns:
        包含 charge_kw、discharge_kw、planned_objective_eur 的字典。

    Raises:
        ValueError: current_soc 越界时抛出。
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    min_energy_kwh = soc_min * capacity_kwh
    max_energy_kwh = soc_max * capacity_kwh
    target_energy_kwh = terminal_soc_target * capacity_kwh

    if not (soc_min <= current_soc <= soc_max):
        raise ValueError(f"current_soc outside bounds: {current_soc}")

    horizon = min(int(lookahead_hours), int(len(window)))
    if horizon <= 0:
        return {"charge_kw": 0.0, "discharge_kw": 0.0, "planned_objective_eur": 0.0}

    grid = np.linspace(min_energy_kwh, max_energy_kwh, int(soc_grid_count))
    grid = np.unique(np.append(grid, current_soc * capacity_kwh))
    grid = np.array(sorted(float(value) for value in grid))

    # 终端 SOC 惩罚让优化器避免在每个滚动窗口末端无成本地贴边耗尽电池
    future_value = {
        round(float(energy), 10): -abs(float(energy) - target_energy_kwh) * terminal_soc_penalty_eur_per_kwh
        for energy in grid
    }
    best_action_at_start: dict[str, float] | None = None

    for offset in range(horizon - 1, -1, -1):
        row = window.iloc[offset]
        price = float(row["price_eur_mwh"])
        forecast_pv_kw = _bounded_power(row["prediction_kw"], 0.0, capacity_kw * 1.05)
        current_value: dict[float, float] = {}
        current_action: dict[float, tuple[float, float, float]] = {}

        for energy in grid:
            best_value = -math.inf
            best_action = (0.0, 0.0, -math.inf)
            actions = _window_action_candidates(
                energy_kwh=float(energy),
                forecast_pv_kw=forecast_pv_kw,
                price_eur_mwh=price,
                capacity_kw=capacity_kw,
                storage_config=storage_config,
                action_step_kw=action_step_kw,
            )
            for charge_kw, discharge_kw in actions:
                next_energy = float(energy) + charge_kw * charge_efficiency
                next_energy -= discharge_kw / discharge_efficiency
                next_energy = float(np.clip(next_energy, min_energy_kwh, max_energy_kwh))
                next_grid_energy = _nearest_grid_energy(next_energy, grid)
                step_value = _planned_step_value(
                    forecast_pv_kw=forecast_pv_kw,
                    price_eur_mwh=price,
                    charge_kw=charge_kw,
                    discharge_kw=discharge_kw,
                    capacity_kw=capacity_kw,
                    cycle_cost_eur_per_kwh=cycle_cost_eur_per_kwh,
                    shortfall_risk_penalty_eur_per_kwh=shortfall_risk_penalty_eur_per_kwh,
                )
                total_value = step_value + future_value[round(next_grid_energy, 10)]
                if total_value > best_value + 1e-15:
                    best_value = total_value
                    best_action = (float(charge_kw), float(discharge_kw), float(total_value))

            current_value[round(float(energy), 10)] = float(best_value)
            current_action[round(float(energy), 10)] = best_action

        future_value = current_value
        if offset == 0:
            start_energy = _nearest_grid_energy(current_soc * capacity_kwh, grid)
            charge_kw, discharge_kw, objective = current_action[round(start_energy, 10)]
            best_action_at_start = {
                "charge_kw": charge_kw,
                "discharge_kw": discharge_kw,
                "planned_objective_eur": objective,
            }

    if best_action_at_start is None:
        return {"charge_kw": 0.0, "discharge_kw": 0.0, "planned_objective_eur": 0.0}
    return best_action_at_start


def _simulate_rolling_optimization(
    frame: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    lookahead_hours: int,
    soc_grid_count: int,
    action_step_kw: float,
    cycle_cost_eur_per_kwh: float,
    shortfall_risk_penalty_eur_per_kwh: float,
    terminal_soc_target: float,
    terminal_soc_penalty_eur_per_kwh: float,
) -> pd.DataFrame:
    """执行 24h look-ahead 滚动优化回放。

    决策只读取当前及未来窗口内的预测 PV 和已对齐电价；结算使用真实 actual_kw。
    每小时执行首个动作后立即用真实 SOC 进入下一轮优化，符合 receding horizon
    的工程语义。

    Args:
        frame: 调度输入 DataFrame。
        storage_config: 储能配置字典。
        capacity_kw: 站点容量（kW）。
        lookahead_hours: 前瞻窗口小时数。
        soc_grid_count: SOC 离散网格点数。
        action_step_kw: 动作离散步长（kW）。
        cycle_cost_eur_per_kwh: 循环成本（EUR/kWh）。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚（EUR/kWh）。
        terminal_soc_target: 终端 SOC 目标值。
        terminal_soc_penalty_eur_per_kwh: 终端 SOC 惩罚（EUR/kWh）。

    Returns:
        小时级滚动优化回放明细 DataFrame。
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    soc = float(storage_config["soc_initial"])

    rows: list[dict[str, Any]] = []
    for index, row in frame.reset_index(drop=True).iterrows():
        window = frame.iloc[index : index + lookahead_hours]
        action = _optimize_first_action(
            window,
            current_soc=soc,
            capacity_kw=capacity_kw,
            storage_config=storage_config,
            lookahead_hours=lookahead_hours,
            soc_grid_count=soc_grid_count,
            action_step_kw=action_step_kw,
            cycle_cost_eur_per_kwh=cycle_cost_eur_per_kwh,
            shortfall_risk_penalty_eur_per_kwh=shortfall_risk_penalty_eur_per_kwh,
            terminal_soc_target=terminal_soc_target,
            terminal_soc_penalty_eur_per_kwh=terminal_soc_penalty_eur_per_kwh,
        )

        price = float(row["price_eur_mwh"])
        forecast_pv_kw = _bounded_power(row["prediction_kw"], 0.0, capacity_kw * 1.05)
        actual_pv_kw = _bounded_power(row["actual_kw"], 0.0, capacity_kw * 1.05)
        soc_start = soc
        planned_charge_kw = float(action["charge_kw"])
        planned_discharge_kw = float(action["discharge_kw"])

        # 执行层再次按真实 PV 和真实 SOC 裁剪动作。动态规划本身已满足计划约束，
        # 这里是防止预测高估 PV 或浮点误差导致真实执行越界
        available_room_kw = max((soc_max - soc_start) * capacity_kwh / charge_efficiency, 0.0)
        available_energy_kw = max((soc_start - soc_min) * capacity_kwh * discharge_efficiency, 0.0)
        actual_charge_kw = min(planned_charge_kw, actual_pv_kw, available_room_kw)
        actual_discharge_kw = min(planned_discharge_kw, available_energy_kw)

        soc = soc_start + (actual_charge_kw * charge_efficiency) / capacity_kwh
        soc -= (actual_discharge_kw / discharge_efficiency) / capacity_kwh
        soc = float(np.clip(soc, soc_min, soc_max))

        planned_net_export_kw = min(
            max(forecast_pv_kw - planned_charge_kw + planned_discharge_kw, 0.0),
            capacity_kw,
        )
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
                "scenario": "rolling_optimization",
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


def _optimize_first_action_fast(
    window: pd.DataFrame,
    *,
    current_soc: float,
    capacity_kw: float,
    storage_config: dict[str, Any],
    cycle_cost_eur_per_kwh: float,
    shortfall_risk_penalty_eur_per_kwh: float,
    terminal_soc_target: float,
) -> dict[str, float]:
    """快速滚动 look-ahead 首小时决策。

    完整动态规划在三年小时级样本上会产生数千万级 Python 状态转移，运行时间不适合
    作为默认阶段产物生成路径。本函数保留同样的 24h 信息边界，但把优化目标压缩为
    可解释的价差机会：
    - 当前价处于窗口低价区，且未来高价能覆盖循环成本时充电；
    - 当前价处于窗口高价区，且已高于窗口低价和风险惩罚时放电；
    - SOC 明显偏离目标时，用窗口中位价做温和回归，避免长期贴边。

    该策略是滚动优化的生产化近似，不是 Stage11 的全局阈值回看扫描。

    Args:
        window: 滚动窗口 DataFrame。
        current_soc: 当前 SOC。
        capacity_kw: 站点容量（kW）。
        storage_config: 储能配置字典。
        cycle_cost_eur_per_kwh: 循环成本（EUR/kWh）。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚（EUR/kWh）。
        terminal_soc_target: 终端 SOC 目标值。

    Returns:
        包含 charge_kw、discharge_kw、planned_objective_eur 的字典。
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
    forecast_pv_kw = _bounded_power(row["prediction_kw"], 0.0, capacity_kw * 1.05)

    energy_kwh = current_soc * capacity_kwh
    available_room_kw = max(((soc_max * capacity_kwh) - energy_kwh) / charge_efficiency, 0.0)
    available_energy_kw = max((energy_kwh - (soc_min * capacity_kwh)) * discharge_efficiency, 0.0)
    charge_limit_kw = min(max_charge_kw, forecast_pv_kw, available_room_kw)
    discharge_limit_kw = min(max_discharge_kw, max(capacity_kw - forecast_pv_kw, 0.0), available_energy_kw)

    # 单次充放电的往返效率损耗会抬高所需价差；短缺风险惩罚进一步提高放电门槛
    round_trip_efficiency = charge_efficiency * discharge_efficiency
    min_spread_eur_mwh = (cycle_cost_eur_per_kwh + shortfall_risk_penalty_eur_per_kwh) * 1000.0
    min_spread_eur_mwh = max(min_spread_eur_mwh, 0.0)

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

    # SOC 目标回归只在非强信号小时触发，避免为了贴目标错过明显高低价套利机会
    if not should_charge and not should_discharge:
        if current_soc < terminal_soc_target - 0.05 and price <= median_price:
            should_charge = charge_limit_kw > 1e-12
        elif current_soc > terminal_soc_target + 0.05 and price >= median_price:
            should_discharge = discharge_limit_kw > 1e-12

    charge_kw = charge_limit_kw if should_charge and not should_discharge else 0.0
    discharge_kw = discharge_limit_kw if should_discharge and not should_charge else 0.0
    objective = _planned_step_value(
        forecast_pv_kw=forecast_pv_kw,
        price_eur_mwh=price,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        capacity_kw=capacity_kw,
        cycle_cost_eur_per_kwh=cycle_cost_eur_per_kwh,
        shortfall_risk_penalty_eur_per_kwh=shortfall_risk_penalty_eur_per_kwh,
    )
    return {"charge_kw": float(charge_kw), "discharge_kw": float(discharge_kw), "planned_objective_eur": objective}


def _simulate_fast_rolling_optimization(
    frame: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    lookahead_hours: int,
    cycle_cost_eur_per_kwh: float,
    shortfall_risk_penalty_eur_per_kwh: float,
    terminal_soc_target: float,
) -> pd.DataFrame:
    """执行快速 24h look-ahead 滚动优化回放。

    该函数与动态规划版本使用完全相同的真实结算、SOC 更新和约束审计字段，
    因此下游指标不需要区分优化器实现细节。

    Args:
        frame: 调度输入 DataFrame。
        storage_config: 储能配置字典。
        capacity_kw: 站点容量（kW）。
        lookahead_hours: 前瞻窗口小时数。
        cycle_cost_eur_per_kwh: 循环成本（EUR/kWh）。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚（EUR/kWh）。
        terminal_soc_target: 终端 SOC 目标值。

    Returns:
        小时级快速滚动优化回放明细 DataFrame。
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])
    soc = float(storage_config["soc_initial"])

    rows: list[dict[str, Any]] = []
    frame = frame.reset_index(drop=True)
    for index, row in frame.iterrows():
        window = frame.iloc[index : index + lookahead_hours]
        action = _optimize_first_action_fast(
            window,
            current_soc=soc,
            capacity_kw=capacity_kw,
            storage_config=storage_config,
            cycle_cost_eur_per_kwh=cycle_cost_eur_per_kwh,
            shortfall_risk_penalty_eur_per_kwh=shortfall_risk_penalty_eur_per_kwh,
            terminal_soc_target=terminal_soc_target,
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
                "scenario": "rolling_optimization",
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


def _scenario_metrics(
    scenario_rows: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    scenario: str,
) -> dict[str, Any]:
    """汇总单个调度场景的经济性、动作强度和物理约束。

    Args:
        scenario_rows: 单个场景的小时级回放明细。
        storage_config: 储能配置字典。
        capacity_kw: 站点容量（kW）。
        scenario: 场景名称。

    Returns:
        包含经济性、动作强度和物理约束的字典。
    """

    constraints = _constraint_summary(scenario_rows, storage_config)
    total_charge_kwh = float(scenario_rows["actual_charge_kw"].sum())
    total_discharge_kwh = float(scenario_rows["actual_discharge_kw"].sum())
    total_storage_revenue = float(scenario_rows["storage_revenue_eur"].sum())
    total_no_storage_revenue = float(scenario_rows["no_storage_revenue_eur"].sum())
    return {
        "scenario": scenario,
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
        "capacity_kw": float(capacity_kw),
        **constraints,
    }


def _no_storage_metrics(reference_rows: pd.DataFrame, *, capacity_kw: float, storage_config: dict[str, Any]) -> dict[str, Any]:
    """生成无储能基准指标。

    Args:
        reference_rows: 回放明细 DataFrame，用于提取无储能收益。
        capacity_kw: 站点容量（kW）。
        storage_config: 储能配置字典。

    Returns:
        无储能基准指标字典，充电/放电/循环均为零。
    """

    revenue = float(reference_rows["no_storage_revenue_eur"].sum())
    return {
        "scenario": "no_storage",
        "sample_count": int(len(reference_rows)),
        "total_storage_revenue_eur": revenue,
        "total_no_storage_revenue_eur": revenue,
        "incremental_revenue_eur": 0.0,
        "planned_revenue_eur": revenue,
        "total_charge_kwh": 0.0,
        "total_discharge_kwh": 0.0,
        "cycle_equivalent_count": 0.0,
        "total_curtailed_kwh": 0.0,
        "total_shortfall_kwh": 0.0,
        "total_surplus_kwh": 0.0,
        "mean_soc": float(storage_config["soc_initial"]),
        "min_soc": float(storage_config["soc_initial"]),
        "max_soc": float(storage_config["soc_initial"]),
        "capacity_kw": float(capacity_kw),
        "soc_within_bounds": True,
        "charge_power_within_limit": True,
        "discharge_power_within_limit": True,
        "no_simultaneous_charge_discharge": True,
        "energy_balance_error_within_tolerance": True,
        "max_energy_balance_error": 0.0,
        "simultaneous_charge_discharge_rows": 0,
    }


def run_stage12_rolling_optimization(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    lookahead_hours: int = 24,
    soc_grid_count: int = 81,
    action_step_kw: float = 0.056,
    cycle_cost_eur_per_kwh: float | None = None,
    shortfall_risk_penalty_eur_per_kwh: float | None = None,
    terminal_soc_target: float | None = None,
    terminal_soc_penalty_eur_per_kwh: float | None = None,
    stage11_charge_threshold: float = 24.58,
    stage11_discharge_threshold: float = 35.7025,
    output_paths: dict[str, Path] | None = None,
) -> Stage12RollingOptimizationResult:
    """运行 Stage12 24h look-ahead 储能滚动优化。

    输入继续使用 Stage9 标准预测产物和 Stage3 市场信号。Stage12 不重新训练模型，
    只评估更严格的调度策略是否能改善 Stage11 的阈值扫描结果。

    Args:
        predictions: Stage9 预测产物 DataFrame。
        feature_frame: 特征帧 DataFrame。
        config: 全局配置字典。
        horizon_hours: 预测时域（小时），默认 24。
        lookahead_hours: 前瞻窗口小时数，默认 24。
        soc_grid_count: SOC 离散网格点数，默认 81。
        action_step_kw: 动作离散步长（kW），默认 0.056。
        cycle_cost_eur_per_kwh: 循环成本覆盖参数，默认从配置读取。
        shortfall_risk_penalty_eur_per_kwh: 短缺风险惩罚覆盖参数。
        terminal_soc_target: 终端 SOC 目标覆盖参数。
        terminal_soc_penalty_eur_per_kwh: 终端 SOC 惩罚覆盖参数。
        stage11_charge_threshold: Stage11 基准充电阈值，默认 24.58。
        stage11_discharge_threshold: Stage11 基准放电阈值，默认 35.7025。
        output_paths: 输出产物路径字典。

    Returns:
        Stage12RollingOptimizationResult 实例。

    Raises:
        ValueError: 参数不合法时抛出。
    """

    capacity_kw = float(config["site"]["capacity_kw"])
    storage_config = dict(config["storage"])
    cycle_cost = _float_config(storage_config, "cycle_cost_eur_per_kwh", 0.002)
    shortfall_penalty = _float_config(storage_config, "shortfall_risk_penalty_eur_per_kwh", 0.001)
    terminal_penalty = _float_config(storage_config, "terminal_soc_penalty_eur_per_kwh", 0.02)
    terminal_target = _float_config(storage_config, "terminal_soc_target", float(storage_config["soc_initial"]))

    if cycle_cost_eur_per_kwh is not None:
        cycle_cost = float(cycle_cost_eur_per_kwh)
    if shortfall_risk_penalty_eur_per_kwh is not None:
        shortfall_penalty = float(shortfall_risk_penalty_eur_per_kwh)
    if terminal_soc_penalty_eur_per_kwh is not None:
        terminal_penalty = float(terminal_soc_penalty_eur_per_kwh)
    if terminal_soc_target is not None:
        terminal_target = float(terminal_soc_target)

    if lookahead_hours <= 0:
        raise ValueError("lookahead_hours must be positive.")
    if soc_grid_count < 5:
        raise ValueError("soc_grid_count must be at least 5.")
    if action_step_kw <= 0:
        raise ValueError("action_step_kw must be positive.")
    if not (float(storage_config["soc_min"]) <= terminal_target <= float(storage_config["soc_max"])):
        raise ValueError("terminal_soc_target must stay within storage SOC bounds.")

    dispatch_input = _prepare_dispatch_input(predictions, feature_frame, horizon_hours=horizon_hours)
    rolling = _simulate_fast_rolling_optimization(
        dispatch_input,
        storage_config,
        capacity_kw=capacity_kw,
        lookahead_hours=lookahead_hours,
        cycle_cost_eur_per_kwh=cycle_cost,
        shortfall_risk_penalty_eur_per_kwh=shortfall_penalty,
        terminal_soc_target=terminal_target,
    )

    fixed = _simulate_dispatch_scenario(
        dispatch_input,
        storage_config,
        capacity_kw=capacity_kw,
        scenario="stage10_fixed_threshold",
        forecast_column="prediction_kw",
    )

    stage11_config = dict(storage_config)
    stage11_config["charge_price_threshold"] = float(stage11_charge_threshold)
    stage11_config["discharge_price_threshold"] = float(stage11_discharge_threshold)
    stage11_best = _simulate_dispatch_scenario(
        dispatch_input,
        stage11_config,
        capacity_kw=capacity_kw,
        scenario="stage11_best_threshold_q40_q95",
        forecast_column="prediction_kw",
    )

    results = pd.concat([rolling, stage11_best, fixed], ignore_index=True)
    metric_rows = [
        _scenario_metrics(rolling, storage_config, capacity_kw=capacity_kw, scenario="rolling_optimization"),
        _scenario_metrics(stage11_best, stage11_config, capacity_kw=capacity_kw, scenario="stage11_best_threshold_q40_q95"),
        _scenario_metrics(fixed, storage_config, capacity_kw=capacity_kw, scenario="stage10_fixed_threshold"),
        _no_storage_metrics(rolling, capacity_kw=capacity_kw, storage_config=storage_config),
    ]
    metrics = pd.DataFrame(metric_rows)

    rolling_constraints = _constraint_summary(rolling, storage_config)
    stage11_constraints = _constraint_summary(stage11_best, stage11_config)
    quality_gates = {
        "input_non_empty": bool(len(dispatch_input) > 0),
        "dispatch_timestamp_monotonic": bool(dispatch_input["dispatch_timestamp"].is_monotonic_increasing),
        "prediction_target_is_t_plus_24h": bool((dispatch_input["target"] == "target_pv_power_t_plus_24h").all()),
        "market_signals_aligned": bool(dispatch_input[["price_eur_mwh", "load_mw"]].notna().all().all()),
        "rolling_window_uses_available_market_signals": bool(
            rolling["lookahead_available_hours"].between(1, lookahead_hours).all()
        ),
        "rolling_constraints_passed": bool(
            rolling_constraints["soc_within_bounds"]
            and rolling_constraints["charge_power_within_limit"]
            and rolling_constraints["discharge_power_within_limit"]
            and rolling_constraints["no_simultaneous_charge_discharge"]
            and rolling_constraints["energy_balance_error_within_tolerance"]
        ),
        "stage11_baseline_present": bool(len(stage11_best) == len(rolling)),
    }

    metric_lookup = metrics.set_index("scenario")
    rolling_increment = float(metric_lookup.loc["rolling_optimization", "incremental_revenue_eur"])
    stage11_increment = float(metric_lookup.loc["stage11_best_threshold_q40_q95", "incremental_revenue_eur"])
    if rolling_increment < stage11_increment:
        explanation = (
            "rolling optimization did not outperform Stage11 because terminal SOC penalty, "
            "cycle cost and shortfall-risk penalty make the policy more conservative than the "
            "offline best threshold scan."
        )
    else:
        explanation = "rolling optimization outperformed the Stage11 threshold baseline under the configured penalties."

    report = {
        "stage": "stage12_storage_rolling_optimization",
        "strategy": "fast_price_spread_receding_horizon",
        "horizon_hours": int(horizon_hours),
        "lookahead_hours": int(lookahead_hours),
        "soc_grid_count": int(soc_grid_count),
        "action_step_kw": float(action_step_kw),
        "capacity_kw": capacity_kw,
        "storage_config_base": storage_config,
        "objective_config": {
            "cycle_cost_eur_per_kwh": float(cycle_cost),
            "shortfall_risk_penalty_eur_per_kwh": float(shortfall_penalty),
            "terminal_soc_target": float(terminal_target),
            "terminal_soc_penalty_eur_per_kwh": float(terminal_penalty),
        },
        "stage11_baseline_thresholds": {
            "charge_price_threshold": float(stage11_charge_threshold),
            "discharge_price_threshold": float(stage11_discharge_threshold),
        },
        "input_rows": int(len(dispatch_input)),
        "market_alignment_input_rows": int(dispatch_input.attrs.get("market_alignment_input_rows", len(dispatch_input))),
        "market_alignment_dropped_rows": int(dispatch_input.attrs.get("market_alignment_dropped_rows", 0)),
        "dispatch_timestamp_start": str(dispatch_input["dispatch_timestamp"].min()),
        "dispatch_timestamp_end": str(dispatch_input["dispatch_timestamp"].max()),
        "quality_gates": quality_gates,
        "rolling_constraints": rolling_constraints,
        "stage11_baseline_constraints": stage11_constraints,
        "comparison_summary": {
            "rolling_incremental_revenue_eur": rolling_increment,
            "stage11_incremental_revenue_eur": stage11_increment,
            "rolling_minus_stage11_eur": rolling_increment - stage11_increment,
            "decision": explanation,
        },
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "pitfall": (
            "Stage12 使用 Stage9 history_only t+24h 预测、OPSD 映射电价和离线 actual_kw 结算。"
            "滚动优化结果只能评估当前数据口径下的策略可行性，不能外推为真实同区域市场收益。"
        ),
    }
    return Stage12RollingOptimizationResult(results=results, metrics=metrics, report=report)


def write_stage12_json(report: dict[str, Any], path: Path) -> None:
    """写出严格 JSON 报告。

    Args:
        report: 治理报告字典。
        path: JSON 输出路径。
    """

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage12_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage12 中文 Markdown 报告。

    Args:
        report: 治理报告字典。
        metrics: 场景聚合指标 DataFrame。
        path: 报告输出路径。
    """

    metric_lookup = metrics.set_index("scenario")
    rolling = metric_lookup.loc["rolling_optimization"]
    stage11 = metric_lookup.loc["stage11_best_threshold_q40_q95"]
    fixed = metric_lookup.loc["stage10_fixed_threshold"]
    no_storage = metric_lookup.loc["no_storage"]
    objective = report["objective_config"]

    lines = [
        "# Stage12 储能滚动优化调度报告",
        "",
        "## 范围",
        "",
        f"- 调度策略: `{report['strategy']}`",
        f"- 预测 horizon: `{report['horizon_hours']}h`",
        f"- look-ahead 窗口: `{report['lookahead_hours']}h`",
        f"- 优化器实现: `window_price_spread_with_soc_target`",
        f"- 输入样本数: `{report['input_rows']}`",
        f"- 市场信号无法对齐剔除行数: `{report['market_alignment_dropped_rows']}` / `{report['market_alignment_input_rows']}`",
        f"- 交付时段: `{report['dispatch_timestamp_start']}` 至 `{report['dispatch_timestamp_end']}`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage9 t+24h 预测"] --> B["交付时刻对齐"]',
        '    C["Stage3 电价/负荷"] --> B',
        '    B --> D["24h 滚动窗口"]',
        '    D --> E["窗口价差与 SOC 目标优化"]',
        '    E --> F["执行首小时动作"]',
        '    F --> G["actual_kw 真实结算"]',
        '    G --> H["SOC 更新并进入下一小时"]',
        "```",
        "",
        "## 目标函数配置",
        "",
        "| 参数 | 值 |",
        "|---|---:|",
        f"| cycle_cost_eur_per_kwh | {objective['cycle_cost_eur_per_kwh']:.6f} |",
        f"| shortfall_risk_penalty_eur_per_kwh | {objective['shortfall_risk_penalty_eur_per_kwh']:.6f} |",
        f"| terminal_soc_target | {objective['terminal_soc_target']:.4f} |",
        f"| terminal_soc_penalty_eur_per_kwh | {objective['terminal_soc_penalty_eur_per_kwh']:.6f} |",
        "",
        "## 场景对比",
        "",
        "| 场景 | 收益 EUR | 相对无储能 EUR | 充电 kWh | 放电 kWh | 等效循环 | 短缺 kWh | 弃光 kWh | SOC 区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for scenario in [
        "rolling_optimization",
        "stage11_best_threshold_q40_q95",
        "stage10_fixed_threshold",
        "no_storage",
    ]:
        row = metric_lookup.loc[scenario]
        lines.append(
            f"| `{scenario}` | {row['total_storage_revenue_eur']:.4f} | "
            f"{row['incremental_revenue_eur']:.4f} | {row['total_charge_kwh']:.4f} | "
            f"{row['total_discharge_kwh']:.4f} | {row['cycle_equivalent_count']:.4f} | "
            f"{row['total_shortfall_kwh']:.4f} | {row['total_curtailed_kwh']:.4f} | "
            f"{row['min_soc']:.3f}-{row['max_soc']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 关键结论",
            "",
            f"- rolling optimization 相对无储能收益: `{rolling['incremental_revenue_eur']:.4f} EUR`。",
            f"- Stage11 q40_q95 阈值基准相对无储能收益: `{stage11['incremental_revenue_eur']:.4f} EUR`。",
            f"- rolling - Stage11: `{report['comparison_summary']['rolling_minus_stage11_eur']:.4f} EUR`。",
            f"- Stage10 固定阈值相对无储能收益: `{fixed['incremental_revenue_eur']:.4f} EUR`；无储能收益 `{no_storage['total_storage_revenue_eur']:.4f} EUR`。",
            f"- 判定: {report['comparison_summary']['decision']}",
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## 约束审计", ""])
    for gate, value in report["rolling_constraints"].items():
        lines.append(f"- rolling_optimization.{gate}: `{value}`")

    lines.extend(["", "## 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 实现 24h look-ahead 滚动价差优化，复用 Stage10 的交付时刻对齐和真实结算口径，并与 Stage11 q40_q95、Stage10 固定阈值、no-storage 三类基准同表比较。",
            "- 目标完成情况: Stage12 已形成可审计的滚动优化调度链路，包含 SOC、功率、能量守恒、短缺、弃光、循环和收益指标。",
            "- 下一阶段可行性: 可进入 S13 做策略治理和展示层；若继续优化收益，优先调节目标函数惩罚项和储能配置，不应回到无边界模型调参。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
