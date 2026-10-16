"""CanonicalPlan → SessionPlan projection adapter (K4, ADR-0180 D-006).

One-way projection: the legacy ChatEngine keeps CanonicalPlan as its own
source of truth (``plan-current`` store); this adapter mirrors the
host-neutral semantics into the SAME SessionPlan envelope the Pi path uses,
so both hosts produce equivalent Harness plan evidence (parity KPI, frontend
session-plan panel on the legacy host).

It never writes CanonicalPlan, never imports Pi-domain modules, and every
entry point is exception-safe (projection is additive disclosure for the
legacy path — a failure must never break a legacy turn).

Steps map via ``step_status_from_canonical``; step ids are ``l{n}`` (legacy
plan steps are n-indexed) so they never collide with chapter-materialized
``step-<capability>`` ids on a shared session.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any, List, Optional

from app.services.distributed_lock import session_lock_registry
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import (
    MAX_DECISIONS,
    MAX_STEPS,
    PlanDecision,
    PlanStep,
    StepEvidence,
    push_bounded,
    step_status_from_canonical,
)
from app.services.session_plan import (
    SESSION_PLAN_STEP,
    SessionPlan,
    SessionPlanEvent,
    _ensure_slot_unlocked,
    ensure_session_plan_slot,
    load_session_plan,
    save_session_plan,
)

logger = logging.getLogger(__name__)

LEGACY_STEP_ID_FMT = "l{}"

#: 锁外 settle 效果的墙钟预算（终验 final gate 可能跑诊断/修复闭环，预算
#: 从宽；超时后 shield 内的结算继续完成，宿主清理路径不被拖死）。
_SETTLE_AFTER_LOCK_BUDGET_S = 30.0

# ── 环境锁绑定（engine 已持会话锁的透传通道）─────────────────────────────
# legacy 引擎在整个 turn 期间持有 ``session_lock``（非重入）；kernel/adapter
# 挂点分布在该作用域内的多个嵌套函数里（_maybe_plan/_flush_plan/turn 边界），
# 逐点传 lock 对象侵入面太大。engine 进入锁作用域时 bind，finally reset；
# adapter 各入口经 ``_current_lock`` 解析 —— 会话匹配则锁透传，否则自取。
_ENGINE_LOCK: ContextVar[Any] = ContextVar("hk_engine_lock", default=None)


def bind_engine_lock(session_id: str, lock: Any) -> Token:
    return _ENGINE_LOCK.set((session_id, lock))


def unbind_engine_lock(token: Token) -> None:
    try:
        _ENGINE_LOCK.reset(token)
    except ValueError:
        # 取消/断连时 turn 收尾可能在另一个 task context 上执行
        # （生成器在别处被 close / task 被取消），token 与当前 context
        # 不匹配 → reset 抛 ValueError。ContextVar 在单事件循环内没有
        # 跨 context 的解绑原语，置回默认值即等价于解绑（本通道每 turn
        # 只 bind 一次，不存在需要保留的外层绑定）。
        _ENGINE_LOCK.set(None)


def _current_lock(session_id: str) -> Any:
    held = _ENGINE_LOCK.get()
    if held is not None and held[0] == session_id:
        return held[1]
    return None


def _resolve_lock(session_id: str, lock: Any) -> Any:
    return lock if lock is not None else _current_lock(session_id)



def _step_event(plan: SessionPlan, step: PlanStep) -> SessionPlanEvent:
    latest = step.latest_evidence()
    return SessionPlanEvent(
        event=SESSION_PLAN_STEP,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "step_id": step.id,
            "goal": step.goal,
            "capability": step.capability,
            "tool": step.tool,
            "status": step.status,
            "attempts": step.attempts,
            "ref": latest.ref if latest else "",
            "host": step.host,
            "turn_id": step.turn_id,
        },
    )


def _journal(
    plan: SessionPlan, kind: str, *, note: str = "", detail: Optional[dict] = None
) -> None:
    push_bounded(
        plan.decisions,
        PlanDecision(kind=kind, at=time.time(), host="chatengine", note=note[:300],
                     detail=detail or {}),
        MAX_DECISIONS,
    )


async def project_orchestrator_plan(
    session_id: str,
    plan: Any,
    *,
    turn_id: str = "",
    lock: Any = None,
) -> List[SessionPlanEvent]:
    """Mirror an orchestrator ``Plan`` (post ``_maybe_plan``) into SessionPlan.

    Creates the envelope slot when absent and (re)builds the ``l{n}`` steps
    from the plan's steps. Existing step state survives by step id (a re-plan
    that keeps a step keeps its evidence).
    """
    if not session_id or plan is None:
        return []
    events: List[SessionPlanEvent] = []
    lock = _resolve_lock(session_id, lock)

    @asynccontextmanager
    async def _scope():
        if lock is not None:
            yield lock
            return
        async with session_lock_registry.lock(
            session_id, fail_on_degraded=True
        ) as acquired:
            yield acquired

    async with _scope() as lock:
        # caller-held lock 时不经 ensure_session_plan_slot（其 miss 路径会
        # 再取非重入锁）—— 直接走无锁 load-or-create。
        envelope = (
            await _ensure_slot_unlocked(session_id, store=None)
            if lock is not None
            else await ensure_session_plan_slot(session_id)
        )
        now = time.time()
        if lock is not None and lock.lost:
            return []
        if not envelope.user_goal:
            envelope.user_goal = str(getattr(plan, "intent", "") or "")
            _journal(envelope, "legacy_projected", note="plan projected (new)")
        existing = {s.id: s for s in envelope.steps}
        steps: List[PlanStep] = []
        for s in getattr(plan, "steps", None) or []:
            sid = LEGACY_STEP_ID_FMT.format(int(getattr(s, "n", 0) or 0))
            prev = existing.get(sid)
            status = "succeeded" if bool(getattr(s, "done", False)) else "pending"
            if prev is not None:
                if status == "succeeded" and prev.status != "succeeded":
                    prev.status = "succeeded"
                    prev.attach_evidence(
                        StepEvidence(tool=str(getattr(s, "tool", "") or ""), at=now)
                    )
                    events.append(_step_event(envelope, prev))
                steps.append(prev)
                continue
            step = PlanStep(
                id=sid,
                goal=str(getattr(s, "goal", "") or "")[:300],
                tool=str(getattr(s, "tool", "") or ""),
                tool_binding=[
                    str(t) for t in (getattr(s, "tool_binding", None) or [])
                ],
                status=status,  # type: ignore[arg-type]
                depends_on=[],
                host="chatengine",
                turn_id=turn_id,
                created_at=now,
                updated_at=now,
            )
            steps.append(step)
        if envelope.steps != steps:
            # review R4：越界赋值绕过 max_length 会在下次 load 时整信封作废
            # （validate 失败 → load None → 静默新建空信封）—— 构造期截断。
            envelope.steps = steps[:MAX_STEPS]
            for st in steps:
                events.append(_step_event(envelope, st))
        hk_metrics.record("host_parity", host="chatengine")
        if lock is not None and lock.lost:
            return []
        await save_session_plan(envelope)
        return events


async def project_canonical_flush(
    session_id: str, canonical: Any, *, lock: Any = None
) -> None:
    """Mirror step-level truth at ``_flush_plan`` time (canonical is truth).

    Marks kernel steps ``l{n}`` succeeded per CanonicalStep.status and
    journals the flush — this is what keeps the projection convergent when
    ``advance_step`` ticked steps mid-turn.
    """
    if not session_id or canonical is None:
        return
    lock = _resolve_lock(session_id, lock)

    @asynccontextmanager
    async def _scope():
        if lock is not None:
            yield lock
            return
        async with session_lock_registry.lock(
            session_id, fail_on_degraded=True
        ) as acquired:
            yield acquired

    async with _scope() as lock:
        envelope = await load_session_plan(session_id)
        if envelope is None or (lock is not None and lock.lost):
            return
        now = time.time()
        changed = False
        for cs in getattr(canonical, "steps", None) or []:
            sid = LEGACY_STEP_ID_FMT.format(int(getattr(cs, "n", 0) or 0))
            step = next((s for s in envelope.steps if s.id == sid), None)
            status = step_status_from_canonical(str(getattr(cs.status, "value", cs.status) or "pending"))
            if step is None:
                step = PlanStep(
                    id=sid,
                    goal=str(getattr(cs, "goal", "") or "")[:300],
                    tool=str(getattr(cs, "tool", "") or ""),
                    status=status,  # type: ignore[arg-type]
                    host="chatengine",
                    created_at=now,
                    updated_at=now,
                )
                push_bounded(envelope.steps, step, MAX_STEPS)
                changed = True
                continue
            if step.status != status:
                if status == "succeeded" and step.status != "succeeded":
                    step.attach_evidence(
                        StepEvidence(tool=str(getattr(cs, "tool", "") or ""), at=now)
                    )
                step.status = status  # type: ignore[assignment]
                step.updated_at = now
                changed = True
        if changed:
            _journal(envelope, "legacy_projected", note="canonical flush mirrored")
            if lock is not None and not lock.lost:
                await save_session_plan(envelope)


async def begin_turn(
    session_id: str, turn_id: str, *, message: str = "", lock: Any = None
) -> None:
    """Legacy turn start — same kernel journal the Pi path uses.

    ``lock``: the engine's already-held session lock (engine hooks run inside
    its ``session_lock`` scope; the lock is not reentrant — pass through).
    """
    lock = _resolve_lock(session_id, lock)
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await get_runtime(session_id).begin_turn(
            turn_id, host="chatengine", message=message, lock=lock
        )
    except Exception:  # noqa: BLE001 — 投影绝不破坏 legacy 回合
        logger.warning("[LegacyAdapter] begin_turn failed session=%s", session_id, exc_info=True)


def status_from_outcome(rt_ev: Any) -> str:
    """TurnEvidence outcome → kernel TurnStatus (unsettled → interrupted)."""
    try:
        outcome = rt_ev.outcome.outcome
    except Exception:  # noqa: BLE001
        return "interrupted"
    name = str(getattr(outcome, "name", "") or "")
    return {
        "SUCCEEDED": "completed",
        "CANCELLED": "cancelled",
        "FAILED": "failed",
    }.get(name, "interrupted")


async def end_turn(
    session_id: str, turn_id: str, *, status: str, lock: Any = None
) -> None:
    """Legacy turn settle — checkpoint like the Pi path (lock pass-through)."""
    lock = _resolve_lock(session_id, lock)
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await get_runtime(session_id).end_turn(
            turn_id,
            host="chatengine",
            status=status,  # type: ignore[arg-type]
            lock=lock,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[LegacyAdapter] end_turn failed session=%s", session_id, exc_info=True)


async def safe_end_turn(session_id: str, turn_id: str, *, status: str) -> None:
    """Shielded settle for engine finally blocks (review R5).

    A re-delivered cancellation during teardown must neither leave the turn
    unsettled (→ mis-flagged ``interrupted`` by the next begin_turn) nor
    propagate out of cleanup. Same shield + budget + swallow discipline as
    the bridge's ``_safe_kernel_end_turn`` / ``_safe_unregister_active_pi_turn``.
    """
    lock = _current_lock(session_id)
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await asyncio.wait_for(
            asyncio.shield(
                asyncio.ensure_future(
                    get_runtime(session_id).end_turn(
                        turn_id,
                        host="chatengine",
                        status=status,  # type: ignore[arg-type]
                        lock=lock,
                    )
                )
            ),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001
        logger.warning("[LegacyAdapter] end_turn failed session=%s", session_id, exc_info=True)


async def settle_after_lock(session_id: str, turn_id: str, *, rt_ev: Any) -> None:
    """ADR-0223 D4：引擎会话锁释放后的 host-neutral settle 效果（never raises）。

    Legacy 此前只有「半个结算」（kernel end_turn + tracker + replay 录制），
    终验 final gate / WorkflowInstance / RuntimeState(turn_settled) / 九域
    checkpoint / 证据链 USER_OUTPUT / parity 全部缺失。本入口让 legacy 与
    Pi 共享 ``harness_kernel.settle`` 的同一结算编排：

    - 引擎在锁内 ``safe_end_turn`` 已预终态化（S1：后继 begin_turn 不得看到
      running），coordinator 以 ``pre_terminated=True`` 收到正规首次调用
      （效果照跑、kernel end_turn 腿诚实记 ``already``）；
    - 效果函数内部自取同一把**非重入**会话锁，故必须等引擎释放锁之后执行；
    - shield + 预算 + 吞异常：重投的 cancellation 不得传播出清理路径。
    """
    if not session_id or not turn_id:
        return
    try:
        from app.services.harness_kernel import settle as hk_settle

        if not hk_settle.settle_effects_enabled():
            return  # kill switch OFF → legacy 保持既有「仅 end_turn」行为
        failure_class = ""
        failure_detail = ""
        try:
            recorder = rt_ev.outcome
            failure_class = str(getattr(recorder, "failure_class", "") or "")[:64]
            failure_detail = str(getattr(recorder, "detail", "") or "")[:200]
        except Exception:  # noqa: BLE001 — 诊断面缺席按空
            pass
        await asyncio.wait_for(
            asyncio.shield(asyncio.ensure_future(hk_settle.settle_turn(
                hk_settle.HarnessSettleInput(
                    session_id=session_id,
                    turn_id=turn_id,
                    host="chatengine",
                    cause=hk_settle.cause_from_legacy_outcome(rt_ev),
                    failure_class=failure_class,
                    failure_detail=failure_detail,
                    pre_terminated=True,
                )
            ))),
            timeout=_SETTLE_AFTER_LOCK_BUDGET_S,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001 — 增值披露绝不破坏 legacy 回合
        logger.warning(
            "[LegacyAdapter] post-lock settle failed session=%s turn=%s",
            session_id, turn_id, exc_info=True,
        )
