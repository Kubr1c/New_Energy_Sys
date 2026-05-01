"""光伏功率预测建模模块。

模块设计原则：
- 时间序列数据必须按时间顺序切分，禁止随机切分以防数据泄漏
- 模型评估须区分白天/夜间样本，夜间零功率下 MAPE 无意义
- 所有预测值必须物理裁剪到 [0, 1.05 × 装机容量]，树模型无约束回归可能产出负值
- 使用验证集 early stopping 防止过拟合，测试集仅用于最终一次性评估

本模块对应项目 Stage 4 的 LightGBM 基线建模功能，负责：
1. 按时间顺序切分训练/验证/测试集
2. 定义多组特征集（预报+时间、天气增强、全量特征）验证数据链路
3. 训练 LightGBM 回归器并收集误差指标、预测值、特征重要性
4. 输出结构化报告与模型序列化文件
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd


TARGET_COLUMNS = [
    "target_pv_power_t_plus_1h",
    "target_pv_power_t_plus_6h",
    "target_pv_power_t_plus_24h",
]


@dataclass(frozen=True)
class BaselineModelingResult:
    """第四阶段 LightGBM 基线建模结果。

    metrics 保存所有实验组的误差指标；predictions 保存验证集和测试集预测；
    feature_importance 保存每个模型的增益重要性，用于后续判断特征是否有效。
    """

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    feature_importance: pd.DataFrame
    report: dict[str, Any]


def _chronological_split(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """按时间顺序切分 train/validation/test。

    时间序列任务禁止随机切分。随机切分会把相邻小时、相邻天气状态和未来季节模式
    泄漏到训练集，导致测试指标虚高。
    """

    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    train_end = int(len(ordered) * 0.70)
    valid_end = int(len(ordered) * 0.85)
    return {
        "train": ordered.iloc[:train_end].copy(),
        "validation": ordered.iloc[train_end:valid_end].copy(),
        "test": ordered.iloc[valid_end:].copy(),
    }


def _feature_sets(columns: list[str]) -> dict[str, list[str]]:
    """定义第四阶段基线特征组。

    三组模型用于验证数据链路是否正确：
    - forecast_time：只用 DA/HA4 与时间特征，作为低复杂度基线；
    - weather_enhanced：加入外部天气字段，验证天气补充是否提供增益；
    - full_features：使用全部可用数值特征，验证完整特征链路可训练。
    """

    excluded = {"timestamp", *TARGET_COLUMNS}
    numeric_columns = [column for column in columns if column not in excluded]

    time_markers = [
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
    ]
    forecast_markers = [
        "pv_forecast_da_kw",
        "pv_forecast_ha4_kw",
        "forecast_",
        "_forecast_",
    ]
    weather_markers = [
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
    ]

    forecast_time = [
        column
        for column in numeric_columns
        if column in time_markers or any(marker in column for marker in forecast_markers)
    ]
    weather_enhanced = [
        column
        for column in numeric_columns
        if column in forecast_time or any(marker in column for marker in weather_markers)
    ]
    full_features = numeric_columns

    return {
        "forecast_time": sorted(set(forecast_time)),
        "weather_enhanced": sorted(set(weather_enhanced)),
        "full_features": sorted(set(full_features)),
    }


def _root_mean_squared_error(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """计算均方根误差（RMSE），不依赖特定版本的 sklearn。"""

    return float(np.sqrt(np.mean(np.square(y_pred - y_true))))


def _metrics(y_true: np.ndarray, y_pred: np.ndarray, *, capacity_kw: float) -> dict[str, float]:
    """计算光伏预测核心误差指标。

    MAPE 仅在白天/非零功率样本上计算，因为光伏在夜间输出为零，
    对零值附近计算 MAPE 在数值上无意义。

    Args:
        y_true: 真实功率数组（kW）
        y_pred: 预测功率数组（kW）
        capacity_kw: 装机容量（kW），用于归一化和白天判定阈值

    Returns:
        包含 mae_kw, rmse_kw, nrmse_capacity, bias_kw, daytime_* 等指标的字典
    """

    error = y_pred - y_true
    absolute_error = np.abs(error)
    daytime_mask = y_true > capacity_kw * 0.01
    daytime_true = y_true[daytime_mask]
    daytime_pred = y_pred[daytime_mask]

    result = {
        "mae_kw": float(np.mean(absolute_error)),
        "rmse_kw": _root_mean_squared_error(y_true, y_pred),
        "nrmse_capacity": _root_mean_squared_error(y_true, y_pred) / capacity_kw,
        "bias_kw": float(np.mean(error)),
        "daytime_sample_count": int(daytime_mask.sum()),
    }
    if len(daytime_true):
        daytime_rmse = _root_mean_squared_error(daytime_true, daytime_pred)
        daytime_mae = float(np.mean(np.abs(daytime_pred - daytime_true)))
        result.update(
            {
                "daytime_mae_kw": daytime_mae,
                "daytime_rmse_kw": daytime_rmse,
                "daytime_nrmse_capacity": daytime_rmse / capacity_kw,
                "daytime_mape": float(np.mean(np.abs((daytime_pred - daytime_true) / daytime_true))),
            }
        )
    else:
        result.update(
            {
                "daytime_mae_kw": np.nan,
                "daytime_rmse_kw": np.nan,
                "daytime_nrmse_capacity": np.nan,
                "daytime_mape": np.nan,
            }
        )
    return result


def _train_one_model(
    *,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    features: list[str],
    target: str,
    random_state: int,
) -> lgb.LGBMRegressor:
    """使用确定性保守超参训练单个 LightGBM 回归器。

    Args:
        train: 训练集 DataFrame
        validation: 验证集 DataFrame，用于 early stopping
        features: 输入特征列名列表
        target: 目标列名
        random_state: 随机种子，保证可复现

    Returns:
        训练完成的 LGBMRegressor 模型
    """

    model = lgb.LGBMRegressor(
        objective="regression",
        boosting_type="gbdt",
        n_estimators=1200,
        learning_rate=0.03,
        num_leaves=31,
        max_depth=-1,
        min_child_samples=30,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.85,
        reg_alpha=0.05,
        reg_lambda=0.2,
        random_state=random_state,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        train[features],
        train[target],
        eval_set=[(validation[features], validation[target])],
        eval_metric="rmse",
        callbacks=[lgb.early_stopping(stopping_rounds=80, verbose=False), lgb.log_evaluation(period=0)],
    )
    return model


def predict_with_bundle(bundle: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """使用已保存的模型捆绑包进行推理，并施加物理裁剪。

    LightGBM 是无约束回归器，在夜间零输出附近可能预测出微小负值。
    生产推理面必须始终应用与评估阶段相同的物理裁剪，否则指标与实际行为不一致。

    Args:
        bundle: 包含 model, features, prediction_lower_bound_kw, prediction_upper_bound_kw 的字典
        frame: 待推理的 DataFrame

    Returns:
        裁剪后的预测功率数组（kW）
    """

    features = bundle["features"]
    prediction = bundle["model"].predict(frame[features], num_iteration=bundle["model"].best_iteration_)
    lower = float(bundle.get("prediction_lower_bound_kw", 0.0))
    upper = float(bundle["prediction_upper_bound_kw"])
    return np.clip(prediction, lower, upper)


def run_lightgbm_baseline(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    model_dir: Path,
    random_state: int = 42,
) -> BaselineModelingResult:
    """运行 LightGBM 基线实验并返回所有产物。

    对每个目标列 × 每个特征组，训练模型并在验证集/测试集上评估，
    序列化模型并汇总指标、预测值、特征重要性。

    Args:
        frame: Stage 3 输出的特征数据集
        config: 项目配置字典，须包含 site.capacity_kw
        model_dir: 模型序列化输出目录
        random_state: 随机种子

    Returns:
        BaselineModelingResult 包含 metrics, predictions, feature_importance, report
    """

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    missing_targets = [target for target in TARGET_COLUMNS if target not in working.columns]
    if missing_targets:
        raise ValueError(f"阶段四输入缺少预测标签: {', '.join(missing_targets)}")

    numeric_columns = working.select_dtypes(include=[np.number]).columns.tolist()
    feature_sets = _feature_sets(["timestamp", *numeric_columns])
    splits = _chronological_split(working)

    model_dir.mkdir(parents=True, exist_ok=True)
    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    importance_rows: list[pd.DataFrame] = []

    for target in TARGET_COLUMNS:
        for feature_set_name, features in feature_sets.items():
            if not features:
                raise ValueError(f"特征组为空: {feature_set_name}")

            model = _train_one_model(
                train=splits["train"],
                validation=splits["validation"],
                features=features,
                target=target,
                random_state=random_state,
            )

            model_path = model_dir / f"lightgbm_{feature_set_name}_{target}.pkl"
            with model_path.open("wb") as handle:
                pickle.dump(
                    {
                        "model": model,
                        "features": features,
                        "target": target,
                        "feature_set": feature_set_name,
                        "capacity_kw": capacity_kw,
                        "prediction_lower_bound_kw": 0.0,
                        "prediction_upper_bound_kw": capacity_kw * 1.05,
                    },
                    handle,
                )

            for split_name in ["validation", "test"]:
                split = splits[split_name]
                prediction = model.predict(split[features], num_iteration=model.best_iteration_)
                prediction = np.clip(prediction, 0.0, capacity_kw * 1.05)
                metric = _metrics(split[target].to_numpy(), prediction, capacity_kw=capacity_kw)
                metric_rows.append(
                    {
                        "target": target,
                        "feature_set": feature_set_name,
                        "split": split_name,
                        "feature_count": len(features),
                        "best_iteration": int(model.best_iteration_ or model.n_estimators),
                        "model_path": str(model_path),
                        **metric,
                    }
                )

                prediction_frames.append(
                    pd.DataFrame(
                        {
                            "timestamp": split["timestamp"].to_numpy(),
                            "target": target,
                            "feature_set": feature_set_name,
                            "split": split_name,
                            "actual_kw": split[target].to_numpy(),
                            "prediction_kw": prediction,
                            "error_kw": prediction - split[target].to_numpy(),
                        }
                    )
                )

            importance_rows.append(
                pd.DataFrame(
                    {
                        "target": target,
                        "feature_set": feature_set_name,
                        "feature": features,
                        "importance_gain": model.booster_.feature_importance(importance_type="gain"),
                        "importance_split": model.booster_.feature_importance(importance_type="split"),
                    }
                )
            )

    metrics = pd.DataFrame(metric_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    feature_importance = pd.concat(importance_rows, ignore_index=True)

    report = {
        "stage": "stage_4_lightgbm_baseline",
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "targets": TARGET_COLUMNS,
        "feature_sets": {name: {"feature_count": len(features), "features": features} for name, features in feature_sets.items()},
        "splits": {
            name: {
                "rows": int(len(split)),
                "start": str(split["timestamp"].min()),
                "end": str(split["timestamp"].max()),
            }
            for name, split in splits.items()
        },
        "best_test_rows": metrics[metrics["split"] == "test"]
        .sort_values(["target", "rmse_kw"])
        .groupby("target", as_index=False)
        .first()
        .to_dict(orient="records"),
        "quality_gates": {
            "lightgbm_imported": True,
            "no_missing_input_values": bool(working.isna().sum().sum() == 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "all_models_trained": bool(len(metrics) == len(TARGET_COLUMNS) * len(feature_sets) * 2),
            "test_predictions_within_physical_bound": bool(predictions[predictions["split"] == "test"]["prediction_kw"].between(0, capacity_kw * 1.05).all()),
        },
        "pitfall": "当前基线验证的是按时间外推的LightGBM预测链路；最终论文结论仍需加入消融实验、误差分组分析和储能调度收益回测。",
    }
    return BaselineModelingResult(
        metrics=metrics,
        predictions=predictions,
        feature_importance=feature_importance,
        report=report,
    )


def write_modeling_report(report: dict[str, Any], metrics: pd.DataFrame, feature_importance: pd.DataFrame, path: Path) -> None:
    """将 LightGBM 基线阶段的紧凑 Markdown 报告写入文件。

    Args:
        report: 结构化报告字典
        metrics: 全部误差指标 DataFrame
        feature_importance: 特征重要性 DataFrame
        path: 报告输出路径
    """

    best_test = metrics[metrics["split"] == "test"].sort_values(["target", "rmse_kw"]).groupby("target", as_index=False).first()
    gates = report["quality_gates"]

    lines = [
        "# Stage 4 LightGBM Baseline Report",
        "",
        "## Scope",
        "",
        f"- Input rows: `{report['input_rows']}`",
        f"- Input columns: `{report['input_columns']}`",
        "- Split method: chronological `70% / 15% / 15%`",
        "- Model: `LightGBM LGBMRegressor`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage 3 feature dataset"] --> B["Chronological split"]',
        '    B --> C["Train LightGBM"]',
        '    C --> D["Validation early stopping"]',
        '    D --> E["Test prediction"]',
        '    E --> F["Metrics and feature importance"]',
        "```",
        "",
        "## Data Split",
        "",
    ]
    for name, split in report["splits"].items():
        lines.append(f"- {name}: `{split['rows']}` rows, `{split['start']}` to `{split['end']}`")

    lines.extend(["", "## Best Test Result by Target", ""])
    lines.append("| Target | Best feature set | RMSE kW | MAE kW | nRMSE capacity | Daytime nRMSE |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for _, row in best_test.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | "
            f"{row['rmse_kw']:.2f} | {row['mae_kw']:.2f} | {row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} |"
        )

    lines.extend(["", "## All Test Metrics", ""])
    lines.append("| Target | Feature set | Features | RMSE kW | MAE kW | Bias kW | Daytime MAPE |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for _, row in metrics[metrics["split"] == "test"].sort_values(["target", "feature_set"]).iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | {int(row['feature_count'])} | "
            f"{row['rmse_kw']:.2f} | {row['mae_kw']:.2f} | {row['bias_kw']:.2f} | {row['daytime_mape']:.4f} |"
        )

    lines.extend(["", "## Top Feature Importance", ""])
    top_importance = (
        feature_importance.sort_values(["target", "feature_set", "importance_gain"], ascending=[True, True, False])
        .groupby(["target", "feature_set"])
        .head(5)
    )
    lines.append("| Target | Feature set | Feature | Gain |")
    lines.append("|---|---|---|---:|")
    for _, row in top_importance.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row['feature_set']}` | `{row['feature']}` | {row['importance_gain']:.2f} |"
        )

    lines.extend(["", "## Quality Gates", ""])
    for gate, passed in gates.items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## Pitfall", "", report["pitfall"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
