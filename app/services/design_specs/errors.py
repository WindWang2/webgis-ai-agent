"""Design Specs —— typed errors（路由/工具映射 4xx；同 templates.versioning 纪律）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class DesignSpecError(Exception):
    """设计规范操作非法 —— 路由层按 ``http_status`` 映射。"""

    http_status = 400

    def __init__(self, message: str, *, code: str = "DESIGN_SPEC_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def error_payload(self) -> Dict[str, Any]:
        """结构化回执（工具 _guard / REST detail 共用形状）。"""
        return {
            "success": False,
            "code": self.code,
            "message": self.message,
        }


class DesignSpecNotFoundError(DesignSpecError):
    http_status = 404

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DESIGN_SPEC_NOT_FOUND")


class DesignSpecForbiddenError(DesignSpecError):
    http_status = 403

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DESIGN_SPEC_FORBIDDEN")


class DesignSpecConflictError(DesignSpecError):
    """命名查重冲突 / 并发版本冲突 —— 409。"""

    http_status = 409

    def __init__(self, message: str, *, code: str = "DESIGN_SPEC_CONFLICT") -> None:
        super().__init__(message, code=code)


class DesignSpecValidationError(DesignSpecError):
    """payload 校验失败（schema / 引用 / 防泄漏）—— 400 + 结构化 violations。"""

    def __init__(
        self,
        message: str,
        *,
        violations: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        super().__init__(message, code="DESIGN_SPEC_INVALID")
        self.violations = list(violations or [])

    def error_payload(self) -> Dict[str, Any]:
        payload = super().error_payload()
        payload["violations"] = self.violations[:32]
        return payload


__all__ = [
    "DesignSpecError",
    "DesignSpecNotFoundError",
    "DesignSpecForbiddenError",
    "DesignSpecConflictError",
    "DesignSpecValidationError",
]
