from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def simulate_rule_based_storage(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Generate rule-based battery dispatch labels.

    Rule:
    - charge when price is below the configured low-price threshold;
    - discharge when price is above the configured high-price threshold;
    - otherwise idle.

    The simulator enforces SOC and power constraints at every step. It is not
    intended to be the final optimization model; it produces a reliable baseline
    and supervised labels for the first training/demo loop.
    """

    output = frame.copy()
    capacity_kwh = float(params["capacity_kwh"])
    max_charge_kw = float(params["max_charge_kw"])
    max_discharge_kw = float(params["max_discharge_kw"])
    charge_efficiency = float(params["charge_efficiency"])
    discharge_efficiency = float(params["discharge_efficiency"])
    soc_min = float(params["soc_min"])
    soc_max = float(params["soc_max"])
    soc = float(params["soc_initial"])
    charge_threshold = float(params["charge_price_threshold"])
    discharge_threshold = float(params["discharge_price_threshold"])

    soc_values: list[float] = []
    charge_values: list[float] = []
    discharge_values: list[float] = []
    revenue_values: list[float] = []

    for _, row in output.iterrows():
        price = row.get("price_eur_mwh")
        if pd.isna(price):
            price = 0.0

        charge_kw = 0.0
        discharge_kw = 0.0

        if price <= charge_threshold:
            # Available room in battery converted to AC-side charging power.
            available_room_kwh = max((soc_max - soc) * capacity_kwh, 0.0)
            charge_kw = min(max_charge_kw, available_room_kwh / charge_efficiency)
            soc += (charge_kw * charge_efficiency) / capacity_kwh
        elif price >= discharge_threshold:
            # Available energy above minimum SOC converted to AC-side discharge.
            available_energy_kwh = max((soc - soc_min) * capacity_kwh, 0.0)
            discharge_kw = min(max_discharge_kw, available_energy_kwh * discharge_efficiency)
            soc -= (discharge_kw / discharge_efficiency) / capacity_kwh

        soc = float(np.clip(soc, soc_min, soc_max))
        revenue = (discharge_kw - charge_kw) * float(price) / 1000.0

        soc_values.append(soc)
        charge_values.append(charge_kw)
        discharge_values.append(discharge_kw)
        revenue_values.append(revenue)

    output["storage_soc"] = soc_values
    output["storage_charge_kw"] = charge_values
    output["storage_discharge_kw"] = discharge_values
    output["storage_revenue_eur"] = revenue_values
    return output


@dataclass(frozen=True)
class Stage10DispatchResult:
    """Stage10 储能调度仿真产物容器。

    results 是小时级回放明细，metrics 是场景级经济性与约束指标，report 是面向
    JSON/Markdown 的质量门禁和路径索引。三者分开保存，避免下游为了读取一项指标
    去解析 Markdown 文本。
    """

    results: pd.DataFrame
    metrics: pd.DataFrame
    report: dict[str, Any]


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    """快速失败式字段校验。

    Stage10 同时消费 Stage9 预测表和 Stage3 特征表。若字段缺失仍继续运行，会把
    错误伪装成“调度效果差”，因此入口处必须直接抛出明确异常。
    """

    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def _prepare_market_frame(feature_frame: pd.DataFrame) -> pd.DataFrame:
    """提取用于结算的市场信号。

    Stage9 的 `timestamp` 是做出 t+24h 预测的特征时刻；预测目标实际发生在
    `timestamp + 24h`。因此 S10 需要用 Stage3 特征表按交付时刻重新匹配电价和
    负荷，不能直接把预测行的特征时刻当成交付时刻。
    """

    _require_columns(feature_frame, ["timestamp", "load_mw", "price_eur_mwh"], "feature_frame")
    market = feature_frame[["timestamp", "load_mw", "price_eur_mwh"]].copy()
    market["timestamp"] = pd.to_datetime(market["timestamp"], errors="coerce", utc=True)
    if market["timestamp"].isna().any():
        bad_count = int(market["timestamp"].isna().sum())
        raise ValueError(f"feature_frame contains invalid timestamps: {bad_count}")

    market["load_mw"] = pd.to_numeric(market["load_mw"], errors="coerce")
    market["price_eur_mwh"] = pd.to_numeric(market["price_eur_mwh"], errors="coerce")
    if market[["load_mw", "price_eur_mwh"]].isna().any().any():
        bad_count = int(market[["load_mw", "price_eur_mwh"]].isna().sum().sum())
        raise ValueError(f"feature_frame contains missing market values: {bad_count}")

    return market.sort_values("timestamp").drop_duplicates("timestamp", keep="last")


def _prepare_dispatch_input(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    *,
    horizon_hours: int,
) -> pd.DataFrame:
    """生成按交付时刻排序的调度输入表。

    输入预测表保留 `forecast_timestamp`，新增 `dispatch_timestamp`。离线回放必须包含
    `actual_kw`，否则只能做计划生成，无法判断短缺、弃光和真实收益。
    """

    required_prediction_columns = [
        "timestamp",
        "target",
        "prediction_kw",
        "prediction_capacity_ratio",
        "actual_kw",
    ]
    _require_columns(predictions, required_prediction_columns, "predictions")

    working = predictions[required_prediction_columns].copy()
    working["forecast_timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    if working["forecast_timestamp"].isna().any():
        bad_count = int(working["forecast_timestamp"].isna().sum())
        raise ValueError(f"predictions contains invalid timestamps: {bad_count}")

    for column in ["prediction_kw", "prediction_capacity_ratio", "actual_kw"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    if working[["prediction_kw", "prediction_capacity_ratio", "actual_kw"]].isna().any().any():
        bad_count = int(working[["prediction_kw", "prediction_capacity_ratio", "actual_kw"]].isna().sum().sum())
        raise ValueError(f"predictions contains missing numeric prediction values: {bad_count}")

    working["dispatch_timestamp"] = working["forecast_timestamp"] + pd.Timedelta(hours=horizon_hours)
    market = _prepare_market_frame(feature_frame).rename(columns={"timestamp": "dispatch_timestamp"})
    merged = working.merge(market, on="dispatch_timestamp", how="left", validate="many_to_one")
    missing_market_mask = merged[["load_mw", "price_eur_mwh"]].isna().any(axis=1)
    dropped_rows = int(missing_market_mask.sum())
    if dropped_rows:
        # 边界或源数据缺口导致无法结算的行必须排除，否则收益会混入不可审计的
        # 填充值。剔除数量写入 attrs，后续报告会显式暴露该数据损耗。
        merged = merged.loc[~missing_market_mask].copy()
    if merged.empty:
        raise ValueError("Stage10 has no rows after aligning market signals to dispatch timestamps.")

    merged = merged.sort_values("dispatch_timestamp").reset_index(drop=True)
    merged.attrs["market_alignment_dropped_rows"] = dropped_rows
    merged.attrs["market_alignment_input_rows"] = int(len(working))
    return merged


def _bounded_power(value: float, lower: float, upper: float) -> float:
    """对功率进行边界裁剪，并保证返回普通 float，便于 JSON 序列化。"""

    return float(np.clip(float(value), lower, upper))


def _simulate_dispatch_scenario(
    frame: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
    scenario: str,
    forecast_column: str,
) -> pd.DataFrame:
    """执行单个调度场景。

    决策逻辑只使用 `forecast_column` 和电价：
    - 高价时优先放电，但总外送不超过电站并网容量；
    - 低价时只用预测到的光伏发电给电池充电，不做电网充电；
    - 中间价格保持空闲。

    结算逻辑使用 `actual_kw` 更新 SOC 和收益。这样可以把“预测驱动计划”和“真实发电
    结算”区分开，避免把离线真实值泄漏进调度决策。
    """

    params = storage_config
    capacity_kwh = float(params["capacity_kwh"])
    max_charge_kw = float(params["max_charge_kw"])
    max_discharge_kw = float(params["max_discharge_kw"])
    charge_efficiency = float(params["charge_efficiency"])
    discharge_efficiency = float(params["discharge_efficiency"])
    soc_min = float(params["soc_min"])
    soc_max = float(params["soc_max"])
    soc = float(params["soc_initial"])
    charge_threshold = float(params["charge_price_threshold"])
    discharge_threshold = float(params["discharge_price_threshold"])

    if capacity_kwh <= 0 or capacity_kw <= 0:
        raise ValueError("storage capacity_kwh and site capacity_kw must be positive.")
    if not (0 < charge_efficiency <= 1 and 0 < discharge_efficiency <= 1):
        raise ValueError("storage charge/discharge efficiency must be in (0, 1].")
    if not (0 <= soc_min <= float(params["soc_initial"]) <= soc_max <= 1):
        raise ValueError("storage SOC bounds must satisfy 0 <= soc_min <= soc_initial <= soc_max <= 1.")

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        price = float(row["price_eur_mwh"])
        forecast_pv_kw = _bounded_power(row[forecast_column], 0.0, capacity_kw * 1.05)
        actual_pv_kw = _bounded_power(row["actual_kw"], 0.0, capacity_kw * 1.05)

        soc_start = soc
        plan_charge_kw = 0.0
        plan_discharge_kw = 0.0

        if price >= discharge_threshold:
            # 高价时利用电池补足预测 PV 与并网容量之间的空间，避免无意义超额放电。
            available_energy_kwh = max((soc_start - soc_min) * capacity_kwh, 0.0)
            plan_discharge_kw = min(
                max_discharge_kw,
                max(capacity_kw - forecast_pv_kw, 0.0),
                available_energy_kwh * discharge_efficiency,
            )
        elif price <= charge_threshold:
            # 低价时只安排光伏侧充电，不允许电网充电；预测 PV 越低，计划充电越保守。
            available_room_kwh = max((soc_max - soc_start) * capacity_kwh, 0.0)
            plan_charge_kw = min(
                max_charge_kw,
                forecast_pv_kw,
                available_room_kwh / charge_efficiency,
            )

        # 真实执行阶段用 actual_kw 约束充电功率。若预测高估，实际可充电量会自动下降。
        actual_charge_kw = min(plan_charge_kw, actual_pv_kw)
        actual_discharge_kw = plan_discharge_kw

        soc = soc_start + (actual_charge_kw * charge_efficiency) / capacity_kwh
        soc -= (actual_discharge_kw / discharge_efficiency) / capacity_kwh
        soc = float(np.clip(soc, soc_min, soc_max))

        planned_net_export_kw = min(
            max(forecast_pv_kw - plan_charge_kw + plan_discharge_kw, 0.0),
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
                "scenario": scenario,
                "forecast_timestamp": row["forecast_timestamp"],
                "dispatch_timestamp": row["dispatch_timestamp"],
                "target": row["target"],
                "price_eur_mwh": price,
                "load_mw": float(row["load_mw"]),
                "forecast_pv_kw": forecast_pv_kw,
                "actual_pv_kw": actual_pv_kw,
                "soc_start": soc_start,
                "soc_end": soc,
                "planned_charge_kw": plan_charge_kw,
                "planned_discharge_kw": plan_discharge_kw,
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
            }
        )

    return pd.DataFrame(rows)


def _constraint_summary(results: pd.DataFrame, storage_config: dict[str, Any]) -> dict[str, bool | float | int]:
    """汇总储能物理约束门禁。

    能量守恒误差按逐小时 SOC 方程重算。阈值设为 1e-9，覆盖浮点误差，但不会掩盖
    充放电公式或效率方向写反这类实现问题。
    """

    capacity_kwh = float(storage_config["capacity_kwh"])
    max_charge_kw = float(storage_config["max_charge_kw"])
    max_discharge_kw = float(storage_config["max_discharge_kw"])
    charge_efficiency = float(storage_config["charge_efficiency"])
    discharge_efficiency = float(storage_config["discharge_efficiency"])
    soc_min = float(storage_config["soc_min"])
    soc_max = float(storage_config["soc_max"])

    expected_soc_end = (
        results["soc_start"]
        + (results["actual_charge_kw"] * charge_efficiency) / capacity_kwh
        - (results["actual_discharge_kw"] / discharge_efficiency) / capacity_kwh
    ).clip(lower=soc_min, upper=soc_max)
    max_energy_error = float((results["soc_end"] - expected_soc_end).abs().max())
    simultaneous = (results["actual_charge_kw"] > 1e-12) & (results["actual_discharge_kw"] > 1e-12)

    return {
        "soc_within_bounds": bool(results["soc_end"].between(soc_min - 1e-12, soc_max + 1e-12).all()),
        "charge_power_within_limit": bool((results["actual_charge_kw"] <= max_charge_kw + 1e-12).all()),
        "discharge_power_within_limit": bool((results["actual_discharge_kw"] <= max_discharge_kw + 1e-12).all()),
        "no_simultaneous_charge_discharge": bool(not simultaneous.any()),
        "energy_balance_error_within_tolerance": bool(max_energy_error <= 1e-9),
        "max_energy_balance_error": max_energy_error,
        "simultaneous_charge_discharge_rows": int(simultaneous.sum()),
    }


def _build_metrics(
    results: pd.DataFrame,
    storage_config: dict[str, Any],
    *,
    capacity_kw: float,
) -> pd.DataFrame:
    """生成场景级调度指标。

    `forecast_dispatch` 是真实要评估的 S10 策略；`perfect_forecast` 是使用 actual_kw
    做决策的上界对照；`no_storage` 是无储能基线。三者同时输出，避免单看收益误判。
    """

    metric_rows: list[dict[str, Any]] = []
    for scenario, scenario_rows in results.groupby("scenario", sort=False):
        constraints = _constraint_summary(scenario_rows, storage_config)
        metric_rows.append(
            {
                "scenario": scenario,
                "sample_count": int(len(scenario_rows)),
                "total_storage_revenue_eur": float(scenario_rows["storage_revenue_eur"].sum()),
                "total_no_storage_revenue_eur": float(scenario_rows["no_storage_revenue_eur"].sum()),
                "incremental_revenue_eur": float(scenario_rows["incremental_revenue_eur"].sum()),
                "planned_revenue_eur": float(scenario_rows["planned_revenue_eur"].sum()),
                "total_charge_kwh": float(scenario_rows["actual_charge_kw"].sum()),
                "total_discharge_kwh": float(scenario_rows["actual_discharge_kw"].sum()),
                "total_curtailed_kwh": float(scenario_rows["curtailed_kw"].sum()),
                "total_shortfall_kwh": float(scenario_rows["shortfall_kw"].sum()),
                "total_surplus_kwh": float(scenario_rows["surplus_kw"].sum()),
                "mean_soc": float(scenario_rows["soc_end"].mean()),
                "min_soc": float(scenario_rows["soc_end"].min()),
                "max_soc": float(scenario_rows["soc_end"].max()),
                "capacity_kw": float(capacity_kw),
                **constraints,
            }
        )

    no_storage = results[results["scenario"] == "forecast_dispatch"].copy()
    metric_rows.append(
        {
            "scenario": "no_storage",
            "sample_count": int(len(no_storage)),
            "total_storage_revenue_eur": float(no_storage["no_storage_revenue_eur"].sum()),
            "total_no_storage_revenue_eur": float(no_storage["no_storage_revenue_eur"].sum()),
            "incremental_revenue_eur": 0.0,
            "planned_revenue_eur": float(no_storage["no_storage_revenue_eur"].sum()),
            "total_charge_kwh": 0.0,
            "total_discharge_kwh": 0.0,
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
    )
    return pd.DataFrame(metric_rows)


def run_stage10_storage_dispatch(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    output_paths: dict[str, Path] | None = None,
) -> Stage10DispatchResult:
    """运行 Stage10 储能调度离线仿真。

    本函数不训练模型，也不重新生成预测；它只消费 Stage9 标准预测表，并在相同时间轴
    上做储能调度回放。收益计算明确使用真实 `actual_kw` 结算，因此报告中必须把它
    表述为“离线回放”，不能表述为真实生产收益承诺。
    """

    capacity_kw = float(config["site"]["capacity_kw"])
    storage_config = dict(config["storage"])
    dispatch_input = _prepare_dispatch_input(
        predictions,
        feature_frame,
        horizon_hours=horizon_hours,
    )

    forecast_dispatch = _simulate_dispatch_scenario(
        dispatch_input,
        storage_config,
        capacity_kw=capacity_kw,
        scenario="forecast_dispatch",
        forecast_column="prediction_kw",
    )
    perfect_input = dispatch_input.copy()
    perfect_input["perfect_forecast_kw"] = perfect_input["actual_kw"]
    perfect_dispatch = _simulate_dispatch_scenario(
        perfect_input,
        storage_config,
        capacity_kw=capacity_kw,
        scenario="perfect_forecast",
        forecast_column="perfect_forecast_kw",
    )

    results = pd.concat([forecast_dispatch, perfect_dispatch], ignore_index=True)
    metrics = _build_metrics(results, storage_config, capacity_kw=capacity_kw)
    forecast_constraints = _constraint_summary(forecast_dispatch, storage_config)
    all_quality_gates = {
        "input_non_empty": bool(len(dispatch_input) > 0),
        "dispatch_timestamp_monotonic": bool(dispatch_input["dispatch_timestamp"].is_monotonic_increasing),
        "prediction_target_is_t_plus_24h": bool((dispatch_input["target"] == "target_pv_power_t_plus_24h").all()),
        "actual_kw_available_for_settlement": bool("actual_kw" in dispatch_input.columns),
        "market_signals_aligned": bool(dispatch_input[["price_eur_mwh", "load_mw"]].notna().all().all()),
        "forecast_dispatch_constraints_passed": bool(all(bool(value) for key, value in forecast_constraints.items() if key.endswith("bounds") or key.endswith("limit") or key.startswith("no_") or key.endswith("tolerance"))),
    }

    report = {
        "stage": "stage10_storage_dispatch",
        "strategy": "price_threshold_forecast_driven_pv_coupled",
        "horizon_hours": int(horizon_hours),
        "capacity_kw": capacity_kw,
        "storage_config": storage_config,
        "input_rows": int(len(dispatch_input)),
        "market_alignment_input_rows": int(dispatch_input.attrs.get("market_alignment_input_rows", len(dispatch_input))),
        "market_alignment_dropped_rows": int(dispatch_input.attrs.get("market_alignment_dropped_rows", 0)),
        "dispatch_timestamp_start": str(dispatch_input["dispatch_timestamp"].min()),
        "dispatch_timestamp_end": str(dispatch_input["dispatch_timestamp"].max()),
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "quality_gates": all_quality_gates,
        "forecast_dispatch_constraints": forecast_constraints,
        "pitfall": (
            "Stage10 是基于 Stage9 history_only t+24h 预测的离线调度回放。"
            "收益使用 actual_kw 结算，仅用于评估当前预测质量下的调度可行性，"
            "不能表述为真实 forecast-cycle 天气链路上线后的最终收益。"
        ),
    }
    return Stage10DispatchResult(results=results, metrics=metrics, report=report)


def write_stage10_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage10 中文 Markdown 报告。"""

    metric_lookup = metrics.set_index("scenario")
    forecast = metric_lookup.loc["forecast_dispatch"]
    perfect = metric_lookup.loc["perfect_forecast"]
    no_storage = metric_lookup.loc["no_storage"]

    lines = [
        "# Stage10 储能调度仿真报告",
        "",
        "## 范围",
        "",
        f"- 调度策略: `{report['strategy']}`",
        f"- 预测 horizon: `{report['horizon_hours']}h`",
        f"- 输入样本数: `{report['input_rows']}`",
        f"- 市场信号无法对齐剔除行数: `{report['market_alignment_dropped_rows']}` / `{report['market_alignment_input_rows']}`",
        f"- 交付时段: `{report['dispatch_timestamp_start']}` 至 `{report['dispatch_timestamp_end']}`",
        f"- 光伏装机容量: `{report['capacity_kw']:.4f} kW`",
        f"- 储能容量: `{float(report['storage_config']['capacity_kwh']):.4f} kWh`",
        f"- 最大充电/放电功率: `{float(report['storage_config']['max_charge_kw']):.4f}` / `{float(report['storage_config']['max_discharge_kw']):.4f} kW`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage9 t+24h 预测"] --> B["交付时刻对齐"]',
        '    C["Stage3 电价/负荷"] --> B',
        '    B --> D["预测驱动储能调度"]',
        '    D --> E["真实 actual_kw 结算"]',
        '    E --> F["SOC / 功率 / 收益明细"]',
        '    E --> G["约束门禁和基准对比"]',
        "```",
        "",
        "## 场景对比",
        "",
        "| 场景 | 收益 EUR | 相对无储能 EUR | 充电 kWh | 放电 kWh | 短缺 kWh | 弃光 kWh | SOC 区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for scenario in ["forecast_dispatch", "perfect_forecast", "no_storage"]:
        row = metric_lookup.loc[scenario]
        lines.append(
            f"| `{scenario}` | {row['total_storage_revenue_eur']:.4f} | "
            f"{row['incremental_revenue_eur']:.4f} | {row['total_charge_kwh']:.4f} | "
            f"{row['total_discharge_kwh']:.4f} | {row['total_shortfall_kwh']:.4f} | "
            f"{row['total_curtailed_kwh']:.4f} | {row['min_soc']:.3f}-{row['max_soc']:.3f} |"
        )

    forecast_gap = float(perfect["incremental_revenue_eur"] - forecast["incremental_revenue_eur"])
    lines.extend(
        [
            "",
            "## 关键结论",
            "",
            f"- 预测驱动调度相对无储能收益: `{forecast['incremental_revenue_eur']:.4f} EUR`。",
            f"- Perfect-forecast 上界相对无储能收益: `{perfect['incremental_revenue_eur']:.4f} EUR`。",
            f"- 当前预测误差对应的收益缺口: `{forecast_gap:.4f} EUR`。",
            f"- 无储能基线收益: `{no_storage['total_no_storage_revenue_eur']:.4f} EUR`。",
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## 约束审计", ""])
    for gate, value in report["forecast_dispatch_constraints"].items():
        lines.append(f"- {gate}: `{value}`")

    lines.extend(["", "## 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: Stage9 预测消费、交付时刻市场信号对齐、储能 SOC 回放、真实发电结算、perfect-forecast/no-storage 基准对比和约束门禁。",
            "- 目标完成情况: Stage10 储能调度仿真链路已闭环，产物可被后续报告或展示模块消费。",
            "- 下一阶段可行性: 可进入 S11，围绕可视化看板、策略敏感性分析或更严格的滚动优化调度推进。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_stage10_json(report: dict[str, Any], path: Path) -> None:
    """写出 JSON 报告，集中处理中文和 Path 序列化。"""

    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
