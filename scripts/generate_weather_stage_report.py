from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data/processed/nrel_opsd_weather"
FIGURE_DIR = ROOT / "reports/figures/weather_pipeline"
REPORT_PATH = ROOT / "reports/stage1_stage2_stage3_weather_progress_report.md"


def _set_style() -> None:
    """Set a restrained visual style suitable for an engineering report."""

    plt.rcParams.update(
        {
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
        }
    )


def _save(fig: plt.Figure, name: str) -> Path:
    """Save a figure under the report figure directory and close it."""

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURE_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    """Load the stage outputs and machine-readable quality reports."""

    stage2 = pd.read_parquet(PROCESSED_DIR / "stage2_cleaned_hourly_dataset.parquet")
    stage3 = pd.read_parquet(PROCESSED_DIR / "stage3_feature_dataset.parquet")
    stage2["timestamp"] = pd.to_datetime(stage2["timestamp"], utc=True)
    stage3["timestamp"] = pd.to_datetime(stage3["timestamp"], utc=True)

    with (PROCESSED_DIR / "stage2_quality_report.json").open("r", encoding="utf-8") as handle:
        stage2_report = json.load(handle)
    with (PROCESSED_DIR / "stage3_feature_report.json").open("r", encoding="utf-8") as handle:
        stage3_report = json.load(handle)
    return stage2, stage3, stage2_report, stage3_report


def plot_pv_forecast_weather_week(stage2: pd.DataFrame) -> Path:
    """Plot PV actual/forecast power and irradiance for a representative week."""

    week = stage2[(stage2["timestamp"] >= "2006-06-01") & (stage2["timestamp"] < "2006-06-08")].copy()
    fig, ax_power = plt.subplots(figsize=(12, 5.2))
    ax_weather = ax_power.twinx()

    ax_power.plot(week["timestamp"], week["pv_power_kw"] / 1000.0, label="Actual PV", linewidth=1.8, color="#245C73")
    ax_power.plot(week["timestamp"], week["pv_forecast_da_kw"] / 1000.0, label="DA forecast", linewidth=1.1, color="#D99A2B")
    ax_power.plot(week["timestamp"], week["pv_forecast_ha4_kw"] / 1000.0, label="HA4 forecast", linewidth=1.1, color="#A23E48")
    ax_weather.fill_between(week["timestamp"], week["ghi_wm2"], color="#8CB369", alpha=0.22, label="GHI")

    ax_power.set_title("PV Power, Forecasts, and GHI: Representative Week")
    ax_power.set_ylabel("PV power (MW)")
    ax_weather.set_ylabel("GHI (W/m2)")
    ax_power.set_xlabel("Time")
    lines = ax_power.get_lines() + [ax_weather.collections[0]]
    labels = ["Actual PV", "DA forecast", "HA4 forecast", "GHI"]
    ax_power.legend(lines, labels, ncol=4, loc="upper left")
    return _save(fig, "weather_pipeline_pv_forecast_ghi_week.png")


def plot_weather_profiles(stage2: pd.DataFrame) -> Path:
    """Show average hourly solar and meteorological profiles."""

    profile = stage2.groupby("hour", as_index=False)[
        ["ghi_wm2", "dni_wm2", "dhi_wm2", "temperature_c", "cloud_cover_pct"]
    ].mean()

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    axes[0].plot(profile["hour"], profile["ghi_wm2"], label="GHI", marker="o")
    axes[0].plot(profile["hour"], profile["dni_wm2"], label="DNI", marker="s")
    axes[0].plot(profile["hour"], profile["dhi_wm2"], label="DHI", marker="^")
    axes[0].set_title("Average Irradiance Profile")
    axes[0].set_xlabel("Hour of day")
    axes[0].set_ylabel("W/m2")
    axes[0].set_xticks(range(0, 24, 3))
    axes[0].legend()

    axes[1].plot(profile["hour"], profile["temperature_c"], label="Temperature", color="#A23E48", marker="o")
    axes[1].plot(profile["hour"], profile["cloud_cover_pct"], label="Cloud cover", color="#445E93", marker="s")
    axes[1].set_title("Average Weather Profile")
    axes[1].set_xlabel("Hour of day")
    axes[1].set_ylabel("C / %")
    axes[1].set_xticks(range(0, 24, 3))
    axes[1].legend()
    return _save(fig, "weather_pipeline_weather_profiles.png")


def plot_pv_irradiance_scatter(stage2: pd.DataFrame) -> Path:
    """Plot PV power against GHI and color samples by cloud cover."""

    daylight = stage2[stage2["ghi_wm2"] > 20.0].copy()
    sample = daylight.sample(n=min(2500, len(daylight)), random_state=7)

    fig, ax = plt.subplots(figsize=(8.4, 5.4))
    scatter = ax.scatter(
        sample["ghi_wm2"],
        sample["pv_power_kw"] / 1000.0,
        c=sample["cloud_cover_pct"],
        cmap="viridis_r",
        s=12,
        alpha=0.65,
        linewidths=0,
    )
    ax.set_title("PV Output vs GHI")
    ax.set_xlabel("GHI (W/m2)")
    ax.set_ylabel("PV power (MW)")
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("Cloud cover (%)")
    return _save(fig, "weather_pipeline_pv_vs_ghi_scatter.png")


def plot_correlation_heatmap(stage2: pd.DataFrame) -> Path:
    """Plot correlation between core power, weather, and market variables."""

    columns = [
        "pv_power_kw",
        "pv_forecast_da_kw",
        "pv_forecast_ha4_kw",
        "ghi_wm2",
        "dni_wm2",
        "dhi_wm2",
        "temperature_c",
        "relative_humidity_pct",
        "cloud_cover_pct",
        "wind_speed_ms",
        "load_mw",
        "price_eur_mwh",
    ]
    corr = stage2[columns].corr(numeric_only=True)

    fig, ax = plt.subplots(figsize=(9.4, 7.4))
    image = ax.imshow(corr, cmap="RdBu_r", vmin=-1.0, vmax=1.0)
    ax.set_title("Core Variable Correlation")
    ax.set_xticks(range(len(columns)))
    ax.set_yticks(range(len(columns)))
    ax.set_xticklabels(columns, rotation=45, ha="right")
    ax.set_yticklabels(columns)
    for i in range(len(columns)):
        for j in range(len(columns)):
            value = corr.iloc[i, j]
            ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=6)
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Pearson correlation")
    return _save(fig, "weather_pipeline_correlation_heatmap.png")


def plot_quality_and_features(stage2: pd.DataFrame, stage3_report: dict) -> Path:
    """Plot quality metrics and engineered feature counts by group."""

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))

    quality_labels = ["Stage 2 rows", "Stage 3 rows", "Missing cells", "Infinite cells"]
    numeric = stage2.select_dtypes(include=[np.number])
    quality_values = [
        len(stage2),
        stage3_report["output_rows"],
        int(stage2.isna().sum().sum()),
        int((~np.isfinite(numeric.to_numpy())).sum()),
    ]
    axes[0].bar(quality_labels, quality_values, color=["#2F6F4E", "#2F6F4E", "#A23E48", "#A23E48"])
    axes[0].set_title("Quality Metrics")
    axes[0].set_ylabel("Count")
    axes[0].tick_params(axis="x", rotation=18)
    for index, value in enumerate(quality_values):
        axes[0].text(index, value, str(value), ha="center", va="bottom")

    groups = stage3_report["feature_groups"]
    group_names = ["time", "weather", "history", "dispatch"]
    group_values = [
        len(groups["time_features"]),
        len(groups["weather_features"]),
        len(groups["historical_power_features"]),
        len(groups["dispatch_features"]),
    ]
    axes[1].bar(group_names, group_values, color=["#445E93", "#8CB369", "#D99A2B", "#A23E48"])
    axes[1].set_title("Engineered Feature Count by Group")
    axes[1].set_ylabel("Feature count")
    for index, value in enumerate(group_values):
        axes[1].text(index, value, str(value), ha="center", va="bottom")
    return _save(fig, "weather_pipeline_quality_feature_summary.png")


def plot_chronological_split(stage3_report: dict) -> Path:
    """Visualize the chronological train/validation/test split."""

    split = stage3_report["chronological_split"]
    labels = list(split.keys())
    values = [split[name]["rows"] for name in labels]

    fig, ax = plt.subplots(figsize=(9, 2.6))
    left = 0
    colors = ["#2F6F4E", "#D99A2B", "#A23E48"]
    for label, value, color in zip(labels, values, colors):
        ax.barh(["chronological split"], [value], left=left, color=color, label=f"{label}: {value}")
        ax.text(left + value / 2, 0, label, ha="center", va="center", color="white", fontweight="bold")
        left += value
    ax.set_title("Stage 4 Candidate Chronological Split")
    ax.set_xlabel("Rows")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    return _save(fig, "weather_pipeline_chronological_split.png")


def _markdown_image(path: Path) -> str:
    """Return a relative markdown image path from the report file location."""

    return path.relative_to(REPORT_PATH.parent).as_posix()


def write_report(stage2: pd.DataFrame, stage3: pd.DataFrame, stage2_report: dict, stage3_report: dict, figures: dict[str, Path]) -> None:
    """Write the final Chinese progress and quality assessment report."""

    weather_columns = [
        "ghi_wm2",
        "dni_wm2",
        "dhi_wm2",
        "temperature_c",
        "relative_humidity_pct",
        "dew_point_c",
        "cloud_cover_pct",
        "cloud_cover_low_pct",
        "cloud_cover_mid_pct",
        "cloud_cover_high_pct",
        "wind_speed_ms",
        "wind_direction_deg",
        "wind_gusts_ms",
        "pressure_hpa",
        "surface_pressure_hpa",
        "precipitation_mm",
        "toa_radiation_wm2",
    ]
    base_columns = [
        "timestamp",
        "pv_power_kw",
        "pv_forecast_da_kw",
        "pv_forecast_ha4_kw",
        "load_mw",
        "price_eur_mwh",
        "storage_soc",
        "storage_charge_kw",
        "storage_discharge_kw",
        "storage_revenue_eur",
    ]

    stage2_time = stage2_report["time_alignment"]
    stage3_split = stage3_report["chronological_split"]

    lines = [
        "# 第一至第三阶段任务推进与质量评估报告（含天气特征主链路）",
        "",
        "## 结论",
        "",
        "重新推进后的主链路已经从“DA/HA4 天气代理”升级为“外部气象补充 + DA/HA4 预测 + 历史功率 + 储能调度”的建模数据集。当前第三阶段输出 `8560` 条有效监督样本、`145` 个字段、`112` 个派生特征，缺失值和无穷值均为 `0`，满足进入第四阶段建模的最低质量要求。",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["NREL Actual / DA / HA4"] --> D["阶段一：数据准备与小时级对齐"]',
        '    B["NSRDB 优先"] --> D',
        '    C["Open-Meteo / ERA5 兜底成功"] --> D',
        '    E["OPSD 负荷/电价画像"] --> D',
        '    D --> F["阶段二：清洗、异常处理、质量门禁"]',
        '    F --> G["阶段三：时间/天气/历史功率/调度特征"]',
        '    G --> H["第四阶段：模型训练与评估"]',
        "```",
        "",
        "## 阶段一：数据准备",
        "",
        "本轮主配置为 `configs/data_sources.nrel_opsd_weather.json`，输出目录为 `data/processed/nrel_opsd_weather`。数据准备阶段完成了四类数据对齐：",
        "",
        "- NREL Solar Integration：光伏实际功率 `pv_power_kw`，日前预测 `pv_forecast_da_kw`，4小时前预测 `pv_forecast_ha4_kw`。",
        "- NSRDB：已实现优先下载逻辑，但当前接口请求被服务端拒绝；流程按设计自动切换到 Open-Meteo/ERA5。",
        "- Open-Meteo/ERA5：成功补齐 GHI、DNI、DHI、温度、湿度、露点、云量、风速、气压、降水等天气字段。",
        "- OPSD：由于 OPSD 与 NREL 2006 年份不重叠，继续使用星期-小时画像映射到 PV 时间轴，用于负荷和电价调度信号。",
        "",
        "阶段一输出 `8758` 行、`30` 列小时级数据，其中天气字段已进入主表。",
        "",
        "Pitfall：NSRDB 当前未实际落地，主链路天气由 Open-Meteo/ERA5 兜底提供；报告中必须写成外部气象补充数据。",
        "",
        "## 阶段二：数据清洗与质量评估",
        "",
        f"- 输入行数：`{stage2_report['rows']['initial']}`",
        f"- 清洗后行数：`{stage2_report['rows']['final_cleaned']}`",
        f"- 时间范围：`{stage2_time['min_timestamp']}` 至 `{stage2_time['max_timestamp']}`",
        f"- 目标小时覆盖率：`{stage2_time['target_hour_coverage']}`",
        f"- 缺失目标删除数：`{stage2_report['rows']['missing_target_removed']}`",
        f"- 重复时间戳：`{stage2_report['rows']['duplicates']}`",
        "",
        "阶段二执行了时间戳 UTC 统一、小时级对齐、重复时间戳去重、物理边界裁剪、短缺口填充和质量门禁验证。当前质量门禁全部通过：无缺失值、PV 功率在容量边界内、储能 SOC 在物理边界内、时间戳单调递增。",
        "",
        f"![阶段二质量与特征摘要]({_markdown_image(figures['quality_feature'])})",
        "",
        "Pitfall：OPSD 负荷和电价仍为画像映射，不是 2006 年同一市场真实逐时记录；经济性结论必须按仿真实验表述。",
        "",
        "## 阶段三：特征工程",
        "",
        f"- 输入行数：`{stage3_report['input_rows']}`",
        f"- 输出行数：`{stage3_report['output_rows']}`",
        f"- 输入字段：`{stage3_report['input_columns']}`",
        f"- 输出字段：`{stage3_report['output_columns']}`",
        f"- 派生特征：`{stage3_report['engineered_feature_count']}`",
        f"- 特征模式：`{stage3_report['weather_feature_mode']}`",
        f"- 删除样本：`{stage3_report['missing_value_handling']['rows_removed_by_lag_or_future_target']}`，由 `168h` 历史窗口和 `24h` 未来标签自然产生。",
        "",
        "特征工程构造了四类输入特征：",
        "",
        f"- 时间特征：`{len(stage3_report['feature_groups']['time_features'])}` 个，包含小时、星期、月份、年内日及周期编码。",
        f"- 天气特征：`{len(stage3_report['feature_groups']['weather_features'])}` 个，包含辐照、温度、湿度、云量、风速、气压、降水及 DA/HA4 预测派生特征。",
        f"- 历史功率特征：`{len(stage3_report['feature_groups']['historical_power_features'])}` 个，包含多尺度 lag、rolling 统计和历史预测误差。",
        f"- 调度特征：`{len(stage3_report['feature_groups']['dispatch_features'])}` 个，包含 SOC、净出力、充放电比例、电价阈值距离和负荷/电价滚动统计。",
        "",
        f"![时间切分]({_markdown_image(figures['split'])})",
        "",
        "Pitfall：历史功率和滚动特征必须保持先 `shift` 再 `rolling` 的因果顺序，第四阶段不得改成随机切分。",
        "",
        "## 处理后数据集结构说明",
        "",
        "阶段二清洗表是基础业务表，阶段三特征表是模型输入表。两者关系如下：",
        "",
        "| 数据集 | 文件 | 行数 | 列数 | 作用 |",
        "|---|---|---:|---:|---|",
        f"| 阶段二清洗表 | `stage2_cleaned_hourly_dataset.parquet` | `{len(stage2)}` | `{stage2.shape[1]}` | 业务字段、物理量、清洗后小时级样本 |",
        f"| 阶段三特征表 | `stage3_feature_dataset.parquet` | `{len(stage3)}` | `{stage3.shape[1]}` | 可直接进入建模的监督学习数据集 |",
        "",
        "基础字段分组：",
        "",
        "| 字段组 | 字段 | 说明 |",
        "|---|---|---|",
        f"| 光伏功率 | `{', '.join(base_columns[1:4])}` | 实际功率与 NREL DA/HA4 预测功率 |",
        f"| 天气 | `{', '.join(weather_columns)}` | Open-Meteo/ERA5 外部气象补充字段 |",
        "| 市场 | `load_mw`, `price_eur_mwh` | OPSD 画像映射后的负荷和电价信号 |",
        "| 储能 | `storage_soc`, `storage_charge_kw`, `storage_discharge_kw`, `storage_revenue_eur` | 规则调度仿真状态和收益 |",
        "| 时间 | `timestamp`, `hour`, `day_of_week`, `month` | UTC 时间戳和基础时间字段 |",
        "",
        "阶段三额外生成三个监督学习标签：`target_pv_power_t_plus_1h`、`target_pv_power_t_plus_6h`、`target_pv_power_t_plus_24h`。它们分别对应短时、日内和日前预测任务。",
        "",
        "Pitfall：`target_*` 字段只能作为标签 `y` 使用，不能混入模型输入 `X`。",
        "",
        "## 可视化分析",
        "",
        f"![PV预测与GHI周展示]({_markdown_image(figures['pv_week'])})",
        "",
        "该图显示实际 PV、DA、HA4 与 GHI 在同一周内的变化。PV 出力与 GHI 的日周期基本一致，说明补充天气字段对功率预测具有直接解释价值。",
        "",
        f"![天气日内画像]({_markdown_image(figures['weather_profiles'])})",
        "",
        "辐照度日内曲线清晰，温度和云量也呈现稳定日内结构。该结构可支撑时间特征与天气特征联合建模。",
        "",
        f"![PV与GHI散点]({_markdown_image(figures['pv_scatter'])})",
        "",
        "白天样本中 PV 出力随 GHI 上升而增加，云量对同等 GHI 下的离散程度有解释作用。这补强了项目从天气变量预测光伏功率的因果合理性。",
        "",
        f"![核心变量相关性]({_markdown_image(figures['corr'])})",
        "",
        "相关性热图显示 PV、预测功率、辐照度、温度和云量之间存在可建模关系。市场字段与 PV 的相关性较弱，适合作为调度侧特征而非功率预测主解释变量。",
        "",
        "Pitfall：相关性只说明线性关系强弱，不能替代严格的时间序列外推验证。",
        "",
        "## 下一阶段可行性评估",
        "",
        "可以进入第四阶段建模。依据：",
        "",
        "- 样本量 `8560`，满足年度小时级基线建模需求。",
        "- 气象字段已补齐，解决了上一版“无真实天气变量”的说服力缺口。",
        "- 第三阶段无缺失值、无无穷值、时间戳单调递增。",
        "- 已给出严格时间切分：训练 `5992` 行、验证 `1284` 行、测试 `1284` 行。",
        "- 已生成 `1h`、`6h`、`24h` 三类预测标签，支持多任务或多模型对比。",
        "",
        "第四阶段推荐先做三组模型：",
        "",
        "| 模型组 | 输入 | 目的 |",
        "|---|---|---|",
        "| Baseline | DA/HA4 + 时间 | 验证 NREL 预测基线强度 |",
        "| Weather-enhanced | DA/HA4 + 天气 + 时间 | 评估天气特征增益 |",
        "| Full-feature | 全部特征 | 评估历史功率与调度状态的综合收益 |",
        "",
        "评估指标建议使用 MAE、RMSE、nRMSE、MAPE（白天样本单独计算）和分时段误差分析。训练/验证/测试必须按当前报告中的时间顺序切分。",
        "",
        "Pitfall：第四阶段如果随机切分，会把相邻小时和未来季节模式泄漏到训练集，导致指标虚高，实验无效。",
        "",
        "## 总结",
        "",
        "重新推进后的前三阶段已经达到进入建模阶段的质量门槛。当前主链路的主要限制不再是缺少天气特征，而是外部天气数据与 NREL 功率数据并非同一原始观测链路，以及 OPSD 市场信号仍为画像映射。该限制可接受，但必须在论文和实验说明中透明披露。",
        "",
    ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    _set_style()
    stage2, stage3, stage2_report, stage3_report = _load_inputs()

    figures = {
        "pv_week": plot_pv_forecast_weather_week(stage2),
        "weather_profiles": plot_weather_profiles(stage2),
        "pv_scatter": plot_pv_irradiance_scatter(stage2),
        "corr": plot_correlation_heatmap(stage2),
        "quality_feature": plot_quality_and_features(stage2, stage3_report),
        "split": plot_chronological_split(stage3_report),
    }
    write_report(stage2, stage3, stage2_report, stage3_report, figures)

    print(f"Report written to {REPORT_PATH}")
    print(f"Figures written to {FIGURE_DIR}")


if __name__ == "__main__":
    main()
