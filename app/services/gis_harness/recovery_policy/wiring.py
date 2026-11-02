"""TDS 单一挂接入口（GOAL 05 / M3，ADR-0279）。

``build_recovery_payload`` —— 在 ToolDispatchService 统一错误分支的
既有 ``harness_failure`` 之后调用：诊断 + 裁决 + LLM 单行提示。
本模块**零 IO、绝不抛出、绝不执行**；TDS 侧以 best_effort 包裹。

LLM note 词序固定（≤240 字符）：
``[recovery] reason=<r>; disposition=<d>; safe_to_try=<a,...>; needs_you=<u>``
—— 不含用户原文、不含跨会话 ID；needs_you ∈ {none,data,approval}。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.gis_harness.recovery_policy.contracts import (
    DiagnosticAttribution,
)
from app.services.gis_harness.recovery_policy.diagnose import (
    diagnose_tool_failure,
)
from app.services.gis_harness.recovery_policy.policy import decide_recovery

_MAX_NOTE = 240

_NEEDS_YOU_LABELS = {
    "needs_user_data": "data",
    "needs_user_approval": "approval",
    "unsafe_to_retry": "approval",
}


def recovery_llm_note(
    diagnostic: Optional[Dict[str, Any]],
    decision: Optional[Dict[str, Any]],
) -> str:
    """从 bounded payload 生成 LLM 单行提示（两引擎共用同一文案源）。"""
    if not isinstance(diagnostic, dict) or not diagnostic.get("reason"):
        return ""
    reason = str(diagnostic.get("reason"))[:48]
    disposition = str(diagnostic.get("disposition"))[:32]
    allowed = ",".join(
        str(a) for a in (decision or {}).get("allowed_actions", [])
    )[:64] or "none"
    needs = _NEEDS_YOU_LABELS.get(disposition, "none")
    budget = diagnostic.get("retry") or {}
    if budget and budget.get("retry_allowed") is False:
        allowed = "none (budget exhausted)"
    note = (
        f"[recovery] reason={reason}; disposition={disposition}; "
        f"safe_to_try={allowed}; needs_you={needs}"
    )
    return note[:_MAX_NOTE]


def build_recovery_payload(
    *,
    status: Optional[str] = None,
    code: Optional[str] = None,
    error_type: Optional[str] = None,
    message: Optional[str] = None,
    tool_name: str = "",
    harness_failure: Any = None,
    session_id: str = "",
    descriptor: Any = None,
    ref_origin_session: Optional[str] = None,
    turn_id: str = "",
    tool_call_id: str = "",
    capability: str = "",
    idempotency_key_present: bool = False,
) -> Dict[str, Any]:
    """诊断 + 裁决 + LLM note（单入口；异常 → 空 payload，绝不阻断）。"""
    try:
        attribution = DiagnosticAttribution(
            session_id=session_id, tool_name=tool_name,
            turn_id=turn_id, tool_call_id=tool_call_id,
            capability=capability)
        diag = diagnose_tool_failure(
            status=status, code=code, error_type=error_type,
            message=message, tool_name=tool_name,
            harness_failure=harness_failure, session_id=session_id,
            attribution=attribution, ref_origin_session=ref_origin_session)
        decision = decide_recovery(
            diag, descriptor=descriptor,
            idempotency_key_present=idempotency_key_present)
        diag_payload = diag.to_bounded_dict()
        dec_payload = decision.to_bounded_dict()
        return {
            "diagnostic": diag_payload,
            "decision": dec_payload,
            "llm_note": recovery_llm_note(diag_payload, dec_payload),
        }
    except Exception:  # noqa: BLE001 — 记录面绝不阻断 dispatch
        return {"diagnostic": None, "decision": None, "llm_note": ""}


__all__ = ["build_recovery_payload", "recovery_llm_note"]
