"""Stage21 Rawhide weather-driven price-scenario dispatch simulation.

Stage21 upgrades the Stage18 Rawhide reference from capacity-ratio PVDAQ
scaling to a weather-driven PV estimate while keeping the existing Stage12
rolling optimizer.  It is still a simulation: PV settlement uses the same
weather-estimated PV curve, not measured Rawhide generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from new_energy_sys.stage11_storage_strategy import _json_safe
from new_energy_sys.stage12_storage_rolling import run_stage12_rolling_optimization
from new_energy_sys.stage18_rawhide_simulation import _reference_site, validate_rawhide_config


OPEN_METEO_TO_STANDARD = {
    "time": "timestamp",
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "relative_humidity_pct",
    "dew_point_2m": "dew_point_c",
    "surface_pressure": "surface_pressure_hpa",
    "precipitation": "precipitation_mm",
    "wind_speed_10m": "wind_speed_ms",
    "wind_direction_10m": "wind_direction_deg",
    "wind_gusts_10m": "wind_gusts_ms",
    "shortwave_radiation": "ghi_wm2",
    "direct_normal_irradiance": "dni_wm2",
    "diffuse_radiation": "dhi_wm2",
    "cloud_cover": "cloud_cover_pct",
    "cloud_cover_low": "cloud_cover_low_pct",
    "cloud_cover_mid": "cloud_cover_mid_pct",
    "cloud_cover_high": "cloud_cover_high_pct",
}


@dataclass(frozen=True)
class Stage21RawhideWeatherDispatchResult:
    """Container for Stage21 weather-driven dispatch artifacts."""

    weather_predictions: pd.DataFrame
    price_scenarios: pd.DataFrame
    dispatch_results: pd.DataFrame
    dispatch_metrics: pd.DataFrame
    report: dict[str, Any]


def _require_mapping_key(mapping: dict[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ValueError(f"{label} missing required field: {key}")
    return mapping[key]


def validate_market_price_scenarios(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the Stage21 market price scenario configuration.

    The validator is deliberately strict because price curves drive the storage
    optimizer.  A malformed synthetic curve must fail loudly rather than silently
    producing a misleading dispatch result.
    """

    rules = config.get("validation_rules", {})
    synthetic_hour_count = int(rules.get("synthetic_hour_count", 24))
    price_min = float(rules.get("price_min_eur_mwh", -100.0))
    price_max = float(rules.get("price_max_eur_mwh", 500.0))
    synthetic = config.get("synthetic_scenarios", [])
    real_candidates = config.get("real_market_candidates", [])

    if not isinstance(synthetic, list) or not synthetic:
        raise ValueError("market price config must define at least one synthetic_scenario.")

    for scenario in synthetic:
        scenario_id = _require_mapping_key(scenario, "id", "synthetic_scenario")
        _require_mapping_key(scenario, "source_type", f"synthetic_scenario {scenario_id}")
        if "is_real_settlement_price" not in scenario:
            raise ValueError(f"synthetic_scenario {scenario_id} must declare is_real_settlement_price.")
        if bool(scenario["is_real_settlement_price"]):
            raise ValueError(f"synthetic_scenario {scenario_id} cannot be marked as real settlement price.")
        values = scenario.get("values_eur_mwh_by_local_hour")
        if values is None:
            if scenario.get("source_type") != "existing_project_proxy":
                raise ValueError(f"synthetic_scenario {scenario_id} missing hourly price values.")
            continue
        if len(values) != synthetic_hour_count:
            raise ValueError(
                f"synthetic_scenario {scenario_id} must contain 24 hourly prices, got {len(values)}."
            )
        for value in values:
            price = float(value)
            if price < price_min or price > price_max:
                raise ValueError(
                    f"synthetic_scenario {scenario_id} price {price} outside configured range "
                    f"[{price_min}, {price_max}]."
                )

    for candidate in real_candidates:
        candidate_id = _require_mapping_key(candidate, "id", "real_market_candidate")
        _require_mapping_key(candidate, "source_type", f"real_market_candidate {candidate_id}")
        if "is_real_settlement_price" not in candidate:
            raise ValueError(f"real_market_candidate {candidate_id} must declare is_real_settlement_price.")
        if "requires_node_mapping" not in candidate:
            raise ValueError(f"real_market_candidate {candidate_id} must declare requires_node_mapping.")

    return {
        "schema_version": config.get("schema_version"),
        "synthetic_scenario_count": int(len(synthetic)),
        "real_market_candidate_count": int(len(real_candidates)),
        "all_scenarios_declare_settlement_truth": True,
        "price_min_eur_mwh": price_min,
        "price_max_eur_mwh": price_max,
    }


def _normalize_weather_frame(weather: pd.DataFrame, *, horizon_hours: int) -> pd.DataFrame:
    """Normalize Open-Meteo or Stage7-style weather rows to Stage21 schema."""

    frame = weather.rename(columns={k: v for k, v in OPEN_METEO_TO_STANDARD.items() if k in weather.columns}).copy()
    if "timestamp" not in frame.columns:
        raise ValueError("weather input missing timestamp/time column.")
    if "ghi_wm2" not in frame.columns:
        raise ValueError("weather input missing GHI field: expected ghi_wm2 or shortwave_radiation.")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["timestamp"]).drop_duplicates("timestamp", keep="last")
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        raise ValueError("weather input is empty after timestamp parsing.")

    numeric_fields = [
        "ghi_wm2",
        "dni_wm2",
        "dhi_wm2",
        "temperature_c",
        "dew_point_c",
        "relative_humidity_pct",
        "surface_pressure_hpa",
        "precipitation_mm",
        "wind_speed_ms",
        "wind_direction_deg",
        "wind_gusts_ms",
        "cloud_cover_pct",
        "cloud_cover_low_pct",
        "cloud_cover_mid_pct",
        "cloud_cover_high_pct",
    ]
    for field in numeric_fields:
        if field in frame.columns:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
    if frame["ghi_wm2"].isna().any():
        raise ValueError("weather input contains missing ghi_wm2 values.")

    frame["ghi_wm2"] = frame["ghi_wm2"].clip(lower=0.0, upper=1400.0)
    for field in ["dni_wm2", "dhi_wm2"]:
        if field in frame.columns:
            frame[field] = frame[field].clip(lower=0.0, upper=1400.0)
    for field in ["cloud_cover_pct", "cloud_cover_low_pct", "cloud_cover_mid_pct", "cloud_cover_high_pct"]:
        if field in frame.columns:
            frame[field] = frame[field].clip(lower=0.0, upper=100.0)

    if "weather_forecast_issue_time" not in frame.columns:
        frame["weather_forecast_issue_time"] = frame["timestamp"] - pd.to_timedelta(horizon_hours, unit="h")
        frame["weather_forecast_issue_time_is_assumed"] = True
    else:
        frame["weather_forecast_issue_time"] = pd.to_datetime(
            frame["weather_forecast_issue_time"], errors="coerce", utc=True
        )
        frame["weather_forecast_issue_time_is_assumed"] = frame.get(
            "weather_forecast_issue_time_is_assumed", False
        )
    if "weather_forecast_lead_time_hour" not in frame.columns:
        frame["weather_forecast_lead_time_hour"] = int(horizon_hours)
    if "weather_provider" not in frame.columns:
        frame["weather_provider"] = "open_meteo_forecast_or_standard_weather_csv"
    return frame


def build_weather_driven_predictions(
    weather: pd.DataFrame,
    config: dict[str, Any],
    *,
    horizon_hours: int = 24,
    performance_ratio: float = 0.82,
) -> pd.DataFrame:
    """Convert Rawhide-site weather into Stage12-compatible PV predictions."""

    if not (0.0 < performance_ratio <= 1.2):
        raise ValueError("performance_ratio must be in (0, 1.2].")

    factors = validate_rawhide_config(config)
    capacity_kw = factors["target_capacity_kw"]
    frame = _normalize_weather_frame(weather, horizon_hours=horizon_hours)

    estimated_kw = (capacity_kw * frame["ghi_wm2"] / 1000.0 * performance_ratio).clip(
        lower=0.0,
        upper=capacity_kw * 1.05,
    )
    output = pd.DataFrame(
        {
            "timestamp": frame["timestamp"] - pd.to_timedelta(horizon_hours, unit="h"),
            "weather_valid_time": frame["timestamp"],
            "target": "target_pv_power_t_plus_24h",
            "prediction_kw": estimated_kw,
            "prediction_capacity_ratio": estimated_kw / capacity_kw,
            # Stage12 consumes actual_kw for settlement accounting.  Stage21
            # marks it explicitly as a weather estimate, not measured Rawhide PV.
            "actual_kw": estimated_kw,
            "weather_estimated_pv_kw": estimated_kw,
            "ghi_wm2": frame["ghi_wm2"],
            "performance_ratio": float(performance_ratio),
            "is_measured_rawhide_generation": False,
            "actual_kw_is_measured": False,
            "settlement_generation_kind": "weather_estimated_pv_not_measured_rawhide",
            "weather_provider": frame["weather_provider"],
            "weather_forecast_issue_time": frame["weather_forecast_issue_time"],
            "weather_forecast_lead_time_hour": pd.to_numeric(
                frame["weather_forecast_lead_time_hour"], errors="coerce"
            ),
            "weather_forecast_issue_time_is_assumed": frame["weather_forecast_issue_time_is_assumed"],
        }
    )
    optional_weather_fields = [
        "dni_wm2",
        "dhi_wm2",
        "temperature_c",
        "relative_humidity_pct",
        "wind_speed_ms",
        "cloud_cover_pct",
        "cloud_cover_low_pct",
        "cloud_cover_mid_pct",
        "cloud_cover_high_pct",
    ]
    for field in optional_weather_fields:
        if field in frame.columns:
            output[field] = frame[field]
    return output.sort_values("weather_valid_time").reset_index(drop=True)


def _synthetic_price_rows(
    predictions: pd.DataFrame,
    scenario: dict[str, Any],
    *,
    profile_timezone: str,
) -> pd.DataFrame:
    values = scenario.get("values_eur_mwh_by_local_hour")
    if values is None:
        raise ValueError(f"scenario {scenario['id']} does not contain synthetic hourly values.")
    tz = ZoneInfo(profile_timezone)
    local_hours = predictions["weather_valid_time"].dt.tz_convert(tz).dt.hour
    prices = [float(values[int(hour)]) for hour in local_hours]
    return pd.DataFrame(
        {
            "timestamp": predictions["weather_valid_time"],
            "price_scenario_id": scenario["id"],
            "price_scenario_label": scenario.get("label", scenario["id"]),
            "price_source_type": scenario["source_type"],
            "is_real_settlement_price": bool(scenario["is_real_settlement_price"]),
            "price_eur_mwh": prices,
            "load_mw": 0.0,
        }
    )


def build_price_scenario_frame(
    predictions: pd.DataFrame,
    scenario_config: dict[str, Any],
    *,
    feature_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Expand configured price scenarios to hourly Stage12 market rows."""

    validate_market_price_scenarios(scenario_config)
    profile_timezone = scenario_config.get("time_basis", {}).get("profile_timezone", "America/Denver")
    frames: list[pd.DataFrame] = []

    for scenario in scenario_config.get("synthetic_scenarios", []):
        if scenario.get("source_type") == "existing_project_proxy":
            if feature_frame is None:
                raise ValueError(f"scenario {scenario['id']} requires feature_frame with price_eur_mwh.")
            market = feature_frame[["timestamp", "load_mw", "price_eur_mwh"]].copy()
            market["timestamp"] = pd.to_datetime(market["timestamp"], errors="coerce", utc=True)
            market = market.dropna(subset=["timestamp", "price_eur_mwh"]).drop_duplicates("timestamp", keep="last")
            joined = pd.DataFrame({"timestamp": predictions["weather_valid_time"]}).merge(
                market,
                on="timestamp",
                how="left",
                validate="one_to_one",
            )
            missing = int(joined["price_eur_mwh"].isna().sum())
            if missing:
                raise ValueError(f"scenario {scenario['id']} missing OPSD proxy prices for {missing} timestamps.")
            joined["price_scenario_id"] = scenario["id"]
            joined["price_scenario_label"] = scenario.get("label", scenario["id"])
            joined["price_source_type"] = scenario["source_type"]
            joined["is_real_settlement_price"] = bool(scenario["is_real_settlement_price"])
            frames.append(
                joined[
                    [
                        "timestamp",
                        "price_scenario_id",
                        "price_scenario_label",
                        "price_source_type",
                        "is_real_settlement_price",
                        "price_eur_mwh",
                        "load_mw",
                    ]
                ]
            )
        else:
            frames.append(_synthetic_price_rows(predictions, scenario, profile_timezone=profile_timezone))

    if not frames:
        raise ValueError("No price scenario rows were generated.")
    return pd.concat(frames, ignore_index=True).sort_values(["price_scenario_id", "timestamp"]).reset_index(drop=True)


def _scenario_feature_frame(price_rows: pd.DataFrame) -> pd.DataFrame:
    return price_rows[["timestamp", "load_mw", "price_eur_mwh"]].copy()


def run_stage21_rawhide_weather_dispatch(
    weather: pd.DataFrame,
    config: dict[str, Any],
    scenario_config: dict[str, Any],
    *,
    feature_frame: pd.DataFrame | None = None,
    horizon_hours: int = 24,
    lookahead_hours: int = 24,
    performance_ratio: float = 0.82,
    output_paths: dict[str, Path] | None = None,
) -> Stage21RawhideWeatherDispatchResult:
    """Run Stage21 weather-driven Rawhide dispatch across price scenarios."""

    output_paths = output_paths or {}
    reference = _reference_site(config)
    predictions = build_weather_driven_predictions(
        weather,
        config,
        horizon_hours=horizon_hours,
        performance_ratio=performance_ratio,
    )
    price_frame = build_price_scenario_frame(predictions, scenario_config, feature_frame=feature_frame)

    result_frames: list[pd.DataFrame] = []
    metric_frames: list[pd.DataFrame] = []
    scenario_reports: dict[str, Any] = {}
    dispatch_modes = ("smooth", "economic")
    for scenario_id, scenario_prices in price_frame.groupby("price_scenario_id", sort=False):
        scenario_feature = _scenario_feature_frame(scenario_prices)
        scenario_meta = scenario_prices.iloc[0]
        scenario_reports[str(scenario_id)] = {
            "price_source_type": scenario_meta["price_source_type"],
            "is_real_settlement_price": bool(scenario_meta["is_real_settlement_price"]),
            "dispatch_modes": {},
        }

        for dispatch_mode in dispatch_modes:
            dispatch = run_stage12_rolling_optimization(
                predictions,
                scenario_feature,
                config,
                horizon_hours=horizon_hours,
                lookahead_hours=lookahead_hours,
                action_step_kw=100.0,
                dispatch_mode=dispatch_mode,
                smooth_power_ramp_limit_kw=250.0,
                smooth_action_step_kw=250.0,
            )
            scenario_results = dispatch.results.copy()
            scenario_metrics = dispatch.metrics.copy()
            for frame in (scenario_results, scenario_metrics):
                frame["price_scenario_id"] = scenario_id
                frame["price_scenario_label"] = scenario_meta["price_scenario_label"]
                frame["price_source_type"] = scenario_meta["price_source_type"]
                frame["is_real_settlement_price"] = bool(scenario_meta["is_real_settlement_price"])
                frame["is_measured_rawhide_generation"] = False
            result_frames.append(scenario_results)
            metric_frames.append(scenario_metrics)
            scenario_reports[str(scenario_id)]["dispatch_modes"][dispatch_mode] = {
                "dispatch_mode_label": dispatch.report["dispatch_mode_label"],
                "quality_gates": dispatch.report["quality_gates"],
                "comparison_summary": dispatch.report["comparison_summary"],
            }

    dispatch_results = pd.concat(result_frames, ignore_index=True)
    dispatch_metrics = pd.concat(metric_frames, ignore_index=True)
    smooth_metrics = dispatch_metrics[
        (dispatch_metrics["scenario"] == "rolling_optimization")
        & (dispatch_metrics["dispatch_mode"] == "smooth")
    ]
    quality_gates = {
        "weather_predictions_non_empty": bool(len(predictions) > 0),
        "weather_prediction_kw_within_bounds": bool(
            predictions["prediction_kw"].between(0.0, float(config["site"]["capacity_kw"]) * 1.05 + 1e-9).all()
        ),
        "not_measured_rawhide_generation": bool((predictions["is_measured_rawhide_generation"] == False).all()),
        "actual_kw_is_weather_estimate": bool((predictions["actual_kw_is_measured"] == False).all()),
        "price_scenarios_generated": bool(price_frame["price_scenario_id"].nunique() >= 1),
        "smooth_ramp_constraints_passed": bool(
            len(smooth_metrics) > 0 and smooth_metrics["ramp_constraint_satisfied"].astype(bool).all()
        ),
        "all_dispatch_constraints_passed": bool(
            dispatch_metrics[
                [
                    "soc_within_bounds",
                    "charge_power_within_limit",
                    "discharge_power_within_limit",
                    "no_simultaneous_charge_discharge",
                    "energy_balance_error_within_tolerance",
                ]
            ].all(axis=None)
        ),
    }
    report = {
        "stage": "stage21_rawhide_weather_price_dispatch",
        "reference_site": reference,
        "weather_source_kind": "open_meteo_or_standard_weather_csv",
        "pv_estimation": {
            "method": "capacity_kw * ghi_wm2 / 1000 * performance_ratio",
            "performance_ratio": float(performance_ratio),
            "capacity_kw": float(config["site"]["capacity_kw"]),
            "is_measured_rawhide_generation": False,
            "actual_kw_is_measured": False,
            "settlement_uses_weather_estimated_pv": True,
        },
        "price_scenarios": _json_safe(
            dispatch_metrics[dispatch_metrics["scenario"] == "rolling_optimization"][
                [
                    "price_scenario_id",
                    "price_scenario_label",
                    "price_source_type",
                    "is_real_settlement_price",
                    "dispatch_mode",
                    "dispatch_mode_label",
                ]
            ]
            .drop_duplicates()
            .to_dict(orient="records")
        ),
        "horizon_hours": int(horizon_hours),
        "lookahead_hours": int(lookahead_hours),
        "default_dispatch_mode": "smooth",
        "dispatch_modes": [
            {"dispatch_mode": "smooth", "dispatch_mode_label": "平滑运行调度"},
            {"dispatch_mode": "economic", "dispatch_mode_label": "经济优先调度"},
        ],
        "input_rows": int(len(predictions)),
        "dispatch_timestamp_start": str(predictions["weather_valid_time"].min()),
        "dispatch_timestamp_end": str(predictions["weather_valid_time"].max()),
        "quality_gates": quality_gates,
        "all_quality_gates_passed": bool(all(quality_gates.values())),
        "scenario_reports": _json_safe(scenario_reports),
        "output_paths": {name: str(path) for name, path in output_paths.items()},
        "pitfall": (
            "Stage21 uses Rawhide-site weather to estimate PV output and configurable price scenarios. "
            "It is not measured Rawhide generation and not real Rawhide settlement revenue unless a future "
            "price source is mapped to a verified Rawhide/PRPA settlement node."
        ),
    }
    return Stage21RawhideWeatherDispatchResult(
        weather_predictions=predictions,
        price_scenarios=price_frame,
        dispatch_results=dispatch_results,
        dispatch_metrics=dispatch_metrics,
        report=report,
    )


def write_stage21_json(report: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_stage21_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    metric_rows = metrics[metrics["scenario"] == "rolling_optimization"].copy()
    lines = [
        "# Stage21 Rawhide 天气驱动与电价场景调度报告",
        "",
        "## 1. 阶段定位",
        "",
        "Stage21 在 Stage18 Rawhide 公开容量参数基础上，使用 Rawhide 坐标天气估算 PV 出力，"
        "并通过可配置电价场景评估 Stage12 rolling 调度响应。该阶段不是 Rawhide 实测发电回放，"
        "也不是真实市场结算收益。",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Rawhide 坐标天气"] --> B["GHI -> PV 出力估算"]',
        '    C["电价场景库"] --> D["小时级 price_eur_mwh"]',
        '    B --> E["Stage12 rolling 调度"]',
        '    D --> E',
        '    E --> F["场景收益 / SOC / 约束报告"]',
        "```",
        "",
        "## 2. PV 与价格边界",
        "",
        f"- PV 估算方法: `{report['pv_estimation']['method']}`",
        f"- performance_ratio: `{report['pv_estimation']['performance_ratio']}`",
        f"- 是否 Rawhide 实测发电: `{report['pv_estimation']['is_measured_rawhide_generation']}`",
        f"- 是否真实结算出力: `{report['pv_estimation']['actual_kw_is_measured']}`",
        f"- 是否用天气估算 PV 做结算回放: `{report['pv_estimation']['settlement_uses_weather_estimated_pv']}`",
        "",
        "## 3. Rolling 调度场景对比",
        "",
        f"- 默认展示模式: `{report['default_dispatch_mode']}`",
        "",
        "| 电价场景 | 调度模式 | 来源类型 | 真实结算价格 | 增量收益 EUR | 最大动作变化 kW | 充电 kWh | 放电 kWh | 等效循环 | 短缺 kWh | SOC 区间 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in metric_rows.iterrows():
        lines.append(
            f"| `{row['price_scenario_id']}` | `{row.get('dispatch_mode_label', row.get('dispatch_mode', ''))}` | "
            f"`{row['price_source_type']}` | "
            f"`{bool(row['is_real_settlement_price'])}` | {row['incremental_revenue_eur']:.4f} | "
            f"{row.get('max_storage_power_delta_kw', 0.0):.4f} | "
            f"{row['total_charge_kwh']:.4f} | {row['total_discharge_kwh']:.4f} | "
            f"{row['cycle_equivalent_count']:.4f} | {row['total_shortfall_kwh']:.4f} | "
            f"{row['min_soc']:.3f}-{row['max_soc']:.3f} |"
        )

    lines.extend(["", "## 4. 质量门禁", ""])
    for gate, value in report["quality_gates"].items():
        lines.append(f"- {gate}: `{value}`")
    lines.append(f"- all_quality_gates_passed: `{report['all_quality_gates_passed']}`")

    lines.extend(["", "## 5. 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 标准化 Rawhide 天气输入、构造天气估算 PV 曲线、展开电价场景、复用 Stage12 rolling 调度并输出场景级指标。",
            "- 目标完成情况: Stage21 已形成天气驱动与价格场景可配置的 Rawhide 增强调度仿真闭环。",
            "- 下一阶段可行性: 可接入 API/前端展示；若要提升真实收益可信度，应先完成 SPP RTO/PRPA 节点映射。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
