"""G33 错误面：领域错误码 → HTTP 状态（对齐 raster_authoring.errors 模式）。"""
from __future__ import annotations

from typing import Any, Dict, Optional


class ThematicAuthoringError(Exception):
    """专题作者域错误。``code`` 是稳定机读码；``field`` 指向出错的断点/类别索引。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: Optional[str] = None,
        index: Optional[int] = None,
        status_code: int = 422,
        correction_hint: str = "",
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.index = index
        self.status_code = status_code
        self.correction_hint = correction_hint
        self.detail = detail or {}

    def payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.field is not None:
            out["field"] = self.field
        if self.index is not None:
            out["index"] = self.index
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        if self.detail:
            out["detail"] = self.detail
        return out


def http_status(err: ThematicAuthoringError) -> int:
    return err.status_code


def error_payload(err: ThematicAuthoringError) -> Dict[str, Any]:
    return err.payload()
