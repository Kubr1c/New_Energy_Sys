"""认证模块向后兼容导入垫片。

模块设计原则：
- 实际认证逻辑位于 backend.app.auth
- 本模块仅重导出 UserInfo / authenticate / create_token / verify_token

本模块对应项目的用户认证与令牌管理功能。
"""

from backend.app.auth import UserInfo, authenticate, create_token, verify_token

__all__ = ["UserInfo", "authenticate", "create_token", "verify_token"]
