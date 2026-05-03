# HRRR 预报驱动的光伏功率预测链路——设计规格

**日期**: 2026-05-03  
**状态**: 已确认  
**关联**: GHI 辐射分解模块 (Erbs/DISC, commit 6eb8c8d)

## 1. 背景与目标

### 1.1 现状

- 已部署模型：LightGBM `history_only`（51 特征），t+24h 测试集 **nRMSE=0.1225**（白天 0.1689）
- 可达上限：LightGBM `full_features`（163 特征，含 NSRDB oracle 天气），**nRMSE=0.0784**
- 36% 的性能差距来自"天气特征是用 NSRDB 实际观测值平移得到的 oracle，而非真预报"
- HRRR Stage7 数据已就绪（2021-2022, F24-F44），GHI→DNI+DHI 分解（DISC 模型）已完成

### 1.2 目标

用 HRRR 真实预报替换 NSRDB oracle 天气特征，打通"预报→特征→预测→评估"完整闭环，量化**"真预报替代 oracle 后精度降多少"**。

### 1.3 非目标

- 不换模型架构（保持 LightGBM）
- 不训练 TFT 或其他深度学习模型
- 不做多站点泛化
- 不做在线推理部署

## 2. 数据流

```
HRRR Stage7 parquet (F24-F44, 17,505 行)
       │
       ▼
[GHI 分解]  DISC → DNI, DHI (已有)
       │
       ▼
[多时次聚合]  对每个目标时间戳 T:
              1. 收集 issue_time ∈ [T-48h, T-24h] 的所有 HRRR 预报
              2. 按 lead_time 反距离加权平均(权重 ∝ 1/lead_time)
              3. 输出: GHI, DNI, DHI, T, cloud_cover (5 变量均值)
       │
       ▼
[特征工程]  + 24h rolling mean/std
            + clearsky_index = GHI / clearsky_ghi(zenith)
            + DHI/GHI ratio
            + lead_time_hour (Step 2b)
       │
       ▼
[训练/评估] LightGBM, 与 history_only / NSRDB oracle 对比
```

### 2.1 多时次聚合规则

- **过滤窗口**: `issue_time ∈ [T-48h, T-24h]`，确保是"日前"预报
- **权重重置**: 若某时次 lead_time < 24h，则排除（非日前）
- **缺值处理**: 若某目标时间无任何 HRRR 覆盖，该行标记为 `weather_missing=True`，模型回退到 history_only 特征
- **聚合方式**: 加权平均，权重 ∝ 1/lead_time（lead 越短越准）

## 3. 特征方案（分步）

### Step 1: 精简 5 变量（~75 特征）

| 来源 | 特征组 | 数量 | 示例 |
|------|--------|------|------|
| 时间编码 | time_features | 12 | sin/cos hour, day_of_week, month |
| PV 历史 | historical_power_features | 38 | lags 1/2/3/6/12/24/48/168h, rolling mean/std |
| **HRRR 天气** | **hrrr_weather** | **5** | ghi, dni, dhi, temperature, cloud_cover |
| HRRR 天气 rolling | hrrr_weather_roll | **10** | 24h rolling mean/std of each |
| HRRR 派生 | hrrr_derived | **3** | clearsky_index, dhi_ghi_ratio, kt |
| 标记 | weather_valid_flag | 1 | HRRR 覆盖标志 |

**总计: ~69 特征**

### Step 2a: +湿度 +风速（+7 特征）

新增 `relative_humidity`, `wind_speed` 及其 rolling stats（4 个），派生 `temp_dew_spread`（1 个），`wind_dir_sin/cos`（2 个）。

### Step 2b: +lead_time（+1 特征）

加权平均后的有效 lead_time_hour 作为单特征，让模型学习预报时效置信度。

## 4. 模型与评估

### 4.1 模型

- LightGBM，超参复用现有 tuned 配置: `n_estimators=1800, lr=0.02, max_depth=10`
- 时间序列分割: 70%/15%/15%（与现有一致）

### 4.2 三条基线对比

| 基线 | 特征 | nRMSE (全/白天) | 含义 |
|------|------|-----------------|------|
| A. history_only | 51 (time+PV) | 0.1225 / 0.1689 | 无天气信息下限 |
| B. NSRDB oracle | 163 (含真实天气) | 0.0784 / 0.0903 | 完美预报上限 |
| **C. HRRR forecast** | **~75 (含HRRR预报)** | **待测** | **真预报可达精度** |

### 4.3 成功标准

- 链路完整性: HRRR→特征→预测→评估跑通，无报错
- 精度提升: HRRR forecast nRMSE < history_only nRMSE (0.1225)
- 闭合分析: 量化"HRRR 预报误差→PV 预测误差"的传递关系

## 5. 文件规划

| 文件 | 用途 |
|------|------|
| `src/new_energy_sys/hrrr_feature_aligner.py` | 多时次聚合逻辑 |
| `src/new_energy_sys/cli/train_hrrr_pv.py` | 训练+评估脚本 |
| `tests/test_hrrr_feature_aligner.py` | 聚合逻辑测试 |
| `scripts/compare_hrrr_pv_baselines.py` | 三基线对比报告 |

## 6. 风险与对策

| 风险 | 概率 | 对策 |
|------|------|------|
| HRRR 覆盖不全(缺 2020 年) | 高 | 2020 年用 history_only 特征，统计算时标记 |
| 多时次聚合引入数据泄露 | 中 | 严格按 `issue_time < T-24h` 过滤，加单元测试 |
| HRRR 预报质量不足导致 nRMSE 反而不及 history_only | 中 | 接受此结果，量化预报误差传递，为后续换模型打基础 |
| 精简 5 变量不够 | 低 | Step 2a/2b 补湿度风速 |

## 7. 依赖

- `irradiance_decomposition.py` (DISC 分解，已有)
- `stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet` (已有)
- `stage2_cleaned_hourly_dataset.parquet` (PV 真值，已有)
- `modeling.py` (LightGBM 训练器，已有)
