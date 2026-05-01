"""任务管理模块向后兼容导入垫片。

模块设计原则：
- 实际任务逻辑位于 backend.app.tasks
- 本模块仅重导出 TaskRecord / get_task / list_available_commands / list_tasks / submit_task

本模块对应项目的异步任务提交与状态查询功能。
"""

from backend.app.tasks import (
    TaskRecord,
    get_task,
    list_available_commands,
    list_tasks,
    submit_task,
)

__all__ = [
    "TaskRecord",
    "get_task",
    "list_available_commands",
    "list_tasks",
    "submit_task",
]
