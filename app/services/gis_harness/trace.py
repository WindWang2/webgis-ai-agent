"""GIS Runtime Trace —— 低成本、有界的内部可观测层（ADR-0088 P7）。

目的：product closure 回路（finalization / runtime repair / observation /
artifact sweep）是确定性系统，出问题时需要**内部**追溯「决策链是什么」，
而不是把大段 trace 注入 LLM 上下文。本模块提供：

- per-session 有界事件环（``MAX_EVENTS_PER_SESSION``，FIFO 淘汰）；
- 全局有界计数器（resolver fallback / artifact 复用 / runtime repair /
  observation 拒绝等指标，键集合有限）；
- 会话级 summary（测试/诊断投影，bounded）。

边界：

- 纯内存、进程内 —— 不持久化、不进 LLM context、不发网络（真实链路
  观测仍以结构化 logger 为主；本模块补的是**聚合视图**）；
- 所有 write 侧调用都是 best-effort（try/except 由调用方兜底）——
  trace 故障绝不影响业务路径；
- 有界：session 数上限 + 每会话事件上限 + 计数器键有限集合。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

# 有界契约
MAX_EVENTS_PER_SESSION = 64
MAX_SESSIONS = 256
MAX_DETAIL_BYTES = 512

# 计数器键（有限集合；未注册键被丢弃 —— 防漂移成无界 dict）
COUNTER_FINALIZATIONS = "finalizations"
COUNTER_FINALIZATION_REPAIRS = "finalization_repairs"
COUNTER_RUNTIME_REPAIRS = "runtime_repairs"
COUNTER_RUNTIME_REPAIR_EXHAUSTED = "runtime_repair_exhausted"
COUNTER_RUNTIME_REPAIR_EXECUTION_DEBTS = "runtime_repair_execution_debts"
COUNTER_ACTION_INTENTS = "action_intents"
COUNTER_OBSERVATION_REJECTS = "observation_rejects"

_KNOWN_COUNTERS = frozenset({
    COUNTER_FINALIZATIONS,
    COUNTER_FINALIZATION_REPAIRS,
    COUNTER_RUNTIME_REPAIRS,
    COUNTER_RUNTIME_REPAIR_EXHAUSTED,
    COUNTER_RUNTIME_REPAIR_EXECUTION_DEBTS,
    COUNTER_ACTION_INTENTS,
    COUNTER_OBSERVATION_REJECTS,
})

# 事件 stage 词表（有限集合）
STAGE_FINALIZATION = "finalization"
STAGE_RUNTIME_REPAIR = "runtime_repair"
STAGE_ACTION_INTENT = "action_intent"
STAGE_OBSERVATION = "observation"


@dataclass
class TraceEvent:
    """单条内部追溯事件（bounded：detail 键数与值长度受限）。"""

    ts: float
    stage: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "stage": self.stage[:32],
            "detail": {
                str(k)[:32]: (v if isinstance(v, (int, float, bool)) else str(v)[:96])
                for k, v in list(self.detail.items())[:8]
            },
        }


class GISRuntimeTrace:
    """进程内 trace 存储（thread-safe；全部有界）。"""

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        max_events: int = MAX_EVENTS_PER_SESSION,
    ) -> None:
        self._lock = threading.Lock()
        self._max_sessions = max_sessions
        self._max_events = max_events
        self._sessions: "OrderedDict[str, Deque[TraceEvent]]" = OrderedDict()
        self._counters: Dict[str, int] = {k: 0 for k in _KNOWN_COUNTERS}

    def record(self, session_id: str, stage: str, **detail: Any) -> None:
        """追加一条事件（LRU 淘汰最旧会话；FIFO 淘汰最旧事件）。"""
        if not session_id or stage not in (
            STAGE_FINALIZATION, STAGE_RUNTIME_REPAIR,
            STAGE_ACTION_INTENT, STAGE_OBSERVATION,
        ):
            return
        event = TraceEvent(ts=time.time(), stage=stage, detail=detail)
        with self._lock:
            ring = self._sessions.get(session_id)
            if ring is None:
                ring = deque(maxlen=self._max_events)
                self._sessions[session_id] = ring
                while len(self._sessions) > self._max_sessions:
                    self._sessions.popitem(last=False)
            else:
                ring = self._sessions[session_id]
                self._sessions.move_to_end(session_id)
            ring.append(event)

    def bump(self, counter: str, n: int = 1) -> None:
        """累加一个已注册计数器（未知键丢弃 —— 键集合有限）。"""
        if counter not in _KNOWN_COUNTERS:
            return
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + n

    def events(self, session_id: str, *, limit: int = 16) -> List[Dict[str, Any]]:
        """会话事件投影（最新在后；bounded）。"""
        with self._lock:
            ring = self._sessions.get(session_id)
            if not ring:
                return []
            return [e.to_dict() for e in list(ring)[-limit:]]

    def counters(self) -> Dict[str, int]:
        """全局计数器快照（bounded dict 副本）。"""
        with self._lock:
            return dict(self._counters)

    def summary(self, session_id: str) -> Dict[str, Any]:
        """会话级聚合（测试/诊断；bounded）。"""
        evs = self.events(session_id, limit=self._max_events)
        by_stage: Dict[str, int] = {}
        for e in evs:
            by_stage[e["stage"]] = by_stage.get(e["stage"], 0) + 1
        return {
            "events": len(evs),
            "by_stage": by_stage,
            "latest": evs[-1] if evs else None,
        }

    def reset(self) -> None:
        """测试隔离。"""
        with self._lock:
            self._sessions.clear()
            self._counters = {k: 0 for k in _KNOWN_COUNTERS}


_trace: Optional[GISRuntimeTrace] = None


def get_runtime_trace() -> GISRuntimeTrace:
    global _trace
    if _trace is None:
        _trace = GISRuntimeTrace()
    return _trace


def reset_runtime_trace() -> None:
    global _trace
    _trace = None


__all__ = [
    "MAX_EVENTS_PER_SESSION",
    "MAX_SESSIONS",
    "TraceEvent",
    "GISRuntimeTrace",
    "get_runtime_trace",
    "reset_runtime_trace",
    "STAGE_FINALIZATION",
    "STAGE_RUNTIME_REPAIR",
    "STAGE_ACTION_INTENT",
    "STAGE_OBSERVATION",
    "COUNTER_FINALIZATIONS",
    "COUNTER_FINALIZATION_REPAIRS",
    "COUNTER_RUNTIME_REPAIRS",
    "COUNTER_RUNTIME_REPAIR_EXHAUSTED",
    "COUNTER_RUNTIME_REPAIR_EXECUTION_DEBTS",
    "COUNTER_ACTION_INTENTS",
    "COUNTER_OBSERVATION_REJECTS",
]
