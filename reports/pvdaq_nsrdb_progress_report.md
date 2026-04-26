# PVDAQ + NSRDB 路线进度与质量评估报告

生成时间：2026-04-24

## 1. 当前结论

`PVDAQ system 10 + NSRDB PSM` 路线已经完成 Stage1 至 Stage4 的闭环验证。数据获取问题已解决，NSRDB-only 链路不再依赖 Open-Meteo 兜底。

当前数据可以支撑下一阶段的误差诊断、消融实验和 TCN/TFT 对比建模。但该路线使用的是 NSRDB 历史/观测型太阳资源数据，不是严格的 forecast-cycle 天气预报数据，因此不能把它表述为“预测时刻真实可获得的天气预报”。

Pitfall：NSRDB 提升了天气数据可信度，但没有解决 forecast weather 严格性；若论文强调真实预测部署，需要单独保留 HRRR 或 forecast API 作为严格天气验证实验。

## 2. 阶段推进状态

| 阶段 | 目标 | 当前结果 | 完成判断 |
|---|---|---:|---|
| Stage1 数据接入 | 接入 PVDAQ 实测功率、NSRDB 气象、调度辅助数据 | 10033 行 | 完成 |
| Stage2 数据清洗 | 缺失值、异常值、时间对齐、重采样、标准化 | 10033 行，覆盖率 98.59% | 完成 |
| Stage3 特征工程 | 时间、天气、历史功率、调度特征 | 9841 行，94 个派生特征 | 完成 |
| Stage4 基线建模 | LightGBM 首个可用版本 | 9 个模型训练完成 | 完成 |

![Stage row counts](figures/pvdaq_nsrdb_progress/stage_row_counts.png)

## 3. 数据结构与表头说明

当前主数据表分三层：

| 数据表 | 行数 | 列数 | 用途 |
|---|---:|---:|---|
| `hourly_training_with_storage.parquet` | 10033 | 28 | Stage1 拼接后的小时级训练底表 |
| `stage2_cleaned_hourly_dataset.parquet` | 10033 | 28 | Stage2 清洗后数据，无缺失、无重复、时间单调 |
| `stage3_feature_dataset.parquet` | 9841 | 125 | Stage3 模型输入表，包含特征和未来标签 |

| 表头/字段组 | 类型 | 含义 |
|---|---|---|
| `timestamp` | datetime UTC | 小时级时间戳；所有 PV、NSRDB、调度特征按该字段对齐。 |
| `pv_power_kw` | float | PVDAQ system 10 实测交流功率，单位 kW，核心预测对象。 |
| `ghi_wm2 / dni_wm2 / dhi_wm2` | float | NSRDB 水平总辐照、直射辐照、散射辐照，单位 W/m2。 |
| `clearsky_*_wm2` | float | NSRDB 晴空辐照基准，用于构造 clear-sky index。 |
| `temperature_c / dew_point_c` | float | 环境温度与露点温度，单位摄氏度。 |
| `relative_humidity_pct` | float | 相对湿度，单位百分比。 |
| `pressure_hpa` | float | 地表气压，单位 hPa；高海拔站点数值低于海平面正常。 |
| `wind_speed_ms / wind_direction_deg` | float | 风速与风向，用于刻画组件散热和天气状态。 |
| `solar_zenith_angle_deg` | float | 太阳天顶角，反映太阳高度。 |
| `surface_albedo / cloud_type / weather_fill_flag` | float | NSRDB 地表反照率、云型编码、数据填补标记。 |
| `load_mw / price_eur_mwh` | float | OPSD 画像映射的负荷和电价，不是真实同区域同刻市场数据。 |
| `storage_*` | float/int | 规则储能仿真状态、充放电功率、SOC、可用容量和收益。 |
| `*_normalized` | float | Stage3 标准化天气特征，便于模型吸收不同量纲变量。 |
| `*_roll_24h_mean` | float | 滞后 1 小时后的 24 小时滚动统计，避免时间泄漏。 |
| `pv_power_lag_* / pv_power_roll_*` | float | 历史功率滞后和滚动特征，是短期预测的主要信息源。 |
| `target_pv_power_t_plus_*h` | float | 监督学习标签：未来 1、6、24 小时 PV 功率。 |

## 4. 数据质量

| 指标 | 当前值 | 判断 |
|---|---:|---|
| 配置期望小时数 | 10176 | 覆盖 2022-01-01 至 2023-02-28 |
| 观测目标小时数 | 10033 | 可用 |
| 目标小时覆盖率 | 98.59% | 达标 |
| Stage2 缺失值 | 0 | 达标 |
| 重复时间戳 | 0 | 达标 |
| Stage3 删除行数 | 192 | 合理，来自滞后窗口和未来标签 |
| Stage3 质量门禁 | 全部通过 | 达标 |

此前 Stage3 曾因 `clearsky_index_ghi = ghi / clearsky_ghi` 在夜间产生 `0/0`，误删大量夜间样本。现在已修正：夜间 clear-sky index 置为 `0`，样本数恢复到 9841 行。

Pitfall：`pressure_hpa` 在报告中出现异常计数，主要因为站点位于高海拔地区，地表气压低于海平面常见范围；这不应直接解释为错误数据。

## 5. 可视化说明

### 5.1 PV 功率与 NSRDB GHI

![PV and GHI week](figures/pvdaq_nsrdb_progress/pv_ghi_week_profile.png)

PV 输出与 GHI 日周期一致，说明 NSRDB 辐照数据与 PVDAQ 功率在时间轴上对齐合理。

### 5.2 PV 功率与辐照关系

![PV vs GHI](figures/pvdaq_nsrdb_progress/pv_vs_ghi_scatter.png)

散点图显示 PV 功率随 GHI 增加而上升，但存在明显离散，原因包括云型、太阳高度角、组件状态、逆变器限幅和站点实测噪声。

### 5.3 天气变量分布

![Weather distributions](figures/pvdaq_nsrdb_progress/weather_distributions.png)

NSRDB 提供的辐照、温度、湿度和风速分布完整，没有缺失填补造成的断裂形态。

### 5.4 特征组规模

![Feature group counts](figures/pvdaq_nsrdb_progress/feature_group_counts.png)

历史功率特征仍是当前模型的最大特征组，天气特征规模足以支持后续消融实验。

### 5.5 Stage4 指标对比

![Stage4 comparison](figures/pvdaq_nsrdb_progress/stage4_nrmse_comparison.png)

## 6. Stage4 测试结果

| 预测目标 | MAE kW | RMSE kW | nRMSE | 日间 nRMSE |
|---|---:|---:|---:|---:|
| t+1h | 0.0368 | 0.0764 | 6.82% | 11.21% |
| t+6h | 0.0738 | 0.1470 | 13.12% | 20.25% |
| t+24h | 0.0925 | 0.1701 | 15.19% | 22.48% |

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
