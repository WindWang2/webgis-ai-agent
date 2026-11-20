"""G46 领域错误面：稳定机读码 + 逐字段定位 + HTTP 状态。

与 G38/G41 错误面同形状（``{code,message,field,index,correction_hint,detail}``），
便于 Pi 工具与 UI 共用。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class LinkedExplorationError(Exception):
    """联合探索域错误。``field`` 指向具体条件/图表/视图字段。"""

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
        out: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            out["field"] = self.field
        if self.index is not None:
            out["index"] = self.index
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        if self.detail:
            out["detail"] = self.detail
        return out


def http_status(err: LinkedExplorationError) -> int:
    return err.status_code


def error_payload(err: LinkedExplorationError) -> Dict[str, Any]:
    return err.payload()
