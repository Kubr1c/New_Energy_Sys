# 当前证据索引

该文件只记录当前论文和系统展示应优先引用的证据，不替代原始报告。详细指标以对应报告为准。

| 主题 | 当前结论 | 证据文件 | 论文主线 |
|---|---|---|---|
| 光伏预测 | LightGBM 是稳定主预测模型，深度学习模型用于对比 | `thesis/main.tex`、预测模型结果表 | 是 |
| 滚动调度 | 系统实现 24 小时前瞻滚动调度 | Stage12/Stage18 相关结果 | 是 |
| 退化修正 | 基准代理电价下净增量为负 | `reports/stage22_degradation_aware_config_report_2026-05-10.md` | 是 |
| 经济情景 | 部分情景可实现正净增量 | `reports/stage22b_economic_sensitivity_report_2026-05-11.md`、`reports/stage23_scenario_dispatch_showcase_report_2026-05-11.md` | 是 |
| 策略蒸馏 | 学生策略可近似滚动调度首步动作并通过约束回放 | `reports/task_report_policy_distillation_replay_2026-05-10.md` | 是 |
| Rawhide 参照场景 | 使用公开容量参数参照仿真，不是实测运行数据 | Stage18/Stage22/Stage23 报告 | 是 |
| HRRR/天气扩展 | 可作为补充数据能力，不作为论文主线 | HRRR 和 Stage21A 报告 | 否 |

## 当前使用边界

- Rawhide 只能写作公开容量参数参照场景或参照仿真。
- OPSD 和情景电价用于仿真分析，不等同于 Rawhide 当地真实市场结算电价。
- 深度学习在本文中主要体现为序列预测对比和两阶段策略蒸馏，不应写成完全替代 LightGBM 或滚动调度。
- HRRR 材料属于天气数据扩展与未来改进材料，不作为当前论文主线。
