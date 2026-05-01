"""FastAPI 向后兼容入口。

模块设计原则：
- 主 API 服务已迁移至 backend.app，本模块仅作短期兼容垫片
- 保持 backend/ 与 frontend/ 的顶层目录分离
- 保留旧命令入口：uvicorn new_energy_sys.api.main:app

本模块对应项目的前端 API 服务入口，转发所有路由与依赖注入。
"""

from backend.app.main import (
    LoginRequest,
    TaskSubmitRequest,
    app,
    get_config,
    get_current_user,
    get_dispatch_metrics,
    get_dl_metrics,
    get_feature_report,
    get_features,
    get_governance,
    get_main_metrics,
    get_predictions,
    get_quality,
    get_rawhide_degradation_metrics,
    get_rawhide_dispatch_metrics,
    get_rawhide_report,
    get_rawhide_sensitivity_metrics,
    get_report_json,
    get_report_md,
    get_sensitivity,
    get_tabular_metrics,
    get_task_status,
    get_tcn_metrics,
    list_commands,
    list_reports,
    list_tasks_endpoint,
    login,
    me,
    submit_task,
)

__all__ = [
    "LoginRequest",
    "TaskSubmitRequest",
    "app",
    "get_config",
    "get_current_user",
    "get_dispatch_metrics",
    "get_dl_metrics",
    "get_feature_report",
    "get_features",
    "get_governance",
    "get_main_metrics",
    "get_predictions",
    "get_quality",
    "get_rawhide_degradation_metrics",
    "get_rawhide_dispatch_metrics",
    "get_rawhide_report",
    "get_rawhide_sensitivity_metrics",
    "get_report_json",
    "get_report_md",
    "get_sensitivity",
    "get_tabular_metrics",
    "get_task_status",
    "get_tcn_metrics",
    "list_commands",
    "list_reports",
    "list_tasks_endpoint",
    "login",
    "me",
    "submit_task",
]
