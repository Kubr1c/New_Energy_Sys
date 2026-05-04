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

### 2.1 已解决的问题

| 问题 | 状态 | 解决方式 |
|------|------|---------|
| 预测 vs 真实时间轴错位 | ✅ 已修复 | valid-time 对齐绘图和评估 |
| 图和指标互相矛盾 | ✅ 已修复 | 对齐后指标与图形一致 |
| 傍晚 Bias 偏高 | ✅ 显著改善 | sunset_attenuation 减少 35% |
| t+1h 短临预测精度不足 | ✅ 显著改善 | Day nRMSE −20.4% |

### 2.2 仍然存在的问题

**问题 1: t+6h 和 t+24h 特征增强无效果**

中长时预测的瓶颈不在特征工程，而在输入信息的质量上限。LightGBM 的 163 个特征已经充分提取了可用信息，新增的太阳几何特征在长 horizon 上被现有的 `target_plus_*` 特征覆盖。

**根本原因**: t+6h 和 t+24h 的预测误差由天气预报不确定性主导。NSRDB `full_features` 中的 `target_plus_*` 特征是 oracle 级别的"完美预报"（真实观测值平移），而实际部署中只能用 HRRR 预报替代——预报本身的误差远大于特征工程的边际增益。

**问题 2: Mixed/Overcast 场景误差仍然较高**

| Horizon | Clear nRMSE | Mixed nRMSE | Overcast nRMSE |
|---------|------------|-------------|----------------|
| t+1h | 0.195 | 0.749 | 1.429 |
| t+6h | 0.277 | 0.739 | 1.266 |
| t+24h | 0.281 | 0.752 | 1.287 |

Overcast nRMSE 数值高（1.3–1.4）但 RMSE 绝对值仅 0.10–0.11 kW——主要因为阴天平均功率极低（分母效应）。Mixed 场景才是真正的难点：RMSE 绝对值约 0.13–0.20 kW，占 1.12 kW 容量的 12–18%。

**根本原因**: Mixed 场景下云量快速变化，小时级 NWP 网格（HRRR 3km）无法解析亚网格尺度的云运动。单站点 1.12 kW 系统对局部遮挡高度敏感。

**问题 3: cloud_scenario 特征（E2）无增量价值**

Origin-time 的晴空指数分类（clear/mixed/overcast）没有为 LightGBM 提供额外信息。原因是现有的连续特征（`clearsky_index_ghi` + rolling stats + `ghi_wm2` + `cloud_cover_pct`）已经充分表达了天气状态。

**问题 4: 缺少不确定度量化和多步预测**

当前所有模型只输出点预测。对于 Mixed/Overcast 场景，光伏功率本身存在强不确定性——仅给出点预测在实际调度中不够用。此外，当前框架是单 horizon 独立建模，没有利用 horizon 之间的时间依赖关系。

---

## 三、模型能力现状评估

### 3.1 LightGBM 基线已达到的成熟度

| 评估维度 | 状态 | 证据 |
|---------|------|------|
| 评估管线可信度 | ✅ 成熟 | valid-time 对齐，天气分层，persistence 对比 |
| 无系统性偏差 | ✅ 合格 | 全局 Bias < 0.02 kW，傍晚 Bias 已修正 |
| t+1h 短临预测 | ✅ 良好 | Day nRMSE=0.261，比 persistence 好 2.7 倍 |
| 晴空场景 | ✅ 良好 | Day nRMSE=0.195，曲线形状准确 |
| 多云/阴天 | ⚠️ 可接受 | nRMSE 0.75–1.43，绝对值 0.10–0.20 kW |
| t+6h/t+24h | ⚠️ 受限于预报质量 | 特征增强无效，需更好预报源或换范式 |

### 3.2 模型已达到的性能天花板

在当前数据条件下，LightGBM + 163 特征的性能已接近上限：

```
t+1h  Day nRMSE = 0.261  (E1, 本阶段最优)
t+6h  Day nRMSE = 0.330  (E1, 与 E0 持平)
t+24h Day nRMSE = 0.335  (E1, 与 E0 持平)

上限参考: NSRDB oracle full_features
t+1h  Day nRMSE = 0.090  (163 特征含 oracle 天气)
t+6h  Day nRMSE = 0.089
t+24h Day nRMSE = 0.091
```

E1 的 t+1h Day nRMSE=0.261 距离 oracle 上限 0.090 还有 **2.9 倍差距**——但这个差距不是模型架构能弥补的，因为 oracle 模型用的是"完美天气预报"（NSRDB 真实观测平移），而 E1 用的是 origin-time 已知信息。两者输入质量差距巨大。

---

## 四、推进路线建议

### 4.1 短临预测（t+1h）：可继续优化

t+1h 是唯一一个特征增强有显著效果的 horizon，也是深度学习最可能产生增益的窗口。

**推荐方向**:
- 深度学习多任务模型（DLinear → TFT），利用 t+1h 到 t+6h 的时序依赖
- 加入最近 1–3 小时的 HRRR 短临预报（如果可用）——1h 时效的 NWP 精度远高于 24h

**预期增益**: DLinear/TFT 可能将 t+1h Day nRMSE 从 0.261 降至 0.22–0.24（基于文献中深度模型在短临光伏预测上的典型提升）

### 4.2 日前预测（t+24h）：输入瓶颈，非模型瓶颈

t+24h 的特征增强无效、HRRR 预报增益极低（gap_closure=0.25%）、深度学习替代 LightGBM 可能也无显著收益。

**三个可能方向**:

| 方向 | 可行性 | 预期增益 | 风险 |
|------|--------|---------|------|
| 更好的 NWP 源（如 ECMWF） | 低——数据获取困难 | 中 | 高——不可控 |
| **多站点/区域聚合预测** | 中——需额外数据 | 中 | 中——站点间相关性未知 |
| 概率预测（Quantile Regression） | **高**——基于现有模型 | 中——提升实用价值 | 低 |
| **接受现状**——LightGBM 已是最优解 | **高** | 无精度增益 | 无 |

### 4.3 推荐下一阶段：混合路线

```
Phase 1 (1–2h): 深度学习多任务 t+1h 专用
  ├── DLinear (baseline) → TFT (主力)
  ├── 输入: origin-time 特征 + valid-time 太阳几何 + ramp
  ├── 输出: t+1h, t+2h, t+3h, t+6h 联合预测
  └── 目标: Day nRMSE < 0.22

Phase 2 (1h): 概率预测
  ├── LightGBM Quantile Regression (P10/P50/P90)
  └── 为调度层提供不确定性区间

Phase 3 (后续): 根据 Phase 1 结果决定
  ├── 如果深度模型 t+1h 显著优于 LightGBM → 扩展到 t+24h
  └── 如果提升有限 → 接受 LightGBM 作为最终模型，进入调度层
```

### 4.4 不需要做的事

- ❌ 在 t+6h/t+24h 上做特征增强——已确认无效果
- ❌ 在 t+24h 上换深度学习模型——天气预报质量是硬天花板，换模型架构无法突破
- ❌ 继续挖掘 HRRR 预报特征——gap_closure=0.25% 说明预报信息已充分利用
- ❌ 对异常样本（53 个）做特殊处理——占比太低，不值得

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

**文档版本**: v1.0  
**作者**: Claude Opus 4.7
