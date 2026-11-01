"""恢复解释面（GOAL 05 / M6，ADR-0279）。

四段紧凑解释（what_failed / what_is_safe_to_try / what_needs_you /
what_already_done）+ 四类处境区分（没算好 / 已算好没显示 / 显示但科学性
未验证 / 导出失败）。供既有证据卡/SSE/LLM 投影消费 —— 不新建控制台。

纪律：纯函数、有界（每段 ≤240 字符）、只消费 bounded payload（诊断/
裁决/对账/指令），不信任、不拼接用户自由文本（防注入面）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

_MAX_SECTION = 240

_SITUATIONS = frozenset({
    "clean",                  # 全部通过
    "computed_incomplete",    # 没算好：科学/分析面失败
    "computed_not_displayed",  # 已算好没显示：渲染/图层面失败
    "displayed_unverified",   # 显示但科学性未验证
    "export_failed",          # 导出交付失败（优先披露）
})


def classify_situation(
    *, science: Optional[str], rendering: Optional[str],
    export: Optional[str] = None,
) -> str:
    """从 goal_verification slot 状态推导四类处境（封闭词表）。

    优先级：export_failed > computed_incomplete > computed_not_displayed
    > displayed_unverified > clean（导出是用户显式要求的交付物，最先披露）。
    """
    if export == "fail":
        return "export_failed"
    if science == "fail":
        return "computed_incomplete"
    if rendering in ("fail", "not_evaluated") and science == "pass":
        return "computed_not_displayed"
    if science == "not_evaluated" and rendering == "pass":
        return "displayed_unverified"
    if science == "fail" and rendering in ("fail", "not_evaluated"):
        return "computed_incomplete"
    if science == "pass" and rendering == "pass":
        return "clean"
    # 混合/未知：按最弱面诚实降级
    if rendering == "fail":
        return "computed_not_displayed"
    if science == "not_evaluated" or rendering == "not_evaluated":
        return "displayed_unverified" if rendering == "pass" else \
            "computed_incomplete"
    return "displayed_unverified"


@dataclass(frozen=True)
class RecoveryExplanation:
    situation: str
    what_failed: str
    what_is_safe_to_try: str
    what_needs_you: str
    what_already_done: str

    def to_bounded_dict(self) -> Dict[str, str]:
        return {
            "situation": self.situation[:32],
            "what_failed": self.what_failed[:_MAX_SECTION],
            "what_is_safe_to_try": self.what_is_safe_to_try[:_MAX_SECTION],
            "what_needs_you": self.what_needs_you[:_MAX_SECTION],
            "what_already_done": self.what_already_done[:_MAX_SECTION],
        }


def _slot_status(verification: Dict[str, Any], slot: str) -> Optional[str]:
    slots = verification.get("slots")
    if not isinstance(slots, list):
        return None
    for s in slots:
        if isinstance(s, dict) and s.get("slot") == slot:
            return str(s.get("status") or "") or None
    return None


def build_recovery_explanation(
    *,
    diagnostic: Optional[Dict[str, Any]],
    decision: Optional[Dict[str, Any]],
    goal_verification: Optional[Dict[str, Any]] = None,
    retry_directive: Optional[Dict[str, Any]] = None,
) -> RecoveryExplanation:
    """bounded payloads → 四段解释（纯函数；绝不抛出）。"""
    try:
        diag = diagnostic if isinstance(diagnostic, dict) else {}
        dec = decision if isinstance(decision, dict) else {}
        ver = goal_verification if isinstance(goal_verification, dict) \
            else {}
        dire = retry_directive if isinstance(retry_directive, dict) else {}

        situation = classify_situation(
            science=_slot_status(ver, "scientific_quality"),
            rendering=_slot_status(ver, "actual_rendering"),
            export=_slot_status(ver, "export_delivery"))
        if situation == "clean" and diag.get("reason"):
            situation = "computed_incomplete"  # 有诊断但 slot 全绿 → 按欠账披露

        reason = str(diag.get("reason") or "unknown")[:48]
        detail = str(diag.get("detail") or "")[:120]
        disposition = str(diag.get("disposition") or "")[:32]

        what_failed = f"{reason}: {detail}" if detail else reason

        allowed = [str(a)[:32] for a in (dec.get("allowed_actions")
                                         or [])[:4]]
        if dec.get("auto_retry"):
            what_safe = "auto retry in progress (bounded)"
        elif allowed:
            what_safe = ", ".join(allowed)
        else:
            what_safe = "none (see needs_you)"

        confirm = dec.get("required_confirmation")
        if disposition == "needs_user_data":
            needs = "provide data/fields (see diagnostic detail)"
        elif disposition == "needs_user_approval" or confirm:
            needs = f"owner approval required ({str(confirm or 'write')[:24]})"
        elif disposition == "unsafe_to_retry":
            needs = "decision required — target is not safe to retry"
        elif disposition == "terminal":
            needs = "none — this failure is terminal for the task"
        else:
            needs = "none"

        done_parts = []
        passed_slots = [
            str(s.get("slot"))[:32]
            for s in (ver.get("slots") or [])
            if isinstance(s, dict) and s.get("status") == "pass"]
        if passed_slots:
            done_parts.append("verified: " + ", ".join(passed_slots[:4]))
        preserved_caps = dire.get("preserved_capabilities")
        if isinstance(preserved_caps, list) and preserved_caps:
            done_parts.append(
                f"preserved {len(preserved_caps)} computed capabilities")
        preserved_refs = dire.get("preserved_refs")
        if isinstance(preserved_refs, list) and preserved_refs:
            done_parts.append(
                "refs kept: " + ", ".join(
                    str(r)[:48] for r in preserved_refs[:3]))
        what_done = "; ".join(done_parts) if done_parts else \
            "nothing verified yet"

        return RecoveryExplanation(
            situation=situation,
            what_failed=what_failed,
            what_is_safe_to_try=what_safe,
            what_needs_you=needs,
            what_already_done=what_done,
        )
    except Exception:  # noqa: BLE001 — 解释面绝不抛出
        return RecoveryExplanation(
            situation="displayed_unverified",
            what_failed="explanation unavailable",
            what_is_safe_to_try="none",
            what_needs_you="none",
            what_already_done="unknown",
        )


__all__ = [
    "RecoveryExplanation",
    "build_recovery_explanation",
    "classify_situation",
]
