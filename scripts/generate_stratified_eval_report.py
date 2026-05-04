#!/usr/bin/env python3
"""天气分层评估报告生成器（Weather-Stratified Evaluation Report）。

对 Stage5 微调后的 LightGBM 全量特征模型，在测试集上按 valid_time 的
天气场景（clear/mixed/overcast/night）对预测误差进行分层评估，并输出
Markdown 格式的结构化报告和可视化图表。

评估关键原则：
  1. TIME ALIGNMENT：预测值始终以 valid_time 对齐（origin_time + horizon）
  2. WEATHER AT VALID TIME：天气场景分类使用 valid_time 时的 NSRDB 观测
  3. DAYTIME 定义：solar_elevation > 5°（pvlib 计算），非 solar_zenith < 85

输出文件：
  - reports/stratified_eval_report.md
  - reports/figures/stratified_rmse_by_scenario.png
  - reports/figures/stratified_bias_by_valid_hour.png
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pvlib

# ── 将 src 加入模块搜索路径 ──────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from new_energy_sys.modeling import _chronological_split

# ── 路径常量 ──────────────────────────────────────────────────────────
DATA_DIR = ROOT / "data/processed/pvdaq_nsrdb_2020_2022"
MODEL_DIR = DATA_DIR / "stage5_models"
REPORT_DIR = ROOT / "reports"
FIGURE_DIR = REPORT_DIR / "figures"
REPORT_PATH = REPORT_DIR / "stratified_eval_report.md"

# ── 站点参数（来自 configs/data_sources.pvdaq_nsrdb_2020_2022.json） ──
SITE = {"lat": 39.7404, "lon": -105.1774, "alt": 1730, "capacity_kw": 1.12}
CLIP_UPPER = SITE["capacity_kw"] * 1.05  # 物理裁剪上限
SOLAR_ELEV_THRESHOLD = 5  # 白天最低太阳高度角（度）

# 三个预测时域：标签名 → 小时数
HORIZONS: dict[str, int] = {"t_plus_1h": 1, "t_plus_6h": 6, "t_plus_24h": 24}
# 模型文件命名模板
MODEL_KEY = "lightgbm_tuned_full_features_target_pv_power_{horizon}.pkl"

# 报告显示的友好标签
HORIZON_LABELS = {"t_plus_1h": "t+1h", "t_plus_6h": "t+6h", "t_plus_24h": "t+24h"}


# ══════════════════════════════════════════════════════════════════════
#  数据加载
# ══════════════════════════════════════════════════════════════════════

def _load_data() -> pd.DataFrame:
    """加载 Stage 3 特征数据集。

    Returns:
        DataFrame，包含 timestamp（UTC）、pv_power_kw、clearsky_index_ghi、
        solar_zenith_angle_deg 及全部 target 列。
    """
    path = DATA_DIR / "stage3_feature_dataset.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"[数据] 已加载 {len(df)} 行, {df.shape[1]} 列, "
          f"时间范围 {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def _load_models() -> dict[str, dict]:
    """加载三个时域的 LightGBM 模型捆绑包。

    Returns:
        dict: horizon_key → {"model", "features", "target", "capacity_kw", ...}
    """
    bundles: dict[str, dict] = {}
    for horizon_key in HORIZONS:
        path = MODEL_DIR / MODEL_KEY.format(horizon=horizon_key)
        if not path.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")
        with open(path, "rb") as f:
            bundles[horizon_key] = pickle.load(f)
        print(f"[模型] 已加载 {horizon_key}: {path.name}")
    return bundles


# ══════════════════════════════════════════════════════════════════════
#  天气场景分类
# ══════════════════════════════════════════════════════════════════════

def _classify_scenario(clearsky_index: float, solar_elevation: float) -> str:
    """基于 clearsky_index_ghi 和太阳高度角判断天气场景。

    Args:
        clearsky_index: 晴空指数（NSRDB 观测值），范围 [0, 1]
        solar_elevation: 太阳高度角（度）

    Returns:
        场景标签: "clear", "mixed", "overcast", "night"
    """
    if solar_elevation <= SOLAR_ELEV_THRESHOLD:
        return "night"
    if clearsky_index >= 0.7:
        return "clear"
    if clearsky_index >= 0.3:
        return "mixed"
    return "overcast"


def _compute_weather_at_valid_time(
    origin_timestamps: pd.Series,
    horizon_hours: int,
    weather_lookup: pd.DataFrame,
) -> dict:
    """获取 valid_time 的天气场景信息。

    valid_time = origin_time + horizon，然后从全量数据中查找该时刻的
    clearsky_index_ghi，并用 pvlib 计算太阳高度角。

    返回所有数组与 origin_timestamps 等长，无天气数据的样本用 NaN/None 填充，
    由调用者根据 has_weather_mask 统一过滤。

    Returns:
        dict 包含（所有数组与 origin_timestamps 等长）:
          - valid_times: valid_time 的 DatetimeIndex
          - scenarios: 天气场景标签列表（无天气数据则 None）
          - valid_hours: valid_time 的小时数（0–23）
          - has_weather_mask: bool 数组，标记哪些样本有有效天气数据
    """
    # valid_time = origin_time + horizon
    valid_times_all = origin_timestamps + pd.Timedelta(hours=horizon_hours)

    # 构建天气查找表（去重 → 索引）
    lookup = weather_lookup[["timestamp", "clearsky_index_ghi"]]
    lookup = lookup.drop_duplicates("timestamp").set_index("timestamp")

    # 对齐：标记哪些 valid_time 能在查找表中找到
    merged = lookup.reindex(valid_times_all)
    has_weather = merged["clearsky_index_ghi"].notna().values
    valid_times = pd.DatetimeIndex(valid_times_all)

    # 对所有 valid_time 计算太阳高度角（pvlib 可处理所有时间戳）
    loc = pvlib.location.Location(
        latitude=SITE["lat"], longitude=SITE["lon"], altitude=SITE["alt"]
    )
    solar_pos = loc.get_solarposition(times=valid_times)
    elevations_all = solar_pos["elevation"].values

    # 在 merged 中取 clearsky_index_ghi（带 NaN），逐样本判定场景
    cs_indices_all = merged["clearsky_index_ghi"].values
    scenarios_all: list[str | None] = []
    for i in range(len(valid_times_all)):
        if not has_weather[i]:
            scenarios_all.append(None)  # 无天气数据
        else:
            scenarios_all.append(
                _classify_scenario(float(cs_indices_all[i]), float(elevations_all[i]))
            )

    return {
        "valid_times": valid_times,
        "scenarios": scenarios_all,
        "valid_hours": valid_times.hour,
        "has_weather_mask": has_weather,
    }


# ══════════════════════════════════════════════════════════════════════
#  指标计算
# ══════════════════════════════════════════════════════════════════════

def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """计算基础评估指标。

    nRMSE = RMSE / mean(actual) ← 在分层场景中更有意义的归一化方式。
    如果 mean(actual) 接近 0（如夜间），nRMSE 返回 NaN。
    """
    error = y_pred - y_true
    rmse = float(np.sqrt(np.mean(np.square(error))))
    mean_actual = float(np.mean(y_true))
    nrmse = rmse / mean_actual if mean_actual > 1e-10 else float("nan")
    return {
        "n": int(len(y_true)),
        "rmse_kw": rmse,
        "nrmse": nrmse,
        "mae_kw": float(np.mean(np.abs(error))),
        "bias_kw": float(np.mean(error)),
        "mean_actual": mean_actual,
    }


def _stratified_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    scenarios: list[str],
) -> pd.DataFrame:
    """按天气场景分层计算指标。

    每层独立计算指标，最后追加 TOTAL 行。
    """
    rows = []
    for scenario in ["clear", "mixed", "overcast", "night"]:
        mask = np.array([s == scenario for s in scenarios])
        n = int(mask.sum())
        if n == 0:
            rows.append({"scenario": scenario, "n": 0, "rmse_kw": float("nan"),
                         "nrmse": float("nan"), "mae_kw": float("nan"),
                         "bias_kw": float("nan"), "mean_actual": float("nan")})
            continue
        m = _metrics(y_true[mask], y_pred[mask])
        m["scenario"] = scenario
        rows.append(m)

    total = _metrics(y_true, y_pred)
    total["scenario"] = "TOTAL"
    rows.append(total)

    return pd.DataFrame(rows).set_index("scenario")


# ══════════════════════════════════════════════════════════════════════
#  绘图
# ══════════════════════════════════════════════════════════════════════

def _set_style() -> None:
    """设置适合工程报告的克制视觉风格。"""
    plt.rcParams.update({
        "figure.dpi": 140,
        "savefig.dpi": 180,
        "font.size": 9,
        "axes.titlesize": 12,
        "axes.labelsize": 9,
        "axes.grid": True,
        "grid.alpha": 0.22,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
    })


def _save_fig(fig: plt.Figure, name: str) -> Path:
    """保存图表到 FIGURE_DIR。"""
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURE_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_stratified_nrmse(
    strat_metrics: dict[str, pd.DataFrame],
) -> Path:
    """分组柱状图：3 时域 × 3 天气场景（白天）的 nRMSE。"""
    scenarios = ["clear", "mixed", "overcast"]
    x = np.arange(len(scenarios))
    width = 0.25

    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = ["#245C73", "#D99A2B", "#A23E48"]

    for i, (hk, hv) in enumerate(strat_metrics.items()):
        vals = [hv.loc[s, "nrmse"] for s in scenarios]
        label = HORIZON_LABELS.get(hk, hk)
        ax.bar(x + i * width, vals, width, label=label, color=colors[i])

    ax.set_title("Stratified nRMSE by Weather Scenario and Horizon")
    ax.set_xlabel("Weather scenario")
    ax.set_ylabel("nRMSE")
    ax.set_xticks(x + width)
    ax.set_xticklabels(scenarios)
    ax.legend(title="Horizon")
    return _save_fig(fig, "stratified_rmse_by_scenario.png")


def _plot_bias_by_valid_hour(
    results_detail: dict[str, dict],
) -> Path:
    """折线图：三个时域的 bias vs valid_hour（仅白天样本）。"""
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = ["#245C73", "#D99A2B", "#A23E48"]

    for i, hk in enumerate(HORIZONS):
        rd = results_detail[hk]
        # 仅白天样本
        day_mask = np.array([s != "night" for s in rd["scenarios"]])
        if day_mask.sum() == 0:
            continue
        hour_arr = rd["valid_hours"][day_mask]
        bias_arr = rd["bias_kw"][day_mask]

        # 按小时聚合平均 bias
        hour_bias = pd.DataFrame({"hour": hour_arr, "bias": bias_arr})
        hour_bias = hour_bias.groupby("hour")["bias"].mean()

        label = HORIZON_LABELS.get(hk, hk)
        ax.plot(hour_bias.index, hour_bias.values, marker="o",
                label=label, color=colors[i], linewidth=1.5)

    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.7)
    ax.set_title("Prediction Bias by Valid Hour (Daytime Only)")
    ax.set_xlabel("Valid hour (UTC)")
    ax.set_ylabel("Mean bias (kW)")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(title="Horizon")
    return _save_fig(fig, "stratified_bias_by_valid_hour.png")


# ══════════════════════════════════════════════════════════════════════
#  单个时域评估流水线
# ══════════════════════════════════════════════════════════════════════

def _evaluate_horizon(
    horizon_key: str,
    horizon_hours: int,
    bundle: dict,
    test: pd.DataFrame,
    weather_lookup: pd.DataFrame,
) -> dict:
    """对一个时域执行完整评估：预测、时间对齐、天气分层、夜间/总指标。

    Args:
        horizon_key: 时域标签（如 "t+1h"）
        horizon_hours: 时域小时数
        bundle: 模型捆绑包
        test: 测试集 DataFrame
        weather_lookup: 全量数据（用于 valid_time 天气查找）

    Returns:
        dict 包含:
          - strat_metrics: pd.DataFrame，各场景 + TOTAL
          - detail: 每个样本的详细信息 dict
          - evening_metrics: 20-23 UTC valid hour 的指标 dict
    """
    print(f"  └─ 预测中...", end="", flush=True)

    # --- 预测（origin time 的特征） ---
    features = bundle["features"]
    preds_raw = bundle["model"].predict(test[features],
                                        num_iteration=bundle["model"].best_iteration_)
    preds = np.clip(preds_raw, 0.0, CLIP_UPPER)

    # 实际值就在 target 列（feature engineering 阶段已 shift）
    actuals = test[bundle["target"]].values
    has_actual = ~np.isnan(actuals)

    # --- valid_time 天气查找 ---
    weather_info = _compute_weather_at_valid_time(
        test["timestamp"], horizon_hours, weather_lookup
    )

    # --- 合并过滤：需要同时有 valid actual 和 valid weather ---
    valid_mask = has_actual & weather_info["has_weather_mask"]
    valid_indices = np.where(valid_mask)[0]
    y_true = actuals[valid_indices]
    y_pred = preds[valid_indices]
    scenarios_list = [str(weather_info["scenarios"][int(i)]) for i in valid_indices]
    valid_hours_arr = weather_info["valid_hours"][valid_mask]

    n_total = len(y_true)
    print(f" {n_total} 个有效样本", flush=True)

    # --- 分层指标 ---
    strat = _stratified_metrics(y_true, y_pred, scenarios_list)

    # --- 细节数据（用于 bias-by-hour 图） ---
    detail = {
        "scenarios": np.array(scenarios_list),
        "valid_hours": valid_hours_arr,
        "bias_kw": (y_pred - y_true),
        "y_true": y_true,
        "y_pred": y_pred,
    }

    # --- 傍晚 bias 分析（20-23 UTC valid hour） ---
    evening_mask = (valid_hours_arr >= 20) & (valid_hours_arr <= 23)
    evening_day_mask = evening_mask & (np.array([s != "night" for s in scenarios_list]))
    if evening_day_mask.sum() > 0:
        eve_metrics = _metrics(y_true[evening_day_mask], y_pred[evening_day_mask])
    else:
        eve_metrics = {"n": 0, "rmse_kw": float("nan"),
                       "mae_kw": float("nan"), "bias_kw": float("nan"),
                       "mean_actual": float("nan"), "nrmse": float("nan")}

    return {
        "strat_metrics": strat,
        "detail": detail,
        "evening_metrics": eve_metrics,
        "n_total": n_total,
    }


# ══════════════════════════════════════════════════════════════════════
#  报告写入
# ══════════════════════════════════════════════════════════════════════

def _write_report(
    model_results: dict[str, dict],
    persistence_results: dict[str, dict],
    figure_paths: list[Path],
) -> None:
    """生成完整的 Markdown 评估报告。"""
    lines = [
        "# PV Prediction Stratified Evaluation Report",
        "",
        f"*Generated: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}*",
        f"*Site: PVDAQ System 10 ({SITE['lat']}, {SITE['lon']}), "
        f"Capacity: {SITE['capacity_kw']} kW*",
        "",
    ]

    # ── 模型评估结果 ──
    lines.append("## Model: LightGBM Tuned (Full Features)")
    lines.append("")

    for horizon_key in HORIZONS:
        h_label = HORIZON_LABELS.get(horizon_key, horizon_key)
        lines.append(f"### {h_label}")
        lines.append("")
        lines.append("| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |")
        lines.append("|----------|---|-----------|---:|----------|-----------:|")
        strat = model_results[horizon_key]["strat_metrics"]
        for scenario in ["clear", "mixed", "overcast", "night", "TOTAL"]:
            if scenario not in strat.index:
                continue
            row = strat.loc[scenario]
            lines.append(
                f"| {scenario:8s} | {int(row['n']):4d} | "
                f"{row['rmse_kw']:.3f} | {row['nrmse']:.3f} | "
                f"{row['mae_kw']:.3f} | {row['bias_kw']:+.3f} |"
            )
        lines.append("")

    # ── 傍晚 Bias 分析 ──
    lines.append("### Evening Bias Analysis (20–23 UTC Valid Hour)")
    lines.append("")
    lines.append("| Horizon | N | Bias (kW) | RMSE (kW) |")
    lines.append("|---------|---|----------:|----------:|")
    for horizon_key in HORIZONS:
        eve = model_results[horizon_key]["evening_metrics"]
        h_label = HORIZON_LABELS.get(horizon_key, horizon_key)
        lines.append(
            f"| {h_label} | {int(eve['n']):4d} | "
            f"{eve['bias_kw']:+.3f} | {eve['rmse_kw']:.3f} |"
        )
    lines.append("")

    # ── 持续性基线对比 ──
    lines.append("## Comparison: Persistence Baseline")
    lines.append("")
    lines.append("*(Prediction = PV power at origin time)*")
    lines.append("")

    for horizon_key in HORIZONS:
        h_label = HORIZON_LABELS.get(horizon_key, horizon_key)
        lines.append(f"### {h_label}")
        lines.append("")
        lines.append("| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |")
        lines.append("|----------|---|-----------|---:|----------|-----------:|")
        strat = persistence_results[horizon_key]["strat_metrics"]
        for scenario in ["clear", "mixed", "overcast", "night", "TOTAL"]:
            if scenario not in strat.index:
                continue
            row = strat.loc[scenario]
            lines.append(
                f"| {scenario:8s} | {int(row['n']):4d} | "
                f"{row['rmse_kw']:.3f} | {row['nrmse']:.3f} | "
                f"{row['mae_kw']:.3f} | {row['bias_kw']:+.3f} |"
            )
        lines.append("")

    # ── 图表 ──
    lines.append("## Figures")
    lines.append("")
    for fig_path in figure_paths:
        rel = fig_path.relative_to(REPORT_DIR).as_posix()
        lines.append(f"![{fig_path.stem}]({rel})")
        lines.append("")
    lines.append(f"*Figures saved to `{FIGURE_DIR}`*")
    lines.append("")

    # ── 结论 ──
    lines.append("## Conclusion")
    lines.append("")

    # 自动生成结论摘要
    for horizon_key in HORIZONS:
        strat = model_results[horizon_key]["strat_metrics"]
        total = strat.loc["TOTAL"]
        h_label = HORIZON_LABELS.get(horizon_key, horizon_key)
        lines.append(
            f"- **{h_label}**: TOTAL RMSE={total['rmse_kw']:.3f} kW, "
            f"nRMSE={total['nrmse']:.3f}, Bias={total['bias_kw']:+.3f} kW"
        )
        for s in ["clear", "mixed", "overcast"]:
            if s in strat.index:
                lines.append(
                    f"  - {s}: nRMSE={strat.loc[s, 'nrmse']:.3f}, "
                    f"N={int(strat.loc[s, 'n'])}"
                )
        lines.append("")

    lines.append("### Quality Gates")
    lines.append("")
    for horizon_key in HORIZONS:
        clr = model_results[horizon_key]["strat_metrics"]
        h_label = HORIZON_LABELS.get(horizon_key, horizon_key)
        has_all = all(s in clr.index for s in ["clear", "mixed", "overcast"])
        if has_all:
            ordering_ok = (
                clr.loc["clear", "nrmse"] <= clr.loc["mixed", "nrmse"]
                and clr.loc["mixed", "nrmse"] <= clr.loc["overcast", "nrmse"]
            )
        else:
            ordering_ok = float("nan")
        lines.append(f"- {h_label} clear < mixed < overcast nRMSE ordering: `{ordering_ok}`")
    lines.append("")

    # ── Pitfall ──
    lines.append("### Pitfall")
    lines.append("")
    lines.append(
        "当前分层基于 NSRDB 观测 clearsky_index_ghi 而非预报值。"
        "在实际预测场景中，valid_time 的 clearsky_index_ghi 不可提前获取，"
        "本报告的分层仅用于理解模型在不同天气条件下的表现差异，不能直接作为"
        "预报时期望性能的准确估计。"
    )
    lines.append("")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[报告] 已写入 {REPORT_PATH}")


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    _set_style()

    # 1. 加载数据
    df = _load_data()

    # 2. 时间切分
    splits = _chronological_split(df)
    test = splits["test"].copy()
    print(f"[切分] 训练 {len(splits['train'])} | "
          f"验证 {len(splits['validation'])} | "
          f"测试 {len(test)}")

    # 3. 加载模型
    bundles = _load_models()
    print("")

    # 4. 逐时域评估
    model_results: dict[str, dict] = {}
    persistence_results: dict[str, dict] = {}

    for horizon_key, horizon_hours in HORIZONS.items():
        # ── 模型评估 ──
        print(f"[评估  {horizon_key}] 模型:")
        model_results[horizon_key] = _evaluate_horizon(
            horizon_key, horizon_hours, bundles[horizon_key], test, df,
        )
        strat = model_results[horizon_key]["strat_metrics"]
        for s in strat.index:
            r = strat.loc[s]
            print(f"  {s:8s}: N={int(r['n']):4d}  "
                  f"RMSE={r['rmse_kw']:.3f}  nRMSE={r['nrmse']:.3f}  "
                  f"Bias={r['bias_kw']:+.3f}")

        # ── 持续性基线评估 ──
        print(f"[评估  {horizon_key}] 持续性基线:")
        preds = test["pv_power_kw"].values
        actuals = test[bundles[horizon_key]["target"]].values
        has_actual = ~np.isnan(actuals)

        weather_info = _compute_weather_at_valid_time(
            test["timestamp"], horizon_hours, df
        )
        valid_mask = has_actual & weather_info["has_weather_mask"]
        valid_indices = np.where(valid_mask)[0]

        yt = actuals[valid_indices]
        yp = preds[valid_indices]
        scenarios_list = [str(weather_info["scenarios"][int(i)]) for i in valid_indices]
        valid_hours_arr = weather_info["valid_hours"][valid_mask]

        pers_strat = _stratified_metrics(yt, yp, scenarios_list)
        print(f"  TOTAL:   N={int(pers_strat.loc['TOTAL', 'n']):4d}  "
              f"RMSE={pers_strat.loc['TOTAL', 'rmse_kw']:.3f}  "
              f"nRMSE={pers_strat.loc['TOTAL', 'nrmse']:.3f}")

        # 傍晚 bias（持续性基线）
        evening_day_mask = (
            (valid_hours_arr >= 20) & (valid_hours_arr <= 23)
            & (np.array([s != "night" for s in scenarios_list]))
        )
        if evening_day_mask.sum() > 0:
            eve = _metrics(yt[evening_day_mask], yp[evening_day_mask])
        else:
            eve = {"n": 0, "rmse_kw": float("nan"), "mae_kw": float("nan"),
                   "bias_kw": float("nan"), "mean_actual": float("nan"),
                   "nrmse": float("nan")}

        persistence_results[horizon_key] = {
            "strat_metrics": pers_strat,
            "evening_metrics": eve,
            "detail": {
                "scenarios": np.array(scenarios_list),
                "valid_hours": valid_hours_arr,
                "bias_kw": yp - yt,
                "y_true": yt,
                "y_pred": yp,
            },
            "n_total": len(yt),
        }

        print("")

    # 5. 绘图
    print("[绘图] 生成图表...")
    _plot_stratified_nrmse({h: model_results[h]["strat_metrics"] for h in HORIZONS})
    _plot_bias_by_valid_hour({h: model_results[h]["detail"] for h in HORIZONS})
    figure_paths = [
        FIGURE_DIR / "stratified_rmse_by_scenario.png",
        FIGURE_DIR / "stratified_bias_by_valid_hour.png",
    ]
    print(f"  └─ 图表已保存到 {FIGURE_DIR}")

    # 6. 写报告
    _write_report(model_results, persistence_results, figure_paths)
    print("\n[完成] 天气分层评估报告已生成完毕。")


if __name__ == "__main__":
    main()
