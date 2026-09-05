"""Agent 执行 trace 事件模式（ADR-0101 Wave 8, §33）。

有界事件模式，覆盖一轮的完整生命周期：

    turn_start → model_selected → context_built → tool_surface_selected →
    model_request → tool_call_proposed → tool_args_normalized →
    dispatch_started → dispatch_completed → artifact_produced →
    plan_progressed → map_product_changed → no_progress_detected →
    fallback → subagent_spawned → subagent_completed → turn_settled

载荷策略（显式，§33 红线）：
- **永不记录**：API key / secret / token / 完整大数据集（>4KB 的 str/list/dict
  值摘要化为 (type, len, head)）；键名命中敏感词的值直接以 "[REDACTED]" 替代；
- 事件环有界（每 turn ≤ max_events，超出丢最旧并记 dropped_count）；
- turn 注册表有界（默认 128 turn，LRU 逐出）；
- 进程内观测面（debug bundle / replay 输入），不落盘 —— 落盘仍由
  tool_metrics（ADR-0044）与 decision_log 负责，不建第二持久化真相。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# 事件种类（封闭词表；新种类必须显式加入）
EVENT_TURN_START = "turn_start"
EVENT_MODEL_SELECTED = "model_selected"
EVENT_CONTEXT_BUILT = "context_built"
EVENT_TOOL_SURFACE_SELECTED = "tool_surface_selected"
EVENT_MODEL_REQUEST = "model_request"
EVENT_TOOL_CALL_PROPOSED = "tool_call_proposed"
EVENT_TOOL_ARGS_NORMALIZED = "tool_args_normalized"
EVENT_DISPATCH_STARTED = "dispatch_started"
EVENT_DISPATCH_COMPLETED = "dispatch_completed"
EVENT_ARTIFACT_PRODUCED = "artifact_produced"
EVENT_PLAN_PROGRESSED = "plan_progressed"
EVENT_MAP_PRODUCT_CHANGED = "map_product_changed"
EVENT_NO_PROGRESS = "no_progress_detected"
EVENT_FALLBACK = "fallback"
EVENT_SUBAGENT_SPAWNED = "subagent_spawned"
EVENT_SUBAGENT_COMPLETED = "subagent_completed"
EVENT_TURN_SETTLED = "turn_settled"

KNOWN_EVENT_KINDS = frozenset({
    EVENT_TURN_START, EVENT_MODEL_SELECTED, EVENT_CONTEXT_BUILT,
    EVENT_TOOL_SURFACE_SELECTED, EVENT_MODEL_REQUEST, EVENT_TOOL_CALL_PROPOSED,
    EVENT_TOOL_ARGS_NORMALIZED, EVENT_DISPATCH_STARTED, EVENT_DISPATCH_COMPLETED,
    EVENT_ARTIFACT_PRODUCED, EVENT_PLAN_PROGRESSED, EVENT_MAP_PRODUCT_CHANGED,
    EVENT_NO_PROGRESS, EVENT_FALLBACK, EVENT_SUBAGENT_SPAWNED,
    EVENT_SUBAGENT_COMPLETED, EVENT_TURN_SETTLED,
})

_SENSITIVE_KEY_HINTS = ("key", "token", "secret", "password", "authorization", "api")
_META_VALUE_MAX_CHARS = 512
_MAX_META_ENTRIES = 16


def bound_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """有界化 + 消毒事件元数据（显式载荷策略）。"""
    out: Dict[str, Any] = {}
    for k, v in list(meta.items())[:_MAX_META_ENTRIES]:
        k = str(k)[:64]
        if any(h in k.lower() for h in _SENSITIVE_KEY_HINTS):
            out[k] = "[REDACTED]"
            continue
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if len(v) <= _META_VALUE_MAX_CHARS else (
                v[:_META_VALUE_MAX_CHARS] + f"…({len(v)} chars)"
            )
        elif isinstance(v, (list, tuple, dict)):
            blob = repr(v)
            out[k] = blob if len(blob) <= 200 else (
                f"<{type(v).__name__} len={len(v)}>"
            )
        else:
            out[k] = repr(v)[:120]
    return out


@dataclass(frozen=True)
class TraceEvent:
    kind: str
    ts: float
    turn_id: str
    session_id: str = ""
    tool_call_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ts": round(self.ts, 3),
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "tool_call_id": self.tool_call_id,
            "meta": dict(self.meta),
        }


@dataclass
class TurnTrace:
    """单个 turn 的事件环（有界）。"""

    turn_id: str
    max_events: int = 256
    events: List[TraceEvent] = field(default_factory=list)
    dropped_count: int = 0
    settled: bool = False

    def append(self, event: TraceEvent) -> None:
        if event.kind == EVENT_TURN_SETTLED:
            self.settled = True
        self.events.append(event)
        if len(self.events) > self.max_events:
            drop = len(self.events) - self.max_events
            del self.events[:drop]
            self.dropped_count += drop

    def kinds(self) -> List[str]:
        return [e.kind for e in self.events]

    def find(self, kind: str) -> List[TraceEvent]:
        return [e for e in self.events if e.kind == kind]

    def summary(self) -> Dict[str, Any]:
        from collections import Counter

        return {
            "turn_id": self.turn_id,
            "events": len(self.events),
            "dropped": self.dropped_count,
            "kinds": dict(Counter(self.kinds())),
            "settled": self.settled,
        }


class TraceRegistry:
    """turn_id → TurnTrace 的有界注册表（LRU，线程安全）。"""

    def __init__(self, max_turns: int = 128, max_events_per_turn: int = 256) -> None:
        self._max_turns = max_turns
        self._max_events = max_events_per_turn
        self._lock = threading.Lock()
        self._traces: "OrderedDict[str, TurnTrace]" = OrderedDict()

    def start_turn(self, turn_id: str, session_id: str = "") -> TurnTrace:
        with self._lock:
            trace = TurnTrace(turn_id=turn_id, max_events=self._max_events)
            self._traces[turn_id] = trace
            self._traces.move_to_end(turn_id)
            while len(self._traces) > self._max_turns:
                self._traces.popitem(last=False)
            return trace

    def get(self, turn_id: str) -> Optional[TurnTrace]:
        with self._lock:
            return self._traces.get(turn_id)

    def emit(
        self,
        turn_id: str,
        kind: str,
        *,
        session_id: str = "",
        tool_call_id: str = "",
        **meta: Any,
    ) -> None:
        """发一个事件（turn 不存在则自动建；未知 kind 忽略 —— 封闭词表）。"""
        if kind not in KNOWN_EVENT_KINDS:
            return
        with self._lock:
            trace = self._traces.get(turn_id)
            if trace is None:
                trace = TurnTrace(turn_id=turn_id, max_events=self._max_events)
                self._traces[turn_id] = trace
                while len(self._traces) > self._max_turns:
                    self._traces.popitem(last=False)
        trace.append(TraceEvent(
            kind=kind,
            ts=time.time(),
            turn_id=turn_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            meta=bound_meta(meta),
        ))

    def pop(self, turn_id: str) -> Optional[TurnTrace]:
        with self._lock:
            return self._traces.pop(turn_id, None)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"turns": len(self._traces)}


_registry = TraceRegistry()


def get_trace_registry() -> TraceRegistry:
    return _registry
