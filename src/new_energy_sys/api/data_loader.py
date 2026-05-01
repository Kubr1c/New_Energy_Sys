"""数据加载模块向后兼容导入垫片。

模块设计原则：
- 实际数据加载逻辑位于 backend.app.data_loader
- 本模块仅重导出所有数据读取、质量报告、指标查询函数

本模块对应项目的产物数据读取与 API 展示数据供给功能。
"""

from backend.app.data_loader import (
    data_dir,
    get_data_quality_report,
    get_deep_learning_metrics,
    get_dispatch_metrics,
    get_feature_importance,
    get_feature_report,
    get_governance_scorecard,
    get_main_model_metrics,
    get_main_model_predictions,
    get_rawhide_degradation_metrics,
    get_rawhide_dispatch_metrics,
    get_rawhide_report,
    get_rawhide_sensitivity_metrics,
    get_sensitivity_metrics,
    get_site_config,
    get_stage_report_json,
    get_stage_report_md,
    get_tabular_model_metrics,
    get_tcn_metrics,
    list_available_stages,
    project_root,
    read_csv_cached,
    read_json_cached,
    read_markdown_cached,
    read_parquet_sample,
)

__all__ = [
    "data_dir",
    "get_data_quality_report",
    "get_deep_learning_metrics",
    "get_dispatch_metrics",
    "get_feature_importance",
    "get_feature_report",
    "get_governance_scorecard",
    "get_main_model_metrics",
    "get_main_model_predictions",
    "get_rawhide_degradation_metrics",
    "get_rawhide_dispatch_metrics",
    "get_rawhide_report",
    "get_rawhide_sensitivity_metrics",
    "get_sensitivity_metrics",
    "get_site_config",
    "get_stage_report_json",
    "get_stage_report_md",
    "get_tabular_model_metrics",
    "get_tcn_metrics",
    "list_available_stages",
    "project_root",
    "read_csv_cached",
    "read_json_cached",
    "read_markdown_cached",
    "read_parquet_sample",
]
