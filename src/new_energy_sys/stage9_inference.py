"""Stage9 主模型推理固化模块。

模块设计原则：
- 消费不训练：只加载 Stage8 已固化的 pickle bundle，不重新训练或调参。
- 快速失败：bundle 缺少关键字段、特征列缺失、列类型异常、NaN/Inf 等问题在入口处直接报错，
  避免产生不可审计的推理产物。
- 容量同源校验：bundle 保存的容量和配置文件容量必须一致，防止不同电站数据混用。
- 标准产物：预测表字段（timestamp、target、prediction_kw、capacity_ratio、物理边界）
  固定不变，下游调度和可视化只依赖这些标准字段。

本模块对应项目 Stage9 的主模型批量推理功能，输出标准预测表、离线指标和质量门禁报告，
供 Stage10 储能调度和展示模块消费。
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from new_energy_sys.modeling import _chronological_split, _metrics, predict_with_bundle


DEFAULT_STAGE9_MODEL_BUNDLE = (
    "stage8_models/lightgbm_tuned_history_only_target_pv_power_t_plus_24h.pkl"
)


@dataclass(frozen=True)
class Stage9InferenceResult:
    """Stage9 主模型推理产物。

    predictions 是下游调度、可视化和离线回放共同消费的标准表；metrics 只在
    输入数据包含真实目标列时生成，用于确认批量推理行为和 Stage8 测试指标一致。
    report 保存质量门禁、模型元数据和产物路径索引，便于交接时追溯。
    """

    predictions: pd.DataFrame
    metrics: pd.DataFrame
    report: dict[str, Any]


def load_model_bundle(path: Path) -> dict[str, Any]:
    """读取并校验模型 bundle 的基础结构。

    Stage9 不重新训练模型，只消费 Stage8 已固化的 pickle bundle。这里必须快速
    失败：如果 bundle 缺少模型、特征清单或物理边界，继续推理会产生不可审计产物。
    """

    if not path.exists():
        raise FileNotFoundError(f"Stage9 model bundle not found: {path}")

    with path.open("rb") as handle:
        bundle = pickle.load(handle)

    required_keys = {
        "model",
        "features",
        "target",
        "feature_set",
        "capacity_kw",
        "prediction_lower_bound_kw",
        "prediction_upper_bound_kw",
    }
    missing = sorted(required_keys.difference(bundle))
    if missing:
        raise ValueError(f"Stage9 model bundle missing keys: {', '.join(missing)}")

    if not isinstance(bundle["features"], list) or not bundle["features"]:
        raise ValueError("Stage9 model bundle has an empty feature list.")
    return bundle


def _prepare_input(frame: pd.DataFrame) -> pd.DataFrame:
    """标准化推理输入的时间列并保持时间顺序。

    线上批量推理可能来自 Stage3 parquet，也可能来自后续服务拼出的特征表。统一在
    入口处解析 timestamp，可以让质量门禁在真正预测前发现空时间、重复乱序等问题。
    """

    if "timestamp" not in frame.columns:
        raise ValueError("Stage9 input missing required column: timestamp")

    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    if working["timestamp"].isna().any():
        bad_count = int(working["timestamp"].isna().sum())
        raise ValueError(f"Stage9 input contains invalid timestamps: {bad_count}")
    return working.sort_values("timestamp").reset_index(drop=True)


def _validate_feature_matrix(frame: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    """校验模型特征矩阵完整性。

    生产推理最常见风险不是模型文件损坏，而是特征列缺失、列类型变成字符串、或出现
    NaN/Inf。这里把每类问题拆开记录，报告里能直接定位失败原因。
    """

    missing_features = [feature for feature in features if feature not in frame.columns]
    if missing_features:
        raise ValueError(
            "Stage9 input missing model features: "
            + ", ".join(missing_features[:20])
            + (" ..." if len(missing_features) > 20 else "")
        )

    feature_matrix = frame[features]
    non_numeric = [
        column for column in features if not pd.api.types.is_numeric_dtype(feature_matrix[column])
    ]
    if non_numeric:
        raise TypeError(
            "Stage9 model features must be numeric. Non-numeric columns: "
            + ", ".join(non_numeric[:20])
            + (" ..." if len(non_numeric) > 20 else "")
        )

    missing_value_count = int(feature_matrix.isna().sum().sum())
    if missing_value_count:
        raise ValueError(f"Stage9 feature matrix contains missing values: {missing_value_count}")

    finite_mask = np.isfinite(feature_matrix.to_numpy(dtype=float))
    if not finite_mask.all():
        invalid_count = int((~finite_mask).sum())
        raise ValueError(f"Stage9 feature matrix contains infinite values: {invalid_count}")

    return {
        "missing_model_features": [],
        "non_numeric_model_features": [],
        "missing_feature_values": missing_value_count,
        "infinite_feature_values": 0,
    }


def _build_prediction_frame(
    *,
    frame: pd.DataFrame,
    bundle: dict[str, Any],
    prediction: np.ndarray,
) -> pd.DataFrame:
    """生成统一预测产物表。

    下游储能调度只应该依赖标准字段：timestamp、target、prediction_kw、capacity
    ratio 和物理边界。若输入包含真实目标列，则额外写入 actual_kw/error_kw，便于
    离线回测；线上未来推理没有真实值时不会制造空的“伪评估”。
    """

    target = str(bundle["target"])
    capacity_kw = float(bundle["capacity_kw"])
    output = pd.DataFrame(
        {
            "timestamp": frame["timestamp"],
            "target": target,
            "model_name": str(bundle.get("model_name", "lightgbm_tuned")),
            "feature_set": str(bundle["feature_set"]),
            "prediction_kw": prediction,
            "prediction_capacity_ratio": prediction / capacity_kw,
            "prediction_lower_bound_kw": float(bundle["prediction_lower_bound_kw"]),
            "prediction_upper_bound_kw": float(bundle["prediction_upper_bound_kw"]),
        }
    )
    if target in frame.columns:
        output["actual_kw"] = frame[target].to_numpy()
        output["error_kw"] = output["prediction_kw"] - output["actual_kw"]
    return output


def _evaluate_predictions(
    frame: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    target: str,
    capacity_kw: float,
) -> pd.DataFrame:
    """在存在真实目标列时计算全量和固定时间切分指标。

    Stage8 的最终比较使用 chronological `70% / 15% / 15%`。Stage9 沿用同一切分，
    这样可以验证同一个 bundle 在独立推理入口下仍能复现测试集指标。
    """

    if target not in frame.columns:
        return pd.DataFrame()

    metric_rows: list[dict[str, Any]] = []
    split_indices = {
        "all": frame.index,
        **{name: split.index for name, split in _chronological_split(frame).items()},
    }
    indexed_predictions = predictions.set_index(frame.index)
    for split_name, index in split_indices.items():
        actual = frame.loc[index, target].to_numpy()
        predicted = indexed_predictions.loc[index, "prediction_kw"].to_numpy()
        metric_rows.append(
            {
                "split": split_name,
                "target": target,
                "sample_count": int(len(index)),
                **_metrics(actual, predicted, capacity_kw=capacity_kw),
            }
        )
    return pd.DataFrame(metric_rows)


def run_stage9_inference(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    model_bundle_path: Path,
    output_paths: dict[str, Path] | None = None,
) -> Stage9InferenceResult:
    """执行 Stage9 主模型批量推理。

    参数中的 config 只用于校验站点装机容量。实际推理边界来自模型 bundle，确保
    “训练时保存的边界”和“推理时使用的边界”同源；如果配置容量和 bundle 不一致，
    直接失败，避免把不同电站或不同单位的数据混用。
    """

    bundle = load_model_bundle(model_bundle_path)
    working = _prepare_input(frame)

    target = str(bundle["target"])
    features = list(bundle["features"])
    bundle_capacity_kw = float(bundle["capacity_kw"])
    config_capacity_kw = float(config["site"]["capacity_kw"])
    if not np.isclose(bundle_capacity_kw, config_capacity_kw):
        raise ValueError(
            "Stage9 capacity mismatch: "
            f"bundle={bundle_capacity_kw}, config={config_capacity_kw}"
        )

    feature_audit = _validate_feature_matrix(working, features)
    prediction = predict_with_bundle(bundle, working)
    predictions = _build_prediction_frame(frame=working, bundle=bundle, prediction=prediction)
    metrics = _evaluate_predictions(
        working,
        predictions,
        target=target,
        capacity_kw=bundle_capacity_kw,
    )

    lower = float(bundle["prediction_lower_bound_kw"])
    upper = float(bundle["prediction_upper_bound_kw"])
    has_target = target in working.columns
    report = {
        "stage": "stage9_main_model_inference",
        "model_bundle_path": str(model_bundle_path),
        "model_name": str(bundle.get("model_name", "lightgbm_tuned")),
        "feature_set": str(bundle["feature_set"]),
        "target": target,
        "feature_count": len(features),
        "capacity_kw": bundle_capacity_kw,
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "timestamp_start": str(working["timestamp"].min()),
        "timestamp_end": str(working["timestamp"].max()),
        "prediction_summary": {
            "min_kw": float(np.min(prediction)),
            "p50_kw": float(np.quantile(prediction, 0.50)),
            "p95_kw": float(np.quantile(prediction, 0.95)),
            "max_kw": float(np.max(prediction)),
            "mean_kw": float(np.mean(prediction)),
        },
        "output_paths": {name: str(path) for name, path in (output_paths or {}).items()},
        "quality_gates": {
            "input_non_empty": bool(len(working) > 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "model_bundle_exists": bool(model_bundle_path.exists()),
            "bundle_target_is_t_plus_24h": bool(target == "target_pv_power_t_plus_24h"),
            "bundle_feature_set_is_history_only": bool(bundle["feature_set"] == "history_only"),
            "config_capacity_matches_bundle": bool(np.isclose(bundle_capacity_kw, config_capacity_kw)),
            "all_model_features_present": bool(not feature_audit["missing_model_features"]),
            "all_model_features_numeric": bool(not feature_audit["non_numeric_model_features"]),
            "no_missing_feature_values": bool(feature_audit["missing_feature_values"] == 0),
            "no_infinite_feature_values": bool(feature_audit["infinite_feature_values"] == 0),
            "predictions_within_physical_bound": bool(predictions["prediction_kw"].between(lower, upper).all()),
            "target_available_for_offline_metrics": bool(has_target),
        },
        "pitfall": (
            "Stage9 固化的是 t+24h LightGBM history_only 主模型推理链路。"
            "它不代表真实 forecast-cycle 天气上线能力，也不应被扩展成无限制调参入口。"
        ),
    }
    return Stage9InferenceResult(predictions=predictions, metrics=metrics, report=report)


def write_stage9_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage9 中文 Markdown 报告。"""

    lines = [
        "# Stage9 主模型推理固化报告",
        "",
        "## 范围",
        "",
        f"- 模型 bundle: `{report['model_bundle_path']}`",
        f"- 模型: `{report['model_name']}`",
        f"- 特征组: `{report['feature_set']}`",
        f"- 预测目标: `{report['target']}`",
        f"- 输入行数: `{report['input_rows']}`",
        f"- 特征数量: `{report['feature_count']}`",
        "- 物理边界: `[0, capacity_kw * 1.05]`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage3 / 在线特征表"] --> B["加载 Stage8 主模型 bundle"]',
        '    B --> C["校验特征列、容量和时间顺序"]',
        '    C --> D["LightGBM 批量推理"]',
        '    D --> E["物理边界裁剪"]',
        '    E --> F["标准预测 CSV"]',
        '    E --> G["推理质量报告"]',
        "```",
        "",
        "## 预测分布",
        "",
        "| 指标 | kW |",
        "|---|---:|",
        f"| min | {report['prediction_summary']['min_kw']:.4f} |",
        f"| p50 | {report['prediction_summary']['p50_kw']:.4f} |",
        f"| p95 | {report['prediction_summary']['p95_kw']:.4f} |",
        f"| max | {report['prediction_summary']['max_kw']:.4f} |",
        f"| mean | {report['prediction_summary']['mean_kw']:.4f} |",
    ]

    if not metrics.empty:
        lines.extend(["", "## 离线指标", ""])
        lines.append("| Split | Samples | nRMSE | Daytime nRMSE | RMSE kW | MAE kW | Bias kW |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, row in metrics.iterrows():
            lines.append(
                f"| `{row['split']}` | {int(row['sample_count'])} | "
                f"{row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} | "
                f"{row['rmse_kw']:.4f} | {row['mae_kw']:.4f} | {row['bias_kw']:.4f} |"
            )

    lines.extend(["", "## 输出产物", ""])
    for name, output_path in report["output_paths"].items():
        lines.append(f"- {name}: `{output_path}`")

    lines.extend(["", "## 质量门禁", ""])
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 主模型 bundle 校验、批量推理、标准预测产物、离线指标复现和质量报告。",
            "- 目标完成情况: Stage9 推理链路已闭环，可作为后续储能调度和展示模块的预测输入。",
            "- 下一阶段可行性: 可进入 S10，将 `stage9_main_model_predictions.csv` 接入储能调度仿真。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
