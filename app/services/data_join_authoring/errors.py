"""G38 typed 错误 —— 与 G31 WorkbenchError 同款 fail-closed 语义。

未知错误码在构造时抛 ValueError（词表漂移即刻暴露）；HTTP 层与工具层
共用 ``payload()`` 的稳定 machine code + 中文 correction_hint。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.data_join_authoring.contracts import ERROR_CODES

#: code → HTTP 状态（G31 _map_error 同款；缺省 400）。
_HTTP_STATUS: Dict[str, int] = {
    "not_owned": 404,
    "run_not_found": 404,
    "stale_plan_revision": 409,
    "stale_source": 409,
    "budget_exceeded": 413,
    "receipt_missing": 410,
    "result_unavailable": 409,
    "run_not_active": 409,
    "source_unresolvable": 410,
    "render_ref_over_budget": 413,
    "internal_error": 500,
}

_HINTS: Dict[str, str] = {
    "cardinality_violation": "诊断显示键基数与声明不符：改声明（如 many_to_one）或先清洗重复键后重试",
    "needs_decision": "存在同值/空值的择一候选：显式指定 tie-break 字段或改用 expand_all/aggregate",
    "null_policy_unsupported": "本版仅支持 nulls never_match（null/missing 双侧不参与匹配）",
    "field_mapping_conflict": "输出字段重名或试图覆盖保留字段：为冲突字段显式指定别名",
    "stale_source": "源内容在预览后发生变化：重新预览后再执行，或把源固化为项目产物修订",
    "render_ref_over_budget": "输出行数超过地图渲染投影上限：用导出交付（G18）获取完整数据",
    "budget_exceeded": "输入/候选对/输出规模超出预算：缩小范围或提高计划内预算后重试",
}


class DataJoinError(Exception):
    """域内 typed 错误。code 必须在 ERROR_CODES 内（fail-closed）。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: Optional[int] = None,
        correction_hint: Optional[str] = None,
        **detail: Any,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown data-join error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status or _HTTP_STATUS.get(code, 400)
        self.correction_hint = correction_hint or _HINTS.get(code)
        self.detail: Dict[str, Any] = {k: v for k, v in detail.items() if v is not None}

    def payload(self) -> Dict[str, Any]:
        """HTTP detail / 工具回执共用的稳定载荷。"""
        out: Dict[str, Any] = {
            "error_code": self.code,
            "message": self.message,
        }
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        if self.detail:
            out["detail"] = self.detail
        return out


def http_status(exc: DataJoinError) -> int:
    return exc.status
