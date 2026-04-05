"""
API Response 统一格式定义
"""
from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel

T = TypeVar("T")

class ApiResponse(BaseModel):
    """
    统一 API 响应格式
    
    无论成功还是失败，都返回此格式：
    {
        "code": "SUCCESS" | "ERROR_CODE",
        "success": true | false,
        "message": "提示信息",
        "data": {...}
    }
    
    示例：
    - 成功: {"code": "SUCCESS", "success": true, "message": "操作成功", "data": {...}}
    - 失败: {"code": "VALIDATE_ERROR", "success": false, "message": "参数错误", "data": null}
    """
    code: str = "SUCCESS"
    success: bool = True
    message: str = ""
    data: Optional[Any] = None
    
    # 便捷构造方法
    @classmethod
    def ok(cls, data=None, message: str = "操作成功"):
        return cls(code="SUCCESS", success=True, message=message, data=data)
    
    @classmethod
    def fail(cls, code: str = "SERVER_ERROR", message: str = "操作失败", data=None):
        return cls(code=code, success=False, message=message, data=data)

# 常用错误码
class ErrCode:
    """错误码常量"""
    VALIDATE_ERROR = "VALIDATE_ERROR"      # 参数校验失败
    NOT_FOUND = "NOT_FOUND"            # 资源不存在
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 权限不足
    SERVER_ERROR = "SERVER_ERROR"      # 服务端错误
    TASK_FAILED = "TASK_FAILED"        # 任务执行失败
    TIMEOUT = "TIMEOUT"              # 超时