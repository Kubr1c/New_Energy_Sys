# Reports Index

This file is the report-entry anchor. It does not replace `PROGRESS.md`; it tells the next tool which report to read and whether that report is current, baseline, reference, draft, or superseded.

Status legend:

- `CURRENT`: use as a current source of truth.
- `BASELINE`: valid comparison baseline, but not the latest conclusion.
- `REFERENCE`: supporting context or method documentation.
- `DRAFT`: useful for writing, but must be checked against current artifacts.
- `SUPERSEDED`: historical record; do not use as the latest conclusion.
- `LEGACY`: old route or old experiment family retained for traceability.

## Start Here

| Status | Path | Purpose |
|---|---|---|
| CURRENT | `PROGRESS.md` | Project-wide progress anchor and current task source. Read this first after switching tools. |
| CURRENT | `reports/project_status_2026-05-05.md` | One-page current status summary for thesis/report integration. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage20b_two_stage_policy_report.md` | Latest dispatch-side deep-learning result: two-stage neural policy distillation. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage16_final_integration_report.md` | Integrated system narrative up to Stage18/Stage19-era delivery; still useful, but Stage20B must be added when writing. |

## Canonical Current Reports

| Status | Path | Purpose |
|---|---|---|
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_report.md` | Stable LightGBM `history_only` t+24h main prediction artifact. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage14_deep_learning_report.md` | Latest deep-learning prediction comparison after rerun; use as prediction-side DL evidence. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage12_storage_rolling_optimization_report.md` | Rolling optimization dispatch baseline and physical-settlement reference. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage15_storage_configuration_sensitivity_report.md` | Storage capacity/objective sensitivity and Pareto-style comparison. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage17_battery_degradation_report.md` | Battery degradation, rainflow cycle counting, SOH, and net-value adjustment. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage18_rawhide_simulation_report.md` | Rawhide public-parameter reference simulation; not measured Rawhide generation. |
| CURRENT | `data/processed/pvdaq_nsrdb_2020_2022/stage20b_two_stage_policy_report.md` | Latest neural dispatch policy result; use before the older Stage20 regression MLP report. |

## Baselines And Comparisons

| Status | Path | Purpose |
|---|---|---|
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage4_lightgbm_report.md` | Early LightGBM baseline. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage5_optimization_report.md` | LightGBM diagnostics, ablation, and tuning baseline. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage6_tcn_report.md` | Early TCN sequence-model report. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage8_tabular_model_report.md` | Tabular model comparison that selected LightGBM as stable main model. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage10_storage_dispatch_report.md` | Fixed-threshold storage-dispatch baseline. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage11_storage_strategy_sensitivity_report.md` | Threshold strategy sensitivity baseline. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage13_storage_strategy_governance_report.md` | Strategy-governance dashboard/report baseline. |
| BASELINE | `reports/technical_report_2026-05-04_stage20_dispatch_dl.md` | Stage20 regression MLP audit baseline; superseded as latest neural policy by Stage20B. |
| BASELINE | `data/processed/pvdaq_nsrdb_2020_2022/stage20_neural_dispatch_report.md` | Machine-generated Stage20 regression MLP report; use only as baseline. |

## Supplemental / Research Reports

| Status | Path | Purpose |
|---|---|---|
| REFERENCE | `reports/hrrr_2021_2022_comprehensive_audit.md` | HRRR 2021/2022 f24 extraction and audit; 2020 blocked by strict probe. |
| REFERENCE | `reports/hrrr_2022_f24_validation_report.md` | 2022 HRRR f24 validation and known 2022-01-07 gap. |
| REFERENCE | `data/processed/pvdaq_nsrdb_2020_2022/stage7_forecast_validation_report_hrrr_2021_2022.md` | Stage7 HRRR feature validation artifact. |
| REFERENCE | `reports/technical_report_2026-05-03_irradiance_and_hrrr_pv_pipeline.md` | Irradiance decomposition and HRRR PV pipeline technical note. |
| REFERENCE | `reports/technical_report_2026-05-04_feature_enhancement_and_roadmap.md` | Feature enhancement and roadmap note; follow `PROGRESS.md` for current next task. |
| REFERENCE | `reports/technical_report_2026-05-04_csi_and_quantile.md` | CSI target, quantile prediction, and gating experiments; supplemental, not the main forecast chain. |
| REFERENCE | `reports/stratified_eval_report.md` | Stratified PV prediction evaluation by clear/mixed/overcast conditions. |
| REFERENCE | `reports/pv_prediction_false_high_full_scope_diagnostic_and_fix_plan.md` | Historical false-high prediction diagnosis and fix plan. |

## Frontend / Deployment

| Status | Path | Purpose |
|---|---|---|
| CURRENT | `docs/production_deployment_guide.md` | Production deployment and acceptance guide. |
| CURRENT | `docs/frontend_production_handover.md` | Frontend production-hardening handover anchor. |
| CURRENT | `docs/frontend_walkthrough.md` | Frontend walkthrough and demo-page explanation. |
| REFERENCE | `docs/startup_troubleshooting.md` | Local startup and troubleshooting notes. |
| REFERENCE | `docs/battery_simulation_method.md` | Stage17 battery simulation method explanation. |

## Thesis / Writing References

| Status | Path | Purpose |
|---|---|---|
| DRAFT | `reports/深度学习储能优化调度研究.md` | Research and implementation bluebook; check against current Stage20B before using. |
| DRAFT | `reports/新能源储能侧优化调度系统毕业论文初稿.docx` | Thesis draft. |
| DRAFT | `reports/新能源储能侧优化调度系统毕业论文初稿_降AIGC修改报告.md` | AIGC reduction/editing report. |
| REFERENCE | `reports/6.上海电力大学本科生毕业设计（论文）撰写规范.docx` | School thesis-writing requirement. |
| REFERENCE | `reports/14.上海电力大学本科生毕业设计（论文）格式示范文本.docx` | School thesis format sample. |
| REFERENCE | `reports/7.上海电力大学毕业设计（论文）封面.doc` | Thesis cover template. |

## Legacy / Superseded Reports

| Status | Path | Purpose |
|---|---|---|
| LEGACY | `reports/stage1_stage2_progress_report.md` | Early Stage1-2 data-processing progress. |
| LEGACY | `reports/stage1_stage2_stage3_weather_progress_report.md` | Early NREL/OPSD/weather route report. |
| LEGACY | `reports/pvdaq_nsrdb_progress_report.md` | Early PVDAQ + NSRDB route report. |
| LEGACY | `reports/pvdaq_openmeteo_stage1_stage3_progress_report.md` | Early PVDAQ + Open-Meteo route report. |
| LEGACY | `reports/stage7_forecast_weather_progress_report.md` | Stage7 management summary; useful history, not current next task. |
| LEGACY | `data/processed/pvdaq_nsrdb_2020_2022/stage14b_training_report.md` | Older Stage14B training report; current Stage14 report is preferred. |
| SUPERSEDED | `reports/anomaly_diagnosis.md` | Old anomaly diagnosis. |
| SUPERSEDED | `reports/hrrr_2022_ghi_fix_plan.md` | HRRR 2022 GHI fix plan superseded by later HRRR audits. |
| SUPERSEDED | `reports/pvdaq_time_axis_stage2_9_rebuild_review_plan.md` | Time-axis repair plan; keep only as historical context. |

## Operating Rule

When a report conflicts with `PROGRESS.md`, prefer `PROGRESS.md`. When two reports conflict, prefer the one marked `CURRENT`; if both are current, prefer the newer generated artifact and record the conflict before editing or writing thesis text.

Pitfall: this index is not a report body. Do not copy large metric tables here; keep detailed metrics in their original stage reports.
