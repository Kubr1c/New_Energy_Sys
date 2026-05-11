# 代码阅读地图

该文件用于减少重复读取代码的范围。除非任务明确要求，不建议从旧报告或全量源码开始阅读。

| 问题 | 优先阅读 | 不必默认阅读 |
|---|---|---|
| 预测结果如何产生 | `src/new_energy_sys/modeling.py`、`src/new_energy_sys/stage9_inference.py`、`src/new_energy_sys/tabular_comparison.py` | HRRR 全流程、旧训练试验脚本 |
| 滚动调度如何产生 | `src/new_energy_sys/stage12_storage_rolling.py`、`src/new_energy_sys/storage.py` | 早期阈值调参细节 |
| 配置敏感性如何产生 | `src/new_energy_sys/stage15_storage_sensitivity.py` | 旧报告全文 |
| 退化成本如何计算 | `src/new_energy_sys/stage17_battery_degradation.py`、`src/new_energy_sys/stage22_degradation_aware_config.py` | 前端展示代码 |
| 正净增量情景如何形成 | `src/new_energy_sys/stage22_degradation_aware_config.py`、`src/new_energy_sys/stage23_scenario_dispatch_showcase.py` | HRRR 过程报告 |
| 策略蒸馏如何验证 | `src/new_energy_sys/stage20_neural_dispatch.py`、`reports/task_report_policy_distillation_replay_2026-05-10.md` | DQN 试验 |
| 前端展示读取哪些结果 | `frontend/src/views/DispatchSimulation.vue`、`frontend/src/views/GovernanceAnalysis.vue`、`backend/app/data_loader.py` | 旧 frontend handover |

## 默认代码阅读顺序

1. 先读 `docs/current/START_HERE.md` 和 `docs/current/CURRENT_EVIDENCE.md`。
2. 仅根据当前问题选择上表中的文件。
3. 若涉及论文表述，优先使用报告和 `thesis/main.tex`，不要直接从过程日志归纳结论。
