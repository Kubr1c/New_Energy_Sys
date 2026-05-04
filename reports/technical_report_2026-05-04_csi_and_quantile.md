# 技术文档：CSI 目标重定义 + Quantile 概率预测 + 天气门控

**日期**: 2026-05-04  
**关联提交**: `f60c710` ~ `a816a80`（3 commits）  
**前一阶段**: HRRR 预报误差诊断（`reports/hrrr_forecast_error_diagnosis.md`）

---

## 一、背景

HRRR 诊断结论：HRRR GHI 预报有 skill（MSE_Skill=0.82 vs persistence），但 HRRR error 与 PV error 相关性仅 0.02。问题不是 HRRR 没信息，是 PV 模型没吃到。

本阶段验证两条路径：
1. **CSI 目标重定义**：改预测目标为 `k = PV / clear_sky_power`，让模型专注天气衰减
2. **概率预测**：输出 P10/P50/P90，为调度提供不确定性区间

---

## 二、Phase 1: CSI 目标重定义

### 实验矩阵（t+24h）

| ID | Target | HRRR | Day nRMSE | Clear nRMSE | Mixed nRMSE | Overcast nRMSE |
|----|--------|------|-----------|-------------|-------------|----------------|
| **C0** | PV power | No | **0.335** | **0.280** | 0.752 | 1.287 |
| C1 | CSI | No | 0.435 | 0.389 | 0.693 | 1.238 |
| C2 | CSI | Yes | 0.432 | 0.386 | 0.692 | 1.191 |
| C3 | Physical | HRRR-only | 0.614 | 0.514 | 1.214 | 3.124 |
| C4 | CSI | Yes + all | 0.432 | 0.387 | 0.683 | 1.205 |

### 关键发现

1. **CSI 是 Mixed/Overcast 专家，不是全局替代方案**
   - Mixed nRMSE: C0 0.752 → C1 0.693 (−8%)
   - Overcast nRMSE: C0 1.287 → C1 1.238 (−4%)
   - Clear nRMSE: C0 0.280 → C1 0.389 (+39%)
   
2. **HRRR 引入的边际增益极小**（C1→C2: 0.435→0.432），CSI 实验未改善 HRRR error 与 PV error 的相关性（仍 ≈0.02）

3. **Clear 样本占 daytime 68%**，主导了 overall 指标——CSI 的 Mixed/Overcast 改善被 Clear 退化淹没

4. **t+6h、t+1h 同步实验显示 CSI 在所有 horizon 上均劣于 PV direct**——CSI 目标重定义不是 horizon 依赖的

### 结论

```
CSI 不是失败，而是不适合作为全局统一 target。
它是 Mixed/Overcast 专家，不是 Clear 专家。
```

---

## 三、Phase 2: LightGBM Quantile Regression

### 双线实验

| Exp | Horizon | PICP | MPIW(kW) | Clear PICP | Mixed PICP | Overcast PICP | Crossing |
|-----|---------|------|----------|------------|------------|---------------|----------|
| Q1 | t+1h | 0.670 | 0.169 | 0.665 | 0.678 | 0.547 | 0.082 |
| Q1 | t+6h | 0.578 | 0.143 | 0.534 | 0.727 | 0.584 | 0.043 |
| Q1 | t+24h | 0.591 | 0.149 | 0.554 | 0.711 | 0.591 | 0.077 |
| Q2 | t+1h | 0.474 | 0.229 | 0.437 | 0.607 | 0.521 | 0.011 |
| Q2 | t+6h | 0.461 | 0.211 | 0.403 | 0.652 | 0.573 | 0.015 |
| Q2 | t+24h | 0.452 | 0.212 | 0.390 | 0.650 | 0.589 | 0.010 |

### 关键发现

1. **PICP 低于 75-85% 目标**：Q1 仅 58-67%，区间偏窄
2. **Q2 (CSI quantile) 失败**：PICP 45-47%，因 bounded CSI target [0, 1.2] 使 alpha=0.9 难以与中位数分离（best_iteration=1）
3. **Clear < Mixed MPIW 达标**：Q1 在所有 horizon 上晴空区间窄于多云 ✓
4. **Crossing rate**：Q1 t+6h 4.3% 达标；t+1h/t+24h 8.1%/7.7% 超标
5. **排序后处理可消除 crossing**，但排序前的高 crossing 说明三个独立模型不稳定

### 结论

Q1 方向正确但需后校准（conformal calibration + regime-specific widening）；Q2 路线不成立。

---

## 四、Phase 3: 天气门控融合

在 codex 评审建议下，针对"CSI 不适合全局替代"的结论，验证门控融合。

### 门控策略对比

| Strategy | Day nRMSE | Clear nRMSE | Regime-weighted Avg |
|----------|-----------|-------------|---------------------|
| C0 (E1 baseline) | 0.326 | 0.274 | 0.759 |
| C2 (CSI+HRRR) | 0.434 | 0.388 | 0.773 |
| **Hard Gate (oracle)** | **0.326** | **0.274** | **0.735 (−3.2%)** |
| Origin-time Gate | 0.326 | 0.274 | 0.759 |
| Soft Blend | 0.330 | 0.279 | 0.748 |

### 关键发现

1. **Oracle gating 有效**：Regime-weighted nRMSE −3.2%，Clear 未退化
2. **Deployable gating 无效**：RF 分类器 24h 提前预测天气类型准确率仅 69%，不足以实现 oracle gate 的收益
3. **Clear 保护达标**：所有阈值 (0.50-0.90) 均保持 Clear nRMSE < 0.300 ✓
4. **C2 的 Mixed 改善在此测试子集上不成立**：C0 Mixed nRMSE(0.717) 优于 C2(0.727)——结果对测试集选择敏感

### 结论

门控上限已验证（oracle gate −3.2%），但部署版受限于 24h 天气类型预测精度。

---

## 五、深度学习重新评估

codex 评审结论：**不建议现在大规模训练 DL**。

| 模型 | 当前价值 | 理由 |
|------|---------|------|
| DLinear | 低 | 线性时序趋势中的额外信息有限 |
| TCN | 中 | 可作为 E1 残差修正的受控实验 |
| TFT | 不建议 | 过重、当前阶段不需要 |

唯一推荐的 DL 实验：**TCN + t+24h + E1 残差修正**——`最终预测 = E1 + TCN_residual_correction`，只在门控和 Quantile 都完成后再考虑。

---

## 六、全局进度总结

| 阶段 | 完成时间 | 结论 |
|------|---------|------|
| ✅ Irradiance decomposition (Erbs/DISC) | 5月3日 | DISC 设为默认，DNI RMSE −13% |
| ✅ NSRDB validation | 5月3日 | 11,927 白天验证，DISC DNI 全场景优于 Erbs |
| ✅ HRRR PV prediction pipeline | 5月3日 | gap_closure=0.25%，HRRR 对 PV 边际增益极低 |
| ✅ Data fix (timezone + GHI) | 5月4日 | Stage1-7 修复，Stage5 重新训练 |
| ✅ Feature enhancement + ablation | 5月4日 | t+1h day_nRMSE −20.4%，傍晚 Bias −35% |
| ✅ Inspection dashboard (V1a+V1b) | 5月4日 | 验收控制台上线，全量数据 2020-2022 |
| ✅ HRRR forecast error diagnosis | 5月4日 | 8 checks, HRRR has skill but PV didn't benefit |
| ✅ CSI target reformulation | 5月4日 | Mixed/Overcast 改善，Clear 退化，不适合全局 |
| ✅ Quantile regression | 5月4日 | Q1 可修，Q2 不成立，PICP 偏低 |
| ✅ Gating experiment | 5月4日 | Oracle gate −3.2%，deployable gate 受限于分类精度 |

### 当前最优模型

**E1 LightGBM PV direct**：t+1h day_nRMSE=0.261, t+6h=0.330, t+24h=0.335

### 下一个方向

1. Quantile 后校准（conformal + regime-specific）→ 提升 PICP 至 75-85%
2. 如时间允许：TCN E1 残差修正 → 低成本验证 DL 价值

---

## 七、产出文件

| Phase | 文件 | 说明 |
|-------|------|------|
| 1 | `src/new_energy_sys/cli/train_csi_target.py` | CSI 实验 C0-C4 |
| 1 | `data/processed/pvdaq_nsrdb_2020_2022/csi_models/` | CSI 模型 + 预测 |
| 2 | `src/new_energy_sys/cli/train_quantile_pv.py` | Q1/Q2 Quantile |
| 2 | `data/processed/pvdaq_nsrdb_2020_2022/quantile_models/` | Quantile 模型 |
| 3 | `src/new_energy_sys/cli/train_gating_experiment.py` | 天气门控 |
| 3 | `reports/technical_report_2026-05-04_csi_and_quantile.md` | 本文档 |

---

**文档版本**: v1.0  
**作者**: Claude Opus 4.7 (Co-Authored-By)  
**外部评审**: GPT-5.5 (codex) — 两次评审（CSI 计划 + Phase 1-2 结果）
