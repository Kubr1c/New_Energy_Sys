"""Stage18 Rawhide Prairie Solar scaled PV-storage simulation.

This module is an orchestration layer over the already validated Stage12,
Stage15, and Stage17 engines.  It does not retrain forecasting models and does
not pretend to replay measured Rawhide generation.  Instead, it uses the public
Rawhide Prairie Solar plant parameters as a real plant reference, scales the
existing PVDAQ prediction artifact to the 22 MW plant size, and then reruns the
dispatch, configuration sensitivity, and battery degradation accounting chain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.stage11_storage_strategy import _json_safe
from new_energy_sys.stage12_storage_rolling import run_stage12_rolling_optimization
from new_energy_sys.stage15_storage_sensitivity import run_stage15_storage_sensitivity
from new_energy_sys.stage17_battery_degradation import run_stage17_battery_degradation


RAWHIDE_REFERENCE_DEFAULTS = {
    "name": "Rawhide Prairie Solar",
    "location": "Rawhide Energy Station, Larimer County, Colorado",
    "pv_capacity_kw_ac": 22000.0,
    "battery_power_kw": 1000.0,
    "battery_energy_kwh": 2000.0,
    "commercial_operation_year": 2021,
    "single_axis_tracking": True,
    "is_measured_rawhide_generation": False,
    "sources": [
        {
            "label": "Platte River Power Authority Rawhide Energy Station",
            "url": "https://prpa.org/generation/rawhide-energy-station/",
        },
        {
            "label": "Platte River Rawhide Prairie Solar FAQ",
            "url": "https://www.prpa.org/wp-content/uploads/2019/04/Rawhide-Prairie-Solar-FAQ-final.pdf",
        },
        {
            "label": "Greenbacker commercial operation announcement",
            "url": (
                "https://greenbackercapital.com/2021/03/"
                "greenbackers-first-solar-plus-storage-power-facility-enters-commercial-operation-"
                "providing-clean-and-continuous-energy-in-colorado/"
            ),
        },
    ],
}


@dataclass(frozen=True)
class Stage18RawhideResult:
    """Container for the complete Stage18 artifact set.

    Attributes:
        scaled_predictions: Stage9 prediction rows rescaled from PVDAQ System 10
            capacity to the Rawhide 22 MW_AC reference capacity.
        rolling_results: Hourly Stage12-compatible dispatch rows.
        dispatch_metrics: Scenario-level Stage12-compatible dispatch metrics.
        sensitivity_results: Hourly Stage15 configuration sensitivity rows.
        sensitivity_metrics: Configuration-level Stage15 sensitivity metrics.
        degradation_results: Hourly Stage17 SOH/degradation replay rows.
        degradation_metrics: Scenario-level Stage17 degradation metrics.
        degradation_sensitivity: Stage17 replacement-cost/life-curve sensitivity.
        report: Machine-readable Stage18 summary and quality gates.
    """

    scaled_predictions: pd.DataFrame
    rolling_results: pd.DataFrame
    dispatch_metrics: pd.DataFrame
    sensitivity_results: pd.DataFrame
    sensitivity_metrics: pd.DataFrame
    degradation_results: pd.DataFrame
    degradation_metrics: pd.DataFrame
    degradation_sensitivity: pd.DataFrame
    report: dict[str, Any]


def _reference_site(config: dict[str, Any]) -> dict[str, Any]:
    """Return Rawhide reference metadata with config overrides applied.

    The config file owns the final numbers used in the simulation.  Defaults are
    kept here only so the module can fail with a clear, complete context when a
    future config omits optional descriptive fields.
    """

    rawhide = dict(RAWHIDE_REFERENCE_DEFAULTS)
    rawhide.update(config.get("rawhide_reference", {}) or {})
    return rawhide


def _required_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    """Fail fast when an upstream artifact is not compatible with Stage18."""

    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def validate_rawhide_config(config: dict[str, Any]) -> dict[str, float]:
    """Validate the Rawhide plant and storage parameters.

    The validation is intentionally strict because this stage is used to support
    a thesis/engineering narrative.  A silent fallback from 22 MW / 2 MWh to the
    old 1.12 kW prototype would invalidate every economic and cycling result.
    """

    site = config.get("site", {})
    storage = config.get("storage", {})
    reference = _reference_site(config)
    source_capacity_kw = float(config.get("rawhide_simulation", {}).get("source_capacity_kw", 1.12))
    target_capacity_kw = float(site.get("capacity_kw", reference["pv_capacity_kw_ac"]))
    capacity_kwh = float(storage.get("capacity_kwh", reference["battery_energy_kwh"]))
    max_charge_kw = float(storage.get("max_charge_kw", reference["battery_power_kw"]))
    max_discharge_kw = float(storage.get("max_discharge_kw", reference["battery_power_kw"]))
    soc_min = float(storage.get("soc_min", np.nan))
    soc_initial = float(storage.get("soc_initial", np.nan))
    soc_max = float(storage.get("soc_max", np.nan))
    charge_efficiency = float(storage.get("charge_efficiency", np.nan))
    discharge_efficiency = float(storage.get("discharge_efficiency", np.nan))

    if source_capacity_kw <= 0:
        raise ValueError("rawhide_simulation.source_capacity_kw must be positive.")
    if target_capacity_kw <= 0:
        raise ValueError("site.capacity_kw must be positive.")
    if capacity_kwh <= 0:
        raise ValueError("storage.capacity_kwh must be positive.")
    if max_charge_kw <= 0 or max_discharge_kw <= 0:
        raise ValueError("storage max charge/discharge power must be positive.")
    if not (0 < charge_efficiency <= 1 and 0 < discharge_efficiency <= 1):
        raise ValueError("storage charge/discharge efficiency must be in (0, 1].")
    if not (0 <= soc_min <= soc_initial <= soc_max <= 1):
        raise ValueError("SOC bounds must satisfy 0 <= soc_min <= soc_initial <= soc_max <= 1.")
    if abs(target_capacity_kw - float(reference["pv_capacity_kw_ac"])) > 1e-9:
        raise ValueError("Stage18 Rawhide config must use the 22 MW_AC reference capacity.")
    if abs(capacity_kwh - float(reference["battery_energy_kwh"])) > 1e-9:
        raise ValueError("Stage18 Rawhide config must use the 2 MWh reference battery energy.")
    if abs(max_discharge_kw - float(reference["battery_power_kw"])) > 1e-9:
        raise ValueError("Stage18 Rawhide config must use the 1 MW reference discharge power.")

    return {
        "source_capacity_kw": source_capacity_kw,
        "target_capacity_kw": target_capacity_kw,
        "source_scale_factor": target_capacity_kw / source_capacity_kw,
        "battery_energy_kwh": capacity_kwh,
        "battery_power_kw": max_discharge_kw,
    }


def scale_predictions_to_rawhide(predictions: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Scale Stage9 predictions from the prototype site to Rawhide size.

    The safest invariant is the capacity ratio: the shape and timing of the
    existing PVDAQ curve are preserved, while every kW-valued field is multiplied
    by 22,000 / 1.12.  This keeps the current forecast error structure intact and
    makes the downstream dispatch result comparable to previous stages.
    """

    _required_columns(
        predictions,
        ["timestamp", "target", "prediction_kw", "prediction_capacity_ratio", "actual_kw", "error_kw"],
        "predictions",
    )
    factors = validate_rawhide_config(config)
    reference = _reference_site(config)
    target_capacity_kw = factors["target_capacity_kw"]
    source_scale_factor = factors["source_scale_factor"]

    output = predictions.copy()
    kw_columns = [
        "prediction_kw",
        "prediction_lower_bound_kw",
        "prediction_upper_bound_kw",
        "actual_kw",
        "error_kw",
    ]
    for column in kw_columns:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce") * source_scale_factor

    numeric_check_columns = [column for column in ["prediction_kw", "actual_kw", "error_kw"] if column in output.columns]
    if output[numeric_check_columns].isna().any().any():
        raise ValueError("Scaled Rawhide predictions contain missing numeric values.")

    # The base Stage9 artifact is clipped at source_capacity_kw * 1.05.  After
    # scaling, the same physical tolerance becomes target_capacity_kw * 1.05.
    upper_limit = target_capacity_kw * 1.05
    output["prediction_kw"] = output["prediction_kw"].clip(lower=0.0, upper=upper_limit)
    output["actual_kw"] = output["actual_kw"].clip(lower=0.0, upper=upper_limit)
    output["error_kw"] = output["prediction_kw"] - output["actual_kw"]
    if "prediction_lower_bound_kw" in output.columns:
        output["prediction_lower_bound_kw"] = output["prediction_lower_bound_kw"].clip(lower=0.0, upper=upper_limit)
    if "prediction_upper_bound_kw" in output.columns:
        output["prediction_upper_bound_kw"] = output["prediction_upper_bound_kw"].clip(lower=0.0, upper=upper_limit)

    output["source_scale_factor"] = source_scale_factor
    output["source_capacity_kw"] = factors["source_capacity_kw"]
    output["rawhide_reference_capacity_kw"] = target_capacity_kw
    output["reference_site_name"] = str(reference["name"])
    output["is_measured_rawhide_generation"] = False
    output["scaling_method"] = "pvdaq_system10_capacity_ratio_scaled_to_rawhide_22mw_ac"
    return output


def _add_reference_columns(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Attach reference-site audit columns to a tabular artifact."""

    factors = validate_rawhide_config(config)
    reference = _reference_site(config)
    output = frame.copy()
    output["source_scale_factor"] = factors["source_scale_factor"]
    output["reference_site_name"] = str(reference["name"])
    output["is_measured_rawhide_generation"] = False
    return output


def _quality_gates(
    *,
    scaled_predictions: pd.DataFrame,
    config: dict[str, Any],
    dispatch_report: dict[str, Any],
    sensitivity_report: dict[str, Any],
    degradation_report: dict[str, Any],
) -> dict[str, bool]:
    """Build Stage18 quality gates from all downstream stages."""

    factors = validate_rawhide_config(config)
    target_capacity_kw = factors["target_capacity_kw"]
    return {
        "rawhide_config_valid": True,
        "scaled_predictions_non_empty": bool(len(scaled_predictions) > 0),
        "scaled_prediction_capacity_ratio_preserved": bool(
            scaled_predictions["prediction_capacity_ratio"].between(-1e-12, 1.05 + 1e-12).all()
        ),
        "scaled_prediction_kw_within_bounds": bool(
            scaled_predictions["prediction_kw"].between(0.0, target_capacity_kw * 1.05 + 1e-9).all()
        ),
        "scaled_actual_kw_within_bounds": bool(
            scaled_predictions["actual_kw"].between(0.0, target_capacity_kw * 1.05 + 1e-9).all()
        ),
        "not_measured_rawhide_generation": bool((scaled_predictions["is_measured_rawhide_generation"] == False).all()),
        "rolling_constraints_passed": bool(dispatch_report["quality_gates"]["rolling_constraints_passed"]),
        "stage11_baseline_present": bool(dispatch_report["quality_gates"]["stage11_baseline_present"]),
        "sensitivity_constraints_passed": bool(
            sensitivity_report["quality_gates"]["all_configuration_constraints_passed"]
        ),
        "sensitivity_pareto_non_empty": bool(sensitivity_report["quality_gates"]["pareto_front_non_empty"]),
        "degradation_soh_monotonic": bool(degradation_report["quality_gates"]["soh_monotonic_nonincreasing"]),
        "degradation_net_value_identity": bool(degradation_report["quality_gates"]["net_value_identity_passed"]),
    }


def run_stage18_rawhide_simulation(
    base_predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    lookahead_hours: int = 24,
    output_paths: dict[str, Path] | None = None,
) -> Stage18RawhideResult:
    """Run the full Stage18 Rawhide scaled simulation chain.

    The chain is deliberately linear and auditable:
    1. scale Stage9 predictions to Rawhide size;
    2. run Stage12 rolling dispatch and its baselines;
    3. run Stage15 Rawhide-size storage sensitivity;
    4. run Stage17 battery degradation over rolling dispatch;
    5. write one Stage18 summary that keeps the real-data boundary explicit.
    """

    output_paths = output_paths or {}
    scaled_predictions = scale_predictions_to_rawhide(base_predictions, config)

    dispatch_result = run_stage12_rolling_optimization(
        scaled_predictions,
        feature_frame,
        config,
        horizon_hours=horizon_hours,
        lookahead_hours=lookahead_hours,
        # Rawhide's 1 MW battery would otherwise inherit the prototype 0.056 kW
        # action step.  A 100 kW step gives a production-scale action grid while
        # preserving deterministic behavior.
        action_step_kw=100.0,
        output_paths={
            "results_csv": output_paths.get("rolling_results_csv", Path("stage18_rawhide_rolling_results.csv")),
            "metrics_csv": output_paths.get("dispatch_metrics_csv", Path("stage18_rawhide_dispatch_metrics.csv")),
            "report_json": output_paths.get("dispatch_report_json", Path("stage18_rawhide_dispatch_report.json")),
        },
    )
    sensitivity_result = run_stage15_storage_sensitivity(
        scaled_predictions,
        feature_frame,
        config,
        horizon_hours=horizon_hours,
        lookahead_hours=lookahead_hours,
        output_paths={
            "results_csv": output_paths.get("sensitivity_results_csv", Path("stage18_rawhide_sensitivity_results.csv")),
            "metrics_csv": output_paths.get("sensitivity_metrics_csv", Path("stage18_rawhide_sensitivity_metrics.csv")),
            "report_json": output_paths.get("sensitivity_report_json", Path("stage18_rawhide_sensitivity_report.json")),
        },
    )
    degradation_result = run_stage17_battery_degradation(
        dispatch_result.results,
        config,
        dispatch_scenario="rolling_optimization",
        output_paths={
            "results_csv": output_paths.get("degradation_replay_csv", Path("stage18_rawhide_degradation_replay.csv")),
            "metrics_csv": output_paths.get("degradation_metrics_csv", Path("stage18_rawhide_degradation_metrics.csv")),
            "sensitivity_csv": output_paths.get(
                "degradation_sensitivity_csv",
                Path("stage18_rawhide_degradation_sensitivity_metrics.csv"),
            ),
            "report_json": output_paths.get("degradation_report_json", Path("stage18_rawhide_degradation_report.json")),
        },
    )

    rolling_results = _add_reference_columns(dispatch_result.results, config)
    dispatch_metrics = _add_reference_columns(dispatch_result.metrics, config)
    sensitivity_results = _add_reference_columns(sensitivity_result.results, config)
    sensitivity_metrics = _add_reference_columns(sensitivity_result.metrics, config)
    degradation_results = _add_reference_columns(degradation_result.results, config)
    degradation_metrics = _add_reference_columns(degradation_result.metrics, config)
    degradation_sensitivity = _add_reference_columns(degradation_result.sensitivity, config)

    factors = validate_rawhide_config(config)
    reference = _reference_site(config)
    quality_gates = _quality_gates(
        scaled_predictions=scaled_predictions,
        config=config,
        dispatch_report=dispatch_result.report,
        sensitivity_report=sensitivity_result.report,
        degradation_report=degradation_result.report,
    )
    report = {
        "stage": "stage18_rawhide_reference_scaled_simulation",
        "reference_site": reference,
        "scaling": {
            "source_dataset": "PVDAQ System 10 + Stage9 LightGBM history_only t+24h predictions",
            "source_capacity_kw": factors["source_capacity_kw"],
            "target_capacity_kw": factors["target_capacity_kw"],
            "source_scale_factor": factors["source_scale_factor"],
            "method": "capacity-ratio scaling of existing hourly PVDAQ prediction and actual-power curves",
            "is_measured_rawhide_generation": False,
        },
        "storage_config": dict(config["storage"]),
        "horizon_hours": int(horizon_hours),
        "lookahead_hours": int(lookahead_hours),
        "input_rows": int(len(scaled_predictions)),
        "quality_gates": quality_gates,
        "all_quality_gates_passed": bool(all(quality_gates.values())),
        "dispatch_summary": _json_safe(dispatch_metrics.set_index("scenario").to_dict(orient="index")),
        "best_sensitivity_config": _json_safe(sensitivity_result.report["best_revenue_config"]),
        "recommended_pareto_config": _json_safe(sensitivity_result.report["recommended_pareto_config"]),
        "degradation_recommended_metrics": _json_safe(degradation_result.report["recommended_metrics"]),
        "output_paths": {name: str(path) for name, path in output_paths.items()},
        "market_boundary": (
            "Stage18 still uses the existing Stage3 OPSD-mapped price/load profile. "
            "It is a Rawhide-parameter reference simulation, not Colorado/Rawhide real-market settlement."
        ),
        "pitfall": (
            "Rawhide public data provides plant capacity and battery parameters, not the full 2020-2022 hourly "
            "measured PV output used here.  The scaled curve must be described as a reference simulation."
        ),
    }
    return Stage18RawhideResult(
        scaled_predictions=scaled_predictions,
        rolling_results=rolling_results,
        dispatch_metrics=dispatch_metrics,
        sensitivity_results=sensitivity_results,
        sensitivity_metrics=sensitivity_metrics,
        degradation_results=degradation_results,
        degradation_metrics=degradation_metrics,
        degradation_sensitivity=degradation_sensitivity,
        report=report,
    )


def write_stage18_json(report: dict[str, Any], path: Path) -> None:
    """Write the Stage18 machine-readable report."""

    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage18_report(
    report: dict[str, Any],
    dispatch_metrics: pd.DataFrame,
    sensitivity_metrics: pd.DataFrame,
    degradation_metrics: pd.DataFrame,
    path: Path,
) -> None:
    """Write the Stage18 Chinese Markdown report."""

    dispatch_lookup = dispatch_metrics.set_index("scenario")
    rolling = dispatch_lookup.loc["rolling_optimization"]
    stage11 = dispatch_lookup.loc["stage11_best_threshold_q40_q95"]
    fixed = dispatch_lookup.loc["stage10_fixed_threshold"]
    no_storage = dispatch_lookup.loc["no_storage"]
    top_sensitivity = sensitivity_metrics.head(5)
    degradation_lookup = degradation_metrics.set_index("scenario")
    rainflow = degradation_lookup.loc["rolling_with_rainflow_degradation"]
    reference = report["reference_site"]

    lines = [
        "# Stage18 Rawhide 真实光伏储能电站参数参照仿真报告",
        "",
        "## 1. 阶段定位",
        "",
        "Stage18 将现有原型容量调度升级为 Rawhide Prairie Solar 公开参数参照仿真。"
        "本阶段不重新训练预测模型，也不声称拥有 Rawhide 小时级实测发电数据；"
        "它将 Stage9 的 PVDAQ 预测和实际功率曲线按容量比例放大到 22 MW_AC，"
        "再复用 Stage12、Stage15、Stage17 评估调度收益、配置权衡和电池退化。",
        "",
        "```mermaid",
        "flowchart TD",
        '    A["Stage9 PVDAQ 1.12 kW 预测"] --> B["按 22 MW / 1.12 kW 放大"]',
        '    C["Rawhide 公开参数<br/>22 MW PV + 1 MW / 2 MWh BESS"] --> D["Rawhide 配置"]',
        '    B --> E["Stage12 rolling 调度"]',
        '    D --> E',
        '    E --> F["Stage15 配置敏感性"]',
        '    E --> G["Stage17 SOH / 退化成本"]',
        '    F --> H["S18 统一报告"]',
        '    G --> H',
        "```",
        "",
        "Pitfall: 本报告是 Rawhide 参数参照仿真，不是 Rawhide 实测运行数据回放。",
        "",
        "## 2. 真实电站参照",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        f"| 参照电站 | {reference['name']} |",
        f"| 位置 | {reference['location']} |",
        f"| 光伏容量 | {float(reference['pv_capacity_kw_ac']) / 1000:.1f} MW_AC |",
        f"| 储能功率 | {float(reference['battery_power_kw']) / 1000:.1f} MW |",
        f"| 储能容量 | {float(reference['battery_energy_kwh']) / 1000:.1f} MWh |",
        f"| 商业运行年份 | {reference['commercial_operation_year']} |",
        f"| 单轴跟踪 | {reference['single_axis_tracking']} |",
        "",
        "资料来源：",
    ]
    for source in reference["sources"]:
        lines.append(f"- [{source['label']}]({source['url']})")

    lines.extend(
        [
            "",
            "## 3. 缩放口径",
            "",
            "| 字段 | 数值 |",
            "|---|---:|",
            f"| 源容量 | {report['scaling']['source_capacity_kw']:.4f} kW |",
            f"| 目标容量 | {report['scaling']['target_capacity_kw']:.1f} kW |",
            f"| 缩放倍率 | {report['scaling']['source_scale_factor']:.6f} |",
            f"| 是否 Rawhide 实测发电 | `{report['scaling']['is_measured_rawhide_generation']}` |",
            "",
            "## 4. 调度场景对比",
            "",
            "| 场景 | 收益 EUR | 相对无储能 EUR | 充电 kWh | 放电 kWh | 等效循环 | 短缺 kWh | 弃光 kWh | SOC 区间 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scenario, row in [
        ("rolling_optimization", rolling),
        ("stage11_best_threshold_q40_q95", stage11),
        ("stage10_fixed_threshold", fixed),
        ("no_storage", no_storage),
    ]:
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
            "## 5. Rawhide 配置敏感性 Top 5",
            "",
            "| config_id | 增量收益 EUR | 容量 kWh | 功率 kW | 循环 | 短缺 kWh | 弃光 kWh | Pareto |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, row in top_sensitivity.iterrows():
        lines.append(
            f"| `{row['config_id']}` | {row['incremental_revenue_eur']:.4f} | "
            f"{row['capacity_kwh']:.1f} | {row['max_discharge_kw']:.1f} | "
            f"{row['cycle_equivalent_count']:.2f} | {row['total_shortfall_kwh']:.2f} | "
            f"{row['total_curtailed_kwh']:.2f} | `{bool(row['pareto_front'])}` |"
        )

    lines.extend(
        [
            "",
            "## 6. 电池退化与净收益",
            "",
            "| 场景 | 毛收益 EUR | 退化成本 EUR | 净收益 EUR | 净增量 EUR | SOH start | SOH end | 等效完整循环 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scenario, row in degradation_lookup.iterrows():
        lines.append(
            f"| `{scenario}` | {row['gross_revenue_eur']:.4f} | "
            f"{row['degradation_cost_eur']:.6f} | {row['net_revenue_eur']:.4f} | "
            f"{row['net_incremental_revenue_eur']:.4f} | {row['soh_start']:.6f} | "
            f"{row['soh_end']:.6f} | {row['equivalent_full_cycles']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 7. 关键结论",
            "",
            f"- Rawhide rolling optimization 相对无储能收益为 `{rolling['incremental_revenue_eur']:.4f} EUR`。",
            f"- Stage11 离线阈值上界相对无储能收益为 `{stage11['incremental_revenue_eur']:.4f} EUR`，只作为上界，不直接固化为生产策略。",
            f"- 推荐 Pareto 配置为 `{report['recommended_pareto_config']['config_id']}`，增量收益 `{float(report['recommended_pareto_config']['incremental_revenue_eur']):.4f} EUR`。",
            f"- Rainflow 退化核算后 rolling 净增量为 `{rainflow['net_incremental_revenue_eur']:.4f} EUR`，SOH 结束值为 `{rainflow['soh_end']:.6f}`。",
            f"- 市场边界: {report['market_boundary']}",
            "",
            "## 8. 质量门禁",
            "",
        ]
    )
    for gate, value in report["quality_gates"].items():
        lines.append(f"- {gate}: `{value}`")
    lines.append(f"- all_quality_gates_passed: `{report['all_quality_gates_passed']}`")

    lines.extend(["", "## 9. 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 新增 Rawhide 公开参数配置、Stage9 预测缩放、Stage12 rolling 调度、Stage15 配置敏感性和 Stage17 电池退化回放。",
            "- 目标完成情况: S18 已把优化调度模块从原型容量扩展为真实 Colorado 光伏储能电站参数参照仿真。",
            "- 下一阶段可行性: 可继续做 S18B，接入 2023-04-01 后 WEIS/PSCO 市场价格扩展验证；也可把 S18 指标接入 API 和前端展示。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
