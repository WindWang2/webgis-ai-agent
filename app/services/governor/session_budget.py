"""Session / Goal / Turn 三级预算账本（R4，ADR-0182 D5/D12）。

治理对象**不是单个工具**而是执行聚合：turn ⊂ goal ⊂ session ⊂ global，
请求沿链检查全部祖先（geocompute ``ResourceGovernor.reserve`` 同款
沿链语义，但维度是 harness 维）。

记账语义分两类（诚实区分，绝不混计）：

- ``live``：**在飞占用量衡**（memory/GPU/像素）—— reservation 建立时记入、
  release 时原样归还，与 geocompute Wave 8 R1 的并发量衡同语义；
- ``cumulative``：**累计消耗**（token/wall-time/external-calls/feature）——
  只增不减，release 不回滚（已消耗就是已消耗）。

provisional 纪律（对齐 ADR-0159 ratchet）：manifest 预算默认
``provisional=True``，违规只产出事实（由 admission 决定告警还是拦截），
本层绝不静默、也绝不直接抛错。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.governor.contract import (
    Dimension,
    ResourceBudget,
    ResourceClass,
    ResourceDemand,
    ResourceReservation,
)

#: 维度 → 记账语义（未列出 = 不在账本记账）
_ACCOUNTING: Dict[Dimension, str] = {
    Dimension.MEMORY_BYTES: "live",
    Dimension.GPU_MEMORY_BYTES: "live",
    Dimension.PIXEL_COUNT: "live",
    Dimension.FEATURE_COUNT: "cumulative",
    Dimension.NETWORK_BYTES: "cumulative",
    Dimension.STORAGE_BYTES: "cumulative",
    Dimension.CONTEXT_TOKENS: "cumulative",
    Dimension.OUTPUT_TOKENS: "cumulative",
    Dimension.WALL_TIME_S: "cumulative",
    Dimension.EXTERNAL_SERVICE_CALLS: "cumulative",
    Dimension.ESTIMATED_LLM_COST: "cumulative",
    Dimension.RENDER_WORK_UNITS: "cumulative",
}

#: 预算链（检查顺序：最内 → 最外）
SCOPE_CHAIN: Tuple[str, ...] = ("turn", "goal", "session", "global")


@dataclass
class BudgetViolation:
    """一条预算事实（scope × check × 投影值 vs 上限）。"""

    scope: str
    scope_id: str
    check: str                  # dim / heavy_count / external_calls
    dimension: Optional[Dimension]
    projected: float
    limit: float
    accounting: str            # live / cumulative / count
    provisional: bool

    def reason_code(self) -> str:
        dim = self.dimension.value if self.dimension else self.check
        return (f"budget:{self.scope}:{dim}:"
                f"{self.projected:.4g}>{self.limit:.4g}")


@dataclass
class _ScopeLedger:
    """一个 (scope, scope_id) 的账面。"""

    live: Dict[Dimension, float] = field(default_factory=dict)
    cumulative: Dict[Dimension, float] = field(default_factory=dict)
    heavy_live: int = 0
    browser_live: int = 0
    export_live: int = 0
    external_calls: float = 0.0
    reservations: Dict[str, ResourceReservation] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)


class SessionBudgetLedger:
    """进程内三级账本（线程安全：threading.Lock —— dispatch 在异步上下文
    调用，但账面操作都是微秒级同步段）。"""

    def __init__(self, budgets: Dict[str, ResourceBudget]):
        # scope 名 → 预算；缺 scope = 该级不限（诚实：不隐式继承）
        self._budgets = dict(budgets)
        # scope 名 → (scope_id → ledger)
        self._ledgers: Dict[str, Dict[str, _ScopeLedger]] = {
            scope: {} for scope in SCOPE_CHAIN
        }
        self._lock = threading.Lock()

    # ── 预算查询 ─────────────────────────────────────────────────────

    def budget_for(self, scope: str, scope_id: str = "") -> Optional[ResourceBudget]:
        """按级取预算（预算按级声明，不按具体 scope_id 个性化 —— V1 简化）。"""
        return self._budgets.get(scope)

    def set_budget(self, budget: ResourceBudget) -> None:
        """注入/更新某级预算（校准回填入口）。"""
        with self._lock:
            self._budgets[budget.scope] = budget

    # ── 准入前投影检查（不改账面）────────────────────────────────────

    def projection_violations(
        self,
        demand: ResourceDemand,
        adjudged: Dict[Dimension, float],
    ) -> List[BudgetViolation]:
        """把 demand 的判定值投影到 turn/goal/session/global 四级账面，
        返回全部违规（空 = 干净）。绝不修改账面（只读检查；账面条目由
        reserve/record 路径创建 —— review P2：纯检查不再 setdefault）。"""
        violations: List[BudgetViolation] = []
        with self._lock:
            for scope in SCOPE_CHAIN:
                budget = self._budgets.get(scope)
                if budget is None:
                    continue
                scope_id = self._scope_id_for(scope, demand)
                ledger = self._ledgers[scope].get(scope_id) or _ScopeLedger()
                for dim, add in adjudged.items():
                    accounting = _ACCOUNTING.get(dim)
                    if accounting is None or add <= 0:
                        continue
                    limit = budget.limit_for(dim)
                    if limit is None:
                        continue
                    current = (ledger.live if accounting == "live"
                               else ledger.cumulative).get(dim, 0.0)
                    projected = current + add
                    if projected > limit:
                        # global 作用域的 cumulative 维是**进程生命周期
                        # 计数器**（review P2 地雷）：一旦 ratchet 翻转
                        # provisional=False，任何长命进程终将全量硬拒。
                        # 强制按 provisional 处理（只告警），直至预算方
                        # 显式引入滚动窗口语义。
                        effective_provisional = (
                            budget.provisional
                            or (scope == "global" and accounting == "cumulative"))
                        violations.append(BudgetViolation(
                            scope=scope, scope_id=scope_id,
                            check="dim", dimension=dim,
                            projected=projected, limit=limit,
                            accounting=accounting,
                            provisional=effective_provisional,
                        ))
                # 计数维
                rc = demand.estimate.resource_class
                if (rc is ResourceClass.HEAVY and budget.max_heavy_concurrent
                        and ledger.heavy_live + 1 > budget.max_heavy_concurrent):
                    violations.append(BudgetViolation(
                        scope=scope, scope_id=scope_id,
                        check="heavy_count", dimension=None,
                        projected=ledger.heavy_live + 1,
                        limit=float(budget.max_heavy_concurrent),
                        accounting="count", provisional=budget.provisional,
                    ))
                if (budget.max_external_calls is not None
                        and ledger.external_calls + 1 > budget.max_external_calls):
                    violations.append(BudgetViolation(
                        scope=scope, scope_id=scope_id,
                        check="external_calls", dimension=None,
                        projected=ledger.external_calls + 1,
                        limit=float(budget.max_external_calls),
                        accounting="count", provisional=budget.provisional,
                    ))
        return violations

    # ── 账面变更（reserve/release 配对）──────────────────────────────

    def reserve(self, reservation: ResourceReservation,
                adjudged: Dict[Dimension, float]) -> None:
        """登记 reservation：live 维 + 计数。release 前有效。"""
        with self._lock:
            view = _demand_view(reservation)
            for scope in SCOPE_CHAIN:
                sid = self._scope_id_for(scope, view)
                ledger = self._ledgers[scope].setdefault(sid, _ScopeLedger())
                for dim, add in adjudged.items():
                    accounting = _ACCOUNTING.get(dim)
                    if accounting == "live":
                        ledger.live[dim] = ledger.live.get(dim, 0.0) + add
                if reservation.resource_class is ResourceClass.HEAVY:
                    ledger.heavy_live += 1
                if reservation.subsystem.value == "browser":
                    ledger.browser_live += 1
                if reservation.subsystem.value == "export":
                    ledger.export_live += 1
                ledger.reservations[reservation.reservation_id] = reservation
                ledger.last_activity = time.monotonic()
            self._evict_idle_sessions_locked()

    #: 会话账本的有界性（review P2：close_session 无生产调用方 → 惰性驱逐）
    _MAX_SESSION_LEDGERS = 512
    _EVICT_TO = 256

    def _evict_idle_sessions_locked(self) -> None:
        sessions = self._ledgers["session"]
        if len(sessions) <= self._MAX_SESSION_LEDGERS:
            return
        evictable = sorted(
            (sid for sid, led in sessions.items() if not led.reservations),
            key=lambda sid: sessions[sid].last_activity)
        for sid in evictable[:len(sessions) - self._EVICT_TO]:
            sessions.pop(sid, None)

    def release(self, reservation: ResourceReservation,
                actual: Optional[Dict[Dimension, float]] = None) -> None:
        """归还 live 维 + 累计 actual（cumulative 维不因 release 回滚）。"""
        actual = actual or {}
        with self._lock:
            for scope in SCOPE_CHAIN:
                sid = self._scope_id_for(scope, _demand_view(reservation))
                ledger = self._ledgers[scope].get(sid)
                if ledger is None:
                    continue
                for dim, add in actual.items():
                    accounting = _ACCOUNTING.get(dim)
                    if accounting == "live":
                        ledger.live[dim] = max(
                            0.0, ledger.live.get(dim, 0.0) - add)
                    elif accounting == "cumulative":
                        ledger.cumulative[dim] = (
                            ledger.cumulative.get(dim, 0.0) + add)
                if reservation.resource_class is ResourceClass.HEAVY:
                    ledger.heavy_live = max(0, ledger.heavy_live - 1)
                if reservation.subsystem.value == "browser":
                    ledger.browser_live = max(0, ledger.browser_live - 1)
                if reservation.subsystem.value == "export":
                    ledger.export_live = max(0, ledger.export_live - 1)
                ledger.reservations.pop(reservation.reservation_id, None)
                ledger.last_activity = time.monotonic()

    # ── R12 挂点：context token 累计 ─────────────────────────────────

    def record_context_tokens(self, session_id: str, turn_id: str,
                              tokens: int) -> None:
        """chat 面汇报的 token 消耗（read-only 视角，无 reservation）。"""
        if tokens <= 0:
            return
        with self._lock:
            for scope, sid in (("turn", turn_id), ("session", session_id)):
                if not sid:
                    continue
                ledger = self._ledgers[scope].setdefault(sid, _ScopeLedger())
                ledger.cumulative[Dimension.CONTEXT_TOKENS] = (
                    ledger.cumulative.get(Dimension.CONTEXT_TOKENS, 0.0) + tokens)
                ledger.last_activity = time.monotonic()

    # ── 观测 ─────────────────────────────────────────────────────────

    def snapshot(self, session_id: str, turn_id: str = "",
                 goal_id: str = "") -> Dict:
        """账面快照（诊断/校准证据；绝不阻塞执行 —— 微秒级读锁段）。"""
        with self._lock:
            out: Dict = {}
            for scope in SCOPE_CHAIN:
                sid = {"turn": turn_id, "goal": goal_id,
                       "session": session_id, "global": "global"}.get(scope, "")
                ledger = self._ledgers[scope].get(sid)
                if ledger is None:
                    continue
                out[scope] = {
                    "scope_id": sid,
                    "live": {d.value: v for d, v in ledger.live.items()},
                    "cumulative": {d.value: v for d, v in ledger.cumulative.items()},
                    "heavy_live": ledger.heavy_live,
                    "browser_live": ledger.browser_live,
                    "export_live": ledger.export_live,
                    "open_reservations": len(ledger.reservations),
                }
            return out

    def active_session_count(self) -> int:
        with self._lock:
            return len(self._ledgers["session"])

    def live_memory_total(self) -> float:
        """全局在飞 memory（adjudged 总和；admission 内存压力输入）。

        只按 session 作用域求和 —— reservation 沿链记账后 global 作用域
        是**祖先视图**而非增量，加进来会把同一份在飞量计双次。
        """
        with self._lock:
            return sum(led.live.get(Dimension.MEMORY_BYTES, 0.0)
                       for led in self._ledgers["session"].values())

    def _scope_id_for(self, scope: str, demand: ResourceDemand) -> str:
        if scope == "turn":
            return demand.turn_id or f"__session__{demand.session_id}"
        if scope == "goal":
            return demand.goal_id or f"__session__{demand.session_id}"
        if scope == "session":
            return demand.session_id or "anonymous"
        return "global"


def live_dims_of(charged: Dict[Dimension, float]) -> Dict[Dimension, float]:
    """过滤出 live 记账语义的维度（准入清理路径用：release 只需原样归还
    reserve 实际加过的 live 维；cumulative 维 reserve 从未加过，若传入会
    被误计入累计消耗）。"""
    return {d: v for d, v in charged.items()
            if _ACCOUNTING.get(d) == "live"}


def _demand_view(reservation: ResourceReservation) -> ResourceDemand:
    """reservation → demand 形状的视图（scope id 解析复用）。"""
    return ResourceDemand(
        session_id=reservation.session_id,
        goal_id=reservation.goal_id,
        turn_id=reservation.turn_id,
        subsystem=reservation.subsystem,
    )


__all__ = [
    "SCOPE_CHAIN",
    "BudgetViolation",
    "SessionBudgetLedger",
]
