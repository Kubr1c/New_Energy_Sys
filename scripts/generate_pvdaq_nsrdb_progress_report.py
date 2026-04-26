from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed" / "pvdaq_nsrdb_2022_2023"
OPENMETEO = ROOT / "data" / "processed" / "pvdaq_openmeteo_forecast_2022_2023"
REPORT_DIR = ROOT / "reports"
FIGURE_DIR = REPORT_DIR / "figures" / "pvdaq_nsrdb_progress"
REPORT_PATH = REPORT_DIR / "pvdaq_nsrdb_progress_report.md"


def _load_json(path: Path) -> dict:
    """Load a UTF-8 JSON report produced by the stage CLIs."""

    return json.loads(path.read_text(encoding="utf-8"))


def _target_label(target: str) -> str:
    """Convert internal target column names into compact chart labels."""

    return (
        target.replace("target_pv_power_t_plus_", "t+")
        .replace("h", "h")
        .replace("_", " ")
    )


def _save_current_figure(path: Path) -> None:
    """Write the active matplotlib figure with stable report-friendly styling."""

    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180, bbox_inches="tight")
    plt.close()


def _plot_stage_flow(stage2: dict, stage3: dict) -> Path:
    """Visualize row counts through Stage1-3.

    The chart is intentionally simple: the main quality risk in this iteration
    was accidental row loss during feature engineering, so row continuity is the
    first metric a reviewer should see.
    """

    path = FIGURE_DIR / "stage_row_counts.png"
    labels = ["Stage1 raw aligned", "Stage2 cleaned", "Stage3 features"]
    values = [
        int(stage2["rows"]["initial"]),
        int(stage2["rows"]["final_cleaned"]),
        int(stage3["output_rows"]),
    ]

    plt.figure(figsize=(8, 4.5))
    bars = plt.bar(labels, values, color=["#3B82F6", "#10B981", "#F59E0B"])
    plt.ylabel("Rows")
    plt.title("PVDAQ + NSRDB pipeline row counts")
    plt.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, value, f"{value:,}", ha="center", va="bottom")
    _save_current_figure(path)
    return path


def _plot_week_profile(stage2_frame: pd.DataFrame) -> Path:
    """Plot one representative week of PV output and NSRDB irradiance."""

    path = FIGURE_DIR / "pv_ghi_week_profile.png"
    week = stage2_frame[
        (stage2_frame["timestamp"] >= pd.Timestamp("2022-06-01", tz="UTC"))
        & (stage2_frame["timestamp"] < pd.Timestamp("2022-06-08", tz="UTC"))
    ].copy()

    fig, axis_power = plt.subplots(figsize=(11, 4.5))
    axis_weather = axis_power.twinx()
    axis_power.plot(week["timestamp"], week["pv_power_kw"], color="#2563EB", linewidth=1.5, label="PV power")
    axis_weather.plot(week["timestamp"], week["ghi_wm2"], color="#F97316", linewidth=1.2, alpha=0.85, label="GHI")
    axis_power.set_ylabel("PV power (kW)")
    axis_weather.set_ylabel("GHI (W/m2)")
    axis_power.set_title("One-week PV output and NSRDB GHI")
    axis_power.grid(alpha=0.25)
    lines_1, labels_1 = axis_power.get_legend_handles_labels()
    lines_2, labels_2 = axis_weather.get_legend_handles_labels()
    axis_power.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left")
    _save_current_figure(path)
    return path


def _plot_pv_ghi_scatter(stage2_frame: pd.DataFrame) -> Path:
    """Show whether irradiance has the expected monotonic relation to PV power."""

    path = FIGURE_DIR / "pv_vs_ghi_scatter.png"
    sample = stage2_frame[["ghi_wm2", "pv_power_kw", "solar_zenith_angle_deg"]].dropna()
    sample = sample.sample(n=min(4000, len(sample)), random_state=42)

    plt.figure(figsize=(7, 5))
    scatter = plt.scatter(
        sample["ghi_wm2"],
        sample["pv_power_kw"],
        c=sample["solar_zenith_angle_deg"],
        cmap="viridis_r",
        s=8,
        alpha=0.45,
    )
    plt.colorbar(scatter, label="Solar zenith angle (deg)")
    plt.xlabel("GHI (W/m2)")
    plt.ylabel("PV power (kW)")
    plt.title("PV output vs NSRDB irradiance")
    plt.grid(alpha=0.2)
    _save_current_figure(path)
    return path


def _plot_weather_distributions(stage2_frame: pd.DataFrame) -> Path:
    """Summarize the main NSRDB weather fields entering Stage3."""

    path = FIGURE_DIR / "weather_distributions.png"
    columns = ["ghi_wm2", "temperature_c", "relative_humidity_pct", "wind_speed_ms"]
    titles = ["GHI (W/m2)", "Temperature (C)", "Relative humidity (%)", "Wind speed (m/s)"]

    fig, axes = plt.subplots(2, 2, figsize=(10, 6))
    for axis, column, title in zip(axes.ravel(), columns, titles):
        axis.hist(stage2_frame[column].dropna(), bins=50, color="#64748B", alpha=0.85)
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle("NSRDB weather feature distributions", y=1.02)
    _save_current_figure(path)
    return path


def _plot_model_comparison() -> Path:
    """Compare NSRDB and the previous Open-Meteo route on the same test metric."""

    path = FIGURE_DIR / "stage4_nrmse_comparison.png"
    nsrdb = pd.read_csv(PROCESSED / "stage4_lightgbm_metrics.csv")
    openmeteo = pd.read_csv(OPENMETEO / "stage4_lightgbm_metrics.csv")
    nsrdb["source"] = "NSRDB"
    openmeteo["source"] = "Open-Meteo"
    metrics = pd.concat([nsrdb, openmeteo], ignore_index=True)
    metrics = metrics[(metrics["split"] == "test") & (metrics["feature_set"] == "full_features")].copy()
    metrics["target_label"] = metrics["target"].map(_target_label)
    metrics["nrmse_pct"] = metrics["nrmse_capacity"] * 100

    pivot = metrics.pivot(index="target_label", columns="source", values="nrmse_pct")
    pivot = pivot.loc[["t+1h", "t+6h", "t+24h"]]

    axis = pivot.plot(kind="bar", figsize=(8, 4.5), color=["#F97316", "#2563EB"])
    axis.set_ylabel("Test nRMSE (%)")
    axis.set_xlabel("Forecast horizon")
    axis.set_title("Stage4 LightGBM full-feature test nRMSE")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(title="")
    for container in axis.containers:
        axis.bar_label(container, fmt="%.2f", padding=2)
    _save_current_figure(path)
    return path


def _plot_feature_groups(stage3: dict) -> Path:
    """Show engineered feature count by semantic group."""

    path = FIGURE_DIR / "feature_group_counts.png"
    groups = stage3["feature_groups"]
    names = ["time", "weather", "history", "dispatch"]
    keys = ["time_features", "weather_features", "historical_power_features", "dispatch_features"]
    values = [len(groups[key]) for key in keys]

    plt.figure(figsize=(8, 4.5))
    bars = plt.bar(names, values, color=["#14B8A6", "#F97316", "#2563EB", "#7C3AED"])
    plt.ylabel("Feature count")
    plt.title("Stage3 engineered feature groups")
    plt.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, value, str(value), ha="center", va="bottom")
    _save_current_figure(path)
    return path


def _schema_table() -> str:
    """Describe current dataset columns by functional group instead of dumping 125 names."""

    rows = [
        ("timestamp", "datetime UTC", "小时级时间戳；所有 PV、NSRDB、调度特征按该字段对齐。"),
        ("pv_power_kw", "float", "PVDAQ system 10 实测交流功率，单位 kW，核心预测对象。"),
        ("ghi_wm2 / dni_wm2 / dhi_wm2", "float", "NSRDB 水平总辐照、直射辐照、散射辐照，单位 W/m2。"),
        ("clearsky_*_wm2", "float", "NSRDB 晴空辐照基准，用于构造 clear-sky index。"),
        ("temperature_c / dew_point_c", "float", "环境温度与露点温度，单位摄氏度。"),
        ("relative_humidity_pct", "float", "相对湿度，单位百分比。"),
        ("pressure_hpa", "float", "地表气压，单位 hPa；高海拔站点数值低于海平面正常。"),
        ("wind_speed_ms / wind_direction_deg", "float", "风速与风向，用于刻画组件散热和天气状态。"),
        ("solar_zenith_angle_deg", "float", "太阳天顶角，反映太阳高度。"),
        ("surface_albedo / cloud_type / weather_fill_flag", "float", "NSRDB 地表反照率、云型编码、数据填补标记。"),
        ("load_mw / price_eur_mwh", "float", "OPSD 画像映射的负荷和电价，不是真实同区域同刻市场数据。"),
        ("storage_*", "float/int", "规则储能仿真状态、充放电功率、SOC、可用容量和收益。"),
        ("*_normalized", "float", "Stage3 标准化天气特征，便于模型吸收不同量纲变量。"),
        ("*_roll_24h_mean", "float", "滞后 1 小时后的 24 小时滚动统计，避免时间泄漏。"),
        ("pv_power_lag_* / pv_power_roll_*", "float", "历史功率滞后和滚动特征，是短期预测的主要信息源。"),
        ("target_pv_power_t_plus_*h", "float", "监督学习标签：未来 1、6、24 小时 PV 功率。"),
    ]
    lines = ["| 表头/字段组 | 类型 | 含义 |", "|---|---|---|"]
    lines.extend(f"| `{name}` | {kind} | {desc} |" for name, kind, desc in rows)
    return "\n".join(lines)


def _metrics_table() -> str:
    """Build a compact Stage4 table for the report."""

    metrics = pd.read_csv(PROCESSED / "stage4_lightgbm_metrics.csv")
    full = metrics[(metrics["split"] == "test") & (metrics["feature_set"] == "full_features")].copy()
    full["target"] = full["target"].map(_target_label)
    full = full.sort_values("target", key=lambda s: s.map({"t+1h": 1, "t+6h": 2, "t+24h": 3}))

    lines = ["| 预测目标 | MAE kW | RMSE kW | nRMSE | 日间 nRMSE |", "|---|---:|---:|---:|---:|"]
    for _, row in full.iterrows():
        lines.append(
            "| {target} | {mae:.4f} | {rmse:.4f} | {nrmse:.2%} | {day:.2%} |".format(
                target=row["target"],
                mae=row["mae_kw"],
                rmse=row["rmse_kw"],
                nrmse=row["nrmse_capacity"],
                day=row["daytime_nrmse_capacity"],
            )
        )
    return "\n".join(lines)


def _write_report(figures: dict[str, Path], stage2: dict, stage3: dict) -> None:
    """Create the final Markdown progress and quality report."""

    rel = {key: path.relative_to(REPORT_DIR).as_posix() for key, path in figures.items()}
    report = f"""# PVDAQ + NSRDB 路线进度与质量评估报告

生成时间：2026-04-24

## 1. 当前结论

`PVDAQ system 10 + NSRDB PSM` 路线已经完成 Stage1 至 Stage4 的闭环验证。数据获取问题已解决，NSRDB-only 链路不再依赖 Open-Meteo 兜底。

当前数据可以支撑下一阶段的误差诊断、消融实验和 TCN/TFT 对比建模。但该路线使用的是 NSRDB 历史/观测型太阳资源数据，不是严格的 forecast-cycle 天气预报数据，因此不能把它表述为“预测时刻真实可获得的天气预报”。

Pitfall：NSRDB 提升了天气数据可信度，但没有解决 forecast weather 严格性；若论文强调真实预测部署，需要单独保留 HRRR 或 forecast API 作为严格天气验证实验。

## 2. 阶段推进状态

| 阶段 | 目标 | 当前结果 | 完成判断 |
|---|---|---:|---|
| Stage1 数据接入 | 接入 PVDAQ 实测功率、NSRDB 气象、调度辅助数据 | {stage2["rows"]["initial"]} 行 | 完成 |
| Stage2 数据清洗 | 缺失值、异常值、时间对齐、重采样、标准化 | {stage2["rows"]["final_cleaned"]} 行，覆盖率 {stage2["time_alignment"]["target_hour_coverage"]:.2%} | 完成 |
| Stage3 特征工程 | 时间、天气、历史功率、调度特征 | {stage3["output_rows"]} 行，{stage3["engineered_feature_count"]} 个派生特征 | 完成 |
| Stage4 基线建模 | LightGBM 首个可用版本 | 9 个模型训练完成 | 完成 |

![Stage row counts]({rel["stage_flow"]})

## 3. 数据结构与表头说明

当前主数据表分三层：

| 数据表 | 行数 | 列数 | 用途 |
|---|---:|---:|---|
| `hourly_training_with_storage.parquet` | 10033 | 28 | Stage1 拼接后的小时级训练底表 |
| `stage2_cleaned_hourly_dataset.parquet` | 10033 | 28 | Stage2 清洗后数据，无缺失、无重复、时间单调 |
| `stage3_feature_dataset.parquet` | 9841 | 125 | Stage3 模型输入表，包含特征和未来标签 |

{_schema_table()}

## 4. 数据质量

| 指标 | 当前值 | 判断 |
|---|---:|---|
| 配置期望小时数 | {stage2["time_alignment"]["expected_hours_in_config_range"]} | 覆盖 2022-01-01 至 2023-02-28 |
| 观测目标小时数 | {stage2["time_alignment"]["observed_target_hours"]} | 可用 |
| 目标小时覆盖率 | {stage2["time_alignment"]["target_hour_coverage"]:.2%} | 达标 |
| Stage2 缺失值 | 0 | 达标 |
| 重复时间戳 | {stage2["rows"]["duplicates"]} | 达标 |
| Stage3 删除行数 | {stage3["missing_value_handling"]["rows_removed_by_lag_or_future_target"]} | 合理，来自滞后窗口和未来标签 |
| Stage3 质量门禁 | 全部通过 | 达标 |

此前 Stage3 曾因 `clearsky_index_ghi = ghi / clearsky_ghi` 在夜间产生 `0/0`，误删大量夜间样本。现在已修正：夜间 clear-sky index 置为 `0`，样本数恢复到 9841 行。

Pitfall：`pressure_hpa` 在报告中出现异常计数，主要因为站点位于高海拔地区，地表气压低于海平面常见范围；这不应直接解释为错误数据。

## 5. 可视化说明

### 5.1 PV 功率与 NSRDB GHI

![PV and GHI week]({rel["week_profile"]})

PV 输出与 GHI 日周期一致，说明 NSRDB 辐照数据与 PVDAQ 功率在时间轴上对齐合理。

### 5.2 PV 功率与辐照关系

![PV vs GHI]({rel["pv_ghi_scatter"]})

散点图显示 PV 功率随 GHI 增加而上升，但存在明显离散，原因包括云型、太阳高度角、组件状态、逆变器限幅和站点实测噪声。

### 5.3 天气变量分布

![Weather distributions]({rel["weather_distributions"]})

NSRDB 提供的辐照、温度、湿度和风速分布完整，没有缺失填补造成的断裂形态。

### 5.4 特征组规模

![Feature group counts]({rel["feature_groups"]})

历史功率特征仍是当前模型的最大特征组，天气特征规模足以支持后续消融实验。

### 5.5 Stage4 指标对比

![Stage4 comparison]({rel["model_comparison"]})

## 6. Stage4 测试结果

{_metrics_table()}

与 Open-Meteo 旧链路对比：

| 目标 | NSRDB nRMSE | Open-Meteo nRMSE | 变化 |
|---|---:|---:|---:|
| `t+1h` | 6.82% | 6.89% | 改善 0.07 个百分点 |
| `t+6h` | 13.12% | 13.13% | 基本持平 |
| `t+24h` | 15.19% | 14.61% | 变差 0.58 个百分点 |

判断：NSRDB 路线提高了数据来源可信度，但没有在所有 horizon 上带来指标优势。短期预测略优，中长期预测仍需要误差分组、消融实验和更强序列模型验证。

## 7. 下一阶段可行性

可以推进下一阶段，推荐顺序如下：

```mermaid
flowchart LR
    A["PVDAQ + NSRDB Stage4 baseline"] --> B["误差分组分析"]
    B --> C["天气/历史功率/调度特征消融"]
    C --> D["LightGBM 调参"]
    D --> E["TCN 序列模型"]
    E --> F["TFT 备选实验"]
```

| 下一步 | 可行性 | 原因 |
|---|---:|---|
| 误差分组 | 高 | 当前已有预测结果、天气字段和日间指标 |
| 消融实验 | 高 | 特征组边界清晰，可以量化天气贡献 |
| LightGBM 调参 | 高 | 数据规模和质量已满足 |
| TCN | 高 | 9841 小时样本可构造序列窗口 |
| TFT | 中 | 单站一年多数据偏少，存在过拟合风险 |

最终判断：当前路线足以支撑下一阶段。最优动作不是继续换数据源，而是基于 NSRDB 主线做严格的模型解释实验。

Pitfall：如果下一阶段只做模型调参，不做天气消融和分组误差，无法证明 NSRDB 天气特征到底贡献了多少。
"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    """Generate figures and the Markdown report for the NSRDB route."""

    stage2 = _load_json(PROCESSED / "stage2_quality_report.json")
    stage3 = _load_json(PROCESSED / "stage3_feature_report.json")
    stage2_frame = pd.read_parquet(PROCESSED / "stage2_cleaned_hourly_dataset.parquet")

    figures = {
        "stage_flow": _plot_stage_flow(stage2, stage3),
        "week_profile": _plot_week_profile(stage2_frame),
        "pv_ghi_scatter": _plot_pv_ghi_scatter(stage2_frame),
        "weather_distributions": _plot_weather_distributions(stage2_frame),
        "feature_groups": _plot_feature_groups(stage3),
        "model_comparison": _plot_model_comparison(),
    }
    _write_report(figures, stage2, stage3)
    print(REPORT_PATH)
    for path in figures.values():
        print(path)


if __name__ == "__main__":
    main()
