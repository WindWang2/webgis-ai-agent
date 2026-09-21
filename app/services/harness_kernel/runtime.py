"""GISSessionRuntime — the GIS-native session lifecycle owner (ADR-0180/0204).

Single production entry for session-scoped Harness semantics on top of the
SessionPlan envelope (ADR-0076 store): hydrate → begin_turn → plan update /
evidence attach → checkpoint → end_turn. The runtime owns **only** GIS/Harness
session semantics — it never hosts a model/tool loop (Pi stays the agent
host, D-002), never touches Pi RPC/token/cache internals, and never forks a
second plan/progress model (steps link to capability rows via
``PlanStep.capability``).

Composition rule (D-001/D-004): capability-progress semantics stay in
``app.services.session_plan`` (single truth); the runtime adds turn/step/
decision/recovery layers **inside the same per-session fail-closed lock** via
``apply_tool_result_with_lock``. Every mutation is bounded (envelope stays
KB-scale; artifacts stay refs).

Canonical lifecycle (ADR-0208): the turn phase vocabulary + transition table
live in ``models``; this module drives them at the seams it already owns —
begin_turn → understanding, intent → planning, begin_step(hits) → qualifying
→ executing, evidence → observing, patch → repairing, end_turn → verifying →
terminal. ``end_turn`` is the ONLY terminalizer; out-of-table transitions are
refused (journalled + counted, never raised). The decision journal IS the
versioned event record (``seq``/``event_id``/``causal_id``); causal events
are idempotent on ``event_id``, so the bridge's lock-contention retry cannot
double-append.

Failure discipline mirrors the repo's "增值披露绝不阻断" convention: callers
(best-effort wiring in the bridge/engine) wrap runtime calls in try/except;
the runtime itself only raises on lock contention (TimeoutError) which the
bridge already retries once.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, List, Optional

from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import (
    MAX_CHECKPOINT_SLOTS,
    MAX_DECISIONS,
    MAX_PHASE_HISTORY,
    MAX_STEPS,
    MAX_TURNS,
    DISPATCH_TRIGGER,
    EVIDENCE_TRIGGER,
    PLAN_TRIGGER,
    QUALIFY_TRIGGER,
    SETTLE_TRIGGER,
    STATUS_TO_TRIGGER,
    USER_MESSAGE_TRIGGER,
    HarnessTurnContext,
    PatchResult,
    PhaseTransition,
    PlanDecision,
    PlanHost,
    PlanPatch,
    PlanStep,
    PlanTurnRecord,
    StepEvidence,
    TurnStatus,
    next_phase,
    push_bounded,
)
from app.services.session_plan import (
    SESSION_PLAN_STEP,
    SESSION_PLAN_SUPERSEDED,
    SessionPlan,
    SessionPlanEvent,
    _ensure_slot_unlocked,
    apply_tool_result_with_lock,
    capabilities_hit_by_tool,
    ensure_session_plan_slot,
    load_session_plan,
    save_session_plan,
)


from app.services.distributed_lock import session_lock_registry

logger = logging.getLogger(__name__)

#: Tools that are milestones of the plan itself, never capability steps.
_INTENT_TOOLS = frozenset({"webgis_map_intent"})
_PRODUCT_TOOLS = frozenset({"webgis_map_product"})
PRODUCT_STEP_ID = "product"

_CP_ALIAS_PREFIX = "session-plan-cp:"

# Chapter-row status → kernel step status (materialization mapping).
_ROW_STATUS_MAP = {
    # data_requirements statuses
    "pending": "pending",
    "available": "succeeded",
    # analysis_steps statuses
    "done": "succeeded",
    "skipped": "skipped",
    "unavailable": "skipped",
    "failed": "failed",
}


def _now() -> float:
    return time.time()


async def _save_if_fresh(plan: SessionPlan, *, store: Any, host: str = "") -> bool:
    """Stale-revision guard (K2 / review S5): refuse to clobber a newer
    persisted envelope.

    Under a healthy session lock the check is a no-op (writes are serialized;
    persisted revision == loaded revision). It bites exactly when the lock
    degraded (cross-pod write raced ahead): the stale writer's kernel-layer
    mutation is dropped with a counter + warning instead of silently
    reverting newer state. One extra envelope read per kernel save —
    correctness first (P1-perf note in ledger covers the amplification).
    """
    persisted = await load_session_plan(plan.session_id, store=store)
    if persisted is not None and int(persisted.revision or 0) > int(plan.revision or 0):
        hk_metrics.record("stale_write_refused", host=host)
        logger.warning(
            "[HarnessKernel] stale envelope write refused session=%s "
            "loaded_rev=%d persisted_rev=%d",
            plan.session_id, plan.revision, persisted.revision,
        )
        return False
    await save_session_plan(plan, store=store)
    return True


@asynccontextmanager
async def _session_scope(session_id: str, lock: Any):
    """Yield the session lock: acquire when not supplied, pass through when
    the caller already holds it (legacy engine hooks run inside the engine's
    own ``session_lock`` scope — the lock is NOT reentrant, so re-acquiring
    would stall 30s per hook call; D-004 lock-through pattern, same as
    ``session_plan.apply_tool_result_with_lock``)."""
    if lock is not None:
        yield lock
        return
    async with session_lock_registry.lock(session_id, fail_on_degraded=True) as acquired:
        yield acquired


def _step_event(plan: SessionPlan, step: PlanStep, ref: str = "") -> SessionPlanEvent:
    latest = step.latest_evidence()
    return SessionPlanEvent(
        event=SESSION_PLAN_STEP,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "step_id": step.id,
            "goal": step.goal,
            "capability": step.capability,
            "tool": step.tool or (latest.tool if latest else ""),
            "status": step.status,
            "attempts": step.attempts,
            "ref": ref or (latest.ref if latest else ""),
            "host": step.host,
            "turn_id": step.turn_id,
        },
    )


def _journal(
    plan: SessionPlan,
    kind: str,
    *,
    host: PlanHost = "unknown",
    turn_id: str = "",
    note: str = "",
    detail: Optional[dict] = None,
) -> None:
    """Append a bounded decision row (unknown kinds pass through — forward
    compat, the vocabulary in models.DECISION_KINDS is advisory)."""
    push_bounded(
        plan.decisions,
        PlanDecision(
            kind=kind,
            at=_now(),
            host=host,
            turn_id=turn_id,
            note=note[:300],
            detail=detail or {},
        ),
        MAX_DECISIONS,
    )


def _event(
    plan: SessionPlan,
    kind: str,
    *,
    host: PlanHost = "unknown",
    turn_id: str = "",
    note: str = "",
    detail: Optional[dict] = None,
    causal_id: str = "",
) -> bool:
    """Append a versioned canonical event row (K4, ADR-0208).

    ``seq`` is envelope-monotonic; ``event_id`` is the idempotency key —
    deterministic (``kind:turn_id:causal_id``) for causal rows so a duplicate
    tool callback or the bridge's lock-contention retry appends nothing, and
    seq-qualified for non-causal rows (each occurrence is a distinct fact).
    Returns True when a row was appended.
    """
    seq = int(getattr(plan, "event_seq", 0) or 0) + 1
    plan.event_seq = seq
    if causal_id:
        # Deterministic idempotency key: a duplicate callback / lock-retry
        # replay produces the same key and appends nothing.
        event_id = f"{kind}:{turn_id}:{causal_id}"
        if any(d.event_id == event_id for d in plan.decisions):
            plan.event_seq = seq - 1
            return False
    else:
        # Non-causal rows: every occurrence is a distinct fact — the seq
        # keeps the id unique (replay order = seq order).
        event_id = f"{kind}:{turn_id}:#{seq}"
    push_bounded(
        plan.decisions,
        PlanDecision(
            kind=kind,
            at=_now(),
            host=host,
            turn_id=turn_id,
            note=note[:300],
            detail=detail or {},
            seq=seq,
            event_id=event_id,
            causal_id=causal_id,
        ),
        MAX_DECISIONS,
    )
    return True


def _running_turn(plan: SessionPlan, turn_id: str) -> Optional[PlanTurnRecord]:
    if not turn_id:
        return None
    return next(
        (
            t
            for t in reversed(plan.turns)
            if t.turn_id == turn_id and t.status == "running"
        ),
        None,
    )


def _advance_unlocked(
    plan: SessionPlan,
    turn_id: str,
    trigger: str,
    *,
    host: PlanHost = "unknown",
) -> str:
    """Advance the canonical turn phase by ``trigger`` (caller holds the lock).

    Table-driven (models.PHASE_TRANSITIONS); terminal edges are reached only
    via ``end_turn``'s settle + status-trigger pair. Out-of-table pairs are
    REFUSED: the phase never changes, a ``phase_refused`` audit row (history
    ring + event journal + metric) records the attempt — fail-closed and
    observable, never fatal. Returns the resulting phase ("" on refusal or
    unknown/inactive turn).
    """
    record = _running_turn(plan, turn_id)
    if record is None:
        return ""
    current = str(record.phase or "created")
    target = next_phase(current, trigger)
    now = _now()
    if target is None:
        record.record_phase(
            PhaseTransition(
                to_phase=current,
                trigger=trigger[:32],
                from_phase=current,
                at=now,
                reason_code="OUTSIDE_TABLE",
            ),
            keep=MAX_PHASE_HISTORY,
        )
        _event(
            plan,
            "phase_refused",
            host=host,
            turn_id=turn_id,
            note=f"{current} -/{trigger}-> ?",
            detail={"from": current, "trigger": trigger[:32]},
        )
        hk_metrics.record("phase_refused", host=host, trigger=trigger[:32])
        return ""
    record.record_phase(
        PhaseTransition(
            to_phase=target, trigger=trigger[:32], from_phase=current, at=now
        ),
        keep=MAX_PHASE_HISTORY,
    )
    if target != current:
        _event(
            plan,
            "phase_changed",
            host=host,
            turn_id=turn_id,
            note=f"{current} -> {target} ({trigger})"[:200],
            detail={"from": current, "to": target, "trigger": trigger[:32]},
        )
    return target


def _chapter_rows(plan: SessionPlan) -> List[dict]:
    """Flatten the chapter's planned rows (data_requirements + analysis_steps)."""
    rows: List[dict] = []
    chapter = plan.gis_chapter or {}
    for key in ("data_requirements", "analysis_steps"):
        for row in chapter.get(key) or []:
            if isinstance(row, dict) and str(row.get("capability") or "").strip():
                rows.append(row)
    return rows


def _materialize_steps(plan: SessionPlan) -> List[PlanStep]:
    """Kernel steps materialized from chapter rows (single layer, D-003).

    Existing step state survives by capability key (a re-intent that keeps a
    capability keeps its evidence); capabilities that vanished are marked
    ``invalidated`` (K5 scope-change semantics); new rows start from the row's
    own status (recovered chapters keep their progress). The product milestone
    step is preserved untouched.
    """
    by_cap: dict[str, PlanStep] = {}
    for s in plan.steps:
        if s.capability:
            by_cap.setdefault(s.capability, s)
    now = _now()
    seen: set[str] = set()
    steps: List[PlanStep] = []
    for row in _chapter_rows(plan):
        cap = str(row.get("capability") or "").strip()
        if cap in seen:
            # 同一 capability 在 data_requirements 与 analysis_steps 各有
            # 一行时只物化一步（capability 层进度行也是按 capability 去重）。
            continue
        seen.add(cap)
        status = _ROW_STATUS_MAP.get(str(row.get("status") or "pending"), "pending")
        existing = by_cap.get(cap)
        if existing is not None:
            # chapter 行仍是真相（goal/tool/依赖可能已重规划）；状态不被回退。
            existing.goal = str(row.get("purpose") or "")[:300]
            existing.tool = str(row.get("resolved_tool") or "")
            existing.tool_binding = [existing.tool] if existing.tool else []
            existing.depends_on = [
                str(d) for d in (row.get("depends_on") or []) if isinstance(d, str)
            ]
            steps.append(existing)
            continue
        step = PlanStep(
            id=f"step-{cap[:48]}",
            goal=str(row.get("purpose") or "")[:300],
            capability=cap,
            tool=str(row.get("resolved_tool") or ""),
            tool_binding=[str(row.get("resolved_tool") or "")]
            if row.get("resolved_tool") else [],
            status=status,  # type: ignore[arg-type]
            depends_on=[
                str(d) for d in (row.get("depends_on") or []) if isinstance(d, str)
            ],
            created_at=now,
            updated_at=now,
        )
        steps.append(step)
    # 产品里程碑步保留（webgis_map_product 的完成证据挂在它上面）。
    product = next((s for s in plan.steps if s.id == PRODUCT_STEP_ID), None)
    if product is not None:
        steps.append(product)
    # 计划外 on-demand 步骤不保留：镜像 capability 层 ``_merge_progress``
    # 语义（未追踪的能力行在 replace 时被丢弃）；再次命中会按需重建。
    return steps


def _reconcile_invalidated(plan: SessionPlan, steps: List[PlanStep]) -> bool:
    """Mark chapter-vanished steps ``invalidated``; True when anything changed."""
    caps = {str(r.get("capability") or "").strip() for r in _chapter_rows(plan)}
    changed = False
    for s in plan.steps:
        if s.capability and s.capability not in caps and s.status not in (
            "invalidated",
            "skipped",
        ):
            s.status = "invalidated"
            s.updated_at = _now()
            changed = True
    return changed


def _settle_step(
    plan: SessionPlan,
    *,
    capability_hits: List[str],
    status: str,
    tool_name: str,
    tool_call_id: str,
    ref: str,
    error: str,
    host: PlanHost,
    turn_id: str,
) -> List[PlanStep]:
    """Attach evidence to the steps serving ``capability_hits``; return changed.

    Registry-mapped capabilities missing from the chapter (unplanned hits)
    get an on-demand step — mirroring ``_mark_progress``'s auto-append row
    behavior in the capability layer, so step and row stay 1:1.
    """
    now = _now()
    changed: List[PlanStep] = []
    hits = set(capability_hits)
    by_cap = {s.capability: s for s in plan.steps if s.capability}
    for cap in hits:
        step = by_cap.get(cap)
        if step is None and status in ("succeeded", "failed"):
            step = PlanStep(
                id=f"step-{cap[:48]}",
                goal=f"计划外能力（registry 命中）：{cap}",
                capability=cap,
                tool=tool_name,
                status="pending",
                host=host,
                turn_id=turn_id,
                created_at=now,
                updated_at=now,
            )
            push_bounded(plan.steps, step, MAX_STEPS)
            by_cap[cap] = step
        if step is None:
            continue
        if step.status == "succeeded" and status == "succeeded":
            # 幂等：同一 tool_call_id 重复成功不 inflate attempts / evidence (#1407)。
            latest = (step.evidence[-1] if getattr(step, "evidence", None) else None)
            if latest is not None and tool_call_id and (
                    getattr(latest, "tool_call_id", "") == tool_call_id):
                continue
            step.attempts += 1
            step.attach_evidence(
                StepEvidence(
                    tool=tool_name, tool_call_id=tool_call_id, ref=ref, at=now
                )
            )
            changed.append(step)
            continue
        if step.status == "invalidated" and status == "failed":
            # invalidated 行不再吃执行证据（其能力已不在章节中）。
            continue
        step.status = status  # type: ignore[assignment]
        step.attempts += 1
        if step.tool in ("", tool_name):
            step.tool = tool_name
        if step.host == "unknown":
            step.host = host
        if turn_id:
            step.turn_id = turn_id
        step.attach_evidence(
            StepEvidence(
                tool=tool_name,
                tool_call_id=tool_call_id,
                ref=ref,
                error=error[:300],
                at=now,
            )
        )
        changed.append(step)
    return changed


class GISSessionRuntime:
    """Session-scoped facade; cheap to construct, no process state."""

    def __init__(self, session_id: str, *, store: Any = None) -> None:
        self.session_id = session_id
        self._store = store

    # ── reads ──────────────────────────────────────────────────────────────

    async def hydrate(self, *, create: bool = True) -> Optional[SessionPlan]:
        """Load (or open) the envelope. Turn-start entry point (K1)."""
        if create:
            return await ensure_session_plan_slot(self.session_id, store=self._store)
        return await load_session_plan(self.session_id, store=self._store)

    # ── turn lifecycle (K1/K7) ─────────────────────────────────────────────

    async def begin_turn(
        self,
        turn_id: str,
        *,
        host: PlanHost,
        message: str = "",
        lock: Any = None,
    ) -> List[SessionPlanEvent]:
        """Open a turn: turn journal + interruption detection (idempotent).

        A still-``running`` older turn record means the previous turn never
        settled (server restart / process death): it is marked ``interrupted``
        and the envelope's recovery counter notes the resume — no state is
        rolled back and no tool is re-executed here (D-008).

        ``lock``: caller-held session lock (legacy engine hooks run inside the
        engine's own ``session_lock`` scope — pass it through instead of
        re-acquiring the non-reentrant lock).
        """
        if not self.session_id or not turn_id:
            return []
        if lock is None:
            # 槽位预创建在锁外：ensure_session_plan_slot 自带双检锁（首创建也
            # 要拿会话锁），若在本方法持锁后调用会自锁死锁（锁非重入）。
            await ensure_session_plan_slot(self.session_id, store=self._store)
        async with _session_scope(self.session_id, lock) as lock:
            if lock is not None and lock.lost:
                return []
            plan = await _ensure_slot_unlocked(self.session_id, store=self._store)
            if plan is None:
                return []
            now = _now()
            events: List[SessionPlanEvent] = []
            if lock is not None and lock.lost:
                return []
            current = next(
                (t for t in plan.turns if t.turn_id == turn_id), None
            )
            if current is not None and current.status == "running":
                _journal(
                    plan, "duplicate_turn", host=host, turn_id=turn_id,
                    note="begin_turn replay for an in-flight turn — ignored",
                )
                hk_metrics.record("duplicate_turn_prevented", host=host)
                await _save_if_fresh(plan, store=self._store, host=host)
                return []
            # Interrupt an older still-running turn (restart/resume path).
            # ADR-0208: the phase walks the table to its terminal state too
            # (running → verifying → interrupted) BEFORE the status write —
            # a settled turn must never mix a terminal status with a
            # running-phase (single terminal truth).
            resumed_from = ""
            for t in plan.turns:
                if t.status == "running":
                    _advance_unlocked(
                        plan, t.turn_id, SETTLE_TRIGGER, host=host
                    )
                    _advance_unlocked(
                        plan, t.turn_id,
                        STATUS_TO_TRIGGER["interrupted"], host=host,
                    )
                    t.status = "interrupted"
                    t.ended_at = now
                    resumed_from = t.turn_id
            if resumed_from:
                plan.recovery.resume_count += 1
                plan.recovery.resumed_from_turn_id = resumed_from
                _journal(
                    plan, "turn_resumed", host=host, turn_id=turn_id,
                    note=f"previous turn {resumed_from} marked interrupted",
                    detail={"resumed_from": resumed_from},
                )
                hk_metrics.record("resume_detected", host=host)
            record = PlanTurnRecord(
                turn_id=turn_id, host=host, started_at=now, status="running",
                phase="created",
            )
            push_bounded(plan.turns, record, MAX_TURNS)
            plan.recovery.last_turn_id = turn_id
            plan.recovery.last_host = host
            plan.recovery.last_turn_status = "running"
            _journal(
                plan, "turn_started", host=host, turn_id=turn_id,
                note=message[:200],
            )
            # ADR-0208: canonical phase opens here — created → understanding
            # (the user message IS the turn's input; no extra bridge seam).
            _advance_unlocked(
                plan, turn_id, USER_MESSAGE_TRIGGER, host=host
            )
            if lock is not None and lock.lost:
                return []
            await _save_if_fresh(plan, store=self._store, host=host)
            hk_metrics.record("turn_started", host=host)
            hk_metrics.record("host_parity", host=host)
            return events

    async def end_turn(
        self,
        turn_id: str,
        *,
        host: PlanHost,
        status: TurnStatus = "completed",
        checkpoint: bool = True,
        lock: Any = None,
    ) -> List[SessionPlanEvent]:
        """Settle a turn: journal + phase terminalization + in-flight step
        settlement + checkpoint.

        ``status`` mirrors the host's own outcome semantics (bridge settlement
        flags on Pi; engine completion on legacy). This is the ONLY method
        that terminalizes the canonical phase (running → verifying → terminal,
        ADR-0208; mirrors ADR-0100's single-finalizer invariant). Running
        steps never survive a settled turn as "running": user cancel →
        ``skipped``, failure/interruption (and a completed turn whose
        dispatch died between begin_step and evidence) → ``failed``
        (retryable), so the projection never lies about in-flight work that
        is no longer in flight.
        """
        if not self.session_id or not turn_id:
            return []
        events: List[SessionPlanEvent] = []
        async with _session_scope(self.session_id, lock) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None:
                return []
            now = _now()
            record = next(
                (t for t in reversed(plan.turns) if t.turn_id == turn_id), None
            )
            if record is None:
                return []
            if record.status != "running":
                return []  # idempotent re-settle (e.g. double finally)
            # Canonical terminalization (before the status write so the
            # advance sees a still-running record): running → verifying →
            # terminal. Unknown statuses skip the table walk (defensive;
            # callers are typed).
            if str(record.phase or "created") not in ("verifying",):
                _advance_unlocked(plan, turn_id, SETTLE_TRIGGER, host=host)
            terminal_trigger = STATUS_TO_TRIGGER.get(str(status))
            if terminal_trigger:
                _advance_unlocked(plan, turn_id, terminal_trigger, host=host)
            record.status = status
            record.ended_at = now
            plan.recovery.last_turn_status = status
            if status == "interrupted":
                plan.recovery.interrupted_at = now
            if status == "completed":
                # 干净收尾清除 resume 挂起标记（恢复提示只服务未收尾的续跑）。
                plan.recovery.resumed_from_turn_id = ""
            # #1407: completed turns must also settle still-running steps
            # (dispatch died between begin_step and apply_tool_evidence).
            settle_to = {
                "cancelled": "skipped",
                "failed": "failed",
                "interrupted": "failed",
                "completed": "failed",
            }.get(status)
            if settle_to:
                for s in plan.steps:
                    if s.status == "running":
                        s.status = settle_to  # type: ignore[assignment]
                        s.updated_at = now
                        events.append(_step_event(plan, s))
            _journal(
                plan, "turn_ended", host=host, turn_id=turn_id,
                note=f"status={status} tool_calls={record.tool_calls}",
            )
            # ADR-0208 (K2): settle-time canonical context summary — the
            # first production consumer of the typed projection. Bounded
            # one line; projection failure must never block settlement.
            try:
                from app.services.harness_kernel.context import build_turn_context

                logger.info(
                    "%s", build_turn_context(plan, turn_id).summary_line()
                )
            except Exception:  # noqa: BLE001 — 投影绝不阻断结算
                logger.debug(
                    "[HarnessKernel] turn context projection failed session=%s",
                    self.session_id, exc_info=True,
                )
            if checkpoint:
                await self._checkpoint_unlocked(
                    plan, reason=f"turn_end:{status}", host=host, turn_id=turn_id
                )
            if lock is not None and lock.lost:
                return []
            await _save_if_fresh(plan, store=self._store, host=host)
            hk_metrics.record("turn_ended", host=host, status=status)
            return events

    # ── step evidence (K3) ────────────────────────────────────────────────

    async def begin_step(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        turn_id: str = "",
        host: PlanHost = "pi",
    ) -> None:
        """Mark the capability steps a dispatch is about to serve ``running``.

        Lockless fast path: tools that hit no planned capability (the common
        read/status case) cost one envelope read and zero writes. Crash after
        this point leaves an honest ``running`` marker for K7 recovery.

        ADR-0208: a hit-bearing dispatch is also the canonical qualification
        seam — the turn advances qualifying → executing (eligibility was just
        resolved at the dispatch-bind gate) and the dispatch is journalled as
        a causal ``tool_started`` event.
        """
        if not self.session_id:
            return
        plan = await load_session_plan(self.session_id, store=self._store)
        if plan is None or not plan.steps:
            return
        hits = capabilities_hit_by_tool(plan, tool_name)
        if not hits:
            return
        async with session_lock_registry.lock(
            self.session_id, fail_on_degraded=True
        ) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None or (lock is not None and lock.lost):
                return
            changed = False
            now = _now()
            journal_len = len(plan.decisions)
            for s in plan.steps:
                if s.capability in hits and s.status == "pending":
                    s.status = "running"
                    if turn_id:
                        s.turn_id = turn_id
                    if s.host == "unknown":
                        s.host = host
                    s.updated_at = now
                    changed = True
            phase_touched = False
            if turn_id:
                # Qualify → dispatch: both edges are table-driven and legal
                # self-loops once the turn is already executing/observing
                # (repeat dispatches record audit rows, no phase churn).
                _advance_unlocked(plan, turn_id, QUALIFY_TRIGGER, host=host)
                if _advance_unlocked(
                    plan, turn_id, DISPATCH_TRIGGER, host=host
                ):
                    phase_touched = True
                _event(
                    plan, "tool_started",
                    host=host, turn_id=turn_id, causal_id=tool_call_id,
                    note=f"{tool_name} -> {len(hits)} capability(ies)",
                    detail={
                        "tool": tool_name[:64],
                        "capabilities": sorted(hits)[:8],
                        "steps_marked_running": changed,
                    },
                )
                if changed:
                    # Qualification view changed (pending → running): one
                    # event per actual state change, not per dispatch.
                    _event(
                        plan, "qualification_changed",
                        host=host, turn_id=turn_id, causal_id=tool_call_id,
                        detail={"capabilities": sorted(hits)[:8]},
                    )
            # Persist on ANY mutation: step flips, phase moves, and
            # event/refusal journal appends alike (an event-only dispatch
            # must still land its audit trail).
            mutated = (
                changed
                or phase_touched
                or len(plan.decisions) > journal_len
            )
            if mutated and lock is not None and not lock.lost:
                await _save_if_fresh(plan, store=self._store, host=host)

    async def apply_tool_evidence(
        self,
        tool_name: str,
        raw_result: Any,
        *,
        success: bool = True,
        geojson_ref: Optional[str] = None,
        tool_call_id: str = "",
        turn_id: str = "",
        host: PlanHost = "pi",
        lock: Any = None,
    ) -> List[SessionPlanEvent]:
        """Post-dispatch plan update: legacy capability semantics + kernel
        step/decision layers, in ONE lock scope (D-004).

        Capability progress/supersede/failure-marking stay exactly
        ``apply_tool_result`` (single truth); this wrapper adds step
        materialization, evidence attachment, milestone journaling, and the
        turn's tool counter — then a single additional save for the additive
        layer.
        """
        events: List[SessionPlanEvent] = []
        async with _session_scope(self.session_id, lock) as lock:
            events, intent_facts = await apply_tool_result_with_lock(
                    self.session_id,
                    tool_name,
                    raw_result,
                    success=success,
                    geojson_ref=geojson_ref,
                    store=self._store,
                    lock=lock,
            )
            if lock is not None and lock.lost:
                return events
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None:
                return events
            now = _now()
            if turn_id:
                record = next(
                    (
                        t
                        for t in reversed(plan.turns)
                        if t.turn_id == turn_id and t.status == "running"
                    ),
                    None,
                )
                if record is not None:
                    record.tool_calls += 1

            # 1) Chapter (re)planned → (re)materialize kernel steps.
            if success and tool_name in _INTENT_TOOLS and plan.gis_chapter:
                steps = _materialize_steps(plan)
                _reconcile_invalidated(plan, steps)
                if steps != plan.steps:
                    plan.steps = steps[:MAX_STEPS]
                    for s in plan.steps:
                        events.append(_step_event(plan, s))
                kind = "plan_replaced" if plan.replaced else "plan_created"
                _journal(
                    plan, kind, host=host, turn_id=turn_id,
                    note=str((plan.gis_chapter or {}).get("query") or "")[:200],
                    detail={"recipe_id": str((plan.gis_chapter or {}).get("recipe_id") or "")},
                )
                hk_metrics.record(
                    "plan_created" if kind == "plan_created" else "plan_replaced",
                    host=host,
                )
                # review S4：superseded 判据改用 capability 层事件（store 的
                # supersede 分支返回 _superseded_event；load 回来的 new 信封
                # 的 superseded 标志恒为 False，旧判据是死代码）。
                if any(e.event == SESSION_PLAN_SUPERSEDED for e in events):
                    hk_metrics.record("plan_superseded", host=host)
                    _journal(
                        plan, "plan_superseded", host=host, turn_id=turn_id,
                        note=f"previous goal: {plan.previous_goal}"[:200],
                    )
                # ADR-0208: intent resolved + plan compiled are canonical
                # lifecycle facts — causal events + phase advance
                # (understanding/replanning → planning).
                _event(
                    plan, "intent_resolved",
                    host=host, turn_id=turn_id, causal_id=tool_call_id,
                    note=str((plan.gis_chapter or {}).get("query") or "")[:200],
                )
                if turn_id:
                    _advance_unlocked(plan, turn_id, PLAN_TRIGGER, host=host)

            # 2) Evidence attach on the steps serving this tool.
            if tool_name in _PRODUCT_TOOLS:
                status = "succeeded" if success else "failed"
                product = next(
                    (s for s in plan.steps if s.id == PRODUCT_STEP_ID), None
                )
                if product is None:
                    product = PlanStep(
                        id=PRODUCT_STEP_ID,
                        goal="地图成品 finalize（webgis_map_product）",
                        status="pending",
                        created_at=now,
                    )
                    push_bounded(plan.steps, product, MAX_STEPS)
                if not (product.status == "succeeded" and success):
                    product.status = status  # type: ignore[assignment]
                    product.attempts += 1
                    product.tool = tool_name
                    product.host = host
                    if turn_id:
                        product.turn_id = turn_id
                    product.attach_evidence(
                        StepEvidence(
                            tool=tool_name,
                            tool_call_id=tool_call_id,
                            ref=geojson_ref or "",
                            at=now,
                        )
                    )
                    events.append(_step_event(plan, product, ref=geojson_ref or ""))
                hk_metrics.record(
                    "step_succeeded" if success else "step_failed",
                    host=host, step="product",
                )
                # ADR-0208: the product milestone IS the turn's goal
                # evaluation fact (finalizer verdict rides the result).
                _event(
                    plan, "goal_evaluated",
                    host=host, turn_id=turn_id, causal_id=tool_call_id,
                    note=f"product milestone {'ok' if success else 'failed'}",
                    detail={"milestone": "product", "success": success},
                )
            elif plan.steps:
                error = ""
                if not success and isinstance(raw_result, dict):
                    error = str(raw_result.get("error") or raw_result.get("message") or "")
                hits = capabilities_hit_by_tool(plan, tool_name)
                if hits:
                    changed = _settle_step(
                        plan,
                        capability_hits=hits,
                        status="succeeded" if success else "failed",
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        ref=geojson_ref or "",
                        error=error,
                        host=host,
                        turn_id=turn_id,
                    )
                    for s in changed:
                        events.append(_step_event(plan, s))
                    hk_metrics.record(
                        "step_succeeded" if success else "step_failed", host=host
                    )
                # ADR-0208: typed causal result events (idempotent per
                # tool_call_id — the bridge's lock-contention retry cannot
                # double-append) replace the old unversioned step_marked row.
                # Turn-scoped facts (events + phase) require an ACTIVE turn:
                # evidence for a settled turn stays step-level truth (hk1
                # semantics) but must not journal turn events or move phases
                # (late callbacks are journalled upstream as tool_late).
                # Phase moves only on real evidence (artifact ref arrives →
                # observing) or a recorded step failure — unplanned reads
                # without artifacts leave the phase alone (no flapping).
                running = _running_turn(plan, turn_id) if turn_id else None
                turn_active = (not turn_id) or running is not None
                if turn_active and success and geojson_ref:
                    _event(
                        plan, "observation_received",
                        host=host, turn_id=turn_id, causal_id=tool_call_id,
                        detail={"ref": geojson_ref or ""},
                    )
                if turn_active and (hits or success is False or geojson_ref):
                    _event(
                        plan,
                        "tool_succeeded" if success else "tool_failed",
                        host=host, turn_id=turn_id, causal_id=tool_call_id,
                        note=f"{tool_name} -> {'ok' if success else f'error: {error}'[:180]}",
                        detail={
                            "tool": tool_name[:64],
                            "ref": geojson_ref or "",
                            "capabilities": sorted(hits)[:8] if hits else [],
                        },
                    )
                if running:
                    if success and geojson_ref:
                        _advance_unlocked(
                            plan, turn_id, EVIDENCE_TRIGGER, host=host
                        )
                    elif not success and hits:
                        _advance_unlocked(
                            plan, turn_id, "tool_failed", host=host
                        )

            if lock is not None and lock.lost:
                return events
            await _save_if_fresh(plan, store=self._store, host=host)
        # 方向 5（execution-graph v1）：意图差异事实 → V5 执行侧同步（与
        # session_plan.apply_tool_result 的 post-lock 段同款；fail-open，
        # 且在会话锁外 —— 绝不延长持锁时间）。
        if intent_facts:
            try:
                from app.services.workflow_runtime.hooks import (
                    record_intent_changes_safe,
                )

                v5_summary = await record_intent_changes_safe(
                    self.session_id, facts=intent_facts)
            except Exception:  # noqa: BLE001 — 附加事实通道
                v5_summary = None
            try:
                from app.services.workflow_runtime.graph_events import (
                    intent_graph_event,
                )

                events.append(intent_graph_event(intent_facts, v5_summary))
            except Exception:  # noqa: BLE001 — 事件是增值投影
                pass
        return events

    # ── K5: plan patch protocol ───────────────────────────────────────────

    async def patch_plan(self, patch: PlanPatch, *, lock: Any = None) -> PatchResult:
        """Mark plan-level patch: invalidate targeted (or all non-terminal)
        steps, journal, and expose ``invalidated_step_ids`` for direction 5.

        No execution engine lives here (direction 5 owns subgraph re-run);
        this is the stable marking/protocol layer only.
        """
        if not self.session_id:
            return PatchResult()
        async with _session_scope(self.session_id, lock) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None or (lock is not None and lock.lost):
                return PatchResult()
            now = _now()
            invalidated: List[str] = []
            invalidated_steps: List[PlanStep] = []
            targets = set(patch.target_step_ids or [])
            for s in plan.steps:
                hit = (s.id in targets) if targets else (
                    s.status in ("pending", "running")
                )
                if hit and s.status != "invalidated":
                    s.status = "invalidated"
                    s.updated_at = now
                    invalidated.append(s.id)
                    invalidated_steps.append(s)
            if not invalidated:
                # Nothing left to invalidate (unknown targets, or a repeat
                # patch over already-invalidated steps): no-op — no second
                # plan_patched/repair_applied event, no phase churn.
                return PatchResult(applied=False, revision=plan.revision)
            _journal(
                plan, "plan_patched", host=patch.host, turn_id=patch.turn_id,
                note=f"{patch.kind}: invalidated={','.join(invalidated)[:200]}",
                detail={"kind": patch.kind, **(patch.detail or {})},
            )
            # ADR-0208: an applied patch is the turn's repair-loop entry —
            # canonical phase moves to repairing (direction-5 drives the
            # repairing → executing re-entry via ``advance_turn_phase``).
            if patch.turn_id:
                moved = _advance_unlocked(
                    plan, patch.turn_id, "repair_requested", host=patch.host
                )
                if moved:
                    _event(
                        plan, "repair_applied",
                        host=patch.host, turn_id=patch.turn_id,
                        note=f"{patch.kind}: {len(invalidated)} step(s)",
                        detail={
                            "kind": patch.kind,
                            "invalidated": invalidated[:8],
                        },
                    )
            if lock is not None and lock.lost:
                return PatchResult(applied=False, revision=plan.revision)
            await _save_if_fresh(plan, store=self._store, host=patch.host)
            hk_metrics.record("plan_patched", host=patch.host, kind=patch.kind)
            return PatchResult(
                invalidated_step_ids=invalidated,
                revision=plan.revision,
                applied=True,
                events=[_step_event(plan, s).model_dump() for s in invalidated_steps],
            )

    # ── ADR-0208: canonical lifecycle surface ──────────────────────────────

    async def record_late_callback(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        callback_turn_id: str,
        active_turn_id: str = "",
        host: PlanHost = "pi",
    ) -> None:
        """Journal a late tool callback (its originating turn is settled).

        Attribution discipline (#1407): the callback never re-opens the
        settled turn, never advances a phase, and never lands on the
        successor turn — it becomes a ``tool_late`` event attributed to the
        ORIGINAL turn (``causal_id`` keeps it idempotent per tool_call_id).
        Observability only; step evidence is deliberately skipped upstream.
        """
        if not self.session_id:
            return
        async with _session_scope(self.session_id, None) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None or (lock is not None and lock.lost):
                return
            appended = _event(
                plan, "tool_late",
                host=host, turn_id=callback_turn_id, causal_id=tool_call_id,
                note=f"{tool_name} arrived after settle (active={active_turn_id or 'none'})"[:200],
                detail={
                    "tool": tool_name[:64],
                    "active_turn": active_turn_id[:64],
                },
            )
            if not appended:
                return
            hk_metrics.record("late_callback", host=host)
            await _save_if_fresh(plan, store=self._store, host=host)

    async def advance_turn_phase(
        self,
        turn_id: str,
        trigger: str,
        *,
        host: PlanHost = "unknown",
        lock: Any = None,
    ) -> str:
        """Public canonical-phase driver (tests, direction-5 re-entry).

        Table-validated like every other advance; returns the resulting
        phase ("" on refusal / inactive turn) — never raises on semantics.
        """
        if not self.session_id or not turn_id or not trigger:
            return ""
        async with _session_scope(self.session_id, lock) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None or (lock is not None and lock.lost):
                return ""
            record = _running_turn(plan, turn_id)
            history_len = len(record.phase_history) if record is not None else 0
            target = _advance_unlocked(plan, turn_id, trigger, host=host)
            # Refusals change no phase but DO append an audit row — persist
            # them too (fail-closed must be observable, not in-memory only).
            changed = bool(target) or (
                record is not None and len(record.phase_history) > history_len
            )
            if changed:
                await _save_if_fresh(plan, store=self._store, host=host)
            return target

    async def turn_context(
        self,
        turn_id: str,
        *,
        mission_ref: str = "",
        governor_hints: Optional[dict] = None,
        knowledge_refs: Optional[List[str]] = None,
    ) -> Optional[HarnessTurnContext]:
        """Build the bounded canonical turn context (K2 projection).

        Pure read over the envelope; cross-domain refs (mission/governor/
        knowledge) are caller-injected so the kernel never imports those
        services.
        """
        plan = await load_session_plan(self.session_id, store=self._store)
        if plan is None:
            return None
        from app.services.harness_kernel.context import build_turn_context

        return build_turn_context(
            plan,
            turn_id,
            mission_ref=mission_ref,
            governor_hints=governor_hints,
            knowledge_refs=knowledge_refs,
        )

    # ── K7: checkpoints ───────────────────────────────────────────────────

    async def _checkpoint_unlocked(
        self,
        plan: SessionPlan,
        *,
        reason: str,
        host: PlanHost,
        turn_id: str,
    ) -> str:
        """Archive the current envelope into the rotating checkpoint ring.

        Aliases ``session-plan-cp:{0..2}`` (bounded, overwritten in place —
        same discipline as the supersede history archive). The checkpoint id
        rides the envelope's recovery block so resume can tell what it is
        standing on. Caller holds the session lock.
        """
        backend = self._store if self._store is not None else _default_store()
        slot = int(plan.revision or 0) % MAX_CHECKPOINT_SLOTS
        alias = f"{_CP_ALIAS_PREFIX}{slot}"
        checkpoint_id = f"cp-{plan.envelope_id}-r{plan.revision}"
        payload = plan.model_dump()
        try:
            ref_id = await backend.resolve_alias(self.session_id, alias)
            if ref_id == alias:
                new_ref = await backend.store(
                    self.session_id, payload, prefix="sessionplan"
                )
                await backend.set_alias(self.session_id, new_ref, alias)
            else:
                if not await backend.overwrite(self.session_id, ref_id, payload):
                    new_ref = await backend.store(
                        self.session_id, payload, prefix="sessionplan"
                    )
                    await backend.set_alias(self.session_id, new_ref, alias)
        except Exception:  # noqa: BLE001 — checkpoint 是增值，绝不阻断 turn
            logger.warning(
                "[HarnessKernel] checkpoint archive failed session=%s",
                self.session_id, exc_info=True,
            )
            return ""
        plan.recovery.checkpoint_id = checkpoint_id
        plan.recovery.checkpoint_at = _now()
        _journal(
            plan, "checkpoint", host=host, turn_id=turn_id,
            note=f"{checkpoint_id} ({reason[:120]})",
        )
        hk_metrics.record("checkpoint_written", host=host)
        return checkpoint_id

    async def checkpoint(
        self,
        *,
        reason: str = "manual",
        host: PlanHost = "unknown",
        turn_id: str = "",
        lock: Any = None,
    ) -> str:
        """Public checkpoint entry (lock-scoped wrapper)."""
        async with _session_scope(self.session_id, lock) as lock:
            plan = await load_session_plan(self.session_id, store=self._store)
            if plan is None or (lock is not None and lock.lost):
                return ""
            cid = await self._checkpoint_unlocked(
                plan, reason=reason, host=host, turn_id=turn_id
            )
            if lock is not None and lock.lost:
                return cid
            await _save_if_fresh(plan, store=self._store, host=host)
            return cid

    async def list_checkpoints(self) -> List[dict]:
        """Best-effort inventory of the checkpoint ring (newest revision first)."""
        backend = self._store if self._store is not None else _default_store()
        out: List[dict] = []
        for slot in range(MAX_CHECKPOINT_SLOTS):
            alias = f"{_CP_ALIAS_PREFIX}{slot}"
            try:
                ref_id = await backend.resolve_alias(self.session_id, alias)
                if ref_id == alias:
                    continue
                data = await backend.get(self.session_id, ref_id)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                out.append(
                    {
                        "alias": alias,
                        "envelope_id": data.get("envelope_id"),
                        "revision": data.get("revision"),
                        "updated_at": data.get("updated_at"),
                    }
                )
        out.sort(key=lambda item: int(item.get("revision") or 0), reverse=True)
        return out


def _default_store() -> Any:
    from app.services.session_data import session_data_manager

    return session_data_manager


def get_runtime(session_id: str, *, store: Any = None) -> GISSessionRuntime:
    """Module entry: one runtime facade per (session, call site)."""
    return GISSessionRuntime(session_id, store=store)
