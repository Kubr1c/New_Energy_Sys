# 任务报告：补齐策略蒸馏回放实验产物

**日期**：2026-05-10
**任务类型**：论文实验链路补齐
**执行工具**：Claude Code (Claude Opus 4.7)
**Git 分支**：main

---

## 一、任务目标

将 Stage20B"两阶段神经网络策略蒸馏"整理成可供论文第六章 6.4 节审核使用的完整实验链路：

`24h 滚动调度教师策略 → 学生两阶段策略 → 物理约束回放 → 教师/学生收益与约束对比 → 论文 6.4 可粘贴片段`

优先复用已有 Stage20B 产物，只在缺少必要字段时新增补齐脚本。

---

## 二、执行摘要

已有 Stage20B 产物包含完整的严格物理回放数据（`stage20b_two_stage_policy_replay.csv`，2596 行逐小时轨迹 + 聚合指标），大部分论文所需指标可直接抽取，无需重新训练模型或重写调度算法。

**主要缺口**：
1. 推理耗时未测量（训练代码未保存模型权重）
2. 缺乏论文格式的输出文件（JSON metrics、comparison CSV、中文报告片段）
3. 缺乏教师-学生时间戳对齐验证
4. 缺乏回放方法中文文档

**解决方式**：新增 `scripts/evaluate_policy_distillation_replay.py`，默认从已有 CSV/JSON 抽取指标；可选 `--measure-timing` 重训练模型并测量推理延迟。

---

## 三、新增文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `scripts/evaluate_policy_distillation_replay.py` | 510 | 产物抽取与补齐主脚本 |

无现有文件被修改。

---

## 四、生成的结果文件（10 个）

输出目录：`data/processed/pvdaq_nsrdb_2020_2022/policy_distillation_replay/`

### 必须产物（论文直接使用）

| # | 文件 | 大小 | 用途 |
|---|---|---|---|
| 1 | `policy_distillation_replay_trajectory.csv` | 890 KB | 逐小时学生策略回放轨迹 |
| 2 | `policy_distillation_replay_metrics.json` | 2.2 KB | 结构化指标（20+ 字段） |
| 3 | `policy_distillation_teacher_student_comparison.csv` | 0.6 KB | 教师 vs 学生对比（2 行 × 14 列） |
| 4 | `policy_distillation_report_snippet.md` | 6.3 KB | 论文 6.4 节中文正文片段 |

### 补充产物

| # | 文件 | 大小 | 用途 |
|---|---|---|---|
| 5 | `teacher_student_alignment_check.json` | 0.4 KB | 时间戳对齐验证（alignment_pass=true） |
| 6 | `policy_distillation_confusion_matrix.csv` | 0.1 KB | 3×3 动作混淆矩阵 |
| 7 | `policy_distillation_replay_method.md` | 4.7 KB | 中文回放方法说明（可直接改写入论文） |

### 图表（可选）

| # | 文件 | 大小 | 用途 |
|---|---|---|---|
| 8 | `teacher_student_soc_comparison.png` | 134 KB | SOC 轨迹对比 |
| 9 | `teacher_student_power_comparison.png` | 150 KB | 充放电功率对比（计划 vs 实际） |
| 10 | `teacher_student_cumulative_revenue.png` | 64 KB | 累计收益曲线（储能 vs 无储能） |

---

## 五、关键指标摘要

### 动作分类性能

| 指标 | 值 | 说明 |
|---|---|---|
| 方向准确率 | **0.9908** | 远高于随机基线 0.3333、多数类基线 0.5073 |
| Macro-F1 | **0.9749** | 三分类宏平均，远超 Stage20 回归 MLP 的 0.3806 |
| 充电召回率 | 0.9932 | |
| 放电召回率 | 0.9804 | |
| 充电→放电误判 | **0** | 致命方向错误为零 |
| 放电→充电误判 | **0** | 致命方向错误为零 |

### 动作混淆矩阵（行=教师，列=学生，回放闭环统计）

| 教师 \ 学生 | 预测 idle | 预测 charge | 预测 discharge |
|---|---:|---:|---:|
| 真实 idle | 1160 | 3 | 13 |
| 真实 charge | 14 | 1303 | 0 |
| 真实 discharge | 13 | 0 | 90 |

### 物理约束回放对比

| 指标 | 教师 (Stage12 Rolling) | 学生 (Two-Stage Policy) |
|---|---|---|
| 测试样本数 | 2596 | 2596 |
| 储能总收益 (EUR) | 13.7891 | **13.8076** |
| 无储能收益 (EUR) | 13.7308 | 13.7308 |
| 增量收益 (EUR) | 0.0583 | **0.0768** |
| 收益保持率 | 1.0000 | **1.3169** |
| 等效循环次数 | 16.79 | **14.12** |
| 总充电量 (kWh) | 41.67 | 35.06 |
| 总放电量 (kWh) | 37.61 | 31.63 |
| SOC 最小值 | 0.1000 | 0.1000 |
| SOC 最大值 | 0.6731 | 0.5924 |
| SOC 越界次数 | 0 | 0 |
| 功率越界次数 | 0 | 0 |
| 同时充放电次数 | 0 | 0 |
| 最大能量守恒误差 | 1.11×10⁻¹⁶ | 1.11×10⁻¹⁶ |
| 约束通过 | 是 | **是** |
| 单步推理耗时 (ms) | 不适用（优化算法） | **0.0012** |

### 推理效率

| 指标 | 值 |
|---|---|
| 学生模型重训练耗时 | 13.9 秒（38 epochs） |
| 学生模型测试集推理总耗时 | 0.00314 秒（2596 样本批量推理） |
| 学生单步推理耗时 | **0.0012 ms/步** |
| 重训练方向准确率 | 0.9908（与原始报告一致） |
| 教师单步耗时 | null（Stage12 是 24h look-ahead 优化算法，非模型推理） |

### 结论强度

收益保持率 **1.3169**（学生增量 0.0768 EUR / 教师增量 0.0583 EUR），结论：**学生策略能够较好保持教师滚动调度收益**。

> 保持率 >1.0 表示在该特定 2596 步测试窗口内学生策略偶然超过教师，不是学生策略在全局上优于教师优化器的证据。论文中应表述为"学生在同窗口内的收益不低于教师"，而非"学生超越教师"。

---

## 六、实际运行命令

### 默认运行（无需重训练，仅读取已有产物）

```powershell
$env:PYTHONPATH='src'
python scripts\evaluate_policy_distillation_replay.py --processed-dir data\processed\pvdaq_nsrdb_2020_2022
```

### 含推理耗时测量（重训练模型 + 计时）

```powershell
$env:PYTHONPATH='src'
python scripts\evaluate_policy_distillation_replay.py `
  --processed-dir data\processed\pvdaq_nsrdb_2020_2022 `
  --config configs\data_sources.pvdaq_nsrdb_2020_2022.json `
  --stage12-results data\processed\pvdaq_nsrdb_2020_2022\stage12_storage_rolling_optimization_results.csv `
  --feature-input data\processed\pvdaq_nsrdb_2020_2022\stage3_feature_dataset.parquet `
  --measure-timing
```

### 验证命令

```powershell
# 帮助信息
$env:PYTHONPATH='src'; python scripts\evaluate_policy_distillation_replay.py --help

# 编译检查
$env:PYTHONPATH='src'; python -m compileall -q scripts\evaluate_policy_distillation_replay.py src\new_energy_sys\stage20_neural_dispatch.py

# 已有测试（7 passed）
$env:PYTHONPATH='src'; python -m pytest tests\test_stage20_neural_dispatch.py -q -p no:cacheprovider

# 输出文件列表
Get-ChildItem data\processed\pvdaq_nsrdb_2020_2022\policy_distillation_replay\
```

---

## 七、未完成项及原因

| 项目 | 状态 | 原因 |
|---|---|---|
| `policy_distillation_scenario_robustness.csv` | 跳过 | 当前产物仅含单一 OPSD 映射电价场景，无同口径多场景数据可公平比较 |
| 教师单步推理耗时 | null | Stage12 是优化算法（24h look-ahead DP），不是单次模型前向传播，无法以单步推理延迟与神经网络直接比较 |
| Stage20 回归 MLP 对比行人 | 未纳入 | Stage20 回归 MLP 的方向准确率（0.4017）低于多数类基线（0.5017），且回放收益不可比（不同数据窗口）。仅在报告中作为基线提及 |
| Rawhide 场景对比 | 未纳入 | Rawhide 是 PVDAQ 容量比例缩放（22 MW / 1.12 kW = 19643倍），不同规模、不同价格场景，不可与 1.12 kW 教师策略同口径比较 |

---

## 八、学生回放模式声明

本实验使用 **`full_student_policy`** 模式：

- 动作类型由学生模型 `direction_head`（3 类 softmax）独立决定
- 充电功率由学生模型 `charge_power_head`（Sigmoid 归一化 × max_charge_kw）输出
- 放电功率由学生模型 `discharge_power_head`（Sigmoid 归一化 × max_discharge_kw）输出
- **不依赖教师轨迹中的功率值**，不需要 teacher-power-oracle
- 功率输出经 `_postprocess_actions()` 进行物理约束裁剪后才执行

---

## 九、需人工审核的风险项

### 1. 收益保持率 >1.0 的表述

学生在 2596 步窗口内增量收益 0.0768 EUR > 教师 0.0583 EUR。这是同一窗口内的采样方差，**不是学生策略在全局上优于教师优化器的证据**。论文中应表述为"学生在同窗口内的收益保持较高"，并明确说明保持率 >1.0 的含义。

`policy_distillation_report_snippet.md` 中已自动处理此情况，写明："收益保持率大于 1.0 表明在该特定测试窗口内学生策略的增量收益偶然超过了教师策略。这不是学生策略在全局上优于教师策略的证据。"

### 2. 混淆矩阵的两种口径

- **训练时混淆矩阵**（report.json）：`[[1164,2,11],[9,1308,0],[2,0,100]]` — 使用教师 SOC 作为模型输入
- **回放闭环混淆矩阵**（replay CSV）：`[[1160,3,13],[14,1303,0],[13,0,90]]` — 使用回放闭环更新的 SOC 作为模型输入

回放闭环版本的 SOC 在每个时间步被实际执行动作更新，与训练时的教师 SOC 分布略有不同，导致少量边界样本的方向预测发生变化。两种口径均有效，但回放口径更能反映真实部署行为。`policy_distillation_report_snippet.md` 中使用了回放口径的混淆矩阵。

### 3. 收益边界声明

所有收益基于：
- **电价**：OPSD 丹麦日间市场映射电价（`DK_1_price_day_ahead`），不是 PVDAQ 站点所在 Colorado/PSCO 区域的真实市场结算价格
- **光伏**：PVDAQ System 10 实测 AC 功率（1.12 kW 铭牌容量）
- **天气**：NSRDB 后验太阳资源数据（2020-2022），不是真实 forecast-cycle 天气预报

论文中不得将上述收益表述为真实同区域市场收益。所有报告片段均已包含此边界声明。

### 4. Rawhide 参照场景

Rawhide 电站仅以公开参数（22 MW PV + 1 MW/2 MWh BESS）进行容量比例缩放，不构成该电站的实测运行数据。

---

## 十、论文 6.4 节写作建议

### 可直接使用的材料

1. `policy_distillation_report_snippet.md` — 完整的 6.4 节草稿，含方法概述、分类指标、回放对比、收益分析、约束分析、结论强度
2. `policy_distillation_replay_method.md` — 实验方法说明，可直接改写进 6.4.1 节
3. `policy_distillation_teacher_student_comparison.csv` — 教师/学生对比原始数据
4. `policy_distillation_confusion_matrix.csv` — 动作混淆矩阵原始数据

### 图表使用建议

- SOC 对比图（`teacher_student_soc_comparison.png`）：可放入论文展示学生策略的 SOC 轨迹稳定性
- 累计收益图（`teacher_student_cumulative_revenue.png`）：可放入论文展示储能 vs 无储能的收益差异
- 功率对比图（`teacher_student_power_comparison.png`）：可选，辅助说明物理约束裁剪效果

### 核心叙事线索

1. 教师策略来自 24h 前瞻滚动调度（不是最优调度上界，是可复现的强基线）
2. 学生策略通过两阶段结构解决了纯回归 MLP 的方向混淆问题（准确率从 0.40 → 0.99）
3. 严格物理回放证明蒸馏策略可满足全部物理约束
4. 策略蒸馏不替代滚动优化器，而是将复杂决策压缩为确定性神经网络推理（0.0012 ms/步）
5. 该实验支撑论文题目中的"基于深度学习"——神经策略取代了人工设定的价格阈值规则

---

## 十一、脚本命令行接口

```
usage: evaluate_policy_distillation_replay.py [-h]
                                              [--processed-dir PROCESSED_DIR]
                                              [--output-dir OUTPUT_DIR]
                                              [--config CONFIG]
                                              [--stage12-results STAGE12_RESULTS]
                                              [--feature-input FEATURE_INPUT]
                                              [--measure-timing]
                                              [--replay-csv REPLAY_CSV]
                                              [--metrics-csv METRICS_CSV]
                                              [--report-json REPORT_JSON]

选项:
  --processed-dir       处理后数据目录（默认: data/processed/pvdaq_nsrdb_2020_2022）
  --output-dir          输出目录（默认: {processed_dir}/policy_distillation_replay）
  --config              配置文件路径（--measure-timing 必需）
  --stage12-results     Stage12 滚动调度结果 CSV（--measure-timing 必需）
  --feature-input       Stage3 特征 Parquet（--measure-timing 必需）
  --measure-timing      重训练两阶段策略并测量推理耗时
  --replay-csv          覆盖回放 CSV 路径
  --metrics-csv         覆盖指标 CSV 路径
  --report-json         覆盖报告 JSON 路径
```

---

*报告生成时间：2026-05-10*
*生成工具：Claude Code (Claude Opus 4.7)*
*项目路径：C:\Project\New_Energy_Sys*
