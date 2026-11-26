"""Data Fusion typed 错误 —— 与 G38 DataJoinError 同款 fail-closed 语义。

未知错误码在构造时抛 ValueError（词表漂移即刻暴露）；HTTP 层与工具层
共用 ``payload()`` 的稳定 machine code + 中文 correction_hint。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.data_fusion_authoring.contracts import ERROR_CODES

#: code → HTTP 状态（缺省 400）。
_HTTP_STATUS: Dict[str, int] = {
    "not_owned": 404,
    "run_not_found": 404,
    "decision_not_found": 404,
    "stale_plan_revision": 409,
    "stale_review_revision": 409,
    "stale_source": 409,
    "budget_exceeded": 413,
    "receipt_missing": 410,
    "result_unavailable": 409,
    "run_not_active": 409,
    "source_unresolvable": 410,
    "render_ref_over_budget": 413,
    "commit_blocked": 409,
    "refresh_not_safe": 409,
    "internal_error": 500,
}

_HINTS: Dict[str, str] = {
    "weights_invalid": "证据权重必须恰好覆盖 external_id/name/address/category/distance、非负且和为 1（0 权重需显式声明）",
    "threshold_invalid": "阈值关系要求：review_min ≤ auto_match_min ≤ 1.0，ambiguous_margin ∈ [0, 0.5]",
    "field_mapping_conflict": "输出字段重名或使用保留前缀 _fusion：为冲突字段显式指定别名",
    "stale_source": "源内容在预览后发生变化：重新预览后再执行，或把源固化为项目产物修订",
    "entity_kind_unsupported": "源几何形态与声明实体种类不符（point_poi 需点几何；area/line 需对应几何）",
    "crs_unresolvable": "两侧源需为可比坐标帧（EPSG:4326/CRS84 或已声明投影）；先用 G45 坐标操作统一帧",
    "commit_blocked": "存在未决审阅项：完成审阅后提交，或把计划提交政策改为 allow_unresolved（未决项按单源输出并披露）",
    "refresh_not_safe": "来源变更面过大：增量重配不安全，请执行全量重算（预算内自动生成新 run）",
    "budget_exceeded": "输入/候选对/输出规模超出预算：缩小范围或提高计划内预算后重试",
}


class DataFusionError(Exception):
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
            raise ValueError(f"unknown data-fusion error code: {code!r}")
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


def http_status(exc: DataFusionError) -> int:
    return exc.status
