"""HarnessResourceGovernor — 跨域资源协调 facade（ADR-0182）。

Governor V1 的**唯一门面**。组合：三级账本（R4）+ 三层背压（R5）+ 准入
策略（R3）+ 健康只读视图（R11）+ RetryBudget（R10）+ 取消协调（R9）+
存储压力（R15）+ 观测面（R16）。它**不是**又一套计算平台：rows/bytes/
nodes 的 L1 权威仍是 geocompute ``ResourceGovernor``，token 估算语义仍是
``context_budget``，provider 熔断仍是 Data Fabric breaker —— 这里只做
**跨系统协调**。

主执行管线（async）::

    demand → should_skip?（取消）→ retry_allowed?（重试）
           → admission.decide（确定性策略链）
           → [enforce] backpressure.acquire（defer 在此落地；max_wait 超时
             升格 degrade/reject）
           → ledger.reserve → 执行（调用方）
           → complete(actual)（charge + release + estimate-vs-actual 观测）

**fail-open 纪律**：本门面任何内部异常 → 记 governor_internal_errors →
放行（绝不阻断 dispatch）。**observe 模式**：决策照常计算与留痕，但
reject/degrade 不阻断（rollback kill-switch）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.services.governor import metrics as gmetrics
from app.services.governor.admission import AdmissionPolicy
from app.services.governor.backpressure import (
    AcquireTicket,
    BackpressureManager,
    QueueTimeoutError,
)
from app.services.governor.cancellation import CancellationCoordinator
from app.services.governor.config import (
    GovernorConfig,
    GovernorMode,
    get_governor_config,
)
from app.services.governor.contract import (
    AdmissionDecision,
    CancelReason,
    Dimension,
    ResourceDecision,
    ResourceDemand,
    ResourceEstimate,
    ResourceReservation,
    ResourceUsage,
)
from app.services.governor.health import HealthView
from app.services.governor.retry_budget import RetryBudget
from app.services.governor.session_budget import SessionBudgetLedger
from app.services.governor.storage_pressure import StoragePressureView

logger = logging.getLogger(__name__)


class HarnessResourceGovernor:
    """进程级 governor 门面（线程安全账本 + 单事件循环背压闸）。"""

    def __init__(
        self,
        config: Optional[GovernorConfig] = None,
        *,
        ledger: Optional[SessionBudgetLedger] = None,
        budgets: Optional[Dict] = None,
        health: Optional[HealthView] = None,
        storage: Optional[StoragePressureView] = None,
    ) -> None:
        self.config = config or get_governor_config()
        if ledger is not None:
            self.ledger = ledger
        else:
            if budgets is None:
                budgets = self._load_budgets_or_empty()
            self.ledger = SessionBudgetLedger(budgets)
        self.bp = BackpressureManager(
            global_heavy=self.config.global_heavy_concurrency,
            raster=self.config.global_raster_concurrency,
            browser=self.config.global_browser_concurrency,
            export=self.config.global_export_concurrency,
            external=self.config.global_heavy_concurrency,
            llm=self.config.global_heavy_concurrency,
            session_concurrency=self.config.session_concurrency,
            session_heavy=self.config.session_heavy_concurrency,
            aging_threshold_s=self.config.aging_threshold_s,
        )
        self.admission = AdmissionPolicy(
            self.ledger,
            global_memory_pressure_bytes=self.config.global_memory_pressure_bytes,
            slo_breach_soft_limit=self.config.slo_breach_soft_limit,
        )
        self.health = health or HealthView()
        self.storage = storage or StoragePressureView(
            high_watermark=self.config.storage_pressure_high)
        self.retries = RetryBudget(
            global_tokens=self.config.retry_tokens_global,
            session_tokens=self.config.retry_tokens_per_session,
        )
        self.cancellations = CancellationCoordinator(self.retries)
        # SLO breach 观测的接入点（observe-only → admission 输入的升级，G12）
        self._slo_breach_total = 0
        self._live_reservations: Dict[str, ResourceReservation] = {}
        self._live_tickets: Dict[str, AcquireTicket] = {}
        self._reservation_lock = asyncio.Lock()

    @staticmethod
    def _load_budgets_or_empty() -> Dict:
        from app.services.governor.config import load_manifest
        try:
            return load_manifest()
        except FileNotFoundError:
            logger.info("governor budget manifest not found; starting empty")
            return {}
        except ValueError:
            logger.warning("governor budget manifest invalid; starting empty",
                           exc_info=True)
            return {}

    # ── 主执行管线 ───────────────────────────────────────────────────

    async def admit_and_reserve(
        self,
        demand: ResourceDemand,
        *,
        open_providers: Optional[List[str]] = None,
        remote_only: bool = False,
    ) -> Tuple[ResourceDecision, Optional[ResourceReservation],
               Optional[AcquireTicket]]:
        """准入 + 预留。返回 (decision, reservation|None, ticket|None)。

        - decision.allowed=True 时 reservation/ticket 必然非 None
          （complete() 需要它们归还）；
        - reject/degrade → (decision, None, None)，调用方不得执行原请求；
        - observe 模式：即便 decision 为 reject/degrade 也放行执行
          （reservation 照常建立 —— 观测语义完整）。
        抛出的唯一异常类型是 QueueTimeoutError 的调用方可见性：本方法把
        排队超时升格为 DEGRADE（或 observe 下放行）—— 自身异常全部
        fail-open。
        """
        enforce = self.config.mode is GovernorMode.ENFORCE
        try:
            if demand.session_id and self.cancellations.is_cancelled(demand.session_id):
                decision = ResourceDecision(
                    decision=AdmissionDecision.REJECT,
                    reasons=["session_cancelled"],
                    mode=self.config.mode.value,
                )
                gmetrics.observe_admission(decision.decision.value, self.config.mode.value)
                return decision, None, None

            if demand.attempt > 1 and demand.retry_class is not None:
                allowed, why = self.retries.retry_allowed(
                    demand.session_id, demand.retry_class,
                    operation=demand.tool_name)
                gmetrics.observe_retry(
                    demand.retry_class.value, "allowed" if allowed else why)
                # 重试被预算/取消否决 → 诚实 REJECT（DEFER 是谎言：没有
                # 可等的队列，等下去只会无限循环 —— R10「重试必须停止」）
                if not allowed and enforce:
                    decision = ResourceDecision(
                        decision=AdmissionDecision.REJECT,
                        reasons=[f"retry_budget:{why}"],
                        suggestions=(
                            ["retry has been cancelled; issue a fresh request"]
                            if why == "deny_cancelled" else [
                                "retry budget exhausted for this scope; "
                                "wait for in-flight work to drain or "
                                "escalate via a new goal",
                            ]),
                        mode=self.config.mode.value,
                    )
                    gmetrics.observe_admission(decision.decision.value,
                                               self.config.mode.value)
                    return decision, None, None

            decision = self.admission.decide(
                demand,
                channel_state=self.bp.channel_state(),
                global_live_memory=self._global_live_memory(),
                open_providers=open_providers,
                slo_breach_total=self._slo_breach_total,
                remote_only=remote_only,
            )
            gmetrics.observe_admission(decision.decision.value, self.config.mode.value)

            # observe：决策留痕但放行执行（rollback kill-switch 语义）。
            # 注意 decision 对象保留诚实决策值（reject 就是 reject），放行
            # 与否由 enforce 标志单独决定。
            allow_execution = decision.allowed or not enforce
            if not decision.allowed and not enforce:
                decision = decision.model_copy(
                    update={"reasons": list(decision.reasons) + ["observe_pass"]})

            if not allow_execution:
                return decision, None, None

            # defer 的真实落地：背压 acquire（max_wait 内排队；超时升格）
            max_wait = demand.max_wait_s
            if max_wait is None:
                max_wait = self.config.queue_max_wait_s
            ticket: Optional[AcquireTicket] = None
            reservation: Optional[ResourceReservation] = None
            try:
                ticket = await self.bp.acquire(
                    session_id=demand.scope_key(),
                    resource_class=demand.estimate.resource_class,
                    estimate=demand.estimate,
                    priority=int(demand.priority),
                    max_wait_s=max_wait,
                )
            except QueueTimeoutError as exc:
                gmetrics.observe_queue_wait(
                    exc.channel or "direct", exc.waited_s)
                if not enforce:
                    ticket = AcquireTicket(channel="", session_id=demand.scope_key())
                elif exc.channel == "cancelled":
                    # 排队期间会话被取消（R9）—— 诚实拒绝
                    decision = ResourceDecision(
                        decision=AdmissionDecision.REJECT,
                        reasons=["session_cancelled_while_queued"],
                        mode=self.config.mode.value,
                    )
                    gmetrics.observe_admission(decision.decision.value,
                                               self.config.mode.value)
                    return decision, None, None
                else:
                    decision = ResourceDecision(
                        decision=AdmissionDecision.DEGRADE,
                        reasons=list(decision.reasons) + [
                            f"queue_timeout:{exc.channel}:{exc.waited_s:.1f}s"],
                        queue_hint=exc.channel,
                        mode=self.config.mode.value,
                    )
                    gmetrics.observe_admission(decision.decision.value,
                                               self.config.mode.value)
                    return decision, None, None
            else:
                if ticket.channel:
                    waited = time.monotonic() - ticket.enqueued_at
                    gmetrics.observe_queue_wait(ticket.channel, waited)

            # acquire 已成功：从这里起任何异常都要先归还 ticket/reservation
            # 再进入外层 fail-open（review P1：fail-open 不得携带泄漏）。
            try:
                reservation = self._build_reservation(demand, decision)
                adjudged = self._adjudged_dims(demand.estimate)
                self.ledger.reserve(reservation, adjudged)
                async with self._reservation_lock:
                    self._live_reservations[reservation.reservation_id] = reservation
                    self._live_tickets[reservation.reservation_id] = ticket
                self._sync_gauges()
                return decision, reservation, ticket
            except BaseException:
                try:
                    if reservation is not None:
                        from app.services.governor.session_budget import live_dims_of
                        self.ledger.release(reservation,
                                            actual=live_dims_of(reservation.charged))
                except Exception:  # noqa: BLE001
                    gmetrics.inc_internal_error("admit_cleanup_ledger")
                try:
                    if ticket is not None:
                        await self.bp.release(ticket, actual_cost=0.0)
                except Exception:  # noqa: BLE001
                    gmetrics.inc_internal_error("admit_cleanup_ticket")
                raise
        except Exception:  # noqa: BLE001 — fail-open 纪律（D5）
            gmetrics.inc_internal_error("admit_and_reserve")
            logger.exception("[resource-governor] admit failed; failing open")
            decision = ResourceDecision(
                decision=AdmissionDecision.ACCEPT,
                reasons=["governor_fail_open"],
                mode=self.config.mode.value,
            )
            return decision, None, None

    async def complete(
        self,
        reservation: Optional[ResourceReservation],
        ticket: Optional[AcquireTicket],
        *,
        usage: Optional[ResourceUsage] = None,
        actual: Optional[Dict[Dimension, float]] = None,
        estimate: Optional[ResourceEstimate] = None,
    ) -> None:
        """执行完成：记账 + 归还 + estimate-vs-actual 观测。绝不抛。

        **幂等认领（review P1 修复）**：完成前先在 live 表中原子认领本
        reservation——认领不到（已被 cancel_session 释放、或 complete 重入）
        则跳过 ledger/背压归还，只保留观测面。否则 cancel + 迟到 complete
        会对同一槽位归还两次，凭空放大通道容量。
        """
        try:
            actual = dict(actual or {})
            if usage is not None:
                actual.update({d: float(v) for d, v in usage.dims.items()})
                if usage.wall_time_s is not None:
                    actual[Dimension.WALL_TIME_S] = float(usage.wall_time_s)
            est = estimate
            release_slot = False
            if reservation is not None:
                async with self._reservation_lock:
                    claimed = self._live_reservations.pop(
                        reservation.reservation_id, None)
                    self._live_tickets.pop(reservation.reservation_id, None)
                if claimed is None or reservation.released:
                    # 已被取消路径释放 / complete 重入 —— 只走观测面
                    self._sync_gauges()
                    return
                reservation.released = True
                release_slot = True
                charged = {d: float(v) for d, v in reservation.charged.items()}
                # estimate-vs-actual（R16）：memory/wall 两维优先；
                # estimate 缺席时以 charged（adjudged 语义）为期望基准
                est_vals = ({d: est.adjudged(d) for d in est.dims}
                            if est is not None else charged)
                for dim in (Dimension.MEMORY_BYTES, Dimension.WALL_TIME_S):
                    expected = est_vals.get(dim, 0.0)
                    observed = actual.get(dim)
                    if expected > 0 and observed is not None:
                        gmetrics.observe_estimate_error(
                            dim.value, expected, float(observed))
                # 归还语义：live 维按 actual（缺省 = charged 原样）归还；
                # cumulative 维按 actual 入账（actual 缺省 = charged 估值 ——
                # 估算消耗也是消耗，诚实计入账本）
                merged_actual: Dict[Dimension, float] = {
                    dim: actual.get(dim, add) for dim, add in charged.items()
                }
                self.ledger.release(reservation, actual=merged_actual)
            if ticket is not None and release_slot:
                cost = max(0.1, actual.get(Dimension.WALL_TIME_S, 1.0))
                await self.bp.release(ticket, actual_cost=cost)
            if usage is not None:
                gmetrics.observe_execution(
                    reservation.subsystem.value if reservation else "other",
                    usage.wall_time_s or 0.0)
                if usage.degraded:
                    gmetrics.observe_fallback(
                        reservation.subsystem.value if reservation else "other",
                        (usage.semantics.value if usage.semantics else "unknown"))
            self._sync_gauges()
        except Exception:  # noqa: BLE001 — 归还路径自吞（D5）
            gmetrics.inc_internal_error("complete")

    # ── 取消 / 会话生命周期 ─────────────────────────────────────────

    async def cancel_session(self, session_id: str,
                             reason: CancelReason) -> int:
        """取消会话：live 释放 + 排队候补唤醒（pending 不启动）。"""
        released = await self._release_all(session_id)
        await self.bp.cancel_session_waiters(session_id)
        self.cancellations.cancel(
            session_id, reason,
            release_reservations=lambda _sid: [r.reservation_id for r in released],
        )
        return len(released)

    async def close_session(self, session_id: str) -> None:
        await self._release_all(session_id)
        self.retries.close_session(session_id)
        self.cancellations.close_session(session_id)
        await self.bp.drop_session(session_id)
        self._sync_gauges()

    def should_skip(self, session_id: str) -> bool:
        """pending 任务启动前的取消检查（R9 硬保证 1）。"""
        return self.cancellations.is_cancelled(session_id)

    def note_slo_breach(self) -> None:
        """观测面 → 准入输入的升级口（observability BudgetRegistry 的
        breach 回调挂这里；G12）。"""
        self._slo_breach_total += 1

    # ── 诊断快照 ─────────────────────────────────────────────────────

    def snapshot(self, session_id: str = "", turn_id: str = "",
                 goal_id: str = "") -> Dict:
        async_live = len(self._live_reservations)
        return {
            "mode": self.config.mode.value,
            "channels": {name: {"in_flight": v[0], "waiting": v[1]}
                         for name, v in self.bp.channel_state().items()},
            "sessions_active": self.bp.session_count(),
            "live_reservations": async_live,
            "budgets": self.ledger.snapshot(session_id, turn_id, goal_id),
            "retries": self.retries.snapshot(),
            "storage": self.storage.snapshot(),
            # ADR-0213 D4：校准样本面（只观测；键数有界可见）
            "calibration_keys": self._calibration_key_count(),
            "slo_breach_total": self._slo_breach_total,
            "cancelled_sessions": self.cancellations.cancelled_count(),
        }

    # ── 内部 ─────────────────────────────────────────────────────────

    def _build_reservation(self, demand: ResourceDemand,
                           decision: ResourceDecision) -> ResourceReservation:
        # accept_with_limits 的限幅落进 charged（执行方消费 decision.limits）
        charged = {}
        est = demand.estimate
        for d in Dimension:
            dv = est.dim(d)
            if not dv.is_meaningful():
                continue
            value = dv.adjudged(dim=d)
            cap = decision.limits.get(d)
            if cap is not None:
                value = min(value, cap)
            if value > 0:
                charged[d] = value
        return ResourceReservation(
            session_id=demand.session_id,
            goal_id=demand.goal_id,
            turn_id=demand.turn_id,
            subsystem=demand.subsystem,
            resource_class=est.resource_class,
            charged=charged,
        )

    @staticmethod
    def _adjudged_dims(est: ResourceEstimate) -> Dict[Dimension, float]:
        return {
            d: est.adjudged(d) for d in Dimension if est.dim(d).is_meaningful()
        }

    def _global_live_memory(self) -> float:
        return self.ledger.live_memory_total()

    @staticmethod
    def _calibration_key_count() -> int:
        try:
            from app.services.governor.calibration import (
                get_calibration_store,
            )
            return get_calibration_store().key_count()
        except Exception:  # noqa: BLE001 — 观测面绝不抛
            return 0

    async def _release_all(self, session_id: str) -> List[ResourceReservation]:
        """释放会话的全部在飞预留：账本归还 + 背压槽位归还（R9 硬保证 2）。"""
        tickets: List[AcquireTicket] = []
        async with self._reservation_lock:
            targets = [r for r in self._live_reservations.values()
                       if r.session_id == session_id]
            for r in targets:
                self._live_reservations.pop(r.reservation_id, None)
                ticket = self._live_tickets.pop(r.reservation_id, None)
                if ticket is not None:
                    tickets.append(ticket)
                r.cancelled = True
                r.released = True
        for r in targets:
            try:
                self.ledger.release(r)
            except Exception:  # noqa: BLE001
                gmetrics.inc_internal_error("cancel_release_ledger")
        for ticket in tickets:
            try:
                await self.bp.release(ticket, actual_cost=0.0)
            except Exception:  # noqa: BLE001
                gmetrics.inc_internal_error("cancel_release_ticket")
        self._sync_gauges()
        return targets

    def _sync_gauges(self) -> None:
        try:
            state = self.bp.channel_state()
            for name, (in_flight, _waiting) in state.items():
                gmetrics.set_inflight(name, in_flight)
            heavy = state.get("heavy", (0, 0))[0]
            gmetrics.set_heavy_inflight(heavy)
            gmetrics.set_sessions_active(self.bp.session_count())
        except Exception:  # noqa: BLE001
            gmetrics.inc_internal_error("sync_gauges")


_PROCESS_GOVERNOR: Optional[HarnessResourceGovernor] = None
_PROCESS_LOCK = threading.Lock()


def get_governor() -> HarnessResourceGovernor:
    """进程级默认 governor（首调时构造；manifest 缺失 → 空预算表）。"""
    global _PROCESS_GOVERNOR
    with _PROCESS_LOCK:
        if _PROCESS_GOVERNOR is None:
            _PROCESS_GOVERNOR = HarnessResourceGovernor()
        return _PROCESS_GOVERNOR


def reset_governor_for_tests(governor: Optional[HarnessResourceGovernor] = None
                             ) -> HarnessResourceGovernor:
    global _PROCESS_GOVERNOR
    with _PROCESS_LOCK:
        if governor is not None:
            _PROCESS_GOVERNOR = governor
        else:
            from app.services.governor.config import reset_governor_config_for_tests
            reset_governor_config_for_tests()
            _PROCESS_GOVERNOR = HarnessResourceGovernor()
        return _PROCESS_GOVERNOR


__all__ = [
    "HarnessResourceGovernor",
    "get_governor",
    "reset_governor_for_tests",
]
