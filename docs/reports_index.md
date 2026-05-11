# Reports Index

本文件是压缩后的报告索引。默认阅读入口已经切换到 `docs/current/START_HERE.md`，历史报告只在需要追溯时进入 `reports/archive/` 或 `docs/archive/`。

## CURRENT

| Path | Purpose |
|---|---|
| `docs/current/START_HERE.md` | 当前项目默认阅读入口。 |
| `docs/current/CURRENT_EVIDENCE.md` | 当前论文和展示侧证据索引。 |
| `docs/current/CODE_READING_MAP.md` | 当前代码阅读地图。 |
| `reports/stage22_degradation_aware_config_report_2026-05-10.md` | 退化约束配置分析，说明基准代理电价下净增量为负。 |
| `reports/stage22b_economic_sensitivity_report_2026-05-11.md` | 经济条件临界分析，说明正净增量所需条件。 |
| `reports/stage23_scenario_dispatch_showcase_report_2026-05-11.md` | 多收益情景调度效果展示，可支撑论文第六章。 |
| `reports/task_report_policy_distillation_replay_2026-05-10.md` | 两阶段策略蒸馏与物理约束回放指标。 |
| `reports/thesis_final_writing_handoff_2026-05-07.md` | 论文写作证据边界和章节整合提示。 |
| `reports/project_status_2026-05-05.md` | 项目总体状态概括。 |
| `reports/module_progress_assessment_2026-05-05.md` | 模块完成度和论文可用性概括。 |

## REFERENCE

| Path | Purpose |
|---|---|
| `reports/stage21a_rawhide_weather_market_feasibility_report.md` | Rawhide 天气驱动和价格场景可行性补充证据。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_report.md` | LightGBM 主预测模型结果。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage12_storage_rolling_optimization_report.md` | 24 小时前瞻滚动调度基准结果。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage15_storage_configuration_sensitivity_report.md` | 储能配置敏感性分析。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage17_battery_degradation_report.md` | 电池退化、SOH 和净收益修正。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage18_rawhide_simulation_report.md` | Rawhide 公开容量参数参照仿真。 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage20b_two_stage_policy_report.md` | 两阶段神经网络策略蒸馏结果。 |

## ARCHIVE

| Path | Purpose |
|---|---|
| `reports/archive/weather_hrrr/` | HRRR、天气扩展和相关过程材料。 |
| `reports/archive/early_progress/` | 早期阶段进度报告。 |
| `reports/archive/frontend_demo/` | 前端演示视频、截图和渲染过程材料。 |
| `reports/archive/thesis_format/` | DOCX、学校模板、格式修复和旧论文文件。 |
| `reports/archive/prediction_diagnostics/` | 预测异常诊断和旧特征改进材料。 |
| `reports/archive/experimental_rl/` | 未进入当前主线的强化学习试验。 |
| `reports/archive/misc/` | 其他历史材料。 |
| `docs/archive/` | 旧前端文档、旧计划、HRRR 计划和旧论文材料。 |

## Operating Rule

当归档材料与当前入口文件冲突时，以 `docs/current/START_HERE.md` 和 `docs/current/CURRENT_EVIDENCE.md` 为准。Rawhide 相关内容始终表述为公开容量参数参照场景，不写成实测运行数据或真实市场结算结果。
