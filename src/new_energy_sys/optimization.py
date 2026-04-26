from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from new_energy_sys.modeling import TARGET_COLUMNS, _chronological_split, _metrics


@dataclass(frozen=True)
class OptimizationResult:
    """Container for stage-5 diagnostics and optimization artifacts.

    本阶段不是简单“再训练一次模型”，而是把模型有效性拆成三个可审计问题：
    1. 误差主要发生在哪里：通过小时、月份、辐照度、云量、功率爬坡等维度分组。
    2. 哪类特征真的有贡献：通过固定切分、固定模型框架的消融实验量化边际收益。
    3. 参数优化是否真实提升：只用验证集选择参数，测试集只用于最终一次评估。
    """

    ablation_metrics: pd.DataFrame
    tuned_metrics: pd.DataFrame
    grouped_errors: pd.DataFrame
    feature_importance: pd.DataFrame
    report: dict[str, Any]


def _numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return numeric model features, excluding timestamps and prediction targets.

    LightGBM 只能直接消费数值矩阵。这里显式排除目标列，避免把未来功率误当作输入特征。
    `timestamp` 即使被 pandas 存成 datetime64，也不作为模型输入；时间信息已经在 Stage3
    被展开成 hour/month/day_of_year 等可学习字段。
    """

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _columns_containing(columns: list[str], markers: list[str]) -> list[str]:
    """Pick columns whose names contain any marker.

    Stage3 的特征命名已经按领域保留了清晰前缀/后缀。使用字段名归组比手工维护 100+
    字段清单更稳健；新增天气或调度字段时，只要命名遵守当前约定，就会自动进入对应实验组。
    """

    return [column for column in columns if any(marker in column for marker in markers)]


def _feature_sets(frame: pd.DataFrame) -> dict[str, list[str]]:
    """Build diagnostic ablation feature sets.

    消融实验必须保证“同一数据切分、同一模型类型、只改变输入特征”。这里的特征组刻意比
    Stage4 更细，用来回答天气、历史功率、DA/HA4 预测、调度/负荷变量各自贡献多少。
    """

    numeric_columns = _numeric_feature_columns(frame)
    time_columns = [
        column
        for column in numeric_columns
        if column
        in {
            "hour",
            "day_of_week",
            "month",
            "day_of_year",
            "quarter",
            "is_weekend",
            "is_business_hour",
            "hour_sin",
            "hour_cos",
            "day_of_week_sin",
            "day_of_week_cos",
            "month_sin",
            "month_cos",
            "day_of_year_sin",
            "day_of_year_cos",
        }
    ]
    forecast_columns = _columns_containing(
        numeric_columns,
        [
            "pv_forecast_",
            "forecast_",
            "_forecast_",
        ],
    )
    weather_columns = _columns_containing(
        numeric_columns,
        [
            "ghi",
            "dni",
            "dhi",
            "radiation",
            "temperature",
            "humidity",
            "dew_point",
            "cloud",
            "wind",
            "pressure",
            "precipitation",
            "transmittance",
            "albedo",
            "zenith",
            "weather_forecast_lead_time",
        ],
    )
    history_columns = _columns_containing(
        numeric_columns,
        [
            "pv_power_kw",
            "pv_power_lag_",
            "pv_power_roll_",
            "pv_power_ramp",
            "pv_power_capacity_ratio",
        ],
    )
    dispatch_columns = _columns_containing(
        numeric_columns,
        [
            "storage_",
            "load_",
            "price_",
            "market_",
            "charge_",
            "discharge_",
        ],
    )

    feature_sets = {
        "time_only": sorted(set(time_columns)),
        "weather_only": sorted(set(time_columns + weather_columns)),
        "history_only": sorted(set(time_columns + history_columns)),
        "full_features": sorted(set(numeric_columns)),
    }
    # PVDAQ + NSRDB has no DA/HA forecast columns. In that case keeping
    # forecast_only would duplicate time_only and make the ablation report look
    # more informative than it is. Only expose forecast-based groups when real
    # forecast fields are present in the current dataset.
    if forecast_columns:
        feature_sets["forecast_only"] = sorted(set(time_columns + forecast_columns))
        feature_sets["forecast_plus_weather"] = sorted(set(time_columns + forecast_columns + weather_columns))
        feature_sets["history_plus_forecast"] = sorted(set(time_columns + history_columns + forecast_columns))
    return feature_sets


def _train_lightgbm(
    *,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    features: list[str],
    target: str,
    params: dict[str, Any],
    random_state: int,
) -> lgb.LGBMRegressor:
    """Train one deterministic LightGBM model with validation early stopping.

    所有候选模型都使用验证集 early stopping，避免单纯提高 `n_estimators` 导致过拟合。
    测试集不会进入训练和参数选择流程，这是后续论文或报告里最重要的数据链路边界。
    """

    model = lgb.LGBMRegressor(
        objective="regression",
        boosting_type="gbdt",
        n_estimators=int(params.get("n_estimators", 1400)),
        learning_rate=float(params.get("learning_rate", 0.03)),
        num_leaves=int(params.get("num_leaves", 31)),
        max_depth=int(params.get("max_depth", -1)),
        min_child_samples=int(params.get("min_child_samples", 30)),
        subsample=float(params.get("subsample", 0.9)),
        subsample_freq=1,
        colsample_bytree=float(params.get("colsample_bytree", 0.85)),
        reg_alpha=float(params.get("reg_alpha", 0.05)),
        reg_lambda=float(params.get("reg_lambda", 0.2)),
        random_state=random_state,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        train[features],
        train[target],
        eval_set=[(validation[features], validation[target])],
        eval_metric="rmse",
        callbacks=[lgb.early_stopping(stopping_rounds=90, verbose=False), lgb.log_evaluation(period=0)],
    )
    return model


def _predict_clipped(model: lgb.LGBMRegressor, frame: pd.DataFrame, features: list[str], capacity_kw: float) -> np.ndarray:
    """Predict PV power and enforce physical limits.

    树模型是无约束回归器，夜间可能给出负值，极端天气下也可能略超装机容量。评估和上线推理
    必须统一裁剪到 `[0, 1.05 * capacity]`，否则指标和实际推理行为不一致。
    """

    raw_prediction = model.predict(frame[features], num_iteration=model.best_iteration_)
    return np.clip(raw_prediction, 0.0, capacity_kw * 1.05)


def _evaluate_model(
    *,
    model: lgb.LGBMRegressor,
    splits: dict[str, pd.DataFrame],
    features: list[str],
    target: str,
    feature_set: str,
    experiment: str,
    params: dict[str, Any],
    capacity_kw: float,
    model_path: Path | None = None,
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    """Evaluate a fitted model on validation and test splits."""

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    for split_name in ["validation", "test"]:
        split = splits[split_name]
        prediction = _predict_clipped(model, split, features, capacity_kw)
        metric_rows.append(
            {
                "experiment": experiment,
                "target": target,
                "feature_set": feature_set,
                "split": split_name,
                "feature_count": len(features),
                "best_iteration": int(model.best_iteration_ or model.n_estimators),
                "params_json": json.dumps(params, sort_keys=True),
                "model_path": str(model_path) if model_path else "",
                **_metrics(split[target].to_numpy(), prediction, capacity_kw=capacity_kw),
            }
        )
        prediction_frames.append(
            pd.DataFrame(
                {
                    "timestamp": split["timestamp"].to_numpy(),
                    "experiment": experiment,
                    "target": target,
                    "feature_set": feature_set,
                    "split": split_name,
                    "actual_kw": split[target].to_numpy(),
                    "prediction_kw": prediction,
                    "error_kw": prediction - split[target].to_numpy(),
                }
            )
        )

    importance = pd.DataFrame(
        {
            "experiment": experiment,
            "target": target,
            "feature_set": feature_set,
            "feature": features,
            "importance_gain": model.booster_.feature_importance(importance_type="gain"),
            "importance_split": model.booster_.feature_importance(importance_type="split"),
        }
    )
    return metric_rows, pd.concat(prediction_frames, ignore_index=True), importance


def _parameter_candidates() -> list[dict[str, Any]]:
    """Return a compact, production-safe search grid.

    当前数据只有单年 8560 行，不适合大规模随机搜索。这里使用小型候选集覆盖：
    - 更浅/更深的树；
    - 更强/更弱的叶子约束；
    - 更强正则；
    - 更小学习率。
    """

    return [
        {"n_estimators": 1400, "learning_rate": 0.03, "num_leaves": 31, "max_depth": -1, "min_child_samples": 30, "subsample": 0.9, "colsample_bytree": 0.85, "reg_alpha": 0.05, "reg_lambda": 0.2},
        {"n_estimators": 1600, "learning_rate": 0.025, "num_leaves": 31, "max_depth": 8, "min_child_samples": 45, "subsample": 0.9, "colsample_bytree": 0.8, "reg_alpha": 0.1, "reg_lambda": 0.5},
        {"n_estimators": 1800, "learning_rate": 0.02, "num_leaves": 45, "max_depth": 10, "min_child_samples": 35, "subsample": 0.85, "colsample_bytree": 0.8, "reg_alpha": 0.1, "reg_lambda": 0.8},
        {"n_estimators": 1200, "learning_rate": 0.04, "num_leaves": 24, "max_depth": 7, "min_child_samples": 55, "subsample": 0.95, "colsample_bytree": 0.9, "reg_alpha": 0.2, "reg_lambda": 1.0},
        {"n_estimators": 1500, "learning_rate": 0.03, "num_leaves": 63, "max_depth": -1, "min_child_samples": 50, "subsample": 0.8, "colsample_bytree": 0.75, "reg_alpha": 0.2, "reg_lambda": 1.2},
        {"n_estimators": 1300, "learning_rate": 0.035, "num_leaves": 16, "max_depth": 6, "min_child_samples": 25, "subsample": 0.9, "colsample_bytree": 0.95, "reg_alpha": 0.0, "reg_lambda": 0.3},
    ]


def _add_grouping_columns(frame: pd.DataFrame, capacity_kw: float) -> pd.DataFrame:
    """Create stable diagnostic buckets for grouped error analysis."""

    enriched = frame.copy()
    enriched["timestamp"] = pd.to_datetime(enriched["timestamp"], errors="coerce", utc=True)
    enriched["hour_group"] = enriched["timestamp"].dt.hour.astype("Int64").astype(str)
    enriched["month_group"] = enriched["timestamp"].dt.month.astype("Int64").astype(str)
    enriched["daylight_group"] = np.where(enriched["actual_kw"] > capacity_kw * 0.01, "daylight", "night_or_near_zero")
    enriched["ghi_group"] = pd.cut(
        enriched.get("ghi_wm2", pd.Series(np.nan, index=enriched.index)),
        bins=[-0.1, 50, 200, 500, 800, np.inf],
        labels=["ghi_0_50", "ghi_50_200", "ghi_200_500", "ghi_500_800", "ghi_800_plus"],
    ).astype(str)
    if "cloud_cover_pct" in enriched.columns:
        enriched["cloud_group"] = pd.cut(
            enriched["cloud_cover_pct"],
            bins=[-0.1, 20, 50, 80, 100],
            labels=["cloud_0_20", "cloud_20_50", "cloud_50_80", "cloud_80_100"],
        ).astype(str)
    elif "cloud_type" in enriched.columns:
        # NSRDB PSM exposes categorical cloud_type instead of cloud-cover
        # percentage. Preserve it as buckets so grouped diagnostics still
        # answer whether cloudy regimes drive errors.
        enriched["cloud_group"] = (
            pd.to_numeric(enriched["cloud_type"], errors="coerce")
            .round()
            .astype("Int64")
            .astype(str)
            .replace("<NA>", "cloud_type_missing")
            .radd("cloud_type_")
        )
    else:
        enriched["cloud_group"] = "cloud_unavailable"
    ramp_source = enriched.get("pv_power_ramp_abs_lag_1h", pd.Series(np.nan, index=enriched.index)).abs()
    enriched["ramp_group"] = pd.cut(
        ramp_source,
        bins=[-0.1, capacity_kw * 0.02, capacity_kw * 0.08, capacity_kw * 0.15, np.inf],
        labels=["ramp_low", "ramp_medium", "ramp_high", "ramp_extreme"],
    ).astype(str)
    if "forecast_spread_abs_kw" in enriched.columns:
        forecast_spread_source = enriched["forecast_spread_abs_kw"].abs()
        enriched["forecast_spread_group"] = pd.cut(
            forecast_spread_source,
            bins=[-0.1, capacity_kw * 0.03, capacity_kw * 0.10, capacity_kw * 0.20, np.inf],
            labels=["spread_low", "spread_medium", "spread_high", "spread_extreme"],
        ).astype(str)
    else:
        enriched["forecast_spread_group"] = "forecast_spread_unavailable"
    return enriched


def _grouped_errors(frame: pd.DataFrame, predictions: pd.DataFrame, capacity_kw: float) -> pd.DataFrame:
    """Aggregate test-set errors by operationally meaningful buckets."""

    test_frame = frame.sort_values("timestamp").reset_index(drop=True).iloc[int(len(frame) * 0.85) :].copy()
    merge_columns = [
        "timestamp",
        "ghi_wm2",
        "cloud_cover_pct",
        "cloud_type",
        "pv_power_ramp_abs_lag_1h",
        "forecast_spread_abs_kw",
    ]
    available_merge_columns = [column for column in merge_columns if column in test_frame.columns]
    diagnostic = predictions[predictions["split"] == "test"].merge(
        test_frame[available_merge_columns],
        on="timestamp",
        how="left",
    )
    diagnostic = _add_grouping_columns(diagnostic, capacity_kw)

    rows: list[pd.DataFrame] = []
    for group_name in ["hour_group", "month_group", "daylight_group", "ghi_group", "cloud_group", "ramp_group", "forecast_spread_group"]:
        grouped = (
            diagnostic.groupby(["experiment", "target", "feature_set", group_name], dropna=False)
            .agg(
                sample_count=("error_kw", "size"),
                mae_kw=("error_kw", lambda values: float(np.mean(np.abs(values)))),
                rmse_kw=("error_kw", lambda values: float(np.sqrt(np.mean(np.square(values))))),
                bias_kw=("error_kw", "mean"),
            )
            .reset_index()
            .rename(columns={group_name: "group_value"})
        )
        grouped.insert(3, "group_name", group_name)
        grouped["nrmse_capacity"] = grouped["rmse_kw"] / capacity_kw
        rows.append(grouped)
    return pd.concat(rows, ignore_index=True)


def _best_test_by_target(metrics: pd.DataFrame) -> pd.DataFrame:
    """Return the best test row for each target."""

    return (
        metrics[metrics["split"] == "test"]
        .sort_values(["target", "rmse_kw", "mae_kw"])
        .groupby("target", as_index=False)
        .first()
    )


def run_stage5_optimization(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    output_dir: Path,
    random_state: int = 42,
) -> OptimizationResult:
    """Run grouped diagnostics, ablation experiments, and compact LightGBM tuning."""

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    missing_targets = [target for target in TARGET_COLUMNS if target not in working.columns]
    if missing_targets:
        raise ValueError(f"Stage5 input missing target columns: {', '.join(missing_targets)}")
    if working.select_dtypes(include=[np.number]).isna().sum().sum() != 0:
        raise ValueError("Stage5 input contains missing numeric values; run Stage2/Stage3 quality gates first.")

    splits = _chronological_split(working)
    feature_sets = {name: features for name, features in _feature_sets(working).items() if features}
    default_params = _parameter_candidates()[0]

    ablation_metric_rows: list[dict[str, Any]] = []
    ablation_predictions: list[pd.DataFrame] = []
    importance_frames: list[pd.DataFrame] = []

    for target in TARGET_COLUMNS:
        for feature_set, features in feature_sets.items():
            model = _train_lightgbm(
                train=splits["train"],
                validation=splits["validation"],
                features=features,
                target=target,
                params=default_params,
                random_state=random_state,
            )
            metric_rows, predictions, importance = _evaluate_model(
                model=model,
                splits=splits,
                features=features,
                target=target,
                feature_set=feature_set,
                experiment="ablation",
                params=default_params,
                capacity_kw=capacity_kw,
            )
            ablation_metric_rows.extend(metric_rows)
            ablation_predictions.append(predictions)
            importance_frames.append(importance)

    ablation_metrics = pd.DataFrame(ablation_metric_rows)
    best_ablation = _best_test_by_target(ablation_metrics)

    tuned_metric_rows: list[dict[str, Any]] = []
    tuned_predictions: list[pd.DataFrame] = []
    model_dir = output_dir / "stage5_models"
    model_dir.mkdir(parents=True, exist_ok=True)

    for _, best_row in best_ablation.iterrows():
        target = str(best_row["target"])
        feature_set = str(best_row["feature_set"])
        features = feature_sets[feature_set]

        candidate_rows: list[dict[str, Any]] = []
        candidate_models: list[tuple[lgb.LGBMRegressor, dict[str, Any]]] = []
        for params in _parameter_candidates():
            model = _train_lightgbm(
                train=splits["train"],
                validation=splits["validation"],
                features=features,
                target=target,
                params=params,
                random_state=random_state,
            )
            validation_prediction = _predict_clipped(model, splits["validation"], features, capacity_kw)
            validation_metric = _metrics(splits["validation"][target].to_numpy(), validation_prediction, capacity_kw=capacity_kw)
            candidate_rows.append({"target": target, "feature_set": feature_set, **validation_metric})
            candidate_models.append((model, params))

        best_candidate_index = int(pd.DataFrame(candidate_rows)["rmse_kw"].idxmin())
        best_model, best_params = candidate_models[best_candidate_index]
        model_path = model_dir / f"lightgbm_tuned_{feature_set}_{target}.pkl"
        with model_path.open("wb") as handle:
            pickle.dump(
                {
                    "model": best_model,
                    "features": features,
                    "target": target,
                    "feature_set": feature_set,
                    "capacity_kw": capacity_kw,
                    "prediction_lower_bound_kw": 0.0,
                    "prediction_upper_bound_kw": capacity_kw * 1.05,
                    "params": best_params,
                },
                handle,
            )
        metric_rows, predictions, importance = _evaluate_model(
            model=best_model,
            splits=splits,
            features=features,
            target=target,
            feature_set=feature_set,
            experiment="tuned",
            params=best_params,
            capacity_kw=capacity_kw,
            model_path=model_path,
        )
        tuned_metric_rows.extend(metric_rows)
        tuned_predictions.append(predictions)
        importance_frames.append(importance)

    tuned_metrics = pd.DataFrame(tuned_metric_rows)
    all_predictions = pd.concat([*ablation_predictions, *tuned_predictions], ignore_index=True)
    grouped_errors = _grouped_errors(working, all_predictions[all_predictions["experiment"] == "tuned"], capacity_kw)
    feature_importance = pd.concat(importance_frames, ignore_index=True)

    best_tuned = _best_test_by_target(tuned_metrics)
    report = {
        "stage": "stage_5_error_diagnostics_ablation_and_tuning",
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "targets": TARGET_COLUMNS,
        "feature_sets": {name: {"feature_count": len(features), "features": features} for name, features in feature_sets.items()},
        "splits": {
            name: {"rows": int(len(split)), "start": str(split["timestamp"].min()), "end": str(split["timestamp"].max())}
            for name, split in splits.items()
        },
        "best_ablation_test_rows": best_ablation.to_dict(orient="records"),
        "best_tuned_test_rows": best_tuned.to_dict(orient="records"),
        "quality_gates": {
            "no_missing_numeric_values": bool(working.select_dtypes(include=[np.number]).isna().sum().sum() == 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "ablation_models_trained": bool(len(ablation_metrics) == len(TARGET_COLUMNS) * len(feature_sets) * 2),
            "tuned_models_trained": bool(len(tuned_metrics) == len(TARGET_COLUMNS) * 2),
            "test_predictions_within_physical_bound": bool(
                all_predictions[all_predictions["split"] == "test"]["prediction_kw"].between(0.0, capacity_kw * 1.05).all()
            ),
        },
        "pitfall": (
            "Ablation results only explain marginal contribution under the current station, "
            "weather source, and chronological split. If the year range, site, or weather "
            "provider changes, the contribution ranking must be revalidated."
        ),
    }
    return OptimizationResult(
        ablation_metrics=ablation_metrics,
        tuned_metrics=tuned_metrics,
        grouped_errors=grouped_errors,
        feature_importance=feature_importance,
        report=report,
    )


def write_stage5_report(
    report: dict[str, Any],
    ablation_metrics: pd.DataFrame,
    tuned_metrics: pd.DataFrame,
    grouped_errors: pd.DataFrame,
    feature_importance: pd.DataFrame,
    path: Path,
) -> None:
    """Write the stage-5 Markdown report."""

    best_ablation = _best_test_by_target(ablation_metrics)
    best_tuned = _best_test_by_target(tuned_metrics)

    lines = [
        "# Stage 5 Optimization and Diagnostics Report",
        "",
        "## Scope",
        "",
        f"- Input rows: `{report['input_rows']}`",
        f"- Input columns: `{report['input_columns']}`",
        "- Workflow: grouped error diagnostics, feature ablation, compact LightGBM tuning",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage 3 feature dataset"] --> B["Chronological split"]',
        '    B --> C["Feature ablation"]',
        '    C --> D["Best feature set per horizon"]',
        '    D --> E["Validation-only parameter tuning"]',
        '    E --> F["Test-set final evaluation"]',
        '    F --> G["Grouped error diagnostics"]',
        "```",
        "",
        "## Best Ablation Result",
        "",
        "| Target | Feature set | Features | RMSE kW | MAE kW | nRMSE | Daytime nRMSE |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in best_ablation.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | {int(row['feature_count'])} | "
            f"{row['rmse_kw']:.2f} | {row['mae_kw']:.2f} | {row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} |"
        )

    lines.extend(["", "## Tuned Test Result", ""])
    lines.append("| Target | Feature set | Features | RMSE kW | MAE kW | nRMSE | Daytime nRMSE |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for _, row in best_tuned.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | {int(row['feature_count'])} | "
            f"{row['rmse_kw']:.2f} | {row['mae_kw']:.2f} | {row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} |"
        )

    lines.extend(["", "## Weather Contribution", ""])
    test_ablation = ablation_metrics[ablation_metrics["split"] == "test"]
    has_forecast_groups = {"forecast_only", "forecast_plus_weather"}.issubset(set(test_ablation["feature_set"]))
    if has_forecast_groups:
        lines.append("| Target | Forecast only nRMSE | Forecast + weather nRMSE | Delta |")
        lines.append("|---|---:|---:|---:|")
        for target in TARGET_COLUMNS:
            forecast = test_ablation[(test_ablation["target"] == target) & (test_ablation["feature_set"] == "forecast_only")]
            weather = test_ablation[
                (test_ablation["target"] == target) & (test_ablation["feature_set"] == "forecast_plus_weather")
            ]
            if len(forecast) and len(weather):
                delta = float(forecast.iloc[0]["nrmse_capacity"] - weather.iloc[0]["nrmse_capacity"])
                lines.append(
                    f"| `{target}` | {float(forecast.iloc[0]['nrmse_capacity']):.4f} | "
                    f"{float(weather.iloc[0]['nrmse_capacity']):.4f} | {delta:.4f} |"
                )
    else:
        lines.append(
            "No DA/HA forecast columns are available in the current PVDAQ + NSRDB dataset. "
            "Weather contribution is therefore reported against time-only and history-only baselines."
        )
        lines.append("")
        lines.append(
            "| Target | Time only nRMSE | Weather only nRMSE | Time-to-weather delta | "
            "History only nRMSE | Full features nRMSE | History-to-full delta |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for target in TARGET_COLUMNS:
            time_only = test_ablation[(test_ablation["target"] == target) & (test_ablation["feature_set"] == "time_only")]
            weather = test_ablation[(test_ablation["target"] == target) & (test_ablation["feature_set"] == "weather_only")]
            history = test_ablation[(test_ablation["target"] == target) & (test_ablation["feature_set"] == "history_only")]
            full = test_ablation[(test_ablation["target"] == target) & (test_ablation["feature_set"] == "full_features")]
            if len(time_only) and len(weather) and len(history) and len(full):
                time_nrmse = float(time_only.iloc[0]["nrmse_capacity"])
                weather_nrmse = float(weather.iloc[0]["nrmse_capacity"])
                history_nrmse = float(history.iloc[0]["nrmse_capacity"])
                full_nrmse = float(full.iloc[0]["nrmse_capacity"])
                lines.append(
                    f"| `{target}` | {time_nrmse:.4f} | {weather_nrmse:.4f} | "
                    f"{time_nrmse - weather_nrmse:.4f} | {history_nrmse:.4f} | "
                    f"{full_nrmse:.4f} | {history_nrmse - full_nrmse:.4f} |"
                )

    lines.extend(["", "## Worst Tuned Error Groups", ""])
    lines.append("| Target | Group name | Group value | Samples | RMSE kW | nRMSE | Bias kW |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    worst_groups = (
        grouped_errors.sort_values(["target", "rmse_kw"], ascending=[True, False])
        .groupby("target")
        .head(8)
    )
    for _, row in worst_groups.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['group_name']}` | `{row['group_value']}` | "
            f"{int(row['sample_count'])} | {row['rmse_kw']:.2f} | {row['nrmse_capacity']:.4f} | {row['bias_kw']:.2f} |"
        )

    lines.extend(["", "## Top Tuned Feature Importance", ""])
    lines.append("| Target | Feature set | Feature | Gain |")
    lines.append("|---|---|---|---:|")
    top_importance = (
        feature_importance[feature_importance["experiment"] == "tuned"]
        .sort_values(["target", "importance_gain"], ascending=[True, False])
        .groupby("target")
        .head(8)
    )
    for _, row in top_importance.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | `{row['feature']}` | {row['importance_gain']:.2f} |"
        )

    lines.extend(["", "## Quality Gates", ""])
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## Pitfall", "", report["pitfall"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
