"""表格模型全量对比模块。

模块设计原则：
- Stage8 是固定、可审计的表格模型对比，不是 AutoML
- 主模型替换规则故意严格，避免为边际收益切换生产路径
- history_only 特征组镜像 Stage5 最强无泄漏配置，禁止 target_plus 特征
- 模型训练使用固定超参数和随机种子，保证可复现性

本模块对应项目 Stage8 的表格模型全量对比功能。
"""

from __future__ import annotations

import importlib
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from new_energy_sys.modeling import TARGET_COLUMNS, _chronological_split, _metrics


STAGE8_TARGET = "target_pv_power_t_plus_24h"
LIGHTGBM_BASELINE_NRMSE = 0.1225
LIGHTGBM_BASELINE_DAYTIME_NRMSE = 0.1689
MATERIAL_IMPROVEMENT_NRMSE = 0.0030


@dataclass(frozen=True)
class TabularComparisonResult:
    """Stage8 表格模型对比产物容器。

    Attributes:
        metrics: 各模型各特征集在各划分上的指标 DataFrame
        predictions: 各模型各特征集的预测值 DataFrame
        report: 包含质量门禁、推荐结论和产物路径的报告字典
    """

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


def ensure_required_optional_dependencies() -> None:
    """当 Stage8 所需的第三方模型包缺失时快速失败。

    Stage8 明确是全量表格模型对比，XGBoost 和 CatBoost 不是可选项。
    将此检查独立出来，使命令行界面可在任何训练工作开始前
    打印确定性的修复命令。

    Raises:
        RuntimeError: xgboost 或 catboost 未安装时抛出
    """

    missing = [
        package
        for package in ["xgboost", "catboost"]
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        raise RuntimeError(
            "Stage8 requires missing packages: "
            f"{', '.join(missing)}. Install with: python -m pip install xgboost catboost"
        )


def _numeric_model_columns(frame: pd.DataFrame) -> list[str]:
    """返回可作为模型输入的数值列。

    Args:
        frame: 输入数据帧

    Returns:
        排除 timestamp 和目标列后的数值列名列表
    """

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _columns_containing(columns: list[str], markers: list[str]) -> list[str]:
    """按 Stage3 稳定命名标记筛选列。

    Args:
        columns: 候选列名列表
        markers: 关键字标记列表

    Returns:
        包含任一标记的列名列表
    """

    return [column for column in columns if any(marker in column for marker in markers)]


def _history_only_features(frame: pd.DataFrame) -> list[str]:
    """构建无泄漏的 Stage8 主特征组。

    该函数故意镜像 Stage5 最强的 history_only 特征组（用于 t+24h）：
    确定性日历特征加上光伏滞后/滚动/斜坡历史。不允许任何 target_plus_ 列，
    因为它们代表未来有效时间信号，会削弱生产就绪性结论。

    Args:
        frame: 输入数据帧

    Returns:
        无泄漏特征名列表

    Raises:
        ValueError: 发现 target_plus_ 特征时抛出
    """

    numeric = _numeric_model_columns(frame)
    time_columns = [
        column
        for column in numeric
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
    history_columns = _columns_containing(
        numeric,
        [
            "pv_power_kw",
            "pv_power_lag_",
            "pv_power_roll_",
            "pv_power_ramp",
            "pv_power_capacity_ratio",
        ],
    )
    features = sorted(set(time_columns + history_columns))
    leaked = [column for column in features if column.startswith("target_plus_")]
    if leaked:
        raise ValueError(f"history_only contains forbidden target_plus features: {', '.join(leaked)}")
    return features


def _full_features_without_target_plus(frame: pd.DataFrame) -> list[str]:
    """构建移除未来有效时间输入的审计对比特征组。

    Args:
        frame: 输入数据帧

    Returns:
        排除 target_plus_ 列后的全部数值特征名列表
    """

    return sorted(
        column
        for column in _numeric_model_columns(frame)
        if not column.startswith("target_plus_")
    )


def _stage8_feature_sets(frame: pd.DataFrame) -> dict[str, list[str]]:
    """解析 Stage8 特征集，主特征组优先排列。

    Args:
        frame: 输入数据帧

    Returns:
        特征集名称到特征名列表的映射，空特征集被过滤
    """

    feature_sets = {
        "history_only": _history_only_features(frame),
        "full_features_without_target_plus": _full_features_without_target_plus(frame),
    }
    return {name: columns for name, columns in feature_sets.items() if columns}


def _fit_lightgbm(train: pd.DataFrame, validation: pd.DataFrame, features: list[str]) -> lgb.LGBMRegressor:
    """训练 Stage5 调优的 LightGBM history-only 基线模型。

    Args:
        train: 训练集
        validation: 验证集（用于早停）
        features: 特征列名列表

    Returns:
        训练完成的 LGBMRegressor 模型
    """

    model = lgb.LGBMRegressor(
        objective="regression",
        boosting_type="gbdt",
        n_estimators=1300,
        learning_rate=0.035,
        num_leaves=16,
        max_depth=6,
        min_child_samples=25,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.95,
        reg_alpha=0.0,
        reg_lambda=0.3,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        train[features],
        train[STAGE8_TARGET],
        eval_set=[(validation[features], validation[STAGE8_TARGET])],
        eval_metric="rmse",
        callbacks=[lgb.early_stopping(stopping_rounds=90, verbose=False), lgb.log_evaluation(period=0)],
    )
    return model


def _fit_xgboost(train: pd.DataFrame, validation: pd.DataFrame, features: list[str]) -> Any:
    """使用固定的保守 GBDT 参数训练 XGBoost 模型。

    Args:
        train: 训练集
        validation: 验证集
        features: 特征列名列表

    Returns:
        训练完成的 XGBRegressor 模型
    """

    from xgboost import XGBRegressor

    model = XGBRegressor(
        n_estimators=1200,
        learning_rate=0.03,
        max_depth=4,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=-1,
        tree_method="hist",
        verbosity=0,
    )
    # XGBoost 各版本早停参数 API 差异较大。固定 n_estimators 保持对比确定性，
    # 避免版本分支问题
    model.fit(
        train[features],
        train[STAGE8_TARGET],
        eval_set=[(validation[features], validation[STAGE8_TARGET])],
        verbose=False,
    )
    return model


def _fit_catboost(train: pd.DataFrame, validation: pd.DataFrame, features: list[str]) -> Any:
    """训练 CatBoost 模型，禁止在工作目录创建 CatBoost 附属文件。

    Args:
        train: 训练集
        validation: 验证集
        features: 特征列名列表

    Returns:
        训练完成的 CatBoostRegressor 模型
    """

    from catboost import CatBoostRegressor

    model = CatBoostRegressor(
        iterations=1200,
        learning_rate=0.03,
        depth=6,
        loss_function="RMSE",
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(
        train[features],
        train[STAGE8_TARGET],
        eval_set=(validation[features], validation[STAGE8_TARGET]),
        use_best_model=True,
    )
    return model


def _fit_extra_trees(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> ExtraTreesRegressor:
    """训练高方差消减树集成基线模型。

    Args:
        train: 训练集
        _validation: 验证集（ExtraTrees 不使用）
        features: 特征列名列表

    Returns:
        训练完成的 ExtraTreesRegressor 模型
    """

    model = ExtraTreesRegressor(
        n_estimators=500,
        max_features="sqrt",
        min_samples_leaf=2,
        random_state=42,
        # Windows 沙箱可能拒绝多进程/线程池管道创建。
        # 使用单进程训练使 Stage8 在受限桌面环境中仍可运行且确定性不变
        n_jobs=1,
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_random_forest(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> RandomForestRegressor:
    """训练标准装袋树基线模型。

    Args:
        train: 训练集
        _validation: 验证集（RandomForest 不使用）
        features: 特征列名列表

    Returns:
        训练完成的 RandomForestRegressor 模型
    """

    model = RandomForestRegressor(
        n_estimators=500,
        max_features="sqrt",
        min_samples_leaf=2,
        random_state=42,
        # 同 _fit_extra_trees，避免 joblib 线程池以防受限环境下 WinError 5，
        # 同时保持模型语义不变
        n_jobs=1,
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_ridge(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> Pipeline:
    """训练标准化线性下界模型。

    Args:
        train: 训练集
        _validation: 验证集（Ridge 不使用）
        features: 特征列名列表

    Returns:
        包含 StandardScaler + Ridge 的 Pipeline 模型
    """

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=1.0)),
        ]
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_elastic_net(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> Pipeline:
    """训练标准化稀疏线性下界模型。

    Args:
        train: 训练集
        _validation: 验证集（ElasticNet 不使用）
        features: 特征列名列表

    Returns:
        包含 StandardScaler + ElasticNet 的 Pipeline 模型
    """

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", ElasticNet(alpha=0.001, l1_ratio=0.2, max_iter=20000, random_state=42)),
        ]
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _model_fitters() -> dict[str, Any]:
    """返回 Stage8 模型训练器字典（按报告顺序排列）。

    Returns:
        模型名称到训练函数的映射
    """

    return {
        "lightgbm_tuned": _fit_lightgbm,
        "xgboost": _fit_xgboost,
        "catboost": _fit_catboost,
        "extra_trees": _fit_extra_trees,
        "random_forest": _fit_random_forest,
        "ridge": _fit_ridge,
        "elastic_net": _fit_elastic_net,
    }


def _predict_clipped(model: Any, frame: pd.DataFrame, features: list[str], capacity_kw: float) -> np.ndarray:
    """执行模型推理并强制光伏物理限值 [0, capacity_kw * 1.05]。

    Args:
        model: 训练好的模型对象
        frame: 输入数据帧
        features: 特征列名列表
        capacity_kw: 电站装机容量 (kW)

    Returns:
        裁剪后的预测值数组
    """

    prediction = np.asarray(model.predict(frame[features]), dtype=float)
    return np.clip(prediction, 0.0, capacity_kw * 1.05)


def _evaluate(
    *,
    model: Any,
    model_name: str,
    feature_set: str,
    features: list[str],
    splits: dict[str, pd.DataFrame],
    capacity_kw: float,
    model_path: Path,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """在验证集和测试集上评估单个已训练模型。

    Args:
        model: 训练好的模型对象
        model_name: 模型名称
        feature_set: 特征集名称
        features: 特征列名列表
        splits: 包含 train/validation/test 的数据划分字典
        capacity_kw: 电站装机容量 (kW)
        model_path: 模型持久化路径

    Returns:
        元组：(指标行列表, 预测值 DataFrame)
    """

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    for split_name in ["validation", "test"]:
        split = splits[split_name]
        prediction = _predict_clipped(model, split, features, capacity_kw)
        metric_rows.append(
            {
                "model": model_name,
                "target": STAGE8_TARGET,
                "feature_set": feature_set,
                "split": split_name,
                "feature_count": len(features),
                "model_path": str(model_path),
                **_metrics(split[STAGE8_TARGET].to_numpy(), prediction, capacity_kw=capacity_kw),
            }
        )
        prediction_frames.append(
            pd.DataFrame(
                {
                    "timestamp": split["timestamp"].to_numpy(),
                    "model": model_name,
                    "target": STAGE8_TARGET,
                    "feature_set": feature_set,
                    "split": split_name,
                    "actual_kw": split[STAGE8_TARGET].to_numpy(),
                    "prediction_kw": prediction,
                    "error_kw": prediction - split[STAGE8_TARGET].to_numpy(),
                }
            )
        )
    return metric_rows, pd.concat(prediction_frames, ignore_index=True)


def _select_recommendation(metrics: pd.DataFrame) -> dict[str, Any]:
    """应用 Stage8 主模型显式选择规则。

    规则：仅当 XGBoost/CatBoost 的 nRMSE 改善量 >= 0.0030 且日间 nRMSE
    不退化时，才允许替换 LightGBM。

    Args:
        metrics: 全部指标 DataFrame

    Returns:
        推荐结论字典，包含 selected_model、can_replace_lightgbm、reason 等
    """

    test = metrics[(metrics["split"] == "test") & (metrics["feature_set"] == "history_only")].copy()
    test = test.sort_values(["nrmse_capacity", "daytime_nrmse_capacity"]).reset_index(drop=True)
    lightgbm = test[test["model"] == "lightgbm_tuned"].iloc[0]
    best = test.iloc[0]
    improvement = float(lightgbm["nrmse_capacity"] - best["nrmse_capacity"])
    daytime_not_worse = float(best["daytime_nrmse_capacity"]) <= float(lightgbm["daytime_nrmse_capacity"])
    can_replace = (
        str(best["model"]) in {"xgboost", "catboost"}
        and float(best["nrmse_capacity"]) < LIGHTGBM_BASELINE_NRMSE
        and improvement >= MATERIAL_IMPROVEMENT_NRMSE
        and daytime_not_worse
    )
    return {
        "selected_model": str(best["model"]) if can_replace else "lightgbm_tuned",
        "best_history_only_model": str(best["model"]),
        "lightgbm_history_only_nrmse": float(lightgbm["nrmse_capacity"]),
        "best_history_only_nrmse": float(best["nrmse_capacity"]),
        "best_vs_lightgbm_nrmse_delta": improvement,
        "can_replace_lightgbm": bool(can_replace),
        "reason": (
            "XGBoost/CatBoost 显著改善 nRMSE 且未退化日间 nRMSE。"
            if can_replace
            else "无合格挑战者能在 Stage8 规则下显著击败 LightGBM history_only。"
        ),
    }


def run_tabular_model_comparison(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    output_dir: Path,
) -> TabularComparisonResult:
    """端到端运行 Stage8 表格模型对比。

    Args:
        frame: Stage3 特征数据帧
        config: 全局配置字典，须包含 site.capacity_kw
        output_dir: 输出目录路径

    Returns:
        TabularComparisonResult 包含 metrics、predictions、report 三部分

    Raises:
        ValueError: 缺少目标列、存在缺失/无穷数值时抛出
        RuntimeError: 缺少 xgboost/catboost 依赖时抛出
    """

    ensure_required_optional_dependencies()

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    if STAGE8_TARGET not in working.columns:
        raise ValueError(f"Stage8 input missing target column: {STAGE8_TARGET}")
    numeric = working.select_dtypes(include=[np.number])
    if numeric.isna().sum().sum() != 0:
        raise ValueError("Stage8 input contains missing numeric values; run Stage3 quality gates first.")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Stage8 input contains infinite numeric values.")

    splits = _chronological_split(working)
    feature_sets = _stage8_feature_sets(working)
    model_dir = output_dir / "stage8_models"
    model_dir.mkdir(parents=True, exist_ok=True)

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    trained_models: list[dict[str, Any]] = []

    for feature_set, features in feature_sets.items():
        for model_name, fitter in _model_fitters().items():
            model = fitter(splits["train"], splits["validation"], features)
            model_path = model_dir / f"{model_name}_{feature_set}_{STAGE8_TARGET}.pkl"
            with model_path.open("wb") as handle:
                pickle.dump(
                    {
                        "model": model,
                        "model_name": model_name,
                        "features": features,
                        "feature_set": feature_set,
                        "target": STAGE8_TARGET,
                        "capacity_kw": capacity_kw,
                        "prediction_lower_bound_kw": 0.0,
                        "prediction_upper_bound_kw": capacity_kw * 1.05,
                    },
                    handle,
                )
            rows, predictions = _evaluate(
                model=model,
                model_name=model_name,
                feature_set=feature_set,
                features=features,
                splits=splits,
                capacity_kw=capacity_kw,
                model_path=model_path,
            )
            metric_rows.extend(rows)
            prediction_frames.append(predictions)
            trained_models.append({"model": model_name, "feature_set": feature_set, "model_path": str(model_path)})

    metrics = pd.DataFrame(metric_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    recommendation = _select_recommendation(metrics)
    expected_metric_rows = len(feature_sets) * len(_model_fitters()) * 2
    report = {
        "stage": "stage8_tabular_model_comparison",
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "target": STAGE8_TARGET,
        "feature_sets": {name: {"feature_count": len(features), "features": features} for name, features in feature_sets.items()},
        "models": list(_model_fitters().keys()),
        "splits": {
            name: {"rows": int(len(split)), "start": str(split["timestamp"].min()), "end": str(split["timestamp"].max())}
            for name, split in splits.items()
        },
        "trained_models": trained_models,
        "recommendation": recommendation,
        "quality_gates": {
            "input_non_empty": bool(len(working) > 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "history_only_has_no_target_plus": bool(
                not any(column.startswith("target_plus_") for column in feature_sets["history_only"])
            ),
            "no_missing_numeric_values": bool(numeric.isna().sum().sum() == 0),
            "no_infinite_numeric_values": bool(np.isfinite(numeric.to_numpy()).all()),
            "all_models_trained": bool(len(metrics) == expected_metric_rows),
            "test_predictions_within_physical_bound": bool(
                predictions[predictions["split"] == "test"]["prediction_kw"].between(0.0, capacity_kw * 1.05).all()
            ),
            "report_has_final_recommendation": bool(recommendation["selected_model"]),
        },
        "pitfall": (
            "Stage8 不是 AutoML，而是固定、可审计的高概率表格模型对比。"
            "替换规则故意严格，避免为边际收益切换生产路径。"
        ),
    }
    return TabularComparisonResult(metrics=metrics, predictions=predictions, report=report)


def write_tabular_comparison_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """写出 Stage8 Markdown 决策报告。

    Args:
        report: Stage8 报告字典
        metrics: 全部指标 DataFrame
        path: 输出文件路径
    """

    test_rows = metrics[metrics["split"] == "test"].sort_values(["feature_set", "nrmse_capacity"])
    history_rows = test_rows[test_rows["feature_set"] == "history_only"].copy()
    recommendation = report["recommendation"]

    lines = [
        "# Stage8 Tabular Model Comparison Report",
        "",
        "## Scope",
        "",
        f"- Target: `{report['target']}`",
        f"- Input rows: `{report['input_rows']}`",
        "- Split: chronological `70% / 15% / 15%`",
        "- Primary feature set: `history_only`",
        "- Decision rule: replace LightGBM only if XGBoost/CatBoost improves nRMSE by at least `0.0030` and does not degrade daytime nRMSE.",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["Stage3 feature dataset"] --> B["history_only features"]',
        '    B --> C["Chronological split"]',
        '    C --> D["Train tabular models"]',
        '    D --> E["Validation/test metrics"]',
        '    E --> F["Strict replacement rule"]',
        '    F --> G["Main model recommendation"]',
        "```",
        "",
        "## Final Recommendation",
        "",
        f"- Selected model: `{recommendation['selected_model']}`",
        f"- Best history-only model: `{recommendation['best_history_only_model']}`",
        f"- Best vs LightGBM nRMSE delta: `{recommendation['best_vs_lightgbm_nrmse_delta']:.4f}`",
        f"- Can replace LightGBM: `{recommendation['can_replace_lightgbm']}`",
        f"- Reason: {recommendation['reason']}",
        "",
        "## History-Only Test Metrics",
        "",
        "| Model | nRMSE | Daytime nRMSE | RMSE kW | MAE kW | Bias kW |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in history_rows.iterrows():
        lines.append(
            f"| `{row['model']}` | {row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} | "
            f"{row['rmse_kw']:.4f} | {row['mae_kw']:.4f} | {row['bias_kw']:.4f} |"
        )

    lines.extend(["", "## All Test Metrics", ""])
    lines.append("| Feature set | Model | nRMSE | Daytime nRMSE | RMSE kW | MAE kW |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for _, row in test_rows.iterrows():
        lines.append(
            f"| `{row['feature_set']}` | `{row['model']}` | {row['nrmse_capacity']:.4f} | "
            f"{row['daytime_nrmse_capacity']:.4f} | {row['rmse_kw']:.4f} | {row['mae_kw']:.4f} |"
        )

    lines.extend(["", "## Quality Gates", ""])
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(["", "## Pitfall", "", report["pitfall"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
