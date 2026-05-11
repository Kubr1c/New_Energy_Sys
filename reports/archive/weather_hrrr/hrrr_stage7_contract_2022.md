# HRRR Stage7 数据契约校验报告

- 判定: `allow_stage7_rerun`
- 原因: HRRR forecast weather satisfies schema, time alignment, physical range, and irradiance gates.

## 门禁

| 门禁 | 结果 | 关键细节 |
|---|---:|---|
| `required_columns` | `True` | `{"missing_columns": []}` |
| `timestamp_coverage` | `True` | `{"coverage_ratio": 0.9982876712328768, "expected_rows": 8760, "observed_rows": 8745, "duplicate_count": 0, "missing_timestamps": ["2022-01-07 09:00:00+00:00", "2022-01-07 10:00:00+00:00", "2022-01-07 11:00:00+00:00", ...` |
| `audit_missing_timestamps` | `True` | `{"audit_status": "completed_with_missing", "audit_missing_count": 15, "calculated_missing_count": 15, "calculated_missing_timestamps": ["2022-01-07 09:00:00+00:00", "2022-01-07 10:00:00+00:00", "2022-01-07 11:00:00+00...` |
| `numeric_physical_ranges` | `True` | `{"violations": {}}` |
| `grid_distance` | `True` | `{"site_latitude": 39.7404, "site_longitude": -105.1774, "max_latitude_delta": 0.004535270049061069, "max_longitude_delta": 0.016208059429146715, "max_allowed_delta": 0.05}` |
| `dswrf_source_trace` | `True` | `{"empty_source_rows": 0, "missing_dswrf_source_rows": 0, "ghi_max_wm2": 1099.0, "source_path_count": 18860}` |
| `precipitation_semantics` | `True` | `{"errors": [], "ok_attempts": 8745, "parquet_rows": 8745, "transform": "accumulated_to_hourly_diff", "missing_transform_examples": [], "negative_clipped_examples": [], "max_hourly_precipitation_mm": 12.078125}` |
| `ghi_distribution` | `True` | `{"overlap_rows": 8719, "nsrdb_daytime_rows": 3895, "hrrr_daytime_nonzero_rate": 0.9761232349165597, "hrrr_ghi_max_wm2": 1099.0, "min_required_nonzero_rate": 0.85, "min_required_max_wm2": 500.0}` |
| `feature_reasonableness_rate` | `True` | `{"reasonable_row_rate": 1.0, "min_required_reasonable_row_rate": 0.95, "flagged_rows": 0, "total_rows": 8745, "examples": []}` |
| `stage7_issue_time_alignment` | `True` | `{"horizons": {"target_plus_6h": {"joined_rows": 8701, "leakage_rows": 0, "lead_time_missing_rows": 0, "passed": true}, "target_plus_24h": {"joined_rows": 8719, "leakage_rows": 0, "lead_time_missing_rows": 0, "passed":...` |

## Pitfall

GHI/DSWRF 是光伏预测的关键输入，不能用 0 值静默替代缺失；该门禁失败时必须重新抽取 HRRR。
