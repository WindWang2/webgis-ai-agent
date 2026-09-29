"""Delegation Protocol（H07/ADR-0216）—— 委派请求的生命周期基座。

本模块是 Master→Subagent 委派的**单一执行语义**（lease/预算/deadline/
取消传播/迟到与重复结果隔离/receipt 验证/因果台账），供三条既有委派
路径共用，不新造 Agent 框架、不新造 DAG IR（ADR-0187 D2 纪律不变）：

- swarm 总控（``orchestrator.py``）按本模块的 phase 词表与台账记录每个
  子任务的生命周期（集群并发仍由 SwarmConcurrencyGovernor 承载）；
- harness 程序化委派（``gis_harness/delegation.py``）经
  ``DelegationGateway`` 执行（并发去重/取消收尾由此补齐）；
- LLM 工具 ``spawn_subagent`` 单发路径经 ``delegation_adapters`` 的
  runner 适配器获得同一 facade（工具结果 dict 逐字节兼容）。

失败语义总纲（fail-closed）：acquire/run/verify 任一阶段异常都折算为
携带 ``failure_reason`` 的诚实终态，绝不遗留非终态、绝不让父任务把
单点崩溃误判为成功。``CancelledError`` 是调用方取消语义：本模块先
落账 CANCELLED 再原样上抛（asyncio 纪律），子 runner 一并终止。

Zero Big Data in Context：请求结构上没有 payload 字段；上下文只有
``ref:`` 提货券（validator fail-closed），与 delegation_contracts 同纪。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.agent_swarm.delegation_contracts import (
    MAX_ERROR_CHARS,
    MAX_REF_LIST,
    MAX_REFS_PER_RECEIPT,
    MAX_SUMMARY_CHARS,
    MAX_TASK_GOAL_CHARS,
    SwarmContractError,
)

logger = logging.getLogger(__name__)

# ─────────────────────────── 有界常量 ───────────────────────────

#: 单委派允许声明的能力上限（allow-list；空 = 不声明 = 仅审计面缺省）。
MAX_CAPABILITIES_PER_REQUEST = 12
#: 台账单委派 phase 历史上限（环形裁剪最老）。
MAX_PHASE_HISTORY = 16
#: 台账保留的代际终态上限（防重复 offer 无界增长）。
MAX_GENERATIONS_REMEMBERED = 4
#: 台账迟到/重复结果隔离区上限（有界披露，超出仅计数）。
MAX_QUARANTINE_RECORDS = 8
#: 资源类词表（H06 resource envelope 的预留接缝：标签 + 调度上限）。
RESOURCE_CLASSES = ("light", "heavy")
#: 调度等待队列深度上限（有界背压：满员诚实拒绝，不无界积压）。
MAX_QUEUE_WAITERS = 32


class DelegationContractError(SwarmContractError):
    """委派协议契约违约（非法 phase 转移 / 非法请求）。fail-closed。"""


# ─────────────────────────── phase 状态机 ───────────────────────────


class DelegationPhase:
    """单次委派请求的生命周期词表（请求级，不是图节点级）。

    CREATED→ACQUIRING→RUNNING→终态；ACQUIRING 可直达全部终态
    （acquire 崩溃/取消/排队过期不经过 RUNNING）。终态不可再转移。
    """

    CREATED = "created"
    ACQUIRING = "acquiring"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


_TERMINAL_PHASES = frozenset({
    DelegationPhase.SUCCEEDED,
    DelegationPhase.DEGRADED,
    DelegationPhase.FAILED,
    DelegationPhase.CANCELLED,
    DelegationPhase.EXPIRED,
})

#: 合法转移表（fail-closed；其它一律 DelegationContractError）。
LEGAL_PHASE_TRANSITIONS: dict[str, frozenset[str]] = {
    DelegationPhase.CREATED: frozenset({DelegationPhase.ACQUIRING}),
    DelegationPhase.ACQUIRING: frozenset({
        DelegationPhase.RUNNING,
        DelegationPhase.SUCCEEDED,  # 零执行委派（runner 秒回）诚实记账
        DelegationPhase.DEGRADED,
        DelegationPhase.FAILED,
        DelegationPhase.CANCELLED,
        DelegationPhase.EXPIRED,
    }),
    DelegationPhase.RUNNING: frozenset(_TERMINAL_PHASES),
}
for _t in _TERMINAL_PHASES:
    LEGAL_PHASE_TRANSITIONS.setdefault(_t, frozenset())


def check_delegation_transition(from_phase: str, to_phase: str) -> None:
    """非法转移即 ``DelegationContractError``（fail-loud —— 调用序 bug 探针）。"""
    if to_phase not in LEGAL_PHASE_TRANSITIONS.get(from_phase, frozenset()):
        raise DelegationContractError(
            f"非法委派 phase 转移 {from_phase}->{to_phase}"
        )


def is_terminal_phase(phase: str) -> bool:
    return phase in _TERMINAL_PHASES


# ─────────────────────────── 请求 / 租约 / 结果 ───────────────────────────


class DelegationBudget(BaseModel):
    """单次委派的资源预算（墙钟必填语义由 deadline_s 承载）。

    ``max_tool_calls`` 为可选 roll-up 上限：runner 适配器自行对接既有
    预算通道（subagent_roles）；本模块只在 outcome 记账，不重复执行。
    """

    model_config = ConfigDict(extra="forbid")

    max_tool_calls: Optional[int] = Field(default=None, ge=0)
    max_total_tokens: Optional[int] = Field(default=None, ge=0)


class DelegationParent(BaseModel):
    """父侧因果锚点（parent/child 追因链；全部有界标识符）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(max_length=120)
    turn_id: str = Field(default="", max_length=120)
    run_id: str = Field(default="", max_length=120)


class DelegationRequest(BaseModel):
    """委派请求（子代理的唯一合法启动面；结构上无法携带 payload）。

    - ``context_refs`` 只收 ``ref:`` 提货券（大地图/GeoJSON 一律 ref）；
    - 无自由 payload 字段（extra="forbid"）—— 秘密/大对象没有合法入口；
    - ``deadline_s`` 缺省 None = 由 runner 自身墙钟预算承载（legacy
      SubagentDispatcher 语义）；显式给出时 gateway 强制（排队 + 运行
      共享同一 deadline，排队过期为 EXPIRED 而非无限 READY）。
    """

    delegation_id: str = Field(pattern=r"^dg-[a-zA-Z0-9_.:#-]{1,80}$")
    goal: str = ""
    role: str = ""
    resource_class: str = "light"
    allowed_capabilities: list[str] = Field(
        default_factory=list, max_length=MAX_CAPABILITIES_PER_REQUEST
    )
    context_refs: list[str] = Field(
        default_factory=list, max_length=MAX_REF_LIST
    )
    budget: DelegationBudget = Field(default_factory=DelegationBudget)
    deadline_s: Optional[float] = Field(default=None, gt=0)
    parent: DelegationParent
    expected_outputs: list[str] = Field(default_factory=list, max_length=12)
    side_effect: str = "pure"
    priority: int = Field(default=5, ge=-10, le=10)
    created_at: float = 0.0

    model_config = ConfigDict(extra="forbid")  # 无 payload 走私入口（fail-closed）

    @field_validator("goal")
    @classmethod
    def _clip_goal(cls, v: str) -> str:
        return v[:MAX_TASK_GOAL_CHARS]

    @field_validator("context_refs")
    @classmethod
    def _refs_only(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.startswith("ref:"):
                raise DelegationContractError(
                    "DelegationRequest.context_refs 只收 ref: 提货券"
                    f"（Zero Big Data in Context）: {item[:48]!r}"
                )
        return v

    @field_validator("resource_class")
    @classmethod
    def _known_resource_class(cls, v: str) -> str:
        if v not in RESOURCE_CLASSES:
            raise DelegationContractError(
                f"未知资源类: {v!r}（合法：{RESOURCE_CLASSES}）"
            )
        return v

    @field_validator("role")
    @classmethod
    def _clip_role(cls, v: str) -> str:
        return v[:64]


class DelegationLease(BaseModel):
    """已获取的执行槽位（ACQUIRING→RUNNING 的凭证）。

    ``generation`` 是同 delegation_id 的单调代际（harness 重试等场景
    register 一次 +1）；迟到结果按 generation 隔离，不污染新一代。
    """

    lease_id: str
    delegation_id: str
    generation: int = Field(ge=1)
    session_id: str
    resource_class: str = "light"
    acquired_at: float = 0.0
    expires_at: Optional[float] = None


class DelegationStatus:
    """结果终态词表（与 SwarmReceiptStatus 对齐 + 取消/过期显式化）。"""

    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DelegationFailureReason:
    """失败原因词表（可追因的最小分类；映射见 ``to_swarm_error_code``）。"""

    ACQUIRE_CRASH = "acquire_crash"          # 槽位获取阶段非取消异常
    CHILD_CRASH = "child_crash"              # runner 执行阶段异常
    TIMEOUT = "timeout"                      # 运行超时（deadline 内无结果）
    EXPIRED = "expired"                      # 排队即过期（未进入运行）
    CANCELLED = "cancelled"                  # 父侧取消
    BUDGET_EXCEEDED = "budget_exceeded"      # runner 报告预算耗尽
    RECEIPT_INVALID = "receipt_invalid"      # receipt 验证拒绝（缺证据等)
    QUEUE_FULL = "queue_full"                # 有界背压：等待队列满员诚实拒绝
    LEDGER_CRASH = "ledger_crash"            # 台账推进异常（观测面违约折算）


#: 失败结果允许的 reason 词表（终态 CANCELLED/EXPIRED 单列，不进本表）。
FAILURE_REASONS = frozenset({
    DelegationFailureReason.ACQUIRE_CRASH,
    DelegationFailureReason.CHILD_CRASH,
    DelegationFailureReason.TIMEOUT,
    DelegationFailureReason.EXPIRED,
    DelegationFailureReason.CANCELLED,
    DelegationFailureReason.BUDGET_EXCEEDED,
    DelegationFailureReason.RECEIPT_INVALID,
    DelegationFailureReason.QUEUE_FULL,
    DelegationFailureReason.LEDGER_CRASH,
})

#: receipt 验证裁决词表。
VERDICT_OK = "ok"
VERDICT_DEGRADED = "degraded"
VERDICT_REJECTED = "rejected"

#: 警告条数上限（有界披露）。
MAX_WARNINGS = 8


class DelegationOutcome(BaseModel):
    """委派结果（成功/降级/失败/取消/过期统一出口；payload 永不内联）。"""

    delegation_id: str
    generation: int = Field(default=1, ge=1)
    lease_id: str = ""
    status: str
    failure_reason: str = ""
    produced_refs: list[str] = Field(
        default_factory=list, max_length=MAX_REFS_PER_RECEIPT
    )
    summary: str = ""
    error: str = ""
    warnings: list[str] = Field(default_factory=list, max_length=MAX_WARNINGS)
    capabilities_used: list[str] = Field(
        default_factory=list, max_length=MAX_CAPABILITIES_PER_REQUEST
    )
    verdict: str = VERDICT_OK
    verdict_reasons: list[str] = Field(default_factory=list, max_length=8)
    attempts: int = 1
    wall_time_s: float = 0.0
    finished_at: float = 0.0
    # 因果链（parent/child 追因；全部有界标识符）
    session_id: str = Field(default="", max_length=120)
    parent_turn_id: str = Field(default="", max_length=120)
    parent_run_id: str = Field(default="", max_length=120)
    # runner 适配器的有界附加记账（budget_usage/reasoning/lineage 摘要；
    # 只收标量短值，禁止 payload 走私 —— validator 有界化）
    runner_extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runner_extras")
    @classmethod
    def _bound_extras(cls, v: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, val in list(v.items())[:12]:
            if isinstance(val, dict):
                out[str(k)[:40]] = {
                    str(kk)[:40]: str(vv)[:120] for kk, vv in list(val.items())[:8]
                }
            elif isinstance(val, (list, tuple)):
                out[str(k)[:40]] = [str(x)[:120] for x in list(val)[:8]]
            else:
                out[str(k)[:40]] = str(val)[:200]
        return out

    @field_validator("produced_refs")
    @classmethod
    def _refs_only(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.startswith("ref:"):
                raise DelegationContractError(
                    f"DelegationOutcome.produced_refs 只收 ref: 提货券: {item[:48]!r}"
                )
        return v

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, v: str) -> str:
        return v[:MAX_SUMMARY_CHARS]

    @field_validator("error")
    @classmethod
    def _clip_error(cls, v: str) -> str:
        return v[:MAX_ERROR_CHARS]

    def is_terminal_success(self) -> bool:
        return self.status in (DelegationStatus.SUCCEEDED, DelegationStatus.DEGRADED)


def to_swarm_error_code(outcome: DelegationOutcome) -> str:
    """DelegationFailureReason → SwarmErrorCode（swarm 层词表映射）。"""
    reason = outcome.failure_reason
    if reason == DelegationFailureReason.TIMEOUT:
        return "timeout"
    if reason == DelegationFailureReason.CANCELLED:
        return "cancelled"
    return "non_retryable"


def verify_outcome(request: DelegationRequest, outcome: DelegationOutcome) -> DelegationOutcome:
    """父侧 receipt 验证（合并前最后一道；缺证据不得当成功）。

    - succeeded 但 summary 为空 → rejected → FAILED(receipt_invalid)；
    - succeeded 但声明了 expected_outputs 且零 ref 产出 → degraded
      （与 swarm ``normalize_receipt`` 的 non_compliant 纪律同型）；
    - ``capabilities_used`` 越出 ``allowed_capabilities``（已声明时）→
      rejected（未授权能力使用 = 契约违约，fail-closed）。
    """
    if outcome.status != DelegationStatus.SUCCEEDED:
        return outcome
    reasons: list[str] = []
    status = outcome.status
    if not outcome.summary.strip():
        reasons.append("empty_summary")
        status = DelegationStatus.FAILED
    elif request.expected_outputs and not outcome.produced_refs:
        reasons.append("expected_outputs_unmet")
        status = DelegationStatus.DEGRADED
    if request.allowed_capabilities:
        granted = set(request.allowed_capabilities)
        alien = [c for c in outcome.capabilities_used if c not in granted]
        if alien:
            reasons.append(f"capabilities_not_granted:{','.join(alien[:4])}")
            status = DelegationStatus.FAILED
    if not reasons:
        return outcome
    if status == DelegationStatus.FAILED:
        return outcome.model_copy(update={
            "status": status,
            "verdict": VERDICT_REJECTED,
            "verdict_reasons": reasons,
            "failure_reason": DelegationFailureReason.RECEIPT_INVALID,
            "error": (outcome.error or "receipt verification rejected: "
                      + "; ".join(reasons))[:MAX_ERROR_CHARS],
        })
    return outcome.model_copy(update={
        "status": status,
        "verdict": VERDICT_DEGRADED,
        "verdict_reasons": reasons,
        "warnings": (list(outcome.warnings) + ["receipt degraded: " + reasons[0]])[:MAX_WARNINGS],
    })


# ─────────────────────────── 公平槽位调度器 ───────────────────────────


class _Waiter:
    """等待队列条目（FIFO；携带类别/会话供类感知唤醒）。"""

    __slots__ = ("fut", "session_id", "resource_class", "generation", "delegation_id")

    def __init__(
        self,
        fut: "asyncio.Future",
        session_id: str,
        resource_class: str,
        generation: int,
        delegation_id: str,
    ) -> None:
        self.fut = fut
        self.session_id = session_id
        self.resource_class = resource_class
        self.generation = generation
        self.delegation_id = delegation_id


class FairSlotScheduler:
    """进程内有界槽位调度：全局上限 + heavy 类上限 + 每会话上限 + FIFO 公平。

    - 等待队列单 deque：按入队顺序唤醒**首个可就绪者**（类感知 + 会话
      感知：队头 heavy 取不到槽位时跳过它唤醒可就绪的 light，不队头阻塞；
      同类同会话严格 FIFO，无饥饿）；
    - 快速路径在有等待者时让位（等待者优先，防插队）；
    - ``acquire(deadline_ts=...)`` 排队超时抛 ``asyncio.TimeoutError``
      （调用方折算 EXPIRED —— 队列里诚实过期，不做无限 READY）；
    - 全部进程内有界对象（与 swarm v1 同纪律，不做分布式 lease）。
    """

    def __init__(
        self,
        *,
        global_max: int = 4,
        per_session_max: int = 2,
        heavy_global_max: Optional[int] = None,
        max_queue: int = MAX_QUEUE_WAITERS,
    ) -> None:
        self._global_max = max(1, int(global_max))
        self._session_max = max(1, int(per_session_max))
        self._heavy_max = self._global_max if heavy_global_max is None else max(1, int(heavy_global_max))
        self._max_queue = max(1, int(max_queue))
        self._active_global = 0
        self._active_heavy = 0
        self._active_by_session: dict[str, int] = {}
        self._active_leases: dict[str, DelegationLease] = {}  # lease_id -> lease
        self._waiters: deque[_Waiter] = deque()
        self._wait_total = 0
        self.queue_rejected = 0
        # 跨 loop 防护（SwarmConcurrencyGovernor 同款）：进程级共享实例
        # 落入新 event loop 时清陈旧台账 —— 旧 loop 的 waiter/lease 其
        # finally 不可达，不清理会永久占位毒化新 loop（pytest function-
        # scoped loop 与测试残留场景）。
        self._loop_key: Optional[int] = None

    def _ensure_loop(self) -> None:
        loop_key = id(asyncio.get_running_loop())
        if self._loop_key != loop_key:
            self._loop_key = loop_key
            self._waiters.clear()
            self._active_global = 0
            self._active_heavy = 0
            self._active_by_session.clear()
            self._active_leases.clear()

    # ── 容量与占用 ──
    def _can_take(self, session_id: str, resource_class: str) -> bool:
        if self._active_global >= self._global_max:
            return False
        if resource_class == "heavy" and self._active_heavy >= self._heavy_max:
            return False
        if self._active_by_session.get(session_id, 0) >= self._session_max:
            return False
        return True

    def _take_slot(
        self, session_id: str, resource_class: str, generation: int, delegation_id: str
    ) -> DelegationLease:
        self._active_global += 1
        if resource_class == "heavy":
            self._active_heavy += 1
        self._active_by_session[session_id] = self._active_by_session.get(session_id, 0) + 1
        lease = DelegationLease(
            lease_id=f"lease-{uuid.uuid4().hex[:12]}",
            delegation_id=delegation_id,
            generation=generation,
            session_id=session_id,
            resource_class=resource_class,
            acquired_at=time.time(),
        )
        self._active_leases[lease.lease_id] = lease
        return lease

    def _release_slot(self, lease: DelegationLease) -> None:
        if self._active_leases.pop(lease.lease_id, None) is None:
            return  # 未知租约：诚实忽略（双 release 防线）
        self._active_global -= 1
        if lease.resource_class == "heavy":
            self._active_heavy -= 1
        sid = lease.session_id
        left = self._active_by_session.get(sid, 0) - 1
        if left > 0:
            self._active_by_session[sid] = left
        else:
            self._active_by_session.pop(sid, None)
        self._pump()

    # ── 唤醒泵：队头起首个「容量可行」的等待者（类/会话感知 FIFO） ──
    def _pump(self) -> None:
        for waiter in list(self._waiters):
            if waiter.fut.done():
                self._waiters.remove(waiter)  # 超时/取消残留清扫
                continue
            if self._can_take(waiter.session_id, waiter.resource_class):
                self._waiters.remove(waiter)
                waiter.fut.set_result(None)
                return

    async def acquire(
        self,
        request: DelegationRequest,
        *,
        generation: int = 1,
        deadline_ts: Optional[float] = None,
    ) -> DelegationLease:
        """获取槽位；``deadline_ts``（monotonic）为排队死线，超时抛 TimeoutError。"""
        self._ensure_loop()
        sid = request.parent.session_id
        rclass = request.resource_class
        did = request.delegation_id
        if not self._waiters and self._can_take(sid, rclass):
            return self._take_slot(sid, rclass, generation, did)
        loop = asyncio.get_running_loop()
        while True:
            if deadline_ts is not None and deadline_ts - loop.time() <= 0:
                raise asyncio.TimeoutError("delegation queue deadline expired")
            if len(self._waiters) >= self._max_queue:
                # 有界背压：满员诚实拒绝（调用方折算失败，不无界积压）
                self.queue_rejected += 1
                raise DelegationContractError(
                    f"delegation queue full ({self._max_queue})"
                )
            fut: asyncio.Future = loop.create_future()
            waiter = _Waiter(fut, sid, rclass, generation, did)
            self._waiters.append(waiter)
            self._wait_total += 1
            try:
                self._pump()  # 新等待者可能立即可就绪（跳过不可就绪队头）
                if deadline_ts is not None:
                    await asyncio.wait_for(fut, deadline_ts - loop.time())
                else:
                    await fut
            except asyncio.TimeoutError:
                raise
            finally:
                # 已完成的 fut 不在队列（pump 时已弹出）；超时/取消路径移除
                if not fut.done():
                    fut.cancel()
                with contextlib.suppress(ValueError):
                    self._waiters.remove(waiter)
            # 被唤醒者已由 pump 验证容量 —— 直接占用（防竞态仍防御判空）。
            if self._can_take(sid, rclass):
                return self._take_slot(sid, rclass, generation, did)

    def release(self, lease: DelegationLease) -> None:
        self._release_slot(lease)

    def snapshot(self) -> dict:
        """有界 metrics 投影（并发上限/占用/等待/累计等待/背压拒绝）。"""
        return {
            "global_max": self._global_max,
            "per_session_max": self._session_max,
            "heavy_global_max": self._heavy_max,
            "max_queue": self._max_queue,
            "active": self._active_global,
            "active_heavy": self._active_heavy,
            "active_by_session": dict(sorted(
                self._active_by_session.items())[:12]),
            "waiting": len(self._waiters),
            "wait_total": self._wait_total,
            "queue_rejected": self.queue_rejected,
        }


# ─────────────────────────── 因果台账（generation fencing） ───────────────────────────

#: settle 裁决词表。
SETTLE_ACCEPTED = "accepted"
SETTLE_DUPLICATE = "duplicate"
SETTLE_LATE = "late"


class _GenerationRecord:
    """单代际记录（phase 历史 + 终态）。有界。"""

    __slots__ = ("generation", "phases", "terminal")

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.phases: list[tuple[str, float]] = [(DelegationPhase.CREATED, time.time())]
        self.terminal: Optional[DelegationOutcome] = None


class DelegationLedger:
    """每 run 有界委派台账：phase 历史 + generation fencing 终态合并。

    - ``register`` 代际 +1（重试/换代场景）；旧代际的后续终态 = late；
    - ``offer`` 是唯一合并点：首终态 accepted，同代再投 duplicate，
      旧代际投递 late（全部有界隔离披露，绝不覆盖已结算事实）；
    - ``snapshot`` 供 metrics 与因果审计（parent/child ids 全保留）。
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.duplicate_discarded = 0
        self.late_discarded = 0
        self.quarantine: list[dict[str, Any]] = []

    # ── 内部 ──
    def _entry_for_register(self, delegation_id: str) -> dict[str, Any]:
        entry = self._entries.get(delegation_id)
        if entry is None:
            entry = {
                "generations": {},
                "current_generation": 0,
                "request_digest": "",
            }
            self._entries[delegation_id] = entry
            self._order.append(delegation_id)
            if len(self._order) > 64:  # 台账有界：最老委派出账
                old = self._order.pop(0)
                self._entries.pop(old, None)
        return entry

    def _quarantine(self, kind: str, delegation_id: str, outcome: DelegationOutcome) -> None:
        self.quarantine.append({
            "kind": kind,
            "delegation_id": delegation_id[:80],
            "generation": int(outcome.generation),
            "status": outcome.status,
            "error": outcome.error[:120],
            "at": time.time(),
        })
        if len(self.quarantine) > MAX_QUARANTINE_RECORDS:
            self.quarantine.pop(0)

    # ── 公开面 ──
    def register(self, delegation_id: str, *, request_digest: str = "") -> int:
        """登记新一代；返回代际号（首登记 = 1）。已终态旧代际保持封闭。"""
        entry = self._entry_for_register(delegation_id)
        entry["current_generation"] += 1
        gen = entry["current_generation"]
        entry["generations"][gen] = _GenerationRecord(gen)
        entry["request_digest"] = request_digest[:80]
        gens = entry["generations"]
        while len(gens) > MAX_GENERATIONS_REMEMBERED:
            oldest = min(gens)
            gens.pop(oldest)
        return gen

    def transition(self, delegation_id: str, to_phase: str, *, generation: Optional[int] = None) -> None:
        """phase 推进（fail-loud：非法转移抛 DelegationContractError）。"""
        entry = self._entries.get(delegation_id)
        if entry is None:
            raise DelegationContractError(f"未登记的委派: {delegation_id}")
        gen = generation if generation is not None else entry["current_generation"]
        record = entry["generations"].get(gen)
        if record is None:
            raise DelegationContractError(
                f"委派 {delegation_id} 代际 {gen} 已出账（不可推进）"
            )
        cur = record.phases[-1][0]
        check_delegation_transition(cur, to_phase)
        record.phases.append((to_phase, time.time()))
        if len(record.phases) > MAX_PHASE_HISTORY:
            record.phases.pop(0)

    def offer(
        self,
        delegation_id: str,
        outcome: DelegationOutcome,
        *,
        generation: Optional[int] = None,
    ) -> str:
        """终态合并（唯一合并点）：fencing 裁决 accepted/duplicate/late。"""
        entry = self._entries.get(delegation_id)
        if entry is None:
            self.late_discarded += 1
            self._quarantine(SETTLE_LATE, delegation_id, outcome)
            return SETTLE_LATE
        gen = generation if generation is not None else outcome.generation
        record = entry["generations"].get(gen)
        if record is None or gen != entry["current_generation"]:
            # 旧代际迟到结果：隔离，绝不污染新一代
            self.late_discarded += 1
            self._quarantine(SETTLE_LATE, delegation_id, outcome)
            return SETTLE_LATE
        if record.terminal is not None:
            self.duplicate_discarded += 1
            self._quarantine(SETTLE_DUPLICATE, delegation_id, outcome)
            return SETTLE_DUPLICATE
        record.terminal = outcome
        if not is_terminal_phase(record.phases[-1][0]):
            record.phases.append((outcome.status, time.time()))
        return SETTLE_ACCEPTED

    def terminal(self, delegation_id: str, *, generation: Optional[int] = None) -> Optional[DelegationOutcome]:
        entry = self._entries.get(delegation_id)
        if entry is None:
            return None
        gen = generation if generation is not None else entry["current_generation"]
        record = entry["generations"].get(gen)
        return record.terminal if record else None

    def phase_of(self, delegation_id: str, *, generation: Optional[int] = None) -> Optional[str]:
        entry = self._entries.get(delegation_id)
        if entry is None:
            return None
        gen = generation if generation is not None else entry["current_generation"]
        record = entry["generations"].get(gen)
        return record.phases[-1][0] if record else None

    def snapshot(self) -> dict:
        """有界 metrics（counts/causal/隔离计数/隔离区）。"""
        counts: dict[str, int] = {}
        by_status: dict[str, int] = {}
        active: list[str] = []
        for did in self._order:
            entry = self._entries.get(did)
            if entry is None:
                continue
            record = entry["generations"].get(entry["current_generation"])
            if record is None:
                continue
            phase = record.phases[-1][0]
            counts[phase] = counts.get(phase, 0) + 1
            if record.terminal is not None:
                by_status[record.terminal.status] = by_status.get(record.terminal.status, 0) + 1
            else:
                active.append(did)
        return {
            "delegations": len(self._order),
            "phases": dict(sorted(counts.items())),
            "outcomes": dict(sorted(by_status.items())),
            "active": [d[:80] for d in active[:12]],
            "duplicate_discarded": self.duplicate_discarded,
            "late_discarded": self.late_discarded,
            "quarantine": [dict(q) for q in self.quarantine],
        }


# ─────────────────────────── runner 协议与 gateway ───────────────────────────


@runtime_checkable
class SubagentRunner(Protocol):
    """委派执行体最小协议（生产适配器与测试假件同形）。

    runner 不得上抛业务异常（折算为失败 outcome）；``CancelledError``
    交还 gateway（取消语义）。
    """

    async def run(self, lease: DelegationLease) -> DelegationOutcome: ...


class FakeSubagentRunner:
    """确定性离线 runner（零 LLM/DB/网络）—— 协议测试专用假件。

    script 按 run 调用次序消费；条目语义：
    - ``"succeed"``            → succeeded + ref:out-<n>；
    - ``{"refs": [...], "summary": ...}`` → 自定义成功产出；
    - ``Exception`` 实例       → 原样抛出（child crash 面）；
    - ``"hang"``               → 永久等待（由测试/取消打破）；
    - ``"sleep:<s>"``          → 延迟后成功（timeout 面）；
    - ``"no_summary"`` / ``"no_refs"`` / ``"alien_cap"`` → 验证面；
    - ``("park", <delay_s>)``  → 立即被取消 + 独立任务延迟投递迟到结果
      （``late_deliver`` 回调，测试接到 ledger.offer 验证 fencing）。
    """

    def __init__(
        self,
        script: Optional[list[Any]] = None,
        *,
        default: Any = "succeed",
        late_deliver: Optional[Callable[[DelegationLease, DelegationOutcome], None]] = None,
    ) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[str] = []
        self.late_deliver = late_deliver
        self._n = 0

    def _next(self) -> Any:
        return self.script.pop(0) if self.script else self.default

    async def run(self, lease: DelegationLease) -> DelegationOutcome:
        self._n += 1
        self.calls.append(lease.delegation_id)
        behavior = self._next()

        def _out(**kw: Any) -> DelegationOutcome:
            base = dict(
                delegation_id=lease.delegation_id,
                generation=lease.generation,
                lease_id=lease.lease_id,
                status=DelegationStatus.SUCCEEDED,
                summary=f"fake done {lease.delegation_id}",
                produced_refs=[f"ref:out-{self._n}"],
                session_id=lease.session_id,
                finished_at=time.time(),
            )
            base.update(kw)
            return DelegationOutcome(**base)

        if isinstance(behavior, BaseException):
            raise behavior
        if behavior == "hang":
            await asyncio.Event().wait()
        if isinstance(behavior, str) and behavior.startswith("sleep:"):
            await asyncio.sleep(float(behavior.split(":", 1)[1]))
            return _out()
        if isinstance(behavior, tuple) and behavior and behavior[0] == "park":
            delay = float(behavior[1])

            async def _late() -> None:
                await asyncio.sleep(delay)
                if self.late_deliver is not None:
                    self.late_deliver(lease, _out(summary="late child result"))

            asyncio.ensure_future(_late())
            await asyncio.Event().wait()  # 本体服从取消
        if isinstance(behavior, dict):
            return _out(**behavior)
        if behavior == "no_summary":
            return _out(summary="")
        if behavior == "no_refs":
            return _out(produced_refs=[])
        if behavior == "alien_cap":
            return _out(capabilities_used=["cap.not.granted"])
        return _out()


class DelegationGateway:
    """委派网关：请求 → lease → runner → 验证 → 台账合并的唯一执行语义。

    保证（对应 DoD）：
    - acquire/run/verify 任一异常 → 诚实终态（绝不 fail-open、绝不 raise
      业务异常；``CancelledError`` 落账后原样上抛 —— asyncio 纪律）；
    - deadline 覆盖排队 + 运行（排队过期 = EXPIRED）；
    - 取消传播：父任务取消 → runner 终止 + CANCELLED 落账；取消后迟到的
      runner 结果经 ledger fencing 隔离；
    - 结果先 ``verify_outcome`` 再 ``ledger.offer``（缺证据不得当成功）。
    """

    def __init__(
        self,
        *,
        scheduler: Optional[FairSlotScheduler] = None,
        ledger: Optional[DelegationLedger] = None,
    ) -> None:
        self._scheduler = scheduler or FairSlotScheduler()
        self._ledger = ledger or DelegationLedger()

    @property
    def scheduler(self) -> FairSlotScheduler:
        return self._scheduler

    @property
    def ledger(self) -> DelegationLedger:
        return self._ledger

    async def execute(
        self,
        request: DelegationRequest,
        runner: SubagentRunner,
    ) -> DelegationOutcome:
        """执行一次委派；除 ``CancelledError``（落账后上抛）外绝不 raise。"""
        started = time.time()
        did = request.delegation_id
        ledger = self._ledger
        loop = asyncio.get_running_loop()
        deadline_ts = (
            loop.time() + float(request.deadline_s)
            if request.deadline_s is not None else None
        )
        generation = ledger.register(did, request_digest=request.goal[:80])
        lease: Optional[DelegationLease] = None

        def _outcome(**kw: Any) -> DelegationOutcome:
            base = dict(
                delegation_id=did,
                generation=generation,
                lease_id=lease.lease_id if lease else "",
                session_id=request.parent.session_id,
                parent_turn_id=request.parent.turn_id,
                parent_run_id=request.parent.run_id,
                wall_time_s=time.time() - started,
                finished_at=time.time(),
            )
            base.update(kw)
            return DelegationOutcome(**base)

        async def _fail(reason: str, error: str) -> DelegationOutcome:
            status = (
                DelegationStatus.CANCELLED if reason == DelegationFailureReason.CANCELLED
                else DelegationStatus.EXPIRED if reason == DelegationFailureReason.EXPIRED
                else DelegationStatus.FAILED
            )
            return _outcome(status=status, failure_reason=reason, error=error[:MAX_ERROR_CHARS])

        # ── ACQUIRING ──
        ledger.transition(did, DelegationPhase.ACQUIRING, generation=generation)
        try:
            lease = await self._scheduler.acquire(
                request, generation=generation, deadline_ts=deadline_ts
            )
        except asyncio.TimeoutError:
            outcome = await _fail(
                DelegationFailureReason.EXPIRED,
                f"queued past deadline ({request.deadline_s:.3f}s) without a slot",
            )
            ledger.offer(did, outcome, generation=generation)
            return outcome
        except DelegationContractError:
            # 有界背压满员拒绝（scheduler 抛出；非转移违约）
            outcome = await _fail(
                DelegationFailureReason.QUEUE_FULL,
                f"delegation queue full ({getattr(self._scheduler, '_max_queue', '?')})",
            )
            ledger.offer(did, outcome, generation=generation)
            return outcome
        except asyncio.CancelledError:
            outcome = await _fail(
                DelegationFailureReason.CANCELLED, "cancelled while acquiring slot"
            )
            ledger.offer(did, outcome, generation=generation)
            raise
        except Exception as exc:  # noqa: BLE001 — acquire 相位 fail-closed 兜底
            logger.exception("[Delegation] acquire crash delegation=%s", did)
            outcome = await _fail(
                DelegationFailureReason.ACQUIRE_CRASH,
                f"acquire crash: {type(exc).__name__}: {exc}",
            )
            ledger.offer(did, outcome, generation=generation)
            return outcome

        # ── RUNNING ──
        try:
            ledger.transition(did, DelegationPhase.RUNNING, generation=generation)
        except Exception as exc:  # noqa: BLE001 — 台账推进异常不违约、不泄漏槽位
            # 共享网关的台账有界（64 FIFO 出账）：排队期间 entry 可能被
            # 出账 —— 此处兜底保证「绝不向上 raise、lease 必被归还」。
            logger.exception("[Delegation] ledger transition crash delegation=%s", did)
            self._scheduler.release(lease)
            outcome = await _fail(
                DelegationFailureReason.LEDGER_CRASH,
                f"ledger transition crash: {exc}",
            )
            ledger.offer(did, outcome, generation=generation)
            return outcome
        runner_task = asyncio.ensure_future(runner.run(lease))

        async def _reap() -> None:
            """有界收割被取消的 runner：吞取消并挂起的 runner 不拖住 gateway。"""
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(runner_task, 2.0)

        try:
            if deadline_ts is not None:
                raw = await asyncio.wait_for(runner_task, deadline_ts - loop.time())
            else:
                raw = await runner_task
        except asyncio.TimeoutError:
            runner_task.cancel()
            await _reap()
            outcome = await _fail(
                DelegationFailureReason.TIMEOUT,
                f"deadline {request.deadline_s:.3f}s exceeded",
            )
        except asyncio.CancelledError:
            runner_task.cancel()
            await _reap()
            outcome = await _fail(
                DelegationFailureReason.CANCELLED, "cancelled by parent"
            )
            ledger.offer(did, outcome, generation=generation)
            raise
        except Exception as exc:  # noqa: BLE001 — child crash 折算诚实失败
            logger.exception("[Delegation] runner crash delegation=%s", did)
            outcome = await _fail(
                DelegationFailureReason.CHILD_CRASH,
                f"runner crash: {type(exc).__name__}: {exc}",
            )
        else:
            outcome = raw if isinstance(raw, DelegationOutcome) else _outcome(
                status=DelegationStatus.FAILED,
                failure_reason=DelegationFailureReason.CHILD_CRASH,
                error="runner returned non-outcome",
            )
            # 3.11+ 取消吸收面（防御性加固）：runner 吞掉 CancelledError
            # 并正常返回时，本协程的取消可能被整体吸收（except 分支未进入）
            # —— 按取消语义诚实折算，绝不让已取消的父任务拿到 success。
            me = asyncio.current_task()
            pending_cancels = int(getattr(me, "cancelling", lambda: 0)()) if me is not None else 0
            if pending_cancels > 0:
                outcome = await _fail(
                    DelegationFailureReason.CANCELLED,
                    "parent cancellation absorbed by runner",
                )
                ledger.offer(did, outcome, generation=generation)
                return outcome  # finally 仍会归还槽位
        finally:
            self._scheduler.release(lease)
        outcome = verify_outcome(request, outcome)
        ledger.offer(did, outcome, generation=generation)
        return outcome
