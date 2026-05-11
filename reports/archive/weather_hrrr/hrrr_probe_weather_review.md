# HRRR 严格 Probe 气象数据审查报告

- 判定: `allow_human_review`
- 机器校验通过: `True`
- 原因: HRRR probe passed strict machine gates; review the weather feature plot before approving full extraction.
- 可视化图表: `C:\Project\New_Energy_Sys\reports\figures\hrrr_probe_weather_features.png`
- 小时级分窗口图表: `C:\Project\New_Energy_Sys\reports\figures\hrrr_probe_weather_features_window_01_20220115.png`, `C:\Project\New_Energy_Sys\reports\figures\hrrr_probe_weather_features_window_02_20220415.png`, `C:\Project\New_Energy_Sys\reports\figures\hrrr_probe_weather_features_window_03_20220715.png`, `C:\Project\New_Energy_Sys\reports\figures\hrrr_probe_weather_features_window_04_20221015.png`

## 门禁结果

| 门禁 | 结果 | 关键细节 |
|---|---:|---|
| `required_columns` | `True` | `{"missing_columns": []}` |
| `timestamp_alignment` | `True` | `{"expected_rows": 192, "observed_rows": 192, "duplicate_count": 0, "missing_timestamps": [], "unexpected_timestamps": []}` |
| `audit_traceability` | `True` | `{"errors": [], "audit_status": "completed", "audit_missing_timestamps": []}` |
| `numeric_physical_ranges` | `True` | `{"violations": {}, "summaries": {"cloud_cover_pct": {"null_count": 0, "min": 0.0, "max": 100.0, "nonzero_rate": 0.71875}, "ghi_wm2": {"null_count": 0, "min": 0.0, "max": 1001.0, "nonzero_rate": 0.4895833333333333}, "p...` |
| `precipitation_semantics` | `True` | `{"errors": [], "ok_attempts": 192, "parquet_rows": 192, "transform": "accumulated_to_hourly_diff", "missing_transform_examples": [], "negative_clipped_examples": [], "max_hourly_precipitation_mm": 1.3896484375}` |
| `grid_distance` | `True` | `{"site_latitude": 39.7404, "site_longitude": -105.1774, "max_latitude_delta": 0.004535270049061069, "max_longitude_delta": 0.016208059429146715, "max_allowed_delta": 0.05}` |
| `dswrf_source_trace` | `True` | `{"empty_source_rows": 0, "missing_dswrf_source_rows": 0, "ghi_max_wm2": 1001.0, "source_path_count": 416}` |
| `issue_time_and_lead` | `True` | `{"invalid_rows": 0, "leakage_rows": 0, "lead_mismatch_rows": 0, "min_lead_hour": 24.0, "max_lead_hour": 29.0}` |
| `ghi_distribution` | `True` | `{"overlap_rows": 192, "nsrdb_daytime_rows": 86, "hrrr_daytime_nonzero_rate": 0.9767441860465116, "summer_hrrr_ghi_max_wm2": 1001.0, "min_required_nonzero_rate": 0.85, "min_required_summer_max_wm2": 500.0}` |
| `feature_reasonableness_rate` | `True` | `{"reasonable_row_rate": 1.0, "min_required_reasonable_row_rate": 0.95, "flagged_rows": 0, "total_rows": 192, "examples": []}` |

## Pitfall

Probe 通过只代表抽取链路和字段物理形态可信，不能替代全量年度合约；全量抽取后仍必须运行 Stage7 年度合约。
