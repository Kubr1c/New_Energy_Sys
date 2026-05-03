# 会话记录 — 2026-05-03

## 总体进度

完成 GHI 辐射分解模块（Erbs + DISC），NSRDB 验证，并完成光伏功率预测下一阶段的设计。

## 一、辐射分解模块

### 1.1 Bug 修复

- **问题**: Erbs 云量修正溢出——`correction = 0.15*GHI*cc/100` 无上限，导致 DHI > GHI，DNI 被 clip 到 0
- **根因**: 多云场景下原始 Erbs 已给高 DHI，云量修正进一步推高→DNI 负→clip→能量不守恒
- **修复**: `correction = min(0.10*GHI*cc/100, 0.90*(GHI-DHI))`，保证 DHI ≤ GHI 永远成立
- **额外修复**: 夜间 GHI 置零（HRRR 残差 GHI 会导致 kt 异常）
- **结果**: 闭合误差 max 从 58.8 W/m² → **0.000000** W/m²

### 1.2 DISC 模型实现

- **算法**: Knc（晴空直射透射率多项式）− δKn（云量修正 a+b·exp(c·AM)），DNI = Kn × I₀
- **关键创新**: 空气-质量修正——HRRR surface_pressure_hpa → 绝对空气质量 → 高原瑞利散射修正
- **代码**: `irradiance_decomposition.py` 支持 `model='erbs'|'disc'`，默认 DISC
- **CLI**: `decompose_hrrr_irradiance.py --model disc|erbs`

### 1.3 NSRDB 验证结果

以 NSRDB REST2 的 DNI/DHI 为真值，11,927 白天对比：

| 指标 | Erbs | DISC | 胜者 |
|------|------|------|------|
| DNI RMSE | 239.5 | **208.6** (−13%) | DISC |
| DNI R² | 0.577 | **0.679** | DISC |
| DHI RMSE | **63.9** | 83.2 | Erbs |
| DHI R² | **0.612** | 0.342 | Erbs |

- **DISC DNI 在所有场景（阴/多云/晴、四季）都赢了**
- **Erbs DHI 在所有场景都赢了**（夏季尤为明显，DISC R²=0.037 基本不可用）
- **结论**: DISC 设为默认（PV 预测 DNI 权重更高）

### 1.4 测试

- 28 个分解测试 + 30 个存量 = **58/58 通过**
- 测试覆盖: 能量闭合、DHI≤GHI、阴阳天、夜间、空输入、云量修正、空气-质量效应

### 1.5 Commit

`6eb8c8d` feat: GHI decomposition with dual-model support (Erbs + DISC) and NSRDB validation

---

## 二、光伏功率预测阶段设计

### 2.1 现状

- 部署模型: LightGBM history_only (51 特征) — nRMSE=0.1225
- 可达上限: LightGBM full_features (163 特征, NSRDB oracle) — nRMSE=0.0784
- 差距 36% 来自"天气特征是 oracle 不是真预报"
- HRRR Stage7 数据已有但从未接入预测链路

### 2.2 设计方案

**三步走:**
1. **Step 1** (精简 5 变量): GHI + DNI + DHI + 温度 + 云量 → ~75 特征，验证链路
2. **Step 2a**: +湿度 +风速 (7 变量)
3. **Step 2b**: +lead_time_hour (多时次可信度)

**多时次聚合:** issue_time ∈ [T-48h, T-24h]，按 1/lead_time 加权平均

**三基线对比:** history_only (下限) vs HRRR forecast (目标) vs NSRDB oracle (上限)

### 2.3 关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 模型选择 | Erbs → DISC (默认) | DNI RMSE −13%, 高原空气-质量修正 |
| 预测阶段方案 | 技术验证优先 (A) | 先量化预报→预测误差传递 |
| HRRR 使用方式 | 多时次聚合最优精度 (B) | 合并多个发报时次 |
| 特征方案 | 分步推进 | 精简 5 变量→扩展→时效特征 |

---

## 三、产出文件

- `src/new_energy_sys/irradiance_decomposition.py` — Erbs + DISC 双模型
- `src/new_energy_sys/cli/decompose_hrrr_irradiance.py` — CLI 工具
- `tests/test_irradiance_decomposition.py` — 28 个测试
- `scripts/validate_decomposition_nsrdb.py` — NSRDB 验证脚本
- `docs/superpowers/specs/2026-05-03-hrrr-pv-forecast-pipeline-design.md` — 预测阶段设计 spec
