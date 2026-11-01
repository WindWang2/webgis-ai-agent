"""多轮恢复指令（GOAL 05 / M5，ADR-0279）。

用户后续说「重试上一个失败步骤 / 换数据源 / 换表达」时，把已落账的
``plan.last_recovery`` + 能力行状态折叠成一个**只读重试指令**：

- 已验证（complete）能力行与 ref **保持**，绝不重置；
- 仅 failed/unavailable 行进入 retry 目标（pending 属正常未执行，不算）；
- 有 workflow 契约的章节指向既有 ``webgis_analysis_workflow_recompute``
  选择性重算 seam（#1869 —— 失败子图由指纹闭包裁决，本层不建调度）；
- 用户提到的 ref：归属他 session 或**不可证明** → fail-closed（绝不猜）；
- 预算耗尽 / terminal 诊断 → 不给重试目标（needs_user 披露）。

纯函数、有界、绝不抛出。执行永远归 agent（投影/LLM 提示）+ 既有工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

_MAX_ITEMS = 8
_MAX_LINE = 240

#: 失败但可重试的能力行状态（pending = 尚未执行，不算失败）。
_RETRYABLE_ROW_STATUSES = frozenset({"failed", "unavailable"})

_VERDICT_OPEN = "retry_failed_subgraph"
_VERDICT_NOTHING = "nothing_to_retry"
_VERDICT_BUDGET = "budget_exhausted"
_VERDICT_NOT_RETRYABLE = "not_retryable"
_VERDICT_FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class RetryDirective:
    verdict: str
    retry_capabilities: List[str] = field(default_factory=list)
    preserved_capabilities: List[str] = field(default_factory=list)
    preserved_refs: List[str] = field(default_factory=list)
    suggested_tools: List[str] = field(default_factory=list)
    fail_closed_reasons: List[str] = field(default_factory=list)

    @property
    def directive_line(self) -> str:
        if self.verdict == _VERDICT_FAIL_CLOSED:
            return ("[GIS Recovery] retry blocked (fail-closed): "
                    + "; ".join(self.fail_closed_reasons[:2]))[:_MAX_LINE]
        if self.verdict == _VERDICT_BUDGET:
            return ("[GIS Recovery] retry budget exhausted — needs user "
                    "decision, no automatic retry")[:_MAX_LINE]
        if self.verdict == _VERDICT_NOT_RETRYABLE:
            return ("[GIS Recovery] last failure is terminal — start a new "
                    "approach instead of retrying")[:_MAX_LINE]
        if self.verdict == _VERDICT_NOTHING or not self.retry_capabilities:
            return "[GIS Recovery] nothing to retry — no failed capabilities"[
                :_MAX_LINE]
        return (
            "[GIS Recovery] retry only failed subgraph: "
            f"{','.join(self.retry_capabilities[:4])}; preserve verified: "
            f"{len(self.preserved_capabilities)} caps/"
            f"{len(self.preserved_refs)} refs; via "
            f"{','.join(self.suggested_tools[:2]) or 'same tools'}"
        )[:_MAX_LINE]

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "verdict": str(self.verdict)[:32],
            "retry_capabilities": [
                str(c)[:64] for c in self.retry_capabilities[:_MAX_ITEMS]],
            "preserved_capabilities": [
                str(c)[:64] for c in self.preserved_capabilities[:_MAX_ITEMS]],
            "preserved_refs": [
                str(r)[:64] for r in self.preserved_refs[:_MAX_ITEMS]],
            "suggested_tools": [
                str(t)[:64] for t in self.suggested_tools[:4]],
            "fail_closed_reasons": [
                str(r)[:160] for r in self.fail_closed_reasons[:4]],
            "directive_line": self.directive_line,
        }


def _rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def resolve_retry_directive(
    *,
    progress_rows: Optional[Sequence[Any]] = None,
    last_recovery: Optional[Dict[str, Any]] = None,
    chapter: Optional[Dict[str, Any]] = None,
    user_ref_hints: Optional[Sequence[str]] = None,
    ref_owner_sessions: Optional[Dict[str, str]] = None,
    current_session_id: str = "",
) -> RetryDirective:
    """把计划事实折叠成重试指令（纯函数；绝不抛出）。"""
    try:
        rows = _rows(progress_rows)
        rec = last_recovery if isinstance(last_recovery, dict) else {}
        diag = rec.get("diagnostic") if isinstance(
            rec.get("diagnostic"), dict) else {}
        chap = chapter if isinstance(chapter, dict) else {}

        retry_caps: List[str] = []
        preserved_caps: List[str] = []
        preserved_refs: List[str] = []
        for row in rows:
            cap = str(row.get("capability") or "")[:64]
            status = str(row.get("status") or "")[:16]
            if not cap:
                continue
            if status in _RETRYABLE_ROW_STATUSES:
                retry_caps.append(cap)
            elif status == "complete":
                preserved_caps.append(cap)
                ref = str(row.get("bound_ref") or "")[:64]
                if ref:
                    preserved_refs.append(ref)

        # ref fail-closed：归属他 session 或不可证明 → 拒绝整条指令的
        # 自动路径（fail-closed 理由逐条披露）。
        fail_reasons: List[str] = []
        hints = [str(h)[:64] for h in (user_ref_hints or []) if h]
        for ref in hints:
            owner = (ref_owner_sessions or {}).get(ref)
            if owner is None:
                fail_reasons.append(
                    f"{ref}: owner session unprovable — fail-closed")
            elif owner != current_session_id:
                fail_reasons.append(
                    f"{ref}: belongs to session {str(owner)[:16]}… — "
                    "cross-session refs are not addressable")
        if fail_reasons:
            return RetryDirective(
                verdict=_VERDICT_FAIL_CLOSED,
                retry_capabilities=[], preserved_capabilities=preserved_caps,
                preserved_refs=preserved_refs, suggested_tools=[],
                fail_closed_reasons=fail_reasons[:4])

        # 诊断门：terminal / 预算耗尽 → 不给重试目标
        disposition = str(diag.get("disposition") or "")
        budget = diag.get("retry") if isinstance(diag.get("retry"), dict) \
            else {}
        if disposition == "terminal":
            return RetryDirective(
                verdict=_VERDICT_NOT_RETRYABLE,
                preserved_capabilities=preserved_caps,
                preserved_refs=preserved_refs)
        if budget and budget.get("retry_allowed") is False:
            return RetryDirective(
                verdict=_VERDICT_BUDGET,
                preserved_capabilities=preserved_caps,
                preserved_refs=preserved_refs)

        suggested: List[str] = []
        if isinstance(chap.get("workflow_contract"), dict) and \
                chap["workflow_contract"].get("workflow_id"):
            suggested.append("webgis_analysis_workflow_recompute")

        verdict = _VERDICT_OPEN if retry_caps else _VERDICT_NOTHING
        return RetryDirective(
            verdict=verdict,
            retry_capabilities=retry_caps[:_MAX_ITEMS],
            preserved_capabilities=preserved_caps[:_MAX_ITEMS],
            preserved_refs=preserved_refs[:_MAX_ITEMS],
            suggested_tools=suggested,
        )
    except Exception:  # noqa: BLE001 — 投影面绝不抛出
        return RetryDirective(verdict=_VERDICT_NOTHING)


__all__ = ["RetryDirective", "resolve_retry_directive"]
