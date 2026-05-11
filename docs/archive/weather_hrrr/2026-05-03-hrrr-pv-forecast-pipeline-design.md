# HRRR 预报驱动的光伏功率预测链路——设计规格 v2

**日期**: 2026-05-03  
**状态**: 已确认 (incorporating review from `docs/hrrr_pv_forecast_design_review_suggestions.md`)  
**关联**: GHI 辐射分解模块 (Erbs/DISC, commit 6eb8c8d)

## 1. 背景与目标

### 1.1 现状

- 已部署模型：LightGBM `history_only`（51 特征），t+24h 测试集 **nRMSE=0.1225**（白天 0.1689）
- 可达上限：LightGBM `full_features`（163 特征，含 NSRDB oracle 天气），**nRMSE=0.0784**（白天 0.0903）
- HRRR Stage7 数据已就绪（2021-2022, F24-F44），GHI→DNI+DHI 分解（DISC 模型）已完成

### 1.2 目标

用 HRRR 真实预报替换 NSRDB oracle 天气特征，量化"真预报替代 oracle 后精度损失"，并判别 DNI/DHI 分解对 PV 预测的边际贡献。

### 1.3 非目标

- 不换模型架构（保持 LightGBM）
- 不训练深度学习模型
- 不做多站点泛化
- 不做在线推理部署

---

## 2. 最高原则：数据泄露零容忍

> 先验证 HRRR 特征是否严格无泄露，再谈模型精度。

日前预测的核心约束：

```
预测目标时间 T 时，只能使用 T-24h 或更早时刻已经可获得的信息。
```

**P0 强制规则：**

```
For each target_time T, collect HRRR forecasts satisfying:
  valid_time == T
  24h <= lead_time_hour <= 48h
  lead_time_hour = valid_time - issue_time
```

**P0 强制单元测试：**

```python
assert all(feature_df["lead_time_min"] >= 24)
assert all(feature_df["issue_time_max"] <= feature_df["target_time"] - pd.Timedelta(hours=24))
assert not feature_df.duplicated(["target_time"]).any()
```

---

## 3. 数据流

```
HRRR Stage7 parquet (F24-F44, 17,505 行, 2021-2022)
       │
       ▼
[GHI 分解]  DISC → DNI, DHI (已有)
       │
       ▼
[多时次聚合]  对每个目标时间戳 T:
              1. 筛选 valid_time == T, 24h <= lead_time_hour <= 48h
              2. 聚合方式: nearest F24/F25, simple mean, weighted(1/lead)
              3. 输出: GHI, DNI, DHI, T, cloud_cover (5 变量)
              4. 保留审计列: target_time, issue_time_min/max,
                 lead_time_min/max, n_forecasts_used, weather_missing
       │
       ▼
[特征工程]  + 24h causal rolling mean/std (valid_time <= T)
            + clearsky_index = GHI / clearsky_ghi (clipped [0,2])
            + dhi_ghi_ratio (NaN when GHI <= 20 W/m², clipped [0,1.5])
            + kt (clipped [0,1.2])
            + lead_time_hour (Step 2b)
       │
       ▼
[训练/评估]  LightGBM, HRRR-overlap only 主实验,
             full-period fallback 单独报告
```

### 3.1 多时次聚合规则

**严格规则:** `valid_time == T AND 24h <= lead_time_hour <= 48h`

| 聚合方式 | 含义 | 消融实验 |
|----------|------|----------|
| nearest lead | 只取 lead_time 最接近 24h 的（min|lead-24|） | ✅ |
| simple mean | 所有符合条件时次等权平均 | ✅ |
| inverse lead weighted | 权重 ∝ 1/lead_time_hour | ✅ |

初始实现 inverse lead weighted，消融实验比较三种。

**缺值处理:** 若某 T 无任何 HRRR 覆盖 → `weather_missing=True`。实验阶段使用**方案 A：单 LightGBM 模型 + weather_missing flag**（天气特征设 NaN，LightGBM 自动学习缺失分支）。生产回退可扩展为方案 B（双模型回退）。

**审计列（保留但不入模）:** `target_time`, `issue_time_min`, `issue_time_max`, `lead_time_min`, `lead_time_max`, `n_forecasts_used`, `weather_missing`

---

## 4. 特征方案

### Step 1: 精简 5 变量（69 特征）

| 来源 | 特征组 | 数量 |
|------|--------|------|
| 时间编码 | time_features | 12 |
| PV 历史 | historical_power_features | 38 |
| HRRR 天气 | hrrr_weather (ghi, dni, dhi, temperature, cloud_cover) | 5 |
| HRRR rolling | 24h causal rolling mean/std of each (valid_time <= T) | 10 |
| HRRR 派生 | clearsky_index, dhi_ghi_ratio, kt | 3 |
| 标记 | weather_missing | 1 |
| **总计** | | **69** |

### 异常值保护

| 特征 | 保护规则 |
|------|----------|
| `dhi_ghi_ratio` | GHI <= 20 W/m² 时设为 NaN; 否则 clip [0, 1.5] |
| `clearsky_index` | GHI <= 0 或 cos(zenith) <= 0 时设 NaN; 否则 clip [0, 2] |
| `kt` | 同 clearsky_index 保护; clip [0, 1.2] |
| 夜间 irradiance ratio | 所有 irradiance 比率特征夜间设 NaN（solar_elevation <= 5°） |

### 所有 rolling 特征因果约束

```python
rolling_24h_mean(T) = mean(weather_forecast[T-23h : T])  # 仅使用 <= T
rolling_24h_std(T)  = std(weather_forecast[T-23h : T])
```

禁止使用 centered window。

### Step 2a: +湿度 +风速（+7 特征）

`relative_humidity`, `wind_speed` + 各 24h rolling stats(4) + `temp_dew_spread`(1) + `wind_dir_sin/cos`(2)

### Step 2b: +lead_time（+1 特征）

加权平均后的有效 lead_time_hour。

---

## 5. 实验设计

### 5.1 主实验（HRRR-overlap only）

**只在 HRRR 有覆盖的 2021-2022 时间范围内比较。** 训练/验证/测试切分与现有时间序列一致（70%/15%/15%）。

### 5.2 消融实验矩阵

**最小必须实验（Step 1）：**

| 实验 | GHI | DNI | DHI | 天气 | 含义 |
|------|-----|-----|-----|------|------|
| A. history_only | - | - | - | - | 下限 |
| B. HRRR-GHI-only | HRRR | - | - | - | 不加分解 |
| C1. HRRR-DISC | HRRR | DISC | DISC | T, cc | 默认方案 |
| D. NSRDB oracle | NSRDB | NSRDB | NSRDB | 全部 | 上限 |

**如果 C1 优于 B（DISC 分解有增益），再跑：**

| 实验 | GHI | DNI | DHI | 含义 |
|------|-----|-----|-----|------|
| C2. HRRR-Erbs | HRRR | Erbs | Erbs | Erbs 对照 |
| C3. HRRR-Hybrid | HRRR | DISC | Erbs | DNI+DHI 各取最优 |

**判断逻辑:**
- C1 ≈ B → DNI/DHI 分解对 PV 预测边际贡献有限
- C1 > B → 分解模块有效，继续比较 C2/C3

### 5.3 聚合方式消融（Step 2b）

比较 nearest F24/F25 vs simple mean F24-F48 vs inverse lead weighted F24-F48。

### 5.4 按天气场景分组评估

| 场景 | 定义 |
|------|------|
| clear | cloud_cover < 20% |
| partly_cloudy | 20% <= cloud_cover < 80% |
| overcast | cloud_cover >= 80% |

每组输出: nRMSE, MAE, Bias, 样本数。

### 5.5 全量回退实验（单独报告）

全时间段（含 2020 年），缺 HRRR 时 weather_missing=True。作为生产鲁棒性参考，**不作为主实验结论依据**。

---

## 6. 评估指标

### 6.1 主指标

```text
Primary: daytime nRMSE（solar_elevation > 5°）
Secondary: all-hour nRMSE
```

白天定义: `solar_elevation > 5°`（避免使用 `actual_power > 0` 混入限电/停机噪声）

### 6.2 Gap Closure 指标

```
gap_closure = (nRMSE_history - nRMSE_HRRR) / (nRMSE_history - nRMSE_oracle)
```

示例: history=0.1225, oracle=0.0784, HRRR=0.1050 → gap_closure = 39.7%

### 6.3 成功分级

| 等级 | 标准 |
|------|------|
| 最低成功 | 链路跑通，HRRR-overlap 测试集无泄露（P0 测试通过） |
| 有效成功 | HRRR forecast **白天** nRMSE < history_only 白天 nRMSE |
| 强成功 | gap_closure >= 30% |

---

## 7. 文件与测试规划

### 7.1 实现文件

| 文件 | 用途 |
|------|------|
| `src/new_energy_sys/hrrr_feature_aligner.py` | 多时次聚合 + 特征工程 + 审计列 |
| `src/new_energy_sys/cli/train_hrrr_pv.py` | 训练+评估+消融实验 |
| `tests/test_hrrr_feature_aligner.py` | 聚合逻辑测试 |
| `scripts/compare_hrrr_pv_baselines.py` | 多基线对比报告 |

### 7.2 P0 强制测试

- `test_no_forecast_with_lead_time_less_than_24h`
- `test_no_forecast_after_target_minus_24h`
- `test_one_feature_row_per_target_time`

### 7.3 P1 特征安全测试

- `test_audit_columns_are_present`
- `test_dhi_ghi_ratio_is_safe` (no inf, within [0, 1.5])
- `test_clearsky_index_range` (no inf, within [0, 2])
- `test_kt_range` (no inf, within [0, 1.2])
- `test_rolling_features_are_causal` (no future valid_time used)

---

## 8. 风险与对策

| 风险 | 概率 | 等级 | 对策 |
|------|------|------|------|
| 聚合窗口/rolling 引入数据泄露 | 高 | **P0** | 严格 `24h<=lead<=48h`, causal rolling, P0 强制测试 |
| HRRR 覆盖不全 (缺 2020 年) | 高 | P1 | 主实验 HRRR-overlap only, 全量回退单独报告 |
| Weather_missing 语义不一致 | 中 | P1 | 明确方案 A: 单模型 + NaN + flag |
| DNI/DHI 分解对 PV 预测无增益 | 中 | P1 | 消融实验 B vs C1 量化边际贡献 |
| HRRR 预报精度不足 | 中 | P2 | 接受结果，gap_closure 量化损失，为后续换模型预备 |
| 精简 5 变量不够 | 低 | P2 | Step 2a/2b 补变量 |

---

## 9. 依赖

- `irradiance_decomposition.py` (DISC 分解，已有)
- `stage7_hrrr_forecast_weather_2021_2022_f24_decomposed.parquet` (已有)
- `stage2_cleaned_hourly_dataset.parquet` (PV 真值，已有)
- `modeling.py` (LightGBM 训练器，已有)

---

## 10. 推荐执行顺序

1. **先修正数据流** — 严格 `valid_time` + `lead_time_hour` 规则
2. **实现 `hrrr_feature_aligner.py`** — 输出审计表，先人工检查再训练
3. **跑最小实验** — A/B/C1/D 四基线
4. **扩展消融** — C2/C3 分解模型对比，聚合方式消融
5. **天气场景分组评估** — 判断 HRRR 在哪类天气下真正提升
