# DQN 储能调度探索实验 — 任务报告

**日期**: 2026-05-10
**定位**: 深度强化学习探索性 baseline（非论文主线方法）
**论文核心**: 两阶段策略蒸馏（Stage20B）

---

## 1. 实验目的

在现有 Stage9 预测 + Stage3 电价/负荷 + Stage12 储能配置基础上，
实现标准 DQN 算法，观察深度强化学习在储能调度环境中的可行性。
结果仅供补充实验和未来展望参考。

## 2. 新增文件

| 文件 | 说明 |
|------|------|
| `src/new_energy_sys/rl_dqn_dispatch.py` | DQN 核心：环境、网络、Agent、训练/回放/评估、基线对比 |
| `scripts/run_dqn_dispatch_experiment.py` | CLI，支持 `--reward-scale` / `--epsilon-decay` / `--epsilon-final` |
| `tests/test_rl_dqn_dispatch.py` | 26 单元 + 1 集成测试 |

**未修改任何现有 Stage12/Stage20B/论文文件**。

## 3. 技术设计概要

- **状态空间**: 64 维（SOC + PV/电价 + 周期编码 + 未来 24h PV/电价序列 + 上一动作 one-hot）
- **动作空间**: 7 离散（idle / charge_25/50/100% / discharge_25/50/100%）
- **网络**: 3 层 MLP (64→128→128→7)，SmoothL1Loss，Adam lr=1e-3，γ=0.99
- **物理约束**: 复用 `_postprocess_actions()` (来自 Stage20B)，SOC/功率/并网/互斥硬裁剪
- **训练/测试分离**: dispatch_input 按时间顺序 70/30 切分，train 仅用于训练，test 仅用于 greedy replay
- **基线生成**: Stage10/Stage12 在同一切分后的 test_input 上运行，保证 `no_storage_revenue` 分母一致

## 4. 实验执行

### 4.1 10K Smoke Test

约束审计 6/6 通过，11 mandatory outputs 全部生成。

### 4.2 调参实验

| 阶段 | reward_scale | ε_decay→ε_final | 增量收益(EUR) | 说明 |
|------|-------------|-------------------|-------------|------|
| Default 100K (全窗口) | 1.0 | 0.995→0.01 | −3.83 | reward 信号被噪声淹没 |
| Tuned 100K (全窗口) | 1000 | 0.997→0.05 | +4.22 | 旧版，train/test 混合 |
| **Tuned 100K (testonly)** | 1000 | 0.997→0.05 | **+1.21** | train/test 严格分离 |
| **Tuned 300K (testonly)** | 1000 | 0.997→0.05 | **+1.29** | 同上，300K 略优于 100K |

> 全窗口版本（rl_dqn_dispatch_tuned_100k/300k）train/test 未分离，
> 仅作参考。最终报告以 `testonly` 版本为准。

## 5. 同窗口策略对比（test 窗口，7574 小时）

以下所有策略在 **同一 test_input** 上计算，`no_storage_revenue_eur` 完全一致
（37.87 EUR），`comparison_alignment_check.json` 中 `no_storage_match=True`。

### 5.1 Tuned 100K (testonly)

| 策略 | incremental_revenue_eur | no_storage_revenue_eur |
|------|------------------------|------------------------|
| DQN greedy replay | +1.21 | 37.87 |
| Stage10 forecast_dispatch | −0.02 | 37.87 |
| Stage12 rolling_optimization | +1.08 | 37.87 |

### 5.2 Tuned 300K (testonly)

| 策略 | incremental_revenue_eur | no_storage_revenue_eur |
|------|------------------------|------------------------|
| DQN greedy replay | +1.29 | 37.87 |
| Stage10 forecast_dispatch | −0.02 | 37.87 |
| Stage12 rolling_optimization | +1.08 | 37.87 |

### 5.3 解读

- Stage10（固定阈值 25/45 EUR/MWh）在此站点基本无套利空间（−0.02 EUR）。
- Stage12（24h 滚动 DP）产生 +1.08 EUR，是可靠参考基线。
- DQN（100K: +1.21，300K: +1.29）与 Stage12 处于同一量级，300K 略优于 100K。
- DQN 在约束全部通过的前提下达到了与滚动 DP 可比的调度效果，
  但未形成显著优势，且离散动作空间限制了进一步优化空间。

## 6. 覆盖率与约束审计

| 项 | 值 |
|----|-----|
| 总 dispatch 行 | 25248 |
| train 行（70%） | 17673 |
| test 行（30%） | 7575 |
| coverage_pass | ✅ True |
| DQN replay 行 | 7574（未行末尾 1 步因缺少 look-ahead 被截断） |

| 约束 | 100K | 300K |
|------|------|------|
| soc_within_bounds | ✅ | ✅ |
| charge_power_within_limit | ✅ | ✅ |
| discharge_power_within_limit | ✅ | ✅ |
| no_simultaneous_charge_discharge | ✅ | ✅ |
| energy_balance_error_within_tolerance | ✅ | ✅ |

## 7. 论文写入建议

- **不进入正文策略对比表**。DQN 与 Stage12 在同一测试窗口上处于相同量级，
  但 DQN 的离散动作空间和训练不稳定性使其不适合作为正式调度方法。
- 可放入「补充实验」或「未来展望」：
  > 作为补充实验实现了标准 DQN 调度策略。在 train/test 严格分离的评估中，
  > DQN（300K 训练）的增量收益为 +1.29 EUR，与 Stage12 滚动 DP（+1.08 EUR）
  > 处于同一量级，所有物理约束检查通过。DQN 的收益提升有限，
  > 后续可尝试连续动作空间（DDPG/SAC）或 Transformer-based 策略网络。
- `rl_dqn_report_snippet.md` 和 `rl_dqn_method.md` 已按此基调撰写。

## 8. 产物目录

```
data/processed/pvdaq_nsrdb_2020_2022/
├── rl_dqn_dispatch/                          ← Default 100K（参考）
├── rl_dqn_dispatch_tuned_100k/               ← Tuned 100K 全窗口（参考）
├── rl_dqn_dispatch_tuned_300k/               ← Tuned 300K 全窗口（参考）
├── rl_dqn_dispatch_tuned_100k_testonly/      ← ✅ Tuned 100K testonly
│   ├── rl_dqn_strategy_comparison.csv        ← no_storage 一致
│   ├── comparison_alignment_check.json       ← no_storage_match=True
│   └── ...（11 mandatory outputs）
└── rl_dqn_dispatch_tuned_300k_testonly/      ← ✅ Tuned 300K testonly
    └── （同上）
```

## 9. 测试结果

```
tests/test_rl_dqn_dispatch.py         26 passed
tests/test_storage_dispatch_core.py     8 passed
tests/test_stage20_neural_dispatch.py   6 passed
Total: 40 passed, 0 failed
```
