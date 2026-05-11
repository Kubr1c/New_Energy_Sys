# Anomaly Diagnosis Report

## Summary

- Total anomaly samples: 53 / 25358 total rows (0.21%)
- Anomaly hours as % of daytime hours: 0.49%

### Condition thresholds applied
| Condition | Threshold | Passed rows |
|-----------|-----------|-------------|
| solar_elevation > 10 | > 10 deg | 10736 |
| clearsky_ghi_wm2 > 500 | > 500 W/m2 | 6415 |
| ghi_wm2 > 250 | > 250 W/m2 | 8057 |
| clearsky_index_ghi > 0.5 | > 0.5 | 10927 |
| pv_power_kw < 0.0224 | < 0.0224 kW | 14542 |

### Anomaly sample GHI stats
| Metric | Value |
|--------|-------|
| Mean GHI | 534.9 W/m2 |
| Mean clearsky_ghi | 659.4 W/m2 |
| Mean CSI | 0.810 |
| Mean solar_elevation | 38.4 deg |
| Mean pv_power | 0.0042 kW |

## By Date (days with >= 3 anomaly hours)

| Date | Anomaly Hours | Mean GHI | Mean CSI | weather_fill_flag |
|------|---------------|----------|----------|-------------------|
| 2020-10-26 | 5 | 468.0 | 0.773 | 0.0 |
| 2020-02-04 | 4 | 492.5 | 0.811 | 0.0 |
| 2020-04-16 | 4 | 626.0 | 0.678 | 0.0 |
| 2021-06-11 | 4 | 1006.2 | 1.000 | 0.0 |
| 2022-01-27 | 3 | 413.0 | 0.732 | 0.0 |
| 2022-09-23 | 3 | 658.3 | 1.000 | 0.0 |

## Cross-tab with weather_fill_flag

| weather_fill_flag | Anomaly Count | % of Anomalies |
|-------------------|---------------|----------------|
| 0 | 49.0 | 92.5% |
| 11 | 2.0 | 3.8% |
| 18 | 1.0 | 1.9% |
| 36 | 1.0 | 1.9% |

## Cross-tab with cloud_type

| cloud_type | Label | Anomaly Count |
|------------|-------|---------------|
| 0 | Clear | 19 |
| 3 | Water | 1 |
| 4 | Supercooled Water | 6 |
| 6 | Opaque Ice | 1 |
| 7 | Cirrus | 22 |
| 8 | Overcast | 4 |

## Recommendation

- **总体占比低 (0.21%)**: 异常样本比例较低，不需要特殊处理，模型会自动学习忽略这些稀疏噪声。

- **综合判断**: 异常样本散布在多个日期，无明显集中模式，且比例极低。建议暂不处理，继续监控。
