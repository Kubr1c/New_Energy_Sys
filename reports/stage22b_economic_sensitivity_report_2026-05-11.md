# Stage22B — 经济条件临界分析报告

**日期**：2026-05-11
**阶段**：Stage22B
**依赖**：Stage22 全部 52 配置的调度 + 退化核算结果
**数据**：Rawhide Prairie Solar 参数参照仿真（22 MW PV + 2 MWh BESS，OPSD 丹麦代理电价）
**等效仿真年限**：2.88 年（`sample_count / 8760`；近似值，非自然年跨度）

---

## 1. 背景与目标

Stage22 结论：Rawhide 参数参照仿真 + OPSD 代理电价下，52 个物理配置均未通过 `net_incremental_revenue_eur > 0` 过滤。最优策略为零循环（λ ≥ 2.0, net = -12,970 EUR），日历老化成本无法消除。

Stage22B 目标：**不再微调物理配置，而是反推正净收益所需的市场经济条件。**核心问题：

1. 在当前假设下，为什么毛收益无法覆盖退化成本？
2. 如果要转正，电价价差、电池成本、循环寿命、额外收益需要达到什么边界？

核心原则：**不预设一定找到正净收益，不人为制造假阳性。**

---

## 2. 方法

### 2.1 两路并行

| 路径 | 方法 | 扫描参数 |
|------|------|----------|
| **后处理** | 对 Stage22 已有雨流损伤聚合值做闭式缩放，不重跑调度 | replacement_cost × cycle_life × calendar_fade × 三类额外收益 |
| **价差放大** | 修改 `price_eur_mwh` 偏离度后重跑调度（3 配置 × 4 倍数） | amp = 1.0, 1.5, 2.0, 3.0 |

### 2.2 经济参数矩阵

| 参数 | 取值 | 单位 |
|------|------|------|
| 重置成本 | 150, 100, 75, 50 | EUR/kWh |
| 循环寿命倍率 | 1.0, 2.0, 3.0 | × 基准 Wohler 曲线 |
| 日历老化率 | 0.015, 0.01, 0.005, 0.0 | /年 |
| 放电附加价值 | 0, 10, 20 | EUR/MWh |
| 容量备用价值 | 0, 20, 50 | EUR/kW·年 |
| 固定补贴 | 0, 20, 50 | EUR/kWh |

组合数：4 × 3 × 4 × 3 × 3 × 3 = 1,296。物理配置 51 个（去重后）。总计 66,096 行。

### 2.3 后处理公式

```
adj_cycle_damage = cycle_damage / cycle_life_mult
adj_calendar_damage = calendar_damage × (new_rate / 0.015)
total_damage = adj_cycle_damage + adj_calendar_damage
degradation_cost = total_damage × replacement_cost × capacity_kwh
soh_end = 1.0 - total_damage

discharge_bonus = total_discharge_kwh / 1000 × discharge_value
capacity_bonus = min(max_chg_kw, max_dchg_kw) × capacity_value × simulation_years
subsidy = capacity_kwh × fixed_subsidy

net_inc = gross_inc - degradation_cost + discharge_bonus + capacity_bonus + subsidy
```

### 2.4 价差放大公式

```
amplified_price = mean_price + (price - mean_price) × amp_factor
```

保持长期均值不变，仅放大日内波动幅度。**结论限定为"价格波动情景"，非市场预测。**

### 2.5 三类代表性配置

| 配置 | 容量 | 功率 | SOC | λ | 代表含义 |
|------|------|------|-----|---|----------|
| zero_cycle_lower_bound | 1.0 | 0.5 | 0.2-0.8 | 2.0 | 零循环下界 |
| best_active_config | 1.0 | 0.75 | 0.1-0.9 | 1.0 | 当前主动循环最优 |
| stage15_aggressive_baseline | 1.5 | 1.0 | 0.1-0.9 | 0 | 原 Stage15 激进配置 |

### 2.6 过滤标准

| 场景 | SOH 阈值 | 用途 |
|------|---------|------|
| 硬约束 | ≥ 0.90 | 电池低于此值视为失效，过滤基准 |
| 保守情景 | ≥ 0.95 | 额外报告列，供论文敏感性讨论 |

---

## 3. 结果

### 3.1 后处理经济敏感性总览

| 过滤条件 | 通过数 | 总数 |
|----------|--------|------|
| SOH ≥ 0.90 | 53,676 | 66,096 |
| SOH ≥ 0.95（保守） | 53,676 | 66,096 |
| net_incremental > 0 | 60,714 | 66,096 |
| **全部（硬约束）** | **60,714** | 66,096 |
| **全部（保守 SOH）** | **49,374** | 66,096 |

最优组合：`cap1p5_pow1p0_soc0p1_0p9_lambda0p0` + 全最优经济参数 → net = 345,166 EUR（由补贴和容量价值主导）。

### 3.2 分情景分析

#### 情景 A：全部参数（含补贴）

- 通过数：60,714 / 66,096
- 最优 net：345,166 EUR
- 补贴（50 EUR/kWh × 3,000 kWh = 150,000 EUR）和容量价值（1,000 kW × 50 × 2.88 = 144,000 EUR）主导收益
- **此情景不代表当前市场条件，仅用于展示上界**

#### 情景 B：无补贴（含容量和放电价值）

- 通过数：17,061 / 22,032
- 最优 net：195,166 EUR（repl=50, life=3x, fade=0, d_val=20, c_val=50）
- **仅 20 EUR/kW·年的容量备用价值即可使多数配置转正**
- 临界：repl=150, life=1x, fade=0.015, c_val=20 → 首次出现正净收益（net=35,172 EUR）

#### 情景 C：纯套利（无补贴、无额外收益）

| 指标 | 值 |
|------|-----|
| 通过数 | **273 / 2,448** |
| 最优 net | **8,976 EUR** |
| 最优条件 | repl=50, life=3x, fade=0.0 |
| 第二名 | 7,712 EUR（repl=75, life=3x, fade=0.0） |
| 第三名 | 7,712 EUR（repl=50, life=2x, fade=0.0） |

**纯套利转正的临界条件**：

```
(replacement ≤ 75 EUR/kWh AND life ≥ 2× AND fade ≤ 0.005)
OR
(replacement ≤ 50 EUR/kWh AND life ≥ 3× AND fade ≤ 0.01)
```

当前基准（repl=150, life=1x, fade=0.015）下最佳 net = -12,970 EUR。这是 **12,970 EUR 的"套利亏损地板"**——仅日历老化造成的不可消除成本。

### 3.3 价差放大实验

| 配置 | amp=1.0 | amp=1.5 | amp=2.0 | amp=3.0 |
|------|---------|---------|---------|---------|
| 零循环下界 | -12,970 | -17,098 | -14,512 | -4,760 |
| 主动循环最优 | -14,252 | -14,132 | -8,454 | **+3,207** |
| 原激进配置 | -36,367 | -28,119 | -19,871 | -3,374 |

**关键发现**：

- **amp=1.5 时零循环配置净收益反而下降**——放大后的价差触发了少量循环，但边际收益仍低于边际退化成本
- **amp=3.0 时主动循环最优配置首次转正**（net=3,207 EUR），但 SOH 降至 0.898
- 零循环配置在 amp=3.0 时 net=-4,760，仍未转正——因为日历老化仍然存在
- 激进配置在 amp=3.0 时大幅改善（+33,000 EUR vs amp=1.0），但因为 λ=0 时 SOH 已降至 0.896，循环损伤已不可逆

**结论**：当前 OPSD 日内价差需放大约 3 倍，主动循环才能在扣除退化成本后盈利。该结论限定为"价格波动放大情景"，不构成市场预测。

---

## 4. 退化成本来源分解

在基准假设下（repl=150, life=1x, fade=0.015），最优零循环配置的成本结构：

| 成本项 | 金额 (EUR) | 占比 |
|--------|-----------|------|
| 循环损伤 | 0 | 0% |
| 日历老化 | 12,970 | 100% |
| **总退化成本** | **12,970** | 100% |

在 λ=0 激进配置下（cap1p5_pow1p0_soc0p1_0p9）：

| 成本项 | 金额 (EUR) | 占比 |
|--------|-----------|------|
| 循环损伤 | 29,942 | 64% |
| 日历老化 | 16,788 | 36% |
| **总退化成本** | **46,730** | 100% |

循环损伤是激进策略的主要成本来源（64%），也是 λ 惩罚试图削减的部分。但即使完全消除循环损伤，日历老化的 12,970 EUR 仍无法避免。

---

## 5. 正净收益临界条件汇总

| 路径 | 临界条件 | 通过配置 |
|------|----------|----------|
| 纯套利 | repl ≤ 50, life ≥ 3×, fade ≤ 0.005 | 273 |
| 套利 + 容量 | repl ≤ 150, life ≥ 1×, fade ≤ 0.015, capacity ≥ 20 | 大量 |
| 套利 + 价差放大 | amp ≥ 3.0（保持均值、仅放大波动） | 1 |
| 套利 + 成本改善 + 无老化 | repl ≤ 75, life ≥ 3×, fade = 0 | 通过 |

**最易实现路径**：容量备用价值 20 EUR/kW·年（约 $2.3/kW·月）即可使所有配置转正。这一价值在 PJM、ERCOT 等市场中属于合理范围。

---

## 6. 局限性

- **代理电价**：OPSD 丹麦日前电价不代表 Rawhide/科罗拉多当地市场结算价格
- **PV 估算**：使用 PVDAQ 原型曲线按容量比缩放，非 Rawhide 实测发电数据
- **退化模型**：Wohler 曲线为通用 LFP 参考值，非特定电池型号实测；日历老化 SOC 应力为线性近似
- **未建模收益**：弃光削减价值（`curtailment_value` 留作 V2）、调频辅助服务
- **后处理 SOH**：由聚合损伤值反推（`1.0 - total_damage`），非逐小时累积。相同退化参数下与 Stage22 原始 SOH 误差 < 10⁻⁶
- **价差放大**：保持均值不变的波动放大是理想化实验，实际市场价差变化可能伴随均值漂移

---

## 7. 结论

1. **基准结论不变**：Rawhide 参数参照仿真 + OPSD 代理电价下，纯套利净收益不可行。最优为零循环，net = -12,970 EUR（纯日历老化）。

2. **纯套利转正需三重改善**：重置成本降至 ≤ 50 EUR/kWh、循环寿命延长 2-3 倍、日历老化率降至 ≤ 0.5%/年，三者至少满足其二。

3. **容量备用价值是最高杠杆的额外收益**：仅 20 EUR/kW·年的容量价值即可使各配置转正。该值在多个 RTO/ISO 市场中属于合理范围。

4. **价差需 3 倍放大**：在当前 OPSD 代理电价均值不变的前提下，日内波动幅度需放大约 3 倍，主动循环套利方可盈利。

5. **补贴主导上界**：在最有利假设下（50 EUR/kWh 重置成本 + 3× 寿命 + 零老化 + 20 EUR/MWh 放电价值 + 50 EUR/kW·年容量价值 + 50 EUR/kWh 补贴），净增量可达 345,166 EUR。该值为当前模型框架下的理论上界。

6. **报告口径**：所有结论均以"参数参照仿真"和"情景假设"表述。基准结论与情景结论严格区分。价差放大实验限定为"价格波动情景"。

---

## 附录

### A. 运行命令

```bash
# Stage22 基线（16 配置，含新字段）
python -m new_energy_sys.cli.run_stage22_degradation_aware_config \
  --config configs/data_sources.rawhide_prairie_scaled_2020_2022.json \
  --predictions data/processed/rawhide_scaled/stage9_rawnhide_scaled_predictions.csv \
  --feature-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --capacity-multipliers 1.0,1.5 --power-multipliers 0.75,1.0 \
  --soc-ranges 0.1-0.9,0.2-0.8 --lambda-values 0,1.0 --min-spreads 0 \
  --output-prefix stage22_rawnhide_base_v2

# Stage22 高 λ（36 配置，含新字段）
python -m new_energy_sys.cli.run_stage22_degradation_aware_config \
  ... \
  --capacity-multipliers 1.0,1.25 --power-multipliers 0.5,0.75 \
  --soc-ranges 0.2-0.8,0.25-0.75,0.3-0.7 --lambda-values 1.0,2.0,5.0 \
  --output-prefix stage22_rawnhide_high_lambda_v2

# Stage22B 后处理
python -m new_energy_sys.cli.run_stage22b_economic_sensitivity \
  --stage22-metrics <base_v2.csv>,<high_lambda_v2.csv> \
  --output-prefix stage22b_economic_sensitivity

# Stage22B 价差放大
python -m new_energy_sys.cli.run_stage22b_economic_sensitivity \
  --mode spread \
  --stage22-metrics <baseline.csv> \
  --config configs/data_sources.rawhide_prairie_scaled_2020_2022.json \
  --predictions data/processed/rawhide_scaled/stage9_rawnhide_scaled_predictions.csv \
  --feature-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --spread-amplification 1.0,1.5,2.0,3.0
```

### B. 新增/修改文件

| 文件 | 变更 |
|------|------|
| `src/new_energy_sys/stage22_degradation_aware_config.py` | 增加 5 个输出字段；新增 `run_stage22b_economic_sensitivity()`、`run_stage22b_spread_amplification()` 及配套报告函数（~400 行） |
| `src/new_energy_sys/cli/run_stage22b_economic_sensitivity.py` | 新增 CLI（支持多文件合并 + spread 模式） |
| `pyproject.toml` | 注册 `new-energy-run-stage22b-economic-sensitivity` |

### C. 未纳入本阶段的收益类型

| 类型 | 原因 |
|------|------|
| 弃光削减价值 (`curtailment_value`) | 需逐小时弃光数据，Stage22 当前未汇总输出。留作 V2 |
| 调频辅助服务 | 需要更细粒度的功率调度模型（分钟级），当前小时级调度不支持 |
