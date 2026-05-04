# 技术文档：PV 预测特征增强阶段——成果、问题与推进路线

**日期**: 2026-05-04  
**关联提交**: `ac4a049` ~ `c308e45`（4 commits）  
**前一阶段**: 辐射分解模块 + HRRR PV 预测链路（`reports/technical_report_2026-05-03_irradiance_and_hrrr_pv_pipeline.md`）

---

## 一、本阶段完成的工作

### 1.1 时间对齐修复

**问题**: 预测值画在了 origin time 而非 valid time，导致 t+6h 预测曲线出现虚假的"相位提前"——指标显示 t+6h 和 t+24h 接近，但图上 t+6h 明显异常差。

**修复**: 所有预测值按其 `valid_time = origin_time + horizon` 平移后与真实值对比。修复后：
- 图和指标之间不再互相矛盾
- t+6h 在晴空窗口的 MAE 从 0.297 kW（虚假）恢复到 ~0.032 kW（正常）
- 排序恢复为 t+1h < t+6h < t+24h（符合物理直觉）

### 1.2 标准化天气分层评估报告（Step A）

**脚本**: `scripts/generate_stratified_eval_report.py`  
**输出**: `reports/stratified_eval_report.md` + 2 张 PNG 图表

按 valid-time 晴空指数（`clearsky_index_ghi`）三分层：

| 场景 | 定义 | t+1h Day nRMSE | t+6h Day nRMSE | t+24h Day nRMSE |
|------|------|---------------|---------------|-----------------|
| Clear | CSI ≥ 0.7 | 0.256 | 0.277 | 0.281 |
| Mixed | 0.3 ≤ CSI < 0.7 | 0.874 | 0.744 | 0.750 |
| Overcast | CSI < 0.3 | 1.634 | 1.278 | 1.298 |

**关键发现**:
- Clear < Mixed < Overcast 排序在所有 horizon 上成立——评估体系可信
- Overcast nRMSE 高达 1.3–1.6，主要因为阴天平均功率极低（分母效应），RMSE 绝对值仅 0.10–0.11 kW
- 模型在所有 horizon 上显著优于 persistence baseline（t+6h 优势最大，达 3.9 倍）

### 1.3 异常低发电样本诊断（Step B）

**脚本**: `scripts/diagnose_anomaly_samples.py`  
**输出**: `reports/anomaly_diagnosis.md` + `reports/anomaly_daytime_low_power_samples.csv`

**异常条件**（严格五条件，避免误伤阴天）：
1. 太阳高度角 > 10°
2. 晴空 GHI > 500 W/m²
3. 实际 GHI > 250 W/m²（排除阴天）
4. 晴空指数 > 0.5（排除厚云）
5. PV 功率 < 0.022 kW

**结果**:
- 仅 53 个异常样本（占全天 0.21%，占白天 0.49%）
- 92.5% 的异常样本 `weather_fill_flag = 0`（原始观测，非填充数据）
- 两种模式：卷云型（22 个，可能污染/临时遮挡）和晴空设备停机型（19 个）
- 6 天有 ≥ 3 个连续异常小时

**结论**: 异常率极低，不需特殊处理。模型会将这些作为稀疏噪声自然处理。

### 1.4 特征增强模块（Step C+D）

**文件**: `src/new_energy_sys/feature_enhancements.py`

三类特征按数据泄漏风险分类：

| 类别 | 特征 | 安全性 | 用途 |
|------|------|--------|------|
| **A** | `pv_ramp_{1,3,6}h_kw_per_h` | ✅ 仅 origin-time 信息 | 训练入模 |
| **A** | `cloud_{clear,mixed,overcast}` | ✅ origin-time CSI | 训练入模（E2） |
| **B** | `valid_h{N}h_solar_elevation/zenith/cos` | ✅ 确定性天文计算 | 训练入模 |
| **B** | `valid_h{N}h_sunset_attenuation` | ✅ 确定性天文计算 | 训练入模 |
| **B** | `valid_h{N}h_hour_sin/cos` | ✅ 确定性天文计算 | 训练入模 |
| **C** | `cloud_scenario_actual_valid` | ❌ 需要 valid-time 真实天气 | 仅评估分层 |

**关键设计决策**:
- ramp 用 timestamp-merge 而非 `shift()`——避免缺失小时错位
- sunset_attenuation 基于 valid time 而非 origin time——直接对应傍晚 Bias
- cloud_scenario 区分 origin（A 类，可入模）和 valid（C 类，仅评估）

### 1.5 消融实验（Step E）

**脚本**: `src/new_energy_sys/cli/train_with_enhanced_features.py`

三实验 × 三 horizon = 9 个模型，LightGBM，复用 Stage5 超参：

| 实验 | 新增特征 | t+1h Day nRMSE | t+6h Day nRMSE | t+24h Day nRMSE |
|------|---------|---------------|---------------|-----------------|
| **E0** | baseline（163 特征） | 0.328 | 0.331 | 0.336 |
| **E1** | +solar +ramp（171 特征） | **0.261** | 0.330 | 0.335 |
| **E2** | +cloud_scenario（174 特征） | 0.263 | 0.333 | 0.336 |

**E1 分场景改善（t+1h）**:

| 场景 | E0 | E1 | Δ |
|------|-----|-----|-----|
| Clear | 0.256 | **0.195** | −24% |
| Mixed | 0.874 | **0.749** | −14% |
| Overcast | 1.634 | 1.429 | −13% |

**傍晚 Bias（20–23 UTC valid hour, t+1h）**:
- E0: +0.030 kW → E1: **+0.019 kW**（−35%）

---

## 二、当前问题诊断

### 2.1 强结论（已被证实）

| 结论 | 证据强度 | 说明 |
|------|---------|------|
| 时间对齐问题已彻底解决 | **强** | origin/valid time 分离，图和指标一致 |
| 天气分层评估必要且有效 | **强** | Clear < Mixed < Overcast 排序在 3 个 horizon 均成立 |
| E1 的 t+1h 改善是真实的 | **强** | Day nRMSE −20.4% 非小波动，且晴空/多云/阴天均受益 |
| E2 cloud_scenario 特征无效 | **强** | LightGBM 已从连续 CSI 变量中提取了分类信息 |
| 普通 tabular 特征工程对 t+6h/t+24h 边际收益低 | **中等偏强** | 仅对 solar + ramp + cloud_scenario 这组特征成立 |

### 2.2 需要谨慎表述的推论

**推论 1: "t+6h/t+24h 已无优化空间"**

当前证据只支持：**在当前这组新增特征（solar + ramp + origin cloud_scenario）中**，t+6h/t+24h 没有明显收益。不能扩大为"已经没有任何可优化空间"。

尚未尝试的方向包括：多步联合建模（multi-output sequence）、CSI 目标重定义、quantile loss、天气分 regime 建模、error correction model、NWP ensemble/lagged NWP 特征。后续若要提升中长时预测，需要改变建模范式或输入信息源，而非继续扩充 tabular 特征。

**推论 2: "HRRR gap_closure=0.25%，所以 HRRR 不值得继续挖"**

这个结论下得过早。gap_closure 极低可能有三种原因：
1. HRRR 24–48h 预报本身确实没提供有效增益
2. HRRR 与 PV 数据在时间/空间对齐或变量使用方式上仍有问题
3. HRRR 特征的使用方式不对（未做 valid-time 对齐、未按 lead-time 区分、未做空间插值）

在确认以下问题之前，不能简单判定 HRRR 信息无效：
- HRRR GHI / cloud_cover 对 actual PV 的相关性是多少？
- HRRR GHI 对 NSRDB GHI 的逐时误差分布如何？
- HRRR 在 Clear / Mixed / Overcast 各场景下误差是否一致？
- HRRR lead time 是否与误差有单调退化关系？

**推论 3: "t+24h 不值得深度学习"**

不应以"降低点预测 RMSE"作为是否上深度模型的唯一判据。深度模型在 t+24h 上的价值可能体现在：
- 多 horizon 一致性（t+1h 到 t+24h 输出合理曲线）
- 峰值时间稳定性
- ramp 事件预测
- quantile interval calibration
- extreme weather 下的鲁棒性

更准确的表述是：**t+24h 不应以降低点预测 RMSE 作为主目标，而应转向概率预测、日累计发电量准确度和多步曲线一致性。**

### 2.3 当前指标体系缺失的诊断维度

| 缺失维度 | 重要性 | 说明 |
|---------|--------|------|
| 误差分解（幅值/相位/爬坡/能量） | 高 | RMSE 相同的模型工程价值可能完全不同 |
| Overcast 场景的 RMSE/capacity | 高 | nRMSE 因分母效应在阴天容易被高估 |
| 误报/漏报发电率 | 中 | 白天 actual≈0 但 pred>阈值 的次数 |
| 异常样本标记（53 个） | 低 | 不作为独立处理，但应保留为 evaluation tag |
| HRRR 预测误差传递分析 | 中 | HRRR 各变量对 NSRDB 真值的误差分布 |

---

## 三、模型能力现状评估

### 3.1 已确认完成

| 阶段 | 状态 | 说明 |
|------|------|------|
| 评估管线校正 | ✅ 完成 | valid-time 对齐、天气分层、persistence 对比 |
| 错误来源分层 | ✅ 完成 | Clear/Mixed/Overcast 三级 + 傍晚 Bias 专项 |
| LightGBM 特征消融 | ✅ 完成 | E0/E1/E2 三实验 × 三 horizon |
| 普通 tabular 特征工程边际收益确认 | ✅ 完成 | t+1h 有显著增益，t+6h/t+24h 已接近上限 |

### 3.2 尚未完成

| 阶段 | 状态 | 说明 |
|------|------|------|
| 多步联合建模验证 | ❌ 未做 | 单 horizon 独立训练，未利用 horizon 间依赖 |
| 概率预测验证 | ❌ 未做 | 仅点预测，无 P10/P50/P90 |
| CSI / clear-sky normalization 目标重定义 | ❌ 未做 | 直接预测 PV power，未先分离日周期 |
| 多站点或区域聚合验证 | ❌ 未做 | 仅单站点 1.12 kW |
| HRRR 误差传递全链路分析 | ❌ 未做 | HRRR 各变量对 NSRDB 的逐时误差分布未知 |

### 3.3 当前最准确的总体判断

**LightGBM 已接近当前 "tabular 特征 + 单 horizon 点预测" 设定下的性能上限。** 不能说接近整个任务的上限——因为多步联合建模、概率预测、CSI 目标重定义等范式级改变尚未尝试。

后续精度提升不应继续依赖普通特征工程，而应转向目标重定义、概率预测和多 horizon 时序建模。

---

## 四、推进路线建议（经进阶评审修正）

### 核心原则

1. **不再将普通 tabular 特征工程作为优先方向**——现有消融显示其边际收益已明显下降：对 t+1h 仍有显著增益（E1 −20.4%），但对 t+6h/t+24h 这组新增特征（solar + ramp + cloud_scenario）已无效果。不能推广为"所有 tabular 特征收益为零"，但继续在此方向投入的性价比已很低
2. **不直接上重型深度学习**——先用简单模型验证范式级改变是否有效
3. **t+24h 不以点预测 RMSE 为主目标**——转向概率区间和日累计精度
4. **对 HRRR 的结论保持开放**——在完成 HRRR vs NSRDB 全链路误差分析前，不判定"HRRR 无用"

### 推荐实验矩阵（6 个实验）

| 编号 | 实验 | 目的 | 成功标准 |
|------|------|------|---------|
| **X1** | LightGBM-PV baseline | 保持 E1 作为对照组 | — |
| **X2** | LightGBM-CSI target | 验证目标重定义 | Mixed nRMSE 或日累计误差下降 |
| **X3** | LightGBM Quantile PV | 概率预测 | P10/P90 覆盖率合理，Mixed/Overcast 区间合理变宽 |
| **X4** | LightGBM Quantile CSI | 概率 + CSI | Mixed/Overcast calibration 改善 |
| **X5** | DLinear multi-horizon PV | 验证时序结构 | t+1h 或 t+6h 优于 E1 |
| **X6** | DLinear multi-horizon CSI | 时序 + 目标重定义 | 综合最优候选 |

### P0：CSI 目标重定义（X2）

**优先级最高。**

当前直接预测 `PV_power`，但 PV 功率同时混合了：
1. 太阳几何（确定性日周期）
2. 天气/云导致的不确定衰减

将目标改为 PV 的晴空指数（CSI），相当于先去掉确定性日周期，让模型专注预测天气衰减。

#### 目标定义

```
PV_clear_sky_power = 确定性晴空 PV 功率（见下方计算方法）
k = PV_actual / PV_clear_sky_power   ← 新目标
最终还原: PV_pred = k_pred × PV_clear_sky_power_valid
```

#### PV_clear_sky_power 计算方法

不要只基于 `clearsky_ghi`，应尽量考虑完整的光伏系统物理模型：

1. **最佳方案**（站点参数完整）: `pvlib clearsky irradiance → transposition (POA) → DC power → AC power`，链式计算
2. **次优方案**（站点参数不完整）: 用训练集拟合 empirical clear-sky envelope——**仅用训练集**，不触碰验证/测试集
3. **最低方案**: `capacity_kw × clearsky_ghi / 1000 × empirical_efficiency_factor`（粗略近似，不推荐但可作为兜底）

#### 数值安全约束

**分母阈值**——低太阳高度角时分母很小，CSI 会爆炸。仅在下述条件下计算 CSI target：

```
solar_elevation_valid > 5°
PV_clear_sky_power_valid > 0.05 × capacity_kw (≈ 0.056 kW)
```

不满足条件时 CSI 设为 NaN，不参与 daytime 训练。

**k 不硬裁到 [0, 1]**——因为测量噪声、云边增强可能导致 `PV_actual > PV_clear_sky_power`：

```
k_clipped = clip(k, 0, 1.2)  # 允许 20% 的云增强余量
```

**logit 变换**（如果选用 X2c）需先缩放和加 epsilon：

```
k_scaled = clip(k / 1.2, 1e-6, 1 - 1e-6)
logit_k = log(k_scaled / (1 - k_scaled))
```

否则边界值会产生无穷。

#### 评估原则

CSI 模型不要只评估 CSI 本身——必须还原成 PV 后评估：

```
PV_pred = CSI_pred × PV_clear_sky_power_valid
→ 报告 PV RMSE / MAE / Bias / daily energy error
→ 按 Mixed / Overcast 分层评估
```

否则可能出现"CSI 指标好看但实际 PV 预测没用"的情况。

#### 实验矩阵

| 子实验 | 目标 | 评估方式 |
|--------|------|---------|
| X2a | PV power（对照组） | 直接评估 |
| X2b | CSI = PV / clear_sky_power | 还原为 PV 后评估 |
| X2c | logit-clipped CSI | 还原为 PV 后评估 |

**成功标准**: Mixed 场景 nRMSE 下降，或日累计发电量误差下降。

### P1：LightGBM Quantile Regression（X3, X4）

**性价比最高的实用提升。**

目标不是降低 P50 RMSE，而是输出 P10/P50/P90 预测区间。对 Mixed/Overcast，点预测本身就不充分——调度系统需要知道不确定性范围。

#### 分位数交叉处理

LightGBM 分别独立训练 P10/P50/P90 三个模型时，可能出现 quantile crossing（如 P10 > P50）。训练后需做单调后处理：

```python
# 简单但有效：逐样本排序
p10, p50, p90 = np.sort([p10_pred, p50_pred, p90_pred], axis=0)
```

更严谨的方式是 isotonic correction 或联合分位数模型（如 LightGBM 的 `objective='quantile'` 配合单一模型多输出），但第一版用逐样本排序就够。

评估指标：

| 指标 | 含义 |
|------|------|
| Pinball Loss | 分位数回归的综合损失 |
| PICP (Prediction Interval Coverage Probability) | 实际值落在区间内的比例 |
| MPIW (Mean Prediction Interval Width) | 区间宽度，越窄越好（在覆盖达标前提下） |
| Calibration by weather scenario | Clear 应窄、Mixed 应宽、Overcast 应覆盖低功率波动 |

**关键检查**:
- Clear 场景区间是否窄（模型应对晴空有信心）
- Mixed 场景区间是否合理变宽
- Overcast 场景是否覆盖低功率波动
- 傍晚时段区间是否反映低高度角的不确定性

### P2：DLinear 多 horizon 时序建模（X5, X6）

**在确认 CSI 和 Quantile 有效之后再推进。**

DLinear 是轻量级时序基线（序列分解 → 趋势 + 季节分支 → 线性投影），参数量和训练成本远低于 TFT，适合先验证历史序列结构是否提供额外信息。

目标不是炫模型，而是回答：

> 过去 24–72 小时序列是否能给未来 1–24 小时提供额外信息？

如果 DLinear 相比 LightGBM 无明显提升，说明问题确实主要受未来天气驱动，时序结构本身的信息已被 LightGBM 的 lag 特征充分提取。

#### 输入设计（关键）

DLinear 原生更适合纯时间序列，对大量外生特征的处理不如 TFT 自然。需明确输入分组：

| 输入类型 | 变量 | 时间范围 |
|---------|------|---------|
| past observed | PV, GHI, CSI, cloud_cover, ramp | t−168h ~ t |
| future known | valid-time solar elevation/zenith/sunset, hour sin/cos, clear-sky power | t+1h ~ t+24h |
| target | PV 或 CSI | t+1h ~ t+24h |

如果不加入外生特征（天气、太阳几何），DLinear 只是拿 PV 历史预测未来，对比 LightGBM 不公平。需确保输入信息量对等。

如果 DLinear 有提升，再考虑上更复杂的架构。

### P3：TFT（条件触发）

**不要一开始就做 TFT。** 仅在以下条件全部满足时才推进：
1. DLinear 或简单 LSTM 已证明"时序结构有增益"
2. 多变量选择网络有明确的变量候选（不能只是"把全部特征丢进去"）
3. Attention 可解释性对论文有价值

TFT 的风险：参数多、训练不稳、样本量可能不够（单站点 2.5 万行对 TFT 偏少）、容易出现训练集好但测试集不稳定的情况。

### HRRR 诊断性检查（推进任何 P1–P3 实验前必做）

在得出"HRRR 信息增益低"的最终结论前，需完成以下 sanity check：

1. **HRRR GHI vs NSRDB GHI 逐时对比**: MAE / RMSE / Bias / R²
2. **按 lead time 分组**: 24h / 30h / 36h / 42h ——检验误差是否随 lead time 单调退化
3. **按 Clear / Mixed / Overcast 分组**: 不同天气场景下 HRRR 预报质量是否一致
4. **按 valid hour 分组**: 是否存在特定时段（如清晨/傍晚）HRRR 系统性偏差
5. **valid_time 对齐检查**: HRRR 的 `timestamp`（valid_time）是否与 PV 的 `timestamp` 完全一致

仅在所有检查通过后，才有资格判定 HRRR 的信息增益是"真的低"还是"pipeline 没吃对"。

### 暂缓事项

| 事项 | 原因 |
|------|------|
| 直接做 TFT | 先用 X2–X6 六个实验确认方向和范式，再决定是否值得 |
| 多站点/区域聚合 | 当前无额外站点数据，且单站点问题尚未充分探索 |
| 换 NWP 源（如 ECMWF） | 数据获取不可控，且 HRRR 的误差特性尚未被充分分析 |
| 异常样本特殊处理 | 53 个（0.21%）占比太低，但应保留为 evaluation tag |
| cloud_scenario 作为训练特征 | E2 已确认无效——更适合作为评估标签 |

---

## 五、产出文件清单

### 5.1 新增文件

| 文件 | 用途 |
|------|------|
| `src/new_energy_sys/feature_enhancements.py` | valid-time 太阳几何 + ramp + cloud_scenario 特征函数 |
| `src/new_energy_sys/cli/train_with_enhanced_features.py` | 特征消融训练 CLI |
| `scripts/generate_stratified_eval_report.py` | 标准化天气分层评估报告 |
| `scripts/diagnose_anomaly_samples.py` | 日间低功率异常诊断 |
| `scripts/visualize_pv_predictions.py` | 时间对齐的 PV 预测可视化 |

### 5.2 生成报告

| 文件 | 内容 |
|------|------|
| `reports/stratified_eval_report.md` | 天气分层评估报告 |
| `reports/anomaly_diagnosis.md` | 异常样本诊断报告 |
| `reports/figures/stratified_rmse_by_scenario.png` | 分层 RMSE 柱状图 |
| `reports/figures/stratified_bias_by_valid_hour.png` | 按 valid hour 的 Bias 折线图 |
| `reports/figures/pv_prediction_3day_zoom_TIME_ALIGNED.png` | 3 天时间对齐对比图 |
| `data/processed/pvdaq_nsrdb_2020_2022/enhanced_models/` | 9 个消融模型 + 指标 CSV/JSON |

### 5.3 模型资产

| 目录 | 内容 | 用途 |
|------|------|------|
| `stage5_models/` | Stage5 tuned（修复后数据） | t+1h/6h/24h full_features |
| `enhanced_models/` | E0/E1/E2 消融模型 | 特征增强对比 |

---

## 六、测试覆盖

存量测试 67 个全部通过，零回归。本阶段为快速验证脚本，未新增单元测试。

---

---

## 七、定稿结论

当前 LightGBM 已接近"tabular 特征 + 单 horizon 点预测"设定下的性能上限。后续不应继续以普通特征工程为主要优化方向，而应转向三类范式级实验：CSI / clear-sky normalization 目标重定义、LightGBM quantile regression 概率预测，以及 DLinear 多 horizon 时序建模。t+24h 不应单纯以点预测 RMSE 改善为目标，而应重点关注概率区间校准、日累计发电量误差和多步曲线一致性。在未完成 HRRR vs NSRDB 全链路误差诊断前，不应对 HRRR 的信息价值做出最终判定。

---

**文档版本**: v2.0（经进阶评审修正）  
**作者**: Claude Opus 4.7
