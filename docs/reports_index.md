# Reports Index

本文件只作为报告入口，不替代 `PROGRESS.md`，不复制阶段报告正文。

## 主线报告

| Path | Purpose |
|---|---|
| `reports/pvdaq_nsrdb_2020_2022_progress_report.md` | PVDAQ + NSRDB 2020-2022 主线进度报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage4_lightgbm_report.md` | Stage4 LightGBM 基线预测报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage5_optimization_report.md` | Stage5 LightGBM 诊断、消融和调参报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage6_tcn_report.md` | Stage6 TCN 序列模型报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage7_forecast_validation_report.md` | Stage7 真实预报天气可用性验证报告 |
| `reports/stage7_forecast_weather_progress_report.md` | Stage7 管理型进度总结报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage8_tabular_model_report.md` | Stage8 表格模型横向对比和主模型选择报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_report.md` | Stage9 LightGBM 主模型推理固化和质量门禁报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage10_storage_dispatch_report.md` | Stage10 储能调度仿真、收益基准和约束门禁报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage11_storage_strategy_sensitivity_report.md` | Stage11 储能策略敏感性分析报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage12_storage_rolling_optimization_report.md` | Stage12 储能滚动优化调度报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage13_storage_strategy_governance_report.md` | Stage13 储能策略治理报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage13_storage_strategy_governance_dashboard.html` | Stage13 储能策略治理静态 HTML 仪表盘 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage14_deep_learning_report.md` | Stage14 深度学习模型对比报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage14b_training_report.md` | Stage14B 完整训练报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage15a_market_data_feasibility_report.md` | Stage15A Colorado / PSCO 同区域真实市场数据可行性验证报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage15_storage_configuration_sensitivity_report.md` | Stage15 储能配置与目标函数敏感性分析报告 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage17_battery_degradation_report.md` | Stage17 电池退化与真实度增强报告，包含 rainflow 循环计数、日历退化、SOH 和净收益 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage18_rawhide_simulation_report.md` | Stage18 Rawhide Prairie Solar 真实电站参数参照仿真报告，包含 22 MW/1 MW/2 MWh 缩放调度、配置敏感性和退化核算 |
| `data/processed/pvdaq_nsrdb_2020_2022/stage16_final_integration_report.md` | Stage16 项目总报告整合，统一整理预测、调度、市场边界、系统展示和论文答辩叙事 |

## 交付文档

| Path | Purpose |
|---|---|
| `docs/production_deployment_guide.md` | 生产部署与验收指南，固化后端环境变量、前端构建、反向代理、API smoke、E2E 和故障排查流程 |
| `docs/project_learning_guide.md` | 项目从零学习上手指南，按框架结构、推进流程、训练过程、阶段问题和后续完善建议组织 |
| `docs/battery_simulation_method.md` | S17 电池仿真增强方法说明，解释 SOH、rainflow、DOD 寿命曲线、日历退化和净收益边界 |
| `docs/frontend_production_handover.md` | 前端生产化整改交接锚点 |
| `docs/frontend_walkthrough.md` | 前端演示和页面说明 |
| `docs/frontend_task.md` | 前端任务说明 |
| `docs/project_plan.md` | 项目计划书 |

## 旧实验报告

| Path | Purpose |
|---|---|
| `reports/stage1_stage2_progress_report.md` | 早期 Stage1-2 数据处理进度 |
| `reports/stage1_stage2_stage3_weather_progress_report.md` | NREL + OPSD + weather 链路阶段报告 |
| `reports/pvdaq_nsrdb_progress_report.md` | PVDAQ + NSRDB 早期链路报告 |
| `reports/pvdaq_openmeteo_stage1_stage3_progress_report.md` | PVDAQ + Open-Meteo 早期链路报告 |

## 参考论文

| Path | Purpose |
|---|---|
| `docs/references/papers/fenrg-12-1445092.pdf` | 参考论文 |
| `docs/references/papers/s00521-024-09923-4.pdf` | 参考论文 |
| `docs/references/papers/sustainability-14-17005-v2.pdf` | 参考论文 |

Pitfall: Stage16 总报告用于统一叙事和答辩材料，不替代各阶段原始指标产物；部署指南用于落地验收，不替代真实目标服务器上的部署记录。
