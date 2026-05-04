#!/usr/bin/env python3
"""
diagnose_anomaly_samples.py

诊断 "白天有充足太阳辐照但 PV 出力接近零" 的异常样本。
对 Stage3 全量数据集 (2020-2022) 进行分析，输出 CSV 明细和 MD 诊断报告。

异常判定条件（5 条件必须同时满足）:
  1. solar_elevation > 10        # 明显白天
  2. clearsky_ghi_wm2 > 500      # 晴空理论辐照高
  3. ghi_wm2 > 250               # 实际辐照也高（排除阴天）
  4. clearsky_index_ghi > 0.5    # 晴空指数确认非厚云
  5. pv_power_kw < 0.02 * 1.12   # PV 出力接近零（~0.022 kW）

Author: Claude Code
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_PATH = Path("data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet")
OUTPUT_DIR = Path("reports")
CSV_PATH = OUTPUT_DIR / "anomaly_daytime_low_power_samples.csv"
MD_PATH = OUTPUT_DIR / "anomaly_diagnosis.md"

SITE_CAPACITY_KW = 1.12
PV_NEAR_ZERO_THRESHOLD = 0.02 * SITE_CAPACITY_KW  # ~0.0224 kW

# NSRDB cloud_type 数值 -> 可读标签映射
CLOUD_TYPE_LABELS = {
    0: "Clear",
    1: "Probably Clear",
    2: "Fog",
    3: "Water",
    4: "Supercooled Water",
    5: "Mixed",
    6: "Opaque Ice",
    7: "Cirrus",
    8: "Overcast",
    9: "Overshooting",
}


def load_data(path: Path) -> pd.DataFrame:
    """加载 Stage3 特征数据集"""
    print(f"[INFO] Loading data from {path}")
    df = pd.read_parquet(path)
    print(f"       Shape: {df.shape}")
    print(f"       Timestamp range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    return df


def compute_solar_elevation(df: pd.DataFrame) -> pd.Series:
    """
    计算太阳高度角。

    使用数据集中已有的 solar_zenith_angle_deg 列:
        solar_elevation = 90 - solar_zenith_angle_deg

    该方式完全确定且无数据泄露风险，比调用 pvlib 快得多。
    """
    if "solar_zenith_angle_deg" not in df.columns:
        raise KeyError(
            "solar_zenith_angle_deg not found in dataset. "
            "Fall back to pvlib? Run: "
            "loc = pvlib.location.Location(latitude=39.74, longitude=-105.18, altitude=1730); "
            "solar_pos = loc.get_solarposition(times=pd.DatetimeIndex(df['timestamp'])); "
            "df['solar_elevation'] = solar_pos['elevation'].values"
        )
    elevation = 90.0 - df["solar_zenith_angle_deg"].values
    return pd.Series(elevation, index=df.index, name="solar_elevation")


def find_anomalies(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """
    应用 5 条严格异常条件，返回异常子集和 daytime 总行数占比。

    异常条件（ALL must be True）:
        1. solar_elevation > 10         -> 明显白天
        2. clearsky_ghi_wm2 > 500       -> 晴空理论辐照高
        3. ghi_wm2 > 250                -> 实际辐照也高（排除阴天）
        4. clearsky_index_ghi > 0.5     -> 晴空指数确认非厚云
        5. pv_power_kw < 0.02 * 1.12    -> PV 出力接近零
    """
    # 条件 1: 太阳高度角 > 10 度
    cond_daytime = df["solar_elevation"] > 10

    # 条件 2: 晴空 GHI 理论值高
    cond_clearsky_high = df["clearsky_ghi_wm2"] > 500

    # 条件 3: 实际 GHI 也高（排除厚云覆盖场景）
    cond_ghi_high = df["ghi_wm2"] > 250

    # 条件 4: 晴空指数 > 0.5（sky 不是厚云）
    cond_csi = df["clearsky_index_ghi"] > 0.5

    # 条件 5: PV 出力接近零
    cond_pv_low = df["pv_power_kw"] < PV_NEAR_ZERO_THRESHOLD

    # 合并掩码
    anomaly_mask = (
        cond_daytime & cond_clearsky_high & cond_ghi_high & cond_csi & cond_pv_low
    )

    anomalies = df[anomaly_mask].copy()
    n_total = len(df)
    n_anomalies = len(anomalies)

    # Daytime 总行数（solar_elevation > 10），用于计算 daytimne 比率
    n_daytime = cond_daytime.sum()
    daytime_pct = (n_anomalies / n_daytime * 100) if n_daytime > 0 else 0.0

    print(f"\n[ANOMALY DETECTION]")
    print(f"  Total rows:                     {n_total}")
    print(f"  Daytime rows (elev > 10):       {n_daytime}")
    print(f"  Anomaly samples found:          {n_anomalies}")
    print(f"  Anomaly % of total:             {n_anomalies / n_total * 100:.2f}%")
    print(f"  Anomaly % of daytime:           {daytime_pct:.2f}%")
    print(f"  PV near-zero threshold:         {PV_NEAR_ZERO_THRESHOLD:.4f} kW")
    print()

    # 分段输出各条件过滤数
    print("[FILTER STAGES]")
    for name, mask in [
        ("1. solar_elevation > 10", cond_daytime),
        ("2. clearsky_ghi_wm2 > 500", cond_clearsky_high),
        ("3. ghi_wm2 > 250", cond_ghi_high),
        ("4. clearsky_index_ghi > 0.5", cond_csi),
        ("5. pv_power_kw < 0.0224", cond_pv_low),
    ]:
        print(f"  {name}: {mask.sum()} rows")

    return anomalies, daytime_pct


def build_by_date_table(anomalies: pd.DataFrame) -> pd.DataFrame:
    """按天聚合异常样本（仅保留 anomaly_hours >= 3 的天）"""
    anomalies = anomalies.copy()

    # 注意：timestamp 是带时区的 datetime64[ns, UTC]
    anomalies["date"] = anomalies["timestamp"].dt.date

    group = anomalies.groupby("date").agg(
        anomaly_hours=("pv_power_kw", "count"),
        mean_ghi=("ghi_wm2", "mean"),
        mean_csi=("clearsky_index_ghi", "mean"),
        weather_fill_flag=(
            "weather_fill_flag",
            lambda x: x.value_counts().index[0] if len(x) > 0 else "N/A",
        ),
    )
    group = group[group["anomaly_hours"] >= 3].sort_values("anomaly_hours", ascending=False)
    group["mean_ghi"] = group["mean_ghi"].round(1)
    group["mean_csi"] = group["mean_csi"].round(3)
    return group.reset_index()


def build_weather_flag_crosstab(anomalies: pd.DataFrame, total_anomalies: int) -> pd.DataFrame:
    """weather_fill_flag 交叉表"""
    counts = anomalies["weather_fill_flag"].value_counts().sort_index()
    tab = pd.DataFrame(
        {
            "weather_fill_flag": counts.index.astype(int),
            "anomaly_count": counts.values,
            "pct_of_anomalies": (counts.values / total_anomalies * 100).round(1),
        }
    )
    return tab


def build_cloud_type_crosstab(anomalies: pd.DataFrame) -> pd.DataFrame:
    """cloud_type 交叉表（含可读标签）"""
    counts = anomalies["cloud_type"].value_counts().sort_index()
    tab = pd.DataFrame(
        {
            "cloud_type": counts.index.astype(int),
            "label": [CLOUD_TYPE_LABELS.get(int(k), f"Unknown ({k})") for k in counts.index],
            "anomaly_count": counts.values,
        }
    )
    return tab


def generate_recommendation(anomalies: pd.DataFrame, total_rows: int) -> str:
    """根据诊断结果生成推荐操作"""
    n = len(anomalies)
    pct = n / total_rows * 100

    # 检查 weather_fill_flag 的分布
    fill_counts = anomalies["weather_fill_flag"].value_counts()
    fill0_count = fill_counts.get(0.0, 0)
    fill_nonzero_count = n - fill0_count
    fill_dominated = fill_nonzero_count > 0.5 * n  # > 50% 有非零 fill_flag

    # 检查日期集中度
    anomalies_by_date = anomalies.groupby(anomalies["timestamp"].dt.date).size()
    concentrated_dates = anomalies_by_date[anomalies_by_date >= 6]  # 一天 >=6 小时异常
    has_concentrated = len(concentrated_dates) > 0

    lines = ["## Recommendation\n"]

    if fill_dominated:
        lines.append(
            f"- **weather_fill_flag != 0 占 {fill_nonzero_count / n * 100:.1f}%**: "
            "NSRDB 填充数据是主导因素。这些样本对应的原始 NSRDB 数据缺失，"
            "填充的 GHI/clearsky_ghi 可能不准确，导致看起来辐照高但 PV 低。"
        )
        lines.append("- **建议**: 在训练时增加 `weather_fill_flag != 0` 特征或将该标志位作为过滤条件。")

    if has_concentrated:
        top_date = concentrated_dates.index[0]
        top_count = concentrated_dates.iloc[0]
        lines.append(
            f"- **日期集中度**: 发现 {len(concentrated_dates)} 天出现 >=6 小时连续异常，"
            f"其中 {top_date} 出现 {top_count} 小时。可能原因：设备停机维护或传感器故障。"
        )
        lines.append("- **建议**: 核查这些日期的 PV 站点运维日志，必要时从训练集中排除。")

    if pct < 0.5:
        lines.append(
            f"- **总体占比低 ({pct:.2f}%)**: 异常样本比例较低，不需要特殊处理，"
            "模型会自动学习忽略这些稀疏噪声。"
        )
    elif pct < 3:
        lines.append(
            f"- **总体占比中等 ({pct:.2f}%)**: 建议增加一个二值特征 `is_daytime_low_power_anomaly` "
            "让模型可以显式学习该模式。"
        )
    else:
        lines.append(
            f"- **总体占比较高 ({pct:.2f}%)**: 需要深入排查数据链路问题（可能是 PV 功率采集异常或"
            " NSRDB 辐照数据系统性偏差）。"
        )

    # 如果没有明显模式
    if not fill_dominated and not has_concentrated and pct < 1:
        lines.append(
            "\n- **综合判断**: 异常样本散布在多个日期，无明显集中模式，且比例极低。"
            "建议暂不处理，继续监控。"
        )

    return "\n".join(lines)


def save_csv(anomalies: pd.DataFrame, path: Path) -> None:
    """保存异常样本 CSV"""
    columns = [
        "timestamp", "pv_power_kw", "ghi_wm2", "clearsky_ghi_wm2",
        "clearsky_index_ghi", "solar_elevation", "cloud_type",
        "weather_fill_flag", "temperature_c", "relative_humidity_pct",
        "wind_speed_ms",
    ]
    # 仅保留存在的列（cloud_cover_pct 可能在数据集中不存在）
    available = [c for c in columns if c in anomalies.columns]
    if "solar_elevation" not in anomalies.columns:
        # 如果还没计算则跳过 solar_elevation
        available = [c for c in available if c != "solar_elevation"]

    out = anomalies[available].copy()
    out = out.sort_values("timestamp")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    print(f"[OUTPUT] Saved anomaly samples: {path} ({len(out)} rows)")


def save_md_report(
    df: pd.DataFrame,
    anomalies: pd.DataFrame,
    by_date: pd.DataFrame,
    fill_tab: pd.DataFrame,
    cloud_tab: pd.DataFrame,
    daytime_pct: float,
    path: Path,
) -> None:
    """生成并保存 Markdown 诊断报告"""
    n_total = len(df)
    n_anomalies = len(anomalies)
    n_daytime = (df["solar_elevation"] > 10).sum()

    lines = [
        "# Anomaly Diagnosis Report\n",
        "## Summary\n",
        f"- Total anomaly samples: {n_anomalies} / {n_total} total rows ({n_anomalies / n_total * 100:.2f}%)",
        f"- Anomaly hours as % of daytime hours: {daytime_pct:.2f}%",
        "",
        "### Condition thresholds applied",
        f"| Condition | Threshold | Passed rows |",
        "|-----------|-----------|-------------|",
        f"| solar_elevation > 10 | > 10 deg | {(df['solar_elevation'] > 10).sum()} |",
        f"| clearsky_ghi_wm2 > 500 | > 500 W/m2 | {(df['clearsky_ghi_wm2'] > 500).sum()} |",
        f"| ghi_wm2 > 250 | > 250 W/m2 | {(df['ghi_wm2'] > 250).sum()} |",
        f"| clearsky_index_ghi > 0.5 | > 0.5 | {(df['clearsky_index_ghi'] > 0.5).sum()} |",
        f"| pv_power_kw < {PV_NEAR_ZERO_THRESHOLD:.4f} | < {PV_NEAR_ZERO_THRESHOLD:.4f} kW | {(df['pv_power_kw'] < PV_NEAR_ZERO_THRESHOLD).sum()} |",
        "",
        f"### Anomaly sample GHI stats",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Mean GHI | {anomalies['ghi_wm2'].mean():.1f} W/m2 |",
        f"| Mean clearsky_ghi | {anomalies['clearsky_ghi_wm2'].mean():.1f} W/m2 |",
        f"| Mean CSI | {anomalies['clearsky_index_ghi'].mean():.3f} |",
        f"| Mean solar_elevation | {anomalies['solar_elevation'].mean():.1f} deg |",
        f"| Mean pv_power | {anomalies['pv_power_kw'].mean():.4f} kW |",
        "",
    ]

    # By Date 表格
    if len(by_date) > 0:
        lines.extend([
            "## By Date (days with >= 3 anomaly hours)\n",
            "| Date | Anomaly Hours | Mean GHI | Mean CSI | weather_fill_flag |",
            "|------|---------------|----------|----------|-------------------|",
        ])
        for _, row in by_date.iterrows():
            lines.append(
                f"| {row['date']} | {row['anomaly_hours']} | "
                f"{row['mean_ghi']:.1f} | {row['mean_csi']:.3f} | {row['weather_fill_flag']} |"
            )
        lines.append("")
    else:
        lines.append("## By Date\n\nNo days with >= 3 anomaly hours found.\n")

    # weather_fill_flag 交叉表
    lines.extend([
        "## Cross-tab with weather_fill_flag\n",
        "| weather_fill_flag | Anomaly Count | % of Anomalies |",
        "|-------------------|---------------|----------------|",
    ])
    for _, row in fill_tab.iterrows():
        lines.append(
            f"| {int(row['weather_fill_flag'])} | {row['anomaly_count']} | {row['pct_of_anomalies']}% |"
        )
    lines.append("")

    # cloud_type 交叉表
    lines.extend([
        "## Cross-tab with cloud_type\n",
        "| cloud_type | Label | Anomaly Count |",
        "|------------|-------|---------------|",
    ])
    for _, row in cloud_tab.iterrows():
        lines.append(
            f"| {int(row['cloud_type'])} | {row['label']} | {row['anomaly_count']} |"
        )
    lines.append("")

    # 推荐操作
    lines.append(generate_recommendation(anomalies, n_total))
    lines.append("")

    # 写入
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    print(f"[OUTPUT] Saved diagnosis report: {path}")


def main():
    print("=" * 60)
    print("Daytime Low-Power Anomaly Diagnosis")
    print("=" * 60)

    # 1. 加载数据
    df = load_data(DATA_PATH)

    # 2. 计算太阳高度角
    print("\n[INFO] Computing solar elevation (using solar_zenith_angle_deg proxy)...")
    df["solar_elevation"] = compute_solar_elevation(df)

    # 3. 检出异常样本
    anomalies, daytime_pct = find_anomalies(df)

    if len(anomalies) == 0:
        print("[RESULT] No anomaly samples found. The dataset appears clean.")
        # 仍然输出空报告
        empty_md = (
            "# Anomaly Diagnosis Report\n\n"
            "## Summary\n\n"
            f"- Total anomaly samples: 0 / {len(df)} total rows (0.0%)\n"
            "- No samples met all 5 anomaly conditions.\n\n"
            "## Recommendation\n\n"
            "- The dataset is clean with respect to daytime low-power anomalies.\n"
        )
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        MD_PATH.write_text(empty_md, encoding="utf-8")
        print(f"[OUTPUT] Saved empty diagnosis report: {MD_PATH}")
        return

    # 4. 构建聚合表格
    by_date = build_by_date_table(anomalies)
    fill_tab = build_weather_flag_crosstab(anomalies, len(anomalies))
    cloud_tab = build_cloud_type_crosstab(anomalies)

    # 5. 打印详细诊断
    print("\n[DIAGNOSIS SUMMARY]")
    print(f"  Anomaly samples:   {len(anomalies)}")
    print(f"  Mean GHI:          {anomalies['ghi_wm2'].mean():.1f} W/m2")
    print(f"  Mean clearsky_ghi: {anomalies['clearsky_ghi_wm2'].mean():.1f} W/m2")
    print(f"  Mean CSI:          {anomalies['clearsky_index_ghi'].mean():.3f}")
    print(f"  Mean pv_power:     {anomalies['pv_power_kw'].mean():.4f} kW")
    print(f"  weather_fill_flag != 0: {(anomalies['weather_fill_flag'] != 0).sum()} / {len(anomalies)} ({(anomalies['weather_fill_flag'] != 0).mean() * 100:.1f}%)")
    print(f"  Days w/ >=3 anomaly hours: {len(by_date)}")

    if len(by_date) > 0:
        print("\n  Top dates by anomaly hours:")
        for _, row in by_date.head(5).iterrows():
            print(f"    {row['date']}: {row['anomaly_hours']} hrs, GHI={row['mean_ghi']}, fill_flag={row['weather_fill_flag']}")

    # 6. 保存输出文件
    save_csv(anomalies, CSV_PATH)
    save_md_report(df, anomalies, by_date, fill_tab, cloud_tab, daytime_pct, MD_PATH)

    print("\n[DONE]")


if __name__ == "__main__":
    main()
