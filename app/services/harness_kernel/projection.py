"""Read-only projections from kernel envelope fields to bounded context lines.

Same discipline as the V4/V6/V7 additive lines in ``session_plan.py``: pure
functions over stored state, single-line output, zero IO, and callers swallow
any failure (projection is additive disclosure — it must never break a turn).
"""
from __future__ import annotations

from typing import Optional

from app.services.harness_kernel.models import PlanStep

_STEP_STATUS_GLYPH = {
    "pending": "…",
    "running": "▶",
    "succeeded": "✓",
    "failed": "✗",
    "skipped": "⤼",
    "invalidated": "∅",
}


def format_recovery_line(plan) -> str:
    """One-line resume hint while a recovery is pending (K7).

    Priority: an unresolved ``resumed_from_turn_id`` (set when a fresh turn
    adopted an interrupted one — stays visible DURING the resume turn) beats
    the plain last-turn status. Empty string when nothing is pending — old
    envelopes and healthy sessions produce zero drift.
    """
    rec = getattr(plan, "recovery", None)
    if rec is None:
        return ""
    resumed_from = str(getattr(rec, "resumed_from_turn_id", "") or "")
    if resumed_from:
        resumes = int(getattr(rec, "resume_count", 0) or 0)
        return (
            f"[SessionPlan Recovery] 本 turn 接续了中断的 turn（{resumed_from}）；"
            f"GIS 状态已从信封恢复，续跑前先核对 open 步骤与证据，"
            f"勿盲目重放已 succeeded 的破坏性副作用（resume_count={resumes}）。"
        )
    status = str(getattr(rec, "last_turn_status", "") or "")
    if status in ("", "completed"):
        return ""
    tid = str(getattr(rec, "last_turn_id", "") or "")
    host = str(getattr(rec, "last_host", "") or "unknown")
    resumes = int(getattr(rec, "resume_count", 0) or 0)
    if status == "interrupted":
        return (
            f"[SessionPlan Recovery] 上一 turn（{tid or 'unknown'}, host={host}）"
            f"未正常收尾（interrupted）；GIS 状态已从信封恢复，续跑前先核对"
            f" open 步骤与证据，勿盲目重放已 succeeded 的破坏性副作用"
            f"（resume_count={resumes}）。"
        )
    if status == "failed":
        return (
            f"[SessionPlan Recovery] 上一 turn（{tid or 'unknown'}, host={host}）"
            f"以 failed 结束；failed 步骤可重试，重试成功会覆写状态。"
        )
    if status == "cancelled":
        return (
            f"[SessionPlan Recovery] 上一 turn（{tid or 'unknown'}, host={host}）"
            f"被用户取消；未完成步骤保持 pending，等待用户指示后继续。"
        )
    return ""


def format_steps_line(plan, *, max_steps: int = 8) -> str:
    """Bounded one-line step projection (host-neutral K6 evidence).

    Shows the most recent ``max_steps`` steps newest-first:
    ``[SessionPlan Steps] s3✓ poi_query(heatmap_data) s2✗ …`` — empty when the
    envelope carries no kernel steps (v1 envelopes / no tool evidence yet).
    """
    steps: list[PlanStep] = [
        s for s in (getattr(plan, "steps", None) or []) if isinstance(s, PlanStep)
    ]
    if not steps:
        return ""
    parts = []
    for s in reversed(steps[-max_steps:]):
        glyph = _STEP_STATUS_GLYPH.get(str(s.status), "…")
        tool = f"({s.tool})" if s.tool else ""
        parts.append(f"{s.id}{glyph}{tool}")
    open_count = sum(
        1 for s in steps if str(s.status) in ("pending", "running", "failed")
    )
    return (
        f"[SessionPlan Steps] n={len(steps)} open={open_count} | "
        + " ".join(parts)
    )
