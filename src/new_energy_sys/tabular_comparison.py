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
    """Stage8 model-comparison artifacts."""

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


def ensure_required_optional_dependencies() -> None:
    """Fail fast when required Stage8 third-party model packages are missing.

    Stage8 is explicitly a full tabular-model comparison. XGBoost and CatBoost
    are not optional for that decision. Keeping this check separate lets the CLI
    print a deterministic remediation command before any training work starts.
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
    """Return numeric columns that can be considered as model inputs."""

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _columns_containing(columns: list[str], markers: list[str]) -> list[str]:
    """Select columns by stable Stage3 naming markers."""

    return [column for column in columns if any(marker in column for marker in markers)]


def _history_only_features(frame: pd.DataFrame) -> list[str]:
    """Build the leakage-safe Stage8 primary feature group.

    This intentionally mirrors Stage5's strongest `history_only` group for
    t+24h: deterministic calendar features plus PV lag/rolling/ramp history.
    No `target_plus_*` column is allowed, because those represent future valid
    time signals and would weaken the production-readiness conclusion.
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
    """Build an audit comparison group that removes future-valid-time inputs."""

    return sorted(
        column
        for column in _numeric_model_columns(frame)
        if not column.startswith("target_plus_")
    )


def _stage8_feature_sets(frame: pd.DataFrame) -> dict[str, list[str]]:
    """Resolve Stage8 feature sets with primary group first."""

    feature_sets = {
        "history_only": _history_only_features(frame),
        "full_features_without_target_plus": _full_features_without_target_plus(frame),
    }
    return {name: columns for name, columns in feature_sets.items() if columns}


def _fit_lightgbm(train: pd.DataFrame, validation: pd.DataFrame, features: list[str]) -> lgb.LGBMRegressor:
    """Train the Stage5 tuned LightGBM history-only baseline."""

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
    """Train XGBoost with fixed, conservative GBDT parameters."""

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
    # XGBoost version APIs differ around early-stopping arguments. Fixed
    # estimators keep the comparison deterministic and avoid version branching.
    model.fit(
        train[features],
        train[STAGE8_TARGET],
        eval_set=[(validation[features], validation[STAGE8_TARGET])],
        verbose=False,
    )
    return model


def _fit_catboost(train: pd.DataFrame, validation: pd.DataFrame, features: list[str]) -> Any:
    """Train CatBoost without creating CatBoost side files in the workspace."""

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
    """Train a high-variance-reducing tree ensemble baseline."""

    model = ExtraTreesRegressor(
        n_estimators=500,
        max_features="sqrt",
        min_samples_leaf=2,
        random_state=42,
        # Windows sandbox can deny multiprocessing/thread-pool pipe creation.
        # Use single-process training so Stage8 remains runnable and
        # deterministic in restricted desktop environments.
        n_jobs=1,
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_random_forest(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> RandomForestRegressor:
    """Train a standard bagged-tree baseline."""

    model = RandomForestRegressor(
        n_estimators=500,
        max_features="sqrt",
        min_samples_leaf=2,
        random_state=42,
        # See `_fit_extra_trees`; avoiding joblib pools prevents WinError 5 in
        # restricted environments while preserving model semantics.
        n_jobs=1,
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_ridge(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> Pipeline:
    """Train a scaled linear lower-bound model."""

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=1.0)),
        ]
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _fit_elastic_net(train: pd.DataFrame, _validation: pd.DataFrame, features: list[str]) -> Pipeline:
    """Train a scaled sparse-linear lower-bound model."""

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", ElasticNet(alpha=0.001, l1_ratio=0.2, max_iter=20000, random_state=42)),
        ]
    )
    model.fit(train[features], train[STAGE8_TARGET])
    return model


def _model_fitters() -> dict[str, Any]:
    """Return Stage8 model fitters in report order."""

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
    """Run model inference and enforce PV physical limits."""

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
    """Evaluate one fitted model on validation and test splits."""

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
    """Apply the explicit Stage8主模型 selection rule."""

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
            "XGBoost/CatBoost materially improves nRMSE and does not degrade daytime nRMSE."
            if can_replace
            else "No eligible challenger materially beats LightGBM history_only under the Stage8 rule."
        ),
    }


def run_tabular_model_comparison(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    output_dir: Path,
) -> TabularComparisonResult:
    """Run Stage8 tabular model comparison end-to-end."""

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
            "Stage8 is not AutoML. It is a fixed, auditable comparison of high-probability tabular models. "
            "The replacement rule is intentionally strict to avoid swapping the production route for a marginal gain."
        ),
    }
    return TabularComparisonResult(metrics=metrics, predictions=predictions, report=report)


def write_tabular_comparison_report(report: dict[str, Any], metrics: pd.DataFrame, path: Path) -> None:
    """Write the Stage8 Markdown decision report."""

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
