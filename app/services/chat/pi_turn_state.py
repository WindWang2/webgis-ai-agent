"""Pi turn 相位状态机（G01-PI-NATIVE-RUNTIME / PI-01，ADR-0286 D1）。

以显式 ``TurnPhase`` 枚举收敛 ``agent_pi_bridge`` 两条路径（``prompt`` /
``stream_prompt``）此前由 6 个布尔旗标 + 2 个字符串拼凑的 turn 生命周期表达：

    CREATED → STARTING → RUNNING ⇄ TOOL_WAIT
                 │          │        │
                 │          ↓        ↓
                 └────→ CANCELLING → SETTLING → SETTLED | FAILED（终态）

设计边界（ADR-0286）：

- **观测与防御面，不改控制流**：现有旗标与单结算 seam 的权威语义不变；相位机
  是叠加的显式表达。非法转移被拒绝并计数（绝不抛异常——观测面不得破坏 turn）。
- **终态映射**：``SETTLED`` 仅对应 kernel ``completed``/``refused``；
  ``cancelled/aborted/interrupted/failed`` 及一切未知词表 → ``FAILED``
  （fail-closed）。细粒度终态（取消 vs 失败）仍由 ``TurnEvidence`` 的
  ``OutcomeRecorder`` first-wins 记录——两记录器互补，不建第二事实源。
- **settlement exactly-once 门闩**：``begin_settlement()`` 首次 True、之后一律
  False；``_settle_turn_outcome`` 借它在 bridge 边界直接拦截第二次结算
  （coordinator 幂等仍是兜底权威）。

线程安全：流 task / dispatch task / abort（另一 HTTP task）都可能写相位，
沿用 ``TurnEvidence`` 的 ``threading.Lock`` 模式。
"""
from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Dict, List, Optional

__all__ = ["TurnPhase", "TurnPhaseTracker", "TERMINAL_PHASES"]


class TurnPhase(str, Enum):
    """一次 Pi turn 的宿主侧相位（任务契约词表）。

    ``settled`` 表示清洁收口（kernel completed/refused）；取消/中止/失败及其
    一切未知终态一律 ``failed``（细粒度差异在 TurnEvidence.outcome 里）。
    """

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    TOOL_WAIT = "tool_wait"
    CANCELLING = "cancelling"
    SETTLING = "settling"
    SETTLED = "settled"
    FAILED = "failed"


TERMINAL_PHASES = frozenset({TurnPhase.SETTLED, TurnPhase.FAILED})

# kernel TurnStatus（含 refused 降级）→ 终相位。未知词表 fail-closed → FAILED。
_STATUS_SUCCESS = frozenset({"completed", "refused"})

# 合法转移表：核心推进 CREATED→STARTING→(RUNNING⇄TOOL_WAIT)→SETTLING→终态；
# 任何非终态可在取消/失败时直达 SETTLING（send-failed 早退、注册期失败等
# 真实路径都会跳过 RUNNING）。
_LEGAL_TRANSITIONS: Dict[TurnPhase, frozenset] = {
    TurnPhase.CREATED: frozenset({TurnPhase.STARTING, TurnPhase.CANCELLING,
                                  TurnPhase.SETTLING, TurnPhase.FAILED}),
    TurnPhase.STARTING: frozenset({TurnPhase.RUNNING, TurnPhase.CANCELLING,
                                   TurnPhase.SETTLING, TurnPhase.FAILED}),
    TurnPhase.RUNNING: frozenset({TurnPhase.TOOL_WAIT, TurnPhase.CANCELLING,
                                  TurnPhase.SETTLING, TurnPhase.FAILED}),
    TurnPhase.TOOL_WAIT: frozenset({TurnPhase.RUNNING, TurnPhase.CANCELLING,
                                    TurnPhase.SETTLING, TurnPhase.FAILED}),
    TurnPhase.CANCELLING: frozenset({TurnPhase.SETTLING, TurnPhase.FAILED}),
    TurnPhase.SETTLING: frozenset({TurnPhase.SETTLED, TurnPhase.FAILED}),
    TurnPhase.SETTLED: frozenset(),
    TurnPhase.FAILED: frozenset(),
}

_MAX_TRANSITION_LOG = 32  # 每 turn 转移轨迹上限（有界，防无界驻留）


def terminal_phase_for_status(status: str) -> TurnPhase:
    """kernel TurnStatus → 终相位（纯函数；未知词表 fail-closed）。"""
    return TurnPhase.SETTLED if str(status or "") in _STATUS_SUCCESS else TurnPhase.FAILED


class TurnPhaseTracker:
    """单 turn 相位机：转移守卫 + settlement 门闩 + 有界观测投影。"""

    __slots__ = ("turn_id", "_phase", "_lock", "_transitions", "_illegal",
                 "_settlement_attempts", "_settlement_open", "_last_status",
                 "_settled_at")

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._phase = TurnPhase.CREATED
        self._lock = threading.Lock()
        self._transitions: List[Dict[str, object]] = []
        self._illegal = 0
        self._settlement_attempts = 0
        self._settlement_open = True
        self._last_status: str = ""
        self._settled_at: Optional[float] = None

    # ── 转移 ────────────────────────────────────────────────────────────────
    def transition(self, new_phase: TurnPhase, *, reason: str = "") -> bool:
        """尝试转移到 ``new_phase``。合法且非终态重复设值 → True（幂等放行）；
        非法/已终态 → 拒绝并计数，返回 False（绝不抛异常）。"""
        with self._lock:
            current = self._phase
            if current in TERMINAL_PHASES:
                self._illegal += 1
                return False
            if new_phase is current:
                # 幂等重设（如多个 tool_execution_end 连发）——放行但不记录。
                return True
            if new_phase not in _LEGAL_TRANSITIONS[current]:
                self._illegal += 1
                return False
            self._phase = new_phase
            if len(self._transitions) < _MAX_TRANSITION_LOG:
                self._transitions.append({
                    "from": current.value,
                    "to": new_phase.value,
                    "reason": str(reason)[:80],
                    "t": round(time.monotonic(), 3),
                })
            return True

    def observe_tool_event(self, event_type: str) -> None:
        """RPC 事件 → 相位捷径（tool_execution_start/end 驱动 TOOL_WAIT⇀RUNNING）。

        只在 RUNNING/TOOL_WAIT 相位起作用；其余相位由 ``transition`` 的守卫
        拒绝（不计数——事件乱序到达不是相位机违规）。
        """
        if event_type == "tool_execution_start":
            if self._phase is TurnPhase.RUNNING:
                self.transition(TurnPhase.TOOL_WAIT, reason="tool_execution_start")
        elif event_type == "tool_execution_end":
            if self._phase is TurnPhase.TOOL_WAIT:
                self.transition(TurnPhase.RUNNING, reason="tool_execution_end")

    # ── settlement 门闩（exactly-once）─────────────────────────────────────
    def begin_settlement(self) -> bool:
        """首次调用 True；之后一律 False（seam 的 bridge 边界拦截器）。"""
        with self._lock:
            self._settlement_attempts += 1
            if not self._settlement_open:
                return False
            self._settlement_open = False
            return True

    def record_terminal_status(self, status: str) -> bool:
        """记录 kernel 终态 → 终相位。应仅发生在 SETTLING；其他非终相位下的
        调用防御性放行并计一次非法（真实控制流不应出现）。"""
        terminal = terminal_phase_for_status(status)
        with self._lock:
            self._last_status = str(status or "")
            self._settled_at = time.monotonic()
            if self._phase not in (TurnPhase.SETTLING,) and self._phase not in TERMINAL_PHASES:
                # 防御：未进入 SETTLING 就收到终态 —— 记非法但保持 fail-safe 收口。
                self._illegal += 1
            if self._phase in TERMINAL_PHASES:
                return False
            self._phase = terminal
            if len(self._transitions) < _MAX_TRANSITION_LOG:
                self._transitions.append({
                    "from": "settling",
                    "to": terminal.value,
                    "reason": f"kernel_status={self._last_status[:40]}",
                    "t": round(time.monotonic(), 3),
                })
            return True

    # ── 只读投影 ────────────────────────────────────────────────────────────
    @property
    def phase(self) -> TurnPhase:
        with self._lock:
            return self._phase

    @property
    def last_settlement_status(self) -> str:
        with self._lock:
            return self._last_status

    def to_dict(self) -> Dict[str, object]:
        with self._lock:
            return {
                "phase": self._phase.value,
                "transitions": [dict(t) for t in self._transitions],
                "illegal_transitions": self._illegal,
                "settlement_attempts": self._settlement_attempts,
                "settlement_status": self._last_status or None,
            }
