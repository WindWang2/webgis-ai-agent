"""G47 typed 错误（Dorling 圆面积 cartogram 作者）。

错误是**产品面**的一部分：每个 code 都对应一个用户可执行的下一步，而不是
"something went wrong"。路由把 code 映射到 HTTP 状态，Pi 工具把它原样放进
回执（模型据此改写计划，而不是盲目重试）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AuthoringError(Exception):
    """领域错误：code + 人类可读 message + 可执行修正建议。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        field: Optional[str] = None,
        correction_hint: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = int(status_code)
        self.field = field
        self.correction_hint = correction_hint
        self.details = details or {}


#: code → HTTP 状态（路由只做映射，不重复判断语义）。
_STATUS_BY_CODE = {
    "INVALID_PLAN": 422,
    "UNSUPPORTED_MODE": 422,
    "INVALID_QUANTITY_SPEC": 422,
    "INVALID_SOLVER_PARAMS": 422,
    "INVALID_QUALITY_GATE": 422,
    "NO_SESSION_MAPSPEC": 404,
    "UNKNOWN_LAYER": 404,
    "NOT_POLYGON_LAYER": 409,
    "SOURCE_NOT_FOUND": 409,
    "SOURCE_NOT_FEATURECOLLECTION": 409,
    "SOURCE_TOO_LARGE": 413,
    "NO_STRONG_REVISION": 409,
    "SOURCE_MUTATION_DURING_READ": 409,
    "STALE_SOURCE": 409,
    "STALE_PREVIEW": 409,
    "STALE_LAYOUT": 409,
    "CAS_SUPERSEDED": 409,
    "MUTATION_REJECTED": 422,
    "FIELD_NOT_FOUND": 422,
    "FIELD_NOT_NUMERIC": 422,
    "MISSING_QUANTITY": 422,
    "ZERO_QUANTITY": 422,
    "RATE_OR_MIXED_UNIT": 422,
    "DUPLICATE_FEATURE_ID": 422,
    "SCOPE_IDS_NOT_FOUND": 422,
    "MISSING_FEATURE_ID": 422,
    "INVALID_GEOMETRY": 422,
    "EMPTY_GEOMETRY": 422,
    "NO_VALID_QUANTITY": 422,
    "ZERO_TOTAL": 422,
    "EXTREME_VALUE_RANGE": 422,
    "UNKNOWN_CRS": 422,
    "GEOGRAPHIC_CRS_REQUIRED": 422,
    "UNSUPPORTED_PROJECTION": 422,
    "UNSUPPORTED_GEOGRAPHY": 422,
    "AXIS_ORDER_SUSPECT": 422,
    "BUDGET_EXCEEDED": 413,
    "SOLVER_NOT_CONVERGED": 409,
    "QUALITY_GATE_FAILED": 409,
    "LAYOUT_NOT_FOUND": 404,
    "LAYOUT_NOT_READY": 409,
    "JOB_NOT_FOUND": 404,
    "EXPORT_UNAVAILABLE": 503,
    "EXPORT_FAILED": 500,
}


def http_status(err: AuthoringError) -> int:
    return _STATUS_BY_CODE.get(err.code, err.status_code)


def error_payload(err: AuthoringError) -> Dict[str, Any]:
    """有界、可机读的错误回执（不含内部栈/路径/密钥）。"""
    payload: Dict[str, Any] = {
        "success": False,
        "code": err.code,
        "message": err.message,
        "status_code": http_status(err),
    }
    if err.field:
        payload["field"] = err.field
    if err.correction_hint:
        payload["correction_hint"] = err.correction_hint
    if err.details:
        payload["details"] = err.details
    return payload
