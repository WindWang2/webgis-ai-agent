"""Delegation Protocol 生产适配器（H07/ADR-0216）。

把既有 ``SubagentDispatcher``（ADR-0104 的 ChatEngine 微会话委派）包装成
``SubagentRunner``，使 harness 程序化委派与 ``spawn_subagent`` 工具单发
路径与 swarm 集群共用同一 ``DelegationGateway`` 执行语义（lease/deadline/
取消传播/台账/receipt 验证），不再各说各话。

红线：
- legacy ``SubagentDispatcher.run`` 内部（预算/角色/取消令牌语义）零改动
  —— 适配器只做进出两侧的契约映射；
- 工具结果 dict 逐字节兼容：``run_single_delegation`` 返回原
  ``SubagentResult``（委派因果 id 写入既有自由字段 ``lineage``）；
  字段面不变 —— 但 gateway 终局裁决为 FAILED/CANCELLED 时出口
  ``success``/``error`` 与台账同步（receipt 验证拒绝不得当成功透出）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from app.services.agent_swarm.delegation import (
    DelegationBudget,
    DelegationFailureReason,
    DelegationGateway,
    DelegationLease,
    DelegationOutcome,
    DelegationParent,
    DelegationRequest,
    DelegationStatus,
    FairSlotScheduler,
)
from app.services.agent_swarm.delegation_contracts import (
    MAX_ERROR_CHARS,
    MAX_REFS_PER_RECEIPT,
    MAX_SUMMARY_CHARS,
)

logger = logging.getLogger(__name__)

#: 每会话委派并发上限（spawn_subagent 单发路径的公平性上限；此前该路径
#: 无任何并发约束）。远高于现实负载，只封失控面。
DEFAULT_SESSION_CONCURRENCY = 4
_DEFAULT_GLOBAL_CONCURRENCY = 8
_SESSION_SCHEDULER_ENV = "GIS_DELEGATION_SESSION_CONCURRENCY"

#: 共享调度器（进程级；每会话上限可经 env 收紧，全局上限固定有界）。
_shared_scheduler: Optional[FairSlotScheduler] = None


def _env_session_cap() -> int:
    import os

    raw = os.getenv(_SESSION_SCHEDULER_ENV, "").strip()
    if not raw:
        return DEFAULT_SESSION_CONCURRENCY
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SESSION_CONCURRENCY


def get_shared_scheduler() -> FairSlotScheduler:
    """进程级共享调度器（同会话并发封顶 + 全局上限 + FIFO 公平）。"""
    global _shared_scheduler
    if _shared_scheduler is None:
        _shared_scheduler = FairSlotScheduler(
            global_max=_DEFAULT_GLOBAL_CONCURRENCY,
            per_session_max=_env_session_cap(),
        )
    return _shared_scheduler


def reset_shared_scheduler_for_tests() -> None:
    global _shared_scheduler
    _shared_scheduler = None


_shared_gateway: Optional[DelegationGateway] = None


def get_shared_gateway() -> DelegationGateway:
    """进程级共享网关（共享调度器 + 共享有界台账；因果/metrics 聚合面）。"""
    global _shared_gateway
    if _shared_gateway is None:
        _shared_gateway = DelegationGateway(scheduler=get_shared_scheduler())
    return _shared_gateway


def reset_shared_gateway_for_tests() -> None:
    global _shared_gateway
    _shared_gateway = None


def outcome_from_subagent_result(
    lease: DelegationLease, result: Any, started: float
) -> DelegationOutcome:
    """legacy ``SubagentResult`` → ``DelegationOutcome``（词表对齐映射）。

    success → SUCCEEDED（refs-only 过滤）；诚实失败按 error 语义折算
    （cancelled / budget_exceeded / 其余 failed）。
    """
    success = bool(getattr(result, "success", False))
    error = str(getattr(result, "error", "") or "")
    refs = [
        r
        for r in (getattr(result, "refs", None) or [])
        if isinstance(r, str) and r.startswith("ref:")
    ][:MAX_REFS_PER_RECEIPT]
    if success:
        status = DelegationStatus.SUCCEEDED
        reason = ""
    elif error.startswith("cancelled"):
        status = DelegationStatus.CANCELLED
        reason = DelegationFailureReason.CANCELLED
    elif error.startswith("budget_exceeded"):
        status = DelegationStatus.FAILED
        reason = DelegationFailureReason.BUDGET_EXCEEDED
    else:
        status = DelegationStatus.FAILED
        reason = DelegationFailureReason.CHILD_CRASH
    return DelegationOutcome(
        delegation_id=lease.delegation_id,
        generation=lease.generation,
        lease_id=lease.lease_id,
        status=status,
        failure_reason=reason,
        produced_refs=refs,
        summary=str(getattr(result, "summary", "") or "")[:MAX_SUMMARY_CHARS],
        error=error[:MAX_ERROR_CHARS],
        attempts=1,
        session_id=lease.session_id,
        wall_time_s=time.time() - started,
        finished_at=time.time(),
        runner_extras={
            "reasoning": str(getattr(result, "reasoning", "") or ""),
            "budget_usage": dict(getattr(result, "budget_usage", None) or {}),
            "lineage": dict(getattr(result, "lineage", None) or {}),
        },
    )


class SubagentDispatcherRunner:
    """legacy ``SubagentDispatcher.run`` → ``SubagentRunner`` 适配器。

    内部语义（预算/角色/取消令牌）零改动；本类只做进出两侧契约映射，
    异常一律折算 child_crash 诚实失败。
    """

    def __init__(
        self,
        registry: Any,
        session_id: str,
        *,
        task: str,
        role: Optional[str] = None,
        domains: Optional[list[str]] = None,
        extra_tools: Optional[list[str]] = None,
        max_rounds: int = 10,
    ) -> None:
        self._registry = registry
        self._session_id = session_id
        self._task = task
        self._role = role
        self._domains = domains
        self._extra_tools = extra_tools
        self._max_rounds = max_rounds
        #: runner 崩溃时为 None。工具 dict 兼容出口优先用 raw SubagentResult
        #: （budget_usage/lineage 值型保真）；DelegationOutcome 只承载
        #: 台账/验证/因果面。
        self.last_raw_result: Any = None

    async def run(self, lease: DelegationLease) -> DelegationOutcome:
        started = time.time()
        try:
            from app.services.subagent import SubagentDispatcher

            dispatcher = SubagentDispatcher(self._registry, self._session_id)
            result = await dispatcher.run(
                task=self._task,
                domains=self._domains,
                extra_tools=self._extra_tools,
                max_rounds=self._max_rounds,
                role=self._role,
            )
        except Exception as exc:  # noqa: BLE001 — 崩溃折算诚实失败
            logger.warning(
                "[Delegation] legacy dispatcher raised session=%s: %s",
                self._session_id,
                exc,
            )
            return DelegationOutcome(
                delegation_id=lease.delegation_id,
                generation=lease.generation,
                lease_id=lease.lease_id,
                status=DelegationStatus.FAILED,
                failure_reason=DelegationFailureReason.CHILD_CRASH,
                error=f"subagent runtime error: {exc}"[:MAX_ERROR_CHARS],
                session_id=lease.session_id,
                wall_time_s=time.time() - started,
                finished_at=time.time(),
            )
        self.last_raw_result = result
        return outcome_from_subagent_result(lease, result, started)


def _result_from_outcome(outcome: DelegationOutcome) -> Any:
    """DelegationOutcome → legacy ``SubagentResult``（工具 dict 逐字节兼容）。"""
    from app.services.subagent import SubagentResult

    lineage = dict(outcome.runner_extras.get("lineage") or {})
    lineage.setdefault("delegation_id", outcome.delegation_id)
    lineage.setdefault("delegation_generation", outcome.generation)
    lineage.setdefault("delegation_status", outcome.status)
    if outcome.verdict_reasons:
        lineage.setdefault("delegation_verdicts", list(outcome.verdict_reasons)[:4])
    budget_usage = dict(outcome.runner_extras.get("budget_usage") or {})
    return SubagentResult(
        success=(outcome.status == DelegationStatus.SUCCEEDED),
        summary=outcome.summary,
        refs=list(outcome.produced_refs),
        reasoning=str(outcome.runner_extras.get("reasoning") or ""),
        error=outcome.error or None,
        budget_usage=budget_usage,
        lineage=lineage,
    )


def _enrich_lineage(result: Any, outcome: DelegationOutcome) -> Any:
    """raw SubagentResult 的 lineage 增量委派因果 id（拷贝后写）。

    出口裁决同步（review H-1）：gateway 终局裁决非终态成功（FAILED/
    CANCELLED/EXPIRED，含 receipt 验证拒绝）时，raw ``success=True``
    不得原样透出 —— ``success``/``error`` 与台账同一口径（缺证据不得
    当成功，见 delegation.py ``verify_outcome``）。裁决对 raw 的
    success/error 就地改写：raw 本即单次出口、无共享读者，"不改原"
    纪律仅对 lineage 成立。
    """
    lineage = dict(getattr(result, "lineage", None) or {})
    lineage.setdefault("delegation_id", outcome.delegation_id)
    lineage.setdefault("delegation_generation", outcome.generation)
    lineage["delegation_status"] = outcome.status
    if outcome.verdict_reasons:
        lineage.setdefault("delegation_verdicts", list(outcome.verdict_reasons)[:4])
    if bool(getattr(result, "success", False)) and not outcome.is_terminal_success():
        result.success = False
        if not getattr(result, "error", None):
            result.error = outcome.error or "delegation rejected by gateway"
    result.lineage = lineage
    return result


async def run_single_delegation(
    registry: Any,
    session_id: str,
    *,
    task: str,
    role: Optional[str] = None,
    domains: Optional[list[str]] = None,
    extra_tools: Optional[list[str]] = None,
    max_rounds: int = 10,
    expected_outputs: Optional[list[str]] = None,
    scheduler: Optional[FairSlotScheduler] = None,
    gateway: Optional[DelegationGateway] = None,
) -> tuple[Any, dict]:
    """单发委派（spawn_subagent 工具路径）：gateway 全语义 + 字典兼容出口。

    返回 ``(SubagentResult, delegation_snapshot)``。runner 正常返回时
    直接回传 raw ``SubagentResult``（budget_usage/lineage 值型与直接调用
    ``SubagentDispatcher.run`` 逐字段同形，因果 id 增量进 lineage 拷贝）；
    gateway 终局裁决 FAILED/CANCELLED（含 receipt 验证拒绝）时，出口
    success/error 与台账同口径。仅 runner 崩溃（无 raw）时按 outcome
    合成诚实失败 result。墙钟预算仍由 legacy dispatcher 内部执行
    （deadline_s 不双写）。
    """
    request = DelegationRequest(
        delegation_id=f"dg-{uuid.uuid4().hex[:12]}",
        goal=task,
        role=role or "",
        parent=DelegationParent(session_id=session_id),
        expected_outputs=list(expected_outputs or [])[:12],
        # deadline None：legacy 预算通道（角色墙钟/轮次）已强制，不重复计时
        deadline_s=None,
        budget=DelegationBudget(),
    )
    runner = SubagentDispatcherRunner(
        registry,
        session_id,
        task=task,
        role=role,
        domains=domains,
        extra_tools=extra_tools,
        max_rounds=max_rounds,
    )
    gw = gateway or (
        DelegationGateway(scheduler=scheduler)
        if scheduler is not None
        else get_shared_gateway()
    )
    try:
        outcome = await gw.execute(request, runner)
    except asyncio.CancelledError:
        # gateway 已落账 CANCELLED 并按 asyncio 纪律原样上抛 —— 此处透传
        raise
    raw = runner.last_raw_result
    if raw is not None:
        result = _enrich_lineage(raw, outcome)
    else:
        result = _result_from_outcome(outcome)
    snapshot = gw.ledger.snapshot()
    return result, snapshot
