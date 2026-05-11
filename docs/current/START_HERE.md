# 当前项目阅读入口

## 1. 项目当前主线

新能源储能侧优化调度系统当前主线为：公开数据整理、光伏功率预测、储能滚动调度、电池退化修正、Rawhide 公开容量参数参照场景、多收益情景净增量分析和结果展示。

## 2. 当前核心结论

- LightGBM 是当前稳定主预测模型，深度学习序列模型用于对比实验。
- 储能调度评价应以退化后净增量为核心指标。
- Rawhide 相关结果属于公开容量参数参照仿真。
- 基准代理电价下，单一套利难以覆盖退化成本。
- 在价格波动增强、容量价值叠加或电池经济性改善情景下，可以形成正净增量场景。
- 两阶段策略蒸馏用于近似滚动调度首步动作，仍需物理约束回放。

## 3. 默认阅读顺序

1. `docs/current/START_HERE.md`
2. `docs/current/CURRENT_EVIDENCE.md`
3. `docs/current/CODE_READING_MAP.md`
4. `reports/stage23_scenario_dispatch_showcase_report_2026-05-11.md`
5. `reports/stage22b_economic_sensitivity_report_2026-05-11.md`
6. `reports/stage22_degradation_aware_config_report_2026-05-10.md`
7. `reports/task_report_policy_distillation_replay_2026-05-10.md`

## 4. 不作为默认阅读材料的内容

HRRR 扩展过程、早期阶段报告、前端演示视频、DOCX 格式修复记录、临时诊断日志和旧论文草稿均归档处理。需要追溯时再进入 `reports/archive/` 或 `docs/archive/`。
