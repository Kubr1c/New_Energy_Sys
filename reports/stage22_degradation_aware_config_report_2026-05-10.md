# Stage22 — 储能退化成本约束下的配置优化报告

**日期**：2026-05-10
**阶段**：Stage22
**数据**：Rawhide Prairie Solar 参数参照仿真（22 MW PV + 2 MWh BESS，OPSD 丹麦代理电价）
**样本量**：25,358 小时（~2.9 年）

---

## 1. 背景

Stage15 推荐的 `cap1p5_pow1p5_obj1` 基于毛增量收益 Pareto 前沿排序，但 Stage17 rainflow 退化核算显示其退化后净增量收益为 **-19,990 EUR**（毛收益 1,743 EUR）。问题根源：Stage12 调度目标中的 `cycle_cost_eur_per_kwh`（0.002 EUR/kWh）是简单吞吐量代理，远低于 rainflow 模型计算的真实退化成本（~0.015-0.020 EUR/kWh），导致过度循环。

Stage22 目标：将调度代理惩罚 + 后验退化核算结合，扫描五维配置空间，筛选退化后净增量为正的储能方案。

---

## 2. 方法

### 2.1 两段式评估

每个配置运行两次：

1. **Stage12 滚动调度**：含 λ 加权的退化代理项（λ × 15 EUR/MWh → 最低套利价差门槛）和外部价差门禁
2. **Stage17 rainflow 退化核算**：对调度 SOC 轨迹做雨流循环计数 → 计算循环损伤 + 日历老化 → 真实净增量收益

### 2.2 扫描维度

| 维度 | 取值 | 说明 |
|------|------|------|
| 容量倍率 | 1.0, 1.25, 1.5 | 相对基准 2 MWh |
| 功率倍率 | 0.5, 0.75, 1.0 | 相对基准 1 MW |
| SOC 区间 | [0.1-0.9], [0.2-0.8], [0.25-0.75], [0.3-0.7] | 窄区间限制深循环 |
| 退化惩罚 λ | 0, 1.0, 2.0, 5.0 | λ × 15 EUR/MWh = 价差门槛 |
| 最小价差 | 0 EUR/MWh | 外部门禁（与 λ 叠加） |

总计 52 个配置完成扫描。

### 2.3 λ 校准

λ 不再直接乘 `cycle_cost_eur_per_kwh`（量级太小，λ=1 仅产生 2 EUR/MWh 门槛），而是映射到有意义的价差阈值：

```
λ=0   → 门槛  0 EUR/MWh  （内部仅 shortfall_penalty ≈ 1 EUR/MWh）
λ=1.0 → 门槛 15 EUR/MWh  （接近 rainflow 等效退化成本 ~0.015 EUR/kWh）
λ=2.0 → 门槛 30 EUR/MWh
λ=5.0 → 门槛 75 EUR/MWh
```

参考值基于：`replacement_cost × (1-soh_eol) / cycles_at_moderate_dod × 1000 ≈ 150 × 0.2 / 5000 × 1000 ≈ 6 EUR/MWh`，上修至 15 EUR/MWh 以覆盖日历老化分摊和浅循环损伤。

### 2.4 过滤标准

- `net_incremental_revenue_eur > 0`
- `soh_end >= 0.90`
- 全部物理约束通过（SOC 边界、功率限制、充放电互斥、能量守恒）

---

## 3. 结果

### 3.1 所有配置均未通过正净收益过滤

| 过滤条件 | 通过数 | 总数 |
|----------|--------|------|
| 物理约束通过 | 52 | 52 |
| SOH ≥ 0.90 | 52 | 52 |
| net_incremental > 0 | **0** | 52 |

### 3.2 λ 效应：存在临界阈值

λ=2.0 (30 EUR/MWh) 时，OPSD 日内价差扣除 10% 往返效率损耗后极少超过此门槛，调度器**完全停止充放电**。λ=2.0 与 λ=5.0 结果完全一致。

以 `cap1.0 / pow0.5 / soc0.2-0.8` 为例：

| 指标 | λ=0 | λ=1.0 | λ≥2.0 |
|------|-----|-------|-------|
| 毛增量收益 (EUR) | 8,559 | 1,998 | **0** |
| 退化成本 (EUR) | 30,629 | 17,444 | **12,970** |
| **净增量收益 (EUR)** | **-22,071** | **-15,446** | **-12,970** |
| 期末 SOH | 0.898 | 0.942 | **0.957** |
| 等效完整循环 (EFC) | 680 | 168 | **0** |
| 最大 DoD | 0.80 | 0.60 | — |
| 总充电量 (MWh) | 1,604 | 442 | 0 |

λ=1.0 相比 λ=0：
- 循环削减 **75%**
- SOH 多保留 **4.4 个百分点**
- 净收益改善 **6,600 EUR**
- **但仍然是负的**

### 3.3 不可逾越的地板：纯日历老化

λ≥2.0 时所有配置结果一致：

```
毛收益 = 0 EUR（无任何充放电动作）
退化成本 = 12,970 EUR（cap=1.0）/ 16,212 EUR（cap=1.25）
净收益 = -12,970 EUR / -16,212 EUR
SOH = 0.9568（日历老化消耗 4.32%）
```

**12,970 EUR 是策略地板的来源**：

```
2,000 kWh × 150 EUR/kWh × (1 - 0.9568) = 12,970 EUR
```

日历老化模型：
```
基础老化 = 1.5%/年 × 2.9 年 = 4.35%
SOC 应力因子 ≈ 1.0（SOC 保持在 50% 附近）
温度应力 = 1.0（无温度数据）
总日历老化 ≈ 4.32% SOH
```

### 3.4 最优配置排名（按净收益降序）

| 排名 | config_id | cap | pow | SOC | λ | gross | deg_cost | **net** | SOH | EFC |
|------|-----------|-----|-----|-----|---|-------|----------|---------|-----|-----|
| 1 | cap1p0_pow0p5_soc0p2_0p8_lambda2p0 | 1.0 | 0.5 | 0.2-0.8 | 2.0 | 0 | 12,970 | **-12,970** | 0.957 | 0 |
| 2 | cap1p0_pow0p5_soc0p25_0p75_lambda1p0 | 1.0 | 0.5 | 0.25-0.75 | 1.0 | 1,920 | 16,305 | **-14,385** | 0.946 | 124 |
| 3 | cap1p0_pow0p75_soc0p1_0p9_lambda1p0 | 1.0 | 0.75 | 0.1-0.9 | 1.0 | 3,017 | 17,268 | **-14,252** | 0.942 | 171 |
| 4 | cap1p0_pow0p5_soc0p3_0p7_lambda1p0 | 1.0 | 0.5 | 0.3-0.7 | 1.0 | 1,520 | 16,239 | **-14,719** | 0.946 | 112 |
| 5 | cap1p0_pow0p5_soc0p2_0p8_lambda1p0 | 1.0 | 0.5 | 0.2-0.8 | 1.0 | 1,998 | 17,444 | **-15,446** | 0.942 | 168 |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| 最劣 | cap1p5_pow1p0_soc0p1_0p9_lambda0p0 | 1.5 | 1.0 | 0.1-0.9 | 0 | 10,363 | 46,730 | **-36,367** | 0.896 | 693 |

**最劣 vs 最优：净收益差距 23,397 EUR**，配置选择影响巨大。

### 3.5 配置规律

- **小容量优于大容量**：cap=1.0 → 绝对退化成本更低，但每 kWh 退化成本相近
- **低功率优于高功率**：pow=0.5 → 限制了 C-rate，减少深循环
- **SOC 区间影响有限**：窄区间（0.25-0.75）在 λ=1.0 下比宽区间（0.1-0.9）净收益好 ~500-1,500 EUR
- **λ 是最强杠杆**：λ=0 → λ=1.0 改善净收益 7,000-14,000 EUR；λ=1.0 → λ=2.0 再改善 2,000-3,000 EUR（但完全停止循环）

---

## 4. 讨论

### 4.1 为什么套利无法覆盖退化成本

```
收入侧：OPSD 日内价差中位数约 15-20 EUR/MWh，扣除 η²=0.9025 往返效率后有效价差约 13.5-18 EUR/MWh
成本侧：等效退化成本约 0.015-0.020 EUR/kWh → 15-20 EUR/MWh 吞吐量

收入 ≈ 成本，边际利润极薄。
```

加上日历老化的固定成本（~13,000 EUR），任何配置都无法实现正净收益。

### 4.2 当前最优策略的经济含义

**零循环 + 仅承担日历老化** = 电池作为纯粹的保险资产存在，不通过套利回收成本。这在以下场景中可能合理：
- 电池服务于可再生消纳/弃光削减（收益体现在电量侧，不在电价套利）
- 电池提供容量备用/调频（收益未在当前模型体现）
- 电池成本由政策补贴覆盖

### 4.3 实现正净收益的条件

在当前模型框架下，至少需要满足以下条件之一：

| 条件 | 当前值 | 目标值 | 可行性 |
|------|--------|--------|--------|
| 重置成本 | 150 EUR/kWh | ≤ 50 EUR/kWh | 钠离子/铁空气电池路线图目标 |
| 日内价差 | ~15-20 EUR/MWh | ≥ 40 EUR/MWh | 高可再生能源渗透市场常见 |
| 循环寿命 (中DOD) | ~5,000 次 | ≥ 15,000 次 | LFP 电池已接近 |
| 多重收益叠加 | 仅套利 | +调频+容量 | 需要市场机制支持 |

### 4.4 局限性

- **代理电价**：OPSD 丹麦日前电价不代表 Rawhide/科罗拉多当地市场结算价格，实际价差可能更高或更低
- **PV 估算**：使用 PVDAQ 原型曲线按容量比缩放，非 Rawhide 实测发电数据
- **退化模型**：Wohler 曲线为通用 LFP 参考值，非特定电池型号实测
- **日历老化简化**：无温度数据，SOC 应力模型为线性近似
- **未建模收益**：弃光削减价值、调频辅助服务、容量市场支付

---

## 5. 结论

1. **Rawhide 参数参照仿真在当前假设下，储能套利不具备正净收益条件。** 52 个配置全部未通过 `net_incremental_revenue_eur > 0` 过滤。

2. **λ ≥ 2.0 (30 EUR/MWh 价差门槛) 导致零循环。** 在当前 OPSD 价格数据中，超过此门槛的有效套利机会极少。

3. **最优策略为零循环，仅承担日历老化（net = -12,970 EUR）。** 这是配置优化的下界——日历老化无法消除。

4. **λ=1.0 (15 EUR/MWh) 是循环削减与收益保留的最佳平衡点。** 循环削减 60-75%，净收益改善 7,000-14,000 EUR，但边际循环仍不盈利。

5. **配置选择对结果影响显著。** 最优（-12,970 EUR）与最劣（-36,367 EUR）配置差距达 23,397 EUR。

---

## 附录

### A. 运行命令

```bash
# 16 配置基线
python -m new_energy_sys.cli.run_stage22_degradation_aware_config \
  --config configs/data_sources.rawhide_prairie_scaled_2020_2022.json \
  --predictions data/processed/rawhide_scaled/stage9_rawnhide_scaled_predictions.csv \
  --feature-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --capacity-multipliers 1.0,1.5 --power-multipliers 0.75,1.0 \
  --soc-ranges 0.1-0.9,0.2-0.8 --lambda-values 0,1.0 --min-spreads 0

# 36 配置高 λ 扫描
python -m new_energy_sys.cli.run_stage22_degradation_aware_config \
  --config configs/data_sources.rawhide_prairie_scaled_2020_2022.json \
  --predictions data/processed/rawhide_scaled/stage9_rawnhide_scaled_predictions.csv \
  --feature-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --capacity-multipliers 1.0,1.25 --power-multipliers 0.5,0.75 \
  --soc-ranges 0.2-0.8,0.25-0.75,0.3-0.7 --lambda-values 1.0,2.0,5.0 --min-spreads 0 \
  --output-prefix stage22_rawnhide_high_lambda
```

### B. 新增文件

| 文件 | 说明 |
|------|------|
| `src/new_energy_sys/stage22_degradation_aware_config.py` | Stage22 核心模块（网格构建、两段式仿真、编排器、报告生成） |
| `src/new_energy_sys/cli/run_stage22_degradation_aware_config.py` | CLI 入口 |
| `pyproject.toml` | 注册 `new-energy-run-stage22-degradation-aware-config` 命令 |

### C. 修改文件

| 文件 | 改动 |
|------|------|
| `src/new_energy_sys/stage12_storage_rolling.py` | `_optimize_first_action_fast()` 和 `_simulate_fast_rolling_optimization()` 新增 `min_spread_eur_mwh` 可选参数（None 时退化至现有行为） |
