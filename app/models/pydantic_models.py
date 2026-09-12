"""
Pydantic 数据模型（用于 API 请求/响应验证）
"""

from typing import Optional, Any
from pydantic import BaseModel


# ==================== 权限相关 ====================

class PermissionCheck(BaseModel):
    """权限检查请求"""
    user_id: str  # DB: users.id is String(255) (UUID)
    resource_type: str
    resource_id: int
    required_permission: str


class PermissionResponse(BaseModel):
    """权限检查响应"""
    allowed: bool
    role: Optional[str] = None


# ==================== 通用响应 ====================

class SuccessResponse(BaseModel):
    """通用成功响应"""
    success: bool = True
    message: str = "操作成功"
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """通用错误响应"""
    success: bool = False
    message: str
    code: Optional[str] = None
