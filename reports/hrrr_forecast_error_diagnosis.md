# HRRR 预报误差诊断报告

**生成时间**: 2026-05-04 14:53

- HRRR 数据: `stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet` (17505 行)
- NSRDB 数据: `stage2_cleaned_hourly_dataset.parquet`
- 匹配行数: 17478 (99.8%)

---

## 1. 时间对齐验证

- HRRR 内部一致性: **全部通过** (timestamp = issue_time + lead_time)
- HRRR  x NSRDB 匹配率: **99.8%** (17478/17505)

---

## 2. 总体误差 (HRRR GHI vs NSRDB 观测)

| 时段 | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) | R2 | N |
|------|-----------|-------------|-------------|-----|----|
白天 (日间) | 115.6 | 165.2 | 29.6 | 0.653 | 8095
夜间 | 4.4 | - | - | - | 9383

---

## 3. Skill Score vs Baselines (日间)

| Baseline | MSE_baseline | MSE_HRRR | Skill_MSE | N |
|----------|-------------|----------|-----------|-----|
Clear-sky | 39807.6 | 27281.2 | 0.315 | 8095
Persistence | 153365.2 | 27281.2 | 0.822 | 8080

- **Skill > 0** = HRRR 优于该 baseline
- Clear-sky: 使用 NSRDB 理论晴空 GHI (`clearsky_ghi_wm2`)
- Persistence: GHI(t+h) = GHI(t=issue_time)

---

## 4. 按 Lead Time 分桶 (日间)

| Lead_Bin | N | Day_Frac | Mean_Elev ( deg) | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |
|----------|---|----------|---------------|------------|-------------|-------------|
24-29h | 8038 | 1.00 | 33.4 | 115.3 | 164.4 | 29.4
30-35h | 57 | 1.00 | 42.0 | 160.6 | 252.0 | 62.9
36-41h | 0 | 0.00 | N/A | N/A | N/A | N/A
42-44h | 0 | 0.00 | N/A | N/A | N/A | N/A

---

## 5. 按天气场景 (日间)

> **说明**: 天气场景基于 NSRDB 观测 CSI (clear-sky index) 对样本进行诊断分层，不作为可部署的预报特征使用。
> CSI = GHI / clearsky_GHI: Clear >= 0.7, Mixed 0.3~0.7, Overcast < 0.3

| Scenario | N | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |
|----------|---|------------|-------------|-------------|
clear | 5783 | 90.3 | 120.5 | -16.1
mixed | 1596 | 158.0 | 209.1 | 113.4
overcast | 716 | 225.8 | 305.9 | 212.3

---

## 6. 按 Valid Hour (日间)

| Hour (UTC) | N | MAE (W/m2) | RMSE (W/m2) | Bias (W/m2) |
|------------|---|------------|-------------|-------------|
 0 | 480 | 90.0 | 106.6 | 71.9
 1 | 296 | 62.9 | 73.0 | 56.7
13 | 288 | 88.8 | 96.8 | -79.2
14 | 482 | 100.6 | 113.3 | -83.1
15 | 728 | 97.1 | 117.6 | -59.1
16 | 727 | 103.8 | 135.3 | -36.8
17 | 727 | 112.1 | 163.0 | 3.4
18 | 727 | 121.6 | 199.6 | 40.6
19 | 728 | 134.5 | 224.1 | 57.9
20 | 728 | 133.5 | 199.1 | 73.3
21 | 728 | 146.5 | 202.7 | 91.1
22 | 728 | 142.4 | 184.7 | 97.7
23 | 728 | 108.0 | 134.3 | 76.9

---

## 7. 按 weather_fill_flag

> Primary metrics: weather_fill_flag=0 (原始观测) 样本为主。

| Flag | N | Daytime_MAE (W/m2) | Daytime_RMSE (W/m2) |
|------|---|---------------------|---------------------|
0 | 6559 | 118.2 | 169.2
4 | 189 | 134.5 | 184.6
11 | 228 | 115.8 | 156.5
14 | 153 | 104.7 | 133.1
100 | 111 | 53.3 | 64.9

---

## 8. HRRR GHI 误差 vs PV 预测误差相关分析

- 数据: `inspection_predictions.parquet`, experiment=stage5, horizon=24h
- 方法: Pearson 相关系数 corr(HRRR GHI error, PV error_kw)

| 分组 | Corr | p-value | N |
|------|------|---------|-----|
Overall (all hours) | 0.023 | 2.17e-03 | 17427
Daytime | 0.024 | 2.92e-02 | 8074
  clear | 0.008 | 5.60e-01 | 5776
  mixed | 0.020 | 4.37e-01 | 1583
  overcast | 0.023 | 5.46e-01 | 715

> **解释**: 正相关表示 HRRR 高估 GHI 时 PV 模型也倾向于高估功率，说明 PV 模型有承接 HRRR 输入误差的趋势。

---

## 9. 结论与决策

### 核心问题评估

1. **时间对齐**: 通过 V
2. **HRRR 技能**: 正向 Skill V (vs clear-sky: 0.315)
3. **PV 误差传导**: HRRR 误差与 PV 误差相关性弱 (r=0.024)
4. **场景退化**: Mixed RMSE (209.1) > Clear RMSE (120.5) - HRRR 在非晴空条件显著退化

### 决策矩阵

| 诊断结果 | 下一步 |
|---------|--------|
HRRR 有 skill 但 PV 没吃到 (Check 8 corr 低) | 改特征使用方式
HRRR 在 Mixed/Overcast 崩溃 | 分场景建模或概率区间

---

### 图表

![Lead-Time Boxplot](figures/hrrr_error_by_lead_time.png)
![Bias/RMSE Heatmaps](figures/hrrr_bias_rmse_by_scenario_hour.png)
![GHI Scatter](figures/hrrr_vs_nsrdb_scatter_by_scenario.png)