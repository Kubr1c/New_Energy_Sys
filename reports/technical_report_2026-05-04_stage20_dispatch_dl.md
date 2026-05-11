# Stage20 调度侧深度学习补强技术报告

> **Status note - 2026-05-05**: This report is now a **BASELINE / AUDIT** record for the Stage20 regression-MLP policy. The latest dispatch-side neural policy result is Stage20B: `data/processed/pvdaq_nsrdb_2020_2022/stage20b_two_stage_policy_report.md`. Use Stage20 to explain why direct regression MLP was insufficient; use Stage20B for the current neural dispatch conclusion.

**日期**: 2026-05-04  
**状态**: 修正版，取代 Claude 早期探索版结论
**核心定位**: Stage20 用于补强论文“调度侧深度学习”内容，但不宣称 MLP 优于显式优化器。

---

## 一、修正背景

早期 Stage20 已经打通两条实验线：

1. 将 Stage14 的 TCN/DLinear 预测结果，以及 Persistence baseline 接入 Stage12 rolling 调度。
2. 用 Stage12 rolling 的 `planned_charge_kw` / `planned_discharge_kw` 训练 MLP policy。

审核后发现 4 个问题：

| 问题 | 影响 | 本次修正 |
|---|---|---|
| MLP 回放绕过 Stage12 物理结算 | 收益和约束结论不可比 | 改为 Stage12 口径：SOC 边界、PV 侧充电、并网容量、shortfall、curtailment |
| 预测源时间窗口不同 | LightGBM/DL/Perfect 收益不可横比 | 所有预测源限制到共同 `dispatch_timestamp` 交集 |
| 方向准确率 baseline 过低 | 53.3% 被误读为强证据 | 增加多数类 baseline、random baseline、Macro-F1、混淆矩阵 |
| MLP 未使用 24h look-ahead | 不能称为 rolling optimizer 蒸馏 | 输入扩展为当前 SOC + 时间编码 + 未来 24h PV/price/load |

Pitfall：如果仍沿用旧报告中的“约束全通过、收益保留率 88%、显著优于随机 baseline”等表述，答辩时很容易被质疑评估口径不一致。

---

## 二、DL 预测驱动调度消融

所有预测源统一限制到共同调度窗口：

- `common_window_rows`: 3681
- `common_start`: 2022-07-30 09:00:00+00:00
- `common_end`: 2022-12-30 23:00:00+00:00

| 预测源 | 增量收益(EUR) | 短缺(kWh) | 弃光(kWh) | 等效循环 |
|---|---:|---:|---:|---:|
| LightGBM history_only | 0.1076 | 124.1371 | 2.2923 | 24.3658 |
| TCN history_only | 0.5395 | 99.4609 | 0.6391 | 59.1295 |
| TCN csi_enhanced | 0.5391 | 106.5891 | 0.8357 | 60.0834 |
| DLinear history_only | 0.5071 | 118.9111 | 0.9915 | 58.9468 |
| Persistence baseline | 0.5138 | 95.0214 | 0.3777 | 55.1517 |
| Perfect forecast reference | 0.1842 | 0.0000 | 0.0000 | 25.6851 |

结论：

- 同窗口后，Stage14 时序预测源在 Stage12 rolling 收益上高于 Stage9 LightGBM。
- Persistence 在该调度窗口也表现较强，说明调度收益不只由 PV RMSE 决定，还受时段、电价、SOC 轨迹和短缺约束影响。
- Perfect forecast 在 rolling 策略下短缺为 0，但收益不是最高，说明当前 rolling 策略的目标函数偏保守，不能简单把“完美预测”解释为收益上限。
- 原始 JSON/CSV 产物中仍保留 `perfect_forecast_upper_bound` 字段名；论文和汇报中建议写作 `Perfect forecast reference`，避免误导为收益上界。

Pitfall：不能写成“TCN 预测精度优于 LightGBM”。这里比较的是“预测源驱动调度后的收益”，不是 PV 预测 RMSE。

---

## 三、MLP 策略蒸馏

### 3.1 输入与训练

MLP policy 现在学习 Stage12 rolling optimizer 的首小时动作近似：

- 输入维度: 77
- 输入内容: 当前 SOC、小时/月周期编码、未来 24h PV 预测、未来 24h 电价、未来 24h 负荷
- 输出: `planned_charge_kw`、`planned_discharge_kw`
- 训练/验证/测试样本: 17492 / 3748 / 3749
- 早停: epoch 13

### 3.2 分类指标

| 指标 | 数值 |
|---|---:|
| Direction accuracy | 0.4017 |
| Majority-class baseline | 0.5017 |
| Random baseline | 0.3333 |
| Macro-F1 | 0.3806 |
| Charge MAE | 0.0233 kW |
| Discharge MAE | 0.0204 kW |

混淆矩阵：

```text
rows = actual [idle, charge, discharge]
cols = pred   [idle, charge, discharge]

[[975, 256, 480],
 [ 84, 379, 1418],
 [  4,   1, 152]]
```

结论：

- MLP 的方向准确率低于多数类 baseline，不能作为“策略学习很强”的证据。
- Macro-F1 为 0.3806，说明模型能学到部分动作模式，但对 charge/idle/discharge 的区分仍不稳。
- 论文中应将其表述为“神经策略蒸馏可实现，但分类决策能力弱于多数类基线，仍需改进”。

Pitfall：不要拿 0.4017 和 0.3333 随机 baseline 单独比较，因为真实标签分布严重不均衡。

---

## 四、严格物理回放

| 场景 | 样本数 | 增量收益(EUR) | 短缺(kWh) | 弃光(kWh) | 等效循环 | SOC 范围 | 收益保留率 |
|---|---:|---:|---:|---:|---:|---|---:|
| MLP policy distillation | 3749 | 0.0934 | 146.1853 | 0.0000 | 18.5321 | 0.1000-0.5930 | 0.8379 |
| Stage12 teacher same window | 3749 | 0.1114 | 126.8429 | 2.3653 | 24.9587 | 0.1000-0.6731 | 1.0000 |

约束验证：

- `actual_charge_kw <= actual_pv_kw`: 通过，违规 0 行
- `actual_net_export_kw <= capacity_kw`: 通过，违规 0 行
- `soc_end ∈ [soc_min, soc_max]`: 通过
- 充放电功率上限: 通过
- 同时充放: 通过，违规 0 行
- 能量平衡误差: 通过

结论：

- MLP 在严格物理回放下收益为 Stage12 teacher 同窗口的 83.79%。
- MLP 短缺更高、循环更低，说明它学到了一部分保守调度行为，但没有稳定复现 Stage12 rolling 的优化能力。
- 这足以支撑“调度侧引入深度学习策略蒸馏实验”，但不能支撑“深度学习调度优于显式优化”。

Pitfall：收益保留率只对同一测试窗口有效，不能和 Stage12 全量窗口或旧版 Stage20 的指标混用。

---

## 五、质量门禁

| 门禁 | 结果 |
|---|---|
| 至少 4 个预测源成功接入 Stage12 | PASS |
| 调度消融 common window 一致 | PASS |
| MLP direction accuracy 高于多数类 baseline | FAIL |
| MLP SOC 在配置边界内 | PASS |
| MLP 无同时充放 | PASS |
| MLP 严格物理回放通过 | PASS |

---

## 六、论文写法建议

推荐表述：

> 本文进一步设计了调度侧深度学习补强实验。一方面，将 TCN、DLinear 等深度学习预测结果接入 24h rolling 储能调度框架，评估预测源变化对调度收益、短缺和电池循环的影响；另一方面，以 rolling optimizer 的首小时动作为监督标签，训练 MLP 调度策略进行策略蒸馏。实验表明，TCN/DLinear 在同窗口调度收益上高于 Stage9 LightGBM，但未证明深度学习预测相对 Persistence baseline 具有稳定优势；MLP 策略在严格物理约束回放下可实现可行调度，但方向分类能力未超过多数类基线，说明显式优化器在当前数据和约束下仍更可靠。

不推荐表述：

- “MLP 显著优于随机策略。”
- “神经调度策略约束全通过，因此可替代 Stage12。”
- “Perfect forecast 是收益上限。”
- “TCN 预测精度优于 LightGBM。”

---

## 七、阶段进度评估

已完成：

- 修正 Stage20 预测源公平比较口径。
- 修正 MLP 24h look-ahead 输入。
- 修正 MLP 严格物理回放。
- 修正方向准确率 baseline。
- 生成新版 Stage20 CSV/JSON/Markdown 产物。

目标完成情况：

- “调度侧加入深度学习”目标已完成。
- “可作为论文正式实验结论”目标基本完成，但 MLP 结果必须如实写成负结果/受限结果。
- 后续已由 Stage20B two-stage policy 完成改进验证；本报告仅作为 regression MLP audit baseline 使用。

下一阶段可行性：

- 可以进入论文写作，不建议继续追加 PPO/DRL。
- 若继续写作，应优先引用 Stage20B 作为当前调度侧神经策略结论；Stage20 用于解释直接回归 MLP 为什么不足。
