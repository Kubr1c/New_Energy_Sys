"""HRRR PV forecast training CLI: 4-baseline ablation experiment runner.

Implements the training + evaluation pipeline for ablation experiments:
  A (history_only):     time features + PV history features, no weather
  B (HRRR-GHI-only):   A + HRRR GHI + GHI rolling stats + weather_missing flag
  C1 (HRRR-DISC):      B + all weather + all rolling + derived ratio/index features
  D (NSRDB oracle):    hardcoded reference metrics (no training)

Usage:
  python -m new_energy_sys.cli.train_hrrr_pv --hrrr <path> --pv <path> [--output-dir <path>]
"""

from __future__ import annotations

import argparse
import os
import pickle
from pathlib import Path
from typing import Any, Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

from new_energy_sys.hrrr_feature_aligner import build_complete_step1_feature_table
from new_energy_sys.modeling import _chronological_split


# ============================================================================
# 特征组检测 — 通过列名模式匹配识别每个特征属于哪个功能组
# ============================================================================

# 时间特征: 周期编码列 + 基础时间属性列
_TIME_PREFIXES = (
    "hour_sin",
    "hour_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "month_sin",
    "month_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "quarter",
    "is_weekend",
    "is_business_hour",
    "day_of_year",
    "hour",
    "day_of_week",
    "month",
)

# 5 个基础气象变量 (离散的 DEC 分解输出)
_WEATHER_BASE = {
    "ghi_wm2",
    "dni_wm2",
    "dhi_wm2",
    "temperature_c",
    "cloud_cover_pct",
}

# 派生气象比/指数
_DERIVED_NAMES = {
    "dhi_ghi_ratio",
    "clearsky_index",
    "kt",
}

# 训练/评估时必须排除的非特征列
_EXCLUDE_COLUMNS = {
    "timestamp",
    "target_time",
    "target_pv_power_t_plus_24h",
    "pv_power_kw",
    "solar_zenith_deg",
}

EXP_LABELS = {
    "A": "history_only",
    "B": "HRRR-GHI-only",
    "C1": "HRRR-DISC",
    "D": "NSRDB oracle",
}


def _detect_feature_groups(df: pd.DataFrame) -> dict[str, list[str]]:
    """通过列名前缀/精确匹配自动检测特征组.

    分组逻辑 (按优先级):
      1. time — 星期/小时/月份/季度的 sin/cos 周期编码 + 基础时间属性
      2. weather — 5 个离散气象变量精确匹配
      3. rolling_weather — 所有 *rolling* 子串的气象滚动统计
      4. derived — 散射比/晴空指数/大气层顶归一指数
      5. flag — weather_missing 布尔标记
      6. history — PV 功率滞后/滚动/爬坡 + 预报误差滞后

    审计列 (issue_time_*, lead_time_*, n_forecasts_*) 和排除列自然被过滤掉。

    Parameters
    ----------
    df : pd.DataFrame
        完整特征表.

    Returns
    -------
    dict[str, list[str]]
        keys: time, history, weather, rolling_weather, derived, flag.
    """
    all_cols = set(df.columns)

    # 排除非特征列: 显式排除 + 审计列
    exclude = set(_EXCLUDE_COLUMNS)
    for c in all_cols:
        if c.startswith(("issue_time_", "lead_time_", "n_forecasts_")):
            exclude.add(c)
        # target_time — 与 timestamp 同时存在, 仅用于审计
        if c == "target_time":
            exclude.add(c)

    candidate_cols = sorted(c for c in df.columns if c not in exclude)

    # ---- 1. 时间特征: startswith 匹配 ----
    time_features = sorted(c for c in candidate_cols if c.startswith(_TIME_PREFIXES))

    # ---- 2. 精确匹配 5 个基础气象列 ----
    weather_features = sorted(c for c in candidate_cols if c in _WEATHER_BASE)

    # ---- 3. 滚动气象: 匹配 *_rolling_* 模式 ----
    rolling_weather = sorted(c for c in candidate_cols if "_rolling_" in c)

    # ---- 4. 派生比值/指数 ----
    derived_features = sorted(c for c in candidate_cols if c in _DERIVED_NAMES)

    # ---- 5. weather_missing 标志 ----
    flag_features = sorted(c for c in candidate_cols if c == "weather_missing")

    # ---- 6. 历史功率特征: PV 滞后/滚动统计/爬坡 + 预报误差 ----
    _HISTORY_PATTERNS = (
        "pv_power_lag_",
        "pv_power_roll_",
        "pv_power_ramp_",
        "pv_power_capacity_ratio_",
        "forecast_error_lag_",
    )
    history_features = sorted(
        c for c in candidate_cols if any(p in c for p in _HISTORY_PATTERNS)
    )

    return {
        "time": time_features,
        "history": history_features,
        "weather": weather_features,
        "rolling_weather": rolling_weather,
        "derived": derived_features,
        "flag": flag_features,
    }


def _build_experiment_feature_sets(
    groups: dict[str, list[str]],
) -> dict[str, list[str]]:
    """根据消融设计定义每组实验使用的特征列.

    消融链路:
      A  = time + history                                          (无气象)
      B  = A + ghi_wm2 + ghi_wm2 滚动统计 + weather_missing       (仅 GHI)
      C1 = A + 全部气象 + 全部滚动 + 全部派生 + weather_missing    (完整 DISC)

    Parameters
    ----------
    groups : dict
        _detect_feature_groups 的输出.

    Returns
    -------
    dict[str, list[str]]
        keys 为 A/B/C1, 每个值为该实验使用的特征列名列表.
    """
    time_f = groups["time"]
    history_f = groups["history"]
    weather_f = groups["weather"]
    rolling_f = groups["rolling_weather"]
    derived_f = groups["derived"]
    flag_f = groups["flag"]

    # --- A: history_only (time + PV history, 不含任何气象) ---
    a_features = sorted(set(time_f + history_f))

    # --- B: HRRR-GHI-only ---
    # A 的基础上 + ghi_wm2 + ghi 滚动统计 + weather_missing 标志
    ghi_only = [c for c in weather_f if c == "ghi_wm2"]
    ghi_rolling = [c for c in rolling_f if c.startswith("ghi_wm2_rolling_")]
    b_features = sorted(set(a_features + ghi_only + ghi_rolling + flag_f))

    # --- C1: HRRR-DISC (完整 DISC 分解 + 派生特征) ---
    c1_features = sorted(set(
        time_f + history_f + weather_f + rolling_f + derived_f + flag_f
    ))

    return {"A": a_features, "B": b_features, "C1": c1_features}


# ============================================================================
# 模型训练与评估
# ============================================================================


def _compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    daytime_mask: Optional[np.ndarray] = None,
) -> dict[str, float]:
    """计算全样本和白天样本的 RMSE / nRMSE / MAE.

    nRMSE = RMSE / mean(actual_power)
    白天筛选依据: daytime_mask (通常为 solar_zenith_deg < 85 或 ghi_wm2 > 10).

    Parameters
    ----------
    y_true : ground truth 功率数组 (kW).
    y_pred : 预测功率数组 (kW).
    daytime_mask : bool 掩码, True=白天样本, None=全量视为白天.

    Returns
    -------
    dict
        keys: all_rmse, all_nrmse, all_mae, n_test,
              daytime_rmse, daytime_nrmse, daytime_mae, n_daytime.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    # 物理裁剪: 树模型无约束回归可能产出负值
    y_pred = np.maximum(y_pred, 0.0)

    # ---- 全样本指标 ----
    errors = y_pred - y_true
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    mean_true = float(np.mean(y_true))
    # 避免全零目标 (夜间) 导致除零
    nrmse = rmse / mean_true if mean_true > 1e-10 else 0.0
    mae = float(np.mean(np.abs(errors)))

    metrics: dict[str, float] = {
        "all_rmse": rmse,
        "all_nrmse": nrmse,
        "all_mae": mae,
        "n_test": int(len(y_true)),
    }

    # ---- 白天指标 ----
    if daytime_mask is not None:
        dt_mask = np.asarray(daytime_mask, dtype=bool)
        n_daytime = int(dt_mask.sum())
        if n_daytime > 0:
            y_true_dt = y_true[dt_mask]
            y_pred_dt = y_pred[dt_mask]
            dt_err = y_pred_dt - y_true_dt
            dt_rmse = float(np.sqrt(np.mean(dt_err ** 2)))
            dt_mean = float(np.mean(y_true_dt))
            dt_nrmse = dt_rmse / dt_mean if dt_mean > 1e-10 else 0.0
            dt_mae = float(np.mean(np.abs(dt_err)))
        else:
            dt_rmse = dt_nrmse = dt_mae = 0.0
        metrics.update({
            "daytime_rmse": dt_rmse,
            "daytime_nrmse": dt_nrmse,
            "daytime_mae": dt_mae,
            "n_daytime": n_daytime,
        })
    else:
        # 无白天掩码: 将全量视为白天 (退化情况)
        metrics.update({
            "daytime_rmse": rmse,
            "daytime_nrmse": nrmse,
            "daytime_mae": mae,
            "n_daytime": len(y_true),
        })

    return metrics


def _make_daytime_mask(test_df: pd.DataFrame) -> np.ndarray:
    """生成白天样本掩码.

    优先级:
      1. solar_zenith_deg < 85 (PV 源数据若有此列)
      2. ghi_wm2 > 10 W/m2 (HRRR 聚合气象)
      3. 全部为 True (兜底, 无白天/夜间区分能力)

    Returns
    -------
    np.ndarray (bool)
    """
    if "solar_zenith_deg" in test_df.columns:
        return test_df["solar_zenith_deg"].values < 85.0
    elif "ghi_wm2" in test_df.columns:
        return test_df["ghi_wm2"].values > 10.0
    else:
        return np.ones(len(test_df), dtype=bool)


def _train_eval_lgb(
    X_train: np.ndarray | pd.DataFrame,
    y_train: np.ndarray | pd.Series,
    X_val: np.ndarray | pd.DataFrame,
    y_val: np.ndarray | pd.Series,
    X_test: np.ndarray | pd.DataFrame,
    y_test: np.ndarray | pd.Series,
    daytime_mask: Optional[np.ndarray] = None,
    model_path: Optional[str] = None,
) -> tuple[dict[str, float], lgb.Booster]:
    """使用 Tuned 超参训练 LightGBM, 返回测试集指标和模型.

    保留技术:
      - early_stopping(80) 通过验证集防止过拟合
      - n_estimators=1800 + lr=0.02 提供充足容量同时避免早期过拟合
      - reg_lambda=0.8 L2 正则化

    Parameters
    ----------
    X_train, y_train : 训练集.
    X_val, y_val : 验证集 (用于 early stopping).
    X_test, y_test : 测试集 (仅一次性评估).
    daytime_mask : 白天样本掩码 (与 y_test 长度一致).
    model_path : 可选, 模型 pickle 保存路径.

    Returns
    -------
    metrics : dict 标量指标.
    model : 训练完毕的 lgb.Booster.
    """
    params = {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "n_estimators": 1800,
        "learning_rate": 0.02,
        "max_depth": 10,
        "num_leaves": 31,
        "reg_lambda": 0.8,
        "verbose": -1,
        "seed": 42,
    }

    train_ds = lgb.Dataset(X_train, y_train)
    val_ds = lgb.Dataset(X_val, y_val, reference=train_ds)

    print(f"  Training LightGBM ({len(X_train)} train, {len(X_val)} val, "
          f"{len(X_test)} test rows)...")
    model = lgb.train(
        params,
        train_ds,
        valid_sets=[val_ds],
        callbacks=[
            lgb.early_stopping(80),
            lgb.log_evaluation(0),  # quiet — 只输出最终结果
        ],
    )
    print(f"  Best iteration: {model.best_iteration}")

    # ---- 测试集预测 ----
    y_pred = model.predict(X_test, num_iteration=model.best_iteration)

    # ---- 计算指标 ----
    metrics = _compute_metrics(y_test, y_pred, daytime_mask=daytime_mask)

    # ---- 保存模型 ----
    if model_path is not None:
        save_dir = os.path.dirname(model_path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump(model, f)
        print(f"  Model saved: {model_path}")

    return metrics, model


# ============================================================================
# 消融实验入口
# ============================================================================


def run_ablation_experiment(
    feature_df: pd.DataFrame,
    output_dir: str,
) -> pd.DataFrame:
    """运行四基线消融实验 (A, B, C1, D).

    流程:
      1. 按时间顺序 70/15/15 切分 train/val/test
      2. 自动检测特征组
      3. 对 A/B/C1 分别训练 LightGBM 并评估
      4. D (NSRDB oracle) 使用硬编码参考指标, 无需训练
      5. 汇总 metrics DataFrame 并保存至 output_dir

    Parameters
    ----------
    feature_df : pd.DataFrame
        ``build_complete_step1_feature_table`` 输出的完整特征表.
    output_dir : str
        产物输出目录 (模型 pickle + metrics CSV).

    Returns
    -------
    pd.DataFrame
        每行一个实验, 列: exp_id, label, n_features, *metrics, model_path.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    target_col = "target_pv_power_t_plus_24h"

    # ========================================================================
    # 1. 按时间顺序切分 (chronological split, 70/15/15)
    # ========================================================================
    splits = _chronological_split(feature_df)
    train_df = splits["train"]
    val_df = splits["validation"]
    test_df = splits["test"]

    print(f"Data split: {len(train_df)} train / {len(val_df)} val / "
          f"{len(test_df)} test rows")

    # ========================================================================
    # 2. 检测特征组
    # ========================================================================
    groups = _detect_feature_groups(feature_df)
    exp_feature_sets = _build_experiment_feature_sets(groups)

    print("\nFeature group counts:")
    for grp_name, cols in sorted(groups.items()):
        print(f"  {grp_name}: {len(cols)} cols")
        if cols:
            preview = cols[:3]
            suffix = "..." if len(cols) > 3 else ""
            print(f"    e.g. {preview}{suffix}")
    print()

    print("Experiment feature set sizes:")
    for exp_id in ["A", "B", "C1"]:
        label = EXP_LABELS.get(exp_id, exp_id)
        print(f"  {exp_id} ({label}): {len(exp_feature_sets[exp_id])} features")
    print()

    # ========================================================================
    # 3. 准备测试集: 清除 NaN 目标 + 白天掩码
    # ========================================================================
    test_clean = test_df.dropna(subset=[target_col])
    dt_mask = _make_daytime_mask(test_clean)
    y_test_global = test_clean[target_col].values.astype(float)

    # Oracle 分母: 测试集实际功率均值
    all_mean = float(np.mean(y_test_global)) if len(y_test_global) > 0 else 1.0
    daytime_vals = y_test_global[dt_mask] if dt_mask.any() else np.array([])
    daytime_mean = float(np.mean(daytime_vals)) if len(daytime_vals) > 0 else 1.0

    rows: list[dict[str, Any]] = []

    # ========================================================================
    # 4. 训练实验 A / B / C1
    # ========================================================================
    for exp_id in ["A", "B", "C1"]:
        features = exp_feature_sets[exp_id]
        label = EXP_LABELS[exp_id]
        print(f"\n{'=' * 60}")
        print(f"Experiment {exp_id} ({label}): {len(features)} features")
        print(f"{'=' * 60}")

        # 清理: 训练/验证也需要删掉 NaN 目标
        train_clean = train_df.dropna(subset=[target_col])
        val_clean = val_df.dropna(subset=[target_col])

        X_train = train_clean[features].values
        y_train = train_clean[target_col].values.astype(float)
        X_val = val_clean[features].values
        y_val = val_clean[target_col].values.astype(float)
        X_test = test_clean[features].values

        model_path = str(output_path / f"lightgbm_{exp_id}.pkl")

        metrics, _model = _train_eval_lgb(
            X_train, y_train,
            X_val, y_val,
            X_test, y_test_global,
            daytime_mask=dt_mask,
            model_path=model_path,
        )

        rows.append({
            "exp_id": exp_id,
            "label": label,
            "n_features": len(features),
            **metrics,
            "model_path": model_path,
        })

    # ========================================================================
    # 5. 实验 D: NSRDB oracle (硬编码参考指标)
    # ========================================================================
    all_nrmse_d = 0.0784
    daytime_nrmse_d = 0.0903
    all_rmse_d = all_nrmse_d * all_mean
    daytime_rmse_d = daytime_nrmse_d * (daytime_mean if daytime_mean > 0 else all_mean)

    rows.append({
        "exp_id": "D",
        "label": "NSRDB oracle",
        "n_features": 0,
        "all_rmse": all_rmse_d,
        "all_nrmse": all_nrmse_d,
        "all_mae": 0.0,
        "daytime_rmse": daytime_rmse_d,
        "daytime_nrmse": daytime_nrmse_d,
        "daytime_mae": 0.0,
        "n_test": len(y_test_global),
        "n_daytime": int(dt_mask.sum()),
        "model_path": "",
    })

    # ========================================================================
    # 6. 汇总输出
    # ========================================================================
    metrics_df = pd.DataFrame(rows)

    print(f"\n{'=' * 60}")
    print("Ablation Results Summary")
    print(f"{'=' * 60}")
    print(metrics_df.to_string(index=False))

    # 保存 metrics CSV
    csv_path = output_path / "ablation_metrics.csv"
    metrics_df.to_csv(csv_path, index=False)
    print(f"\nMetrics saved: {csv_path}")

    return metrics_df


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="Train HRRR PV forecast models with 4-baseline ablation.",
    )
    parser.add_argument(
        "--hrrr",
        required=True,
        help="Path to HRRR Stage7 decomposed parquet (含 DNI/DHI 分解).",
    )
    parser.add_argument(
        "--pv",
        required=True,
        help="Path to Stage2 cleaned hourly PV dataset parquet.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed/hrrr_pv_models",
        help="Output directory for models and metrics CSV.",
    )
    return parser.parse_args()


def main() -> None:
    """入口: 构建特征表 → 运行消融实验 → 输出产物."""
    args = parse_args()

    print("=" * 60)
    print("HRRR PV Training CLI — Ablation Experiment Runner")
    print("=" * 60)
    print(f"HRRR path: {args.hrrr}")
    print(f"PV path:   {args.pv}")
    print(f"Output:    {args.output_dir}")
    print()

    # 构建特征表
    print("Building feature table...")
    df = build_complete_step1_feature_table(args.hrrr, args.pv)
    print(f"Feature table shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print()

    # 运行消融实验
    metrics = run_ablation_experiment(df, args.output_dir)

    print(f"\n{'=' * 60}")
    print("Done.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
