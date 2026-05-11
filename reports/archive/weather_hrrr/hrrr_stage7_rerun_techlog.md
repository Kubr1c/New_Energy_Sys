# HRRR Stage7 合同校验与重跑技术日志

**日期**: 2026-05-03
**操作人**: Bow1e
**分支**: feature/hrrr-2022-ghi-fix

---

## 一、背景

2022 HRRR GHI 全零问题已修复（GRIB2 DSWRF 回退），2021 数据一直有效。需要对两个年份运行 Stage7 数据合同校验，验证 HRRR 预报数据满足 Stage7 集成要求，然后用 HRRR 数据重跑 Stage7 替换原有的 Open-Meteo 预报。

## 二、Step 1: Stage7 合同校验

### 运行命令

```bash
python -m new_energy_sys.cli.validate_hrrr_stage7_contract \
  --config configs/data_sources.pvdaq_nsrdb_2020_2022.json \
  --stage2-input data/processed/pvdaq_nsrdb_2020_2022/stage2_cleaned_hourly_dataset.parquet \
  --stage3-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --hrrr-weather <year_parquet> \
  --hrrr-audit <year_audit> \
  --output-json reports/hrrr_stage7_contract_<year>.json \
  --output-md reports/hrrr_stage7_contract_<year>.md
```

### 结果

| 门禁 | 2021 | 2022 |
|------|------|------|
| required_columns | ✅ | ✅ |
| timestamp_coverage | ✅ (100%) | ✅ (99.83%) |
| audit_missing_timestamps | ✅ | ✅ |
| numeric_physical_ranges | ✅ | ✅ |
| grid_distance | ✅ | ✅ |
| dswrf_source_trace | ✅ (1062 W/m²) | ✅ (1099 W/m²) |
| precipitation_semantics | ✅ | ✅ |
| ghi_distribution | ✅ (97.7%) | ✅ (97.6%) |
| feature_reasonableness_rate | ✅ (100%) | ✅ (100%) |
| stage7_issue_time_alignment | ✅ | ✅ |

**判定**: 两个年份均为 `allow_stage7_rerun`

### 产物

- `reports/hrrr_stage7_contract_2021.json`
- `reports/hrrr_stage7_contract_2021.md`
- `reports/hrrr_stage7_contract_2022.json`
- `reports/hrrr_stage7_contract_2022.md`

---

## 三、Step 2: Stage7 HRRR 重跑

### 前置操作

1. **备份**: 原有 Stage7 (Open-Meteo) 产物已备份至 `data/processed/pvdaq_nsrdb_2020_2022/_backup_20260503_stage7_openmeteo/`
2. **合并 HRRR**: 将 2021 (8760 行) + 2022 (8745 行) 合并为 `stage7_hrrr_forecast_weather_2021_2022_f24.parquet` (17,505 行)
3. **环境准备**: 创建 `.venv`，安装 lightgbm, scikit-learn, torch, pandas, pyarrow

### 运行命令

```bash
python -m new_energy_sys.cli.run_stage7 \
  --config configs/data_sources.pvdaq_nsrdb_2020_2022.json \
  --stage3-input data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet \
  --forecast-weather data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_2021_2022_f24.parquet
```

### HRRR vs Open-Meteo 字段差异

HRRR 缺少以下 Open-Meteo 预报字段：
- `dhi_wm2`, `dni_wm2` (散射/直射辐照)
- `wind_gusts_ms` (阵风)
- `cloud_cover_low_pct`, `cloud_cover_mid_pct`, `cloud_cover_high_pct` (分层云量)

Stage7 代码自动适配，仅使用 HRRR 存在的 9 个预报字段。

### 结果对比

| 指标 | Open-Meteo (原) | **HRRR (新)** | 变化 |
|------|----------------|--------------|------|
| 输入行数 | ~26,280 (2020-2022) | **17,445** (2021-2022) | -33.6% |
| TCN nRMSE | 0.1398 | **0.1236** | **-11.6%** |
| TCN 日间 nRMSE | 0.2124 | **0.1807** | **-14.9%** |
| 质量门禁 | 8/8 | 8/8 | — |
| nRMSE <= 0.1225 | ❌ | ❌ (差 0.0011) | — |
| 日间 nRMSE <= 0.1689 | ❌ | ❌ | — |

### 关键发现

1. **HRRR 显著优于 Open-Meteo**: nRMSE 降低 11.6%，日间降低 14.9%
2. **距验收线仅差 0.0011**: HRRR nRMSE 0.1236 vs 阈值 0.1225。如果补充缺失的 GHI 散射分量或有更多训练数据，可能达到验收线
3. **行数减少**: 2020 年无 HRRR 数据，17,445 行 vs 原始 26,280 行
4. **Stage5 LightGBM 仍为最优**: nRMSE 0.0789，HRRR TCN 未能超越

### 文件清单

**新增文件**:
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_2021_2022_f24.parquet` — 合并的 HRRR 预报天气
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_feature_dataset_hrrr_2021_2022.parquet` — HRRR Stage7 特征集
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_forecast_weather_dataset_hrrr_2021_2022.parquet` — HRRR 预报有效时间表
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_tcn_metrics_hrrr_2021_2022.csv` — TCN 指标
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_tcn_predictions_hrrr_2021_2022.csv` — TCN 预测
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_forecast_validation_report_hrrr_2021_2022.json` — 报告 JSON
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_forecast_validation_report_hrrr_2021_2022.md` — 报告 MD
- `data/processed/pvdaq_nsrdb_2020_2022/stage7_tcn_models_hrrr_2021_2022/` — TCN 模型

**备份目录**:
- `data/processed/pvdaq_nsrdb_2020_2022/_backup_20260503_stage7_openmeteo/` — 原始 Stage7 (Open-Meteo) 产物

**原始文件**: 已恢复至原路径，未改动。

---

## 四、结论与建议

1. **HRRR 数据质量合格**: 两个年份通过全部 10 道 Stage7 合同门禁
2. **HRRR 预报能力优于 Open-Meteo**: TCN nRMSE 降低 11.6%
3. **当前暂不推进 TCN 生产**: nRMSE 未达到验收线（差 0.0011），且 LightGBM 仍为最优
4. **后续可能方向**:
   - 补充 2020 年 HRRR 数据（需解决 NOAA 数据缺口问题）
   - 尝试多站点扩展以提高 TCN 泛化能力
   - 将 HRRR 数据用于 LightGBM 模型（而非仅 TCN）

## 五、Pitfall

- Stage7 报告模板硬编码了 "Open-Meteo f24" 数据源描述，HRRR 重跑后报告文字未更新。实际使用的是 NOAA HRRR f24 数据。
- TCN 训练轮次为 20 epochs，可能不充分。若增加 epochs 或调整学习率，HRRR 可能达到验收线。
