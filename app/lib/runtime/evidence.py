"""运行时证据封套：outcome / TurnEvidence / TurnEvidenceRegistry。

``TurnEvidence`` 是一次 turn 的进程内证据累加器，回答 /goal §5/§8：

    identity   : request / session / turn / run
    timing     : started / first_event(TTFT proxy) / llm(legacy) / context / tool / map_ack_wait / total
    outcome    : succeeded / failed / cancelled / superseded / partial / not_evaluated
    work       : llm_rounds / tool_calls / tool_retries / deduped_tool_calls / sse_events /
                 map_actions_issued / map_actions_acked / artifacts
    warnings   : 有界（孤儿 map 动作、stale、资源异常…）

关键机制（Matt #1 P0 修正）：Pi 路径上工具/ACK 证据来自 *独立的 HTTP 请求 task*
（/pi-tools/execute、/map-action-ack），流任务里的 ContextVar 看不到它们。因此
``TurnEvidence`` 不是纯 ContextVar 累加器，而是 **turn_id 键控的进程内注册表**
（``TURN_EVIDENCE``）：所有 adapter 按 turn_id 写入；流任务在 turn 结束时汇总。

并发安全：被流 task / dispatch task / ack task 写入，且 dispatch 可能经
``asyncio.to_thread`` 在工具线程里——故所有可变状态用 ``threading.Lock`` 保护。

单进程假设：注册表进程内、有界（FIFO 64）。workers>1 时 Pi 回调可能落到非归属
worker，turn 关联查不到 → graceful 跳过（记 warning，不崩）。
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# ── 常量（有界，防高基数 / 无界驻留）──────────────────────────────────────────
_MAX_ACTIVE_TURNS = 64           # 注册表最多同时跟踪的 turn
_MAX_WARNINGS = 32               # 每 turn 的 warning 上限
_MAX_TRACKED_MAP_ACTIONS = 256   # 每 turn 跟踪的 issued map action 上限
_STALE_ACTION_TTL_S = 120.0      # issued 超过该时长仍无终态 → 视为孤儿（仅 warning，不伪造终态）


class Outcome(str, Enum):
    """一次工作单元的终态。失败不坍缩成单一 "failed"。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"          # 取消不是失败
    SUPERSEDED = "superseded"        # 被更新者取代（map camera coalesce / replay）
    PARTIAL = "partial"              # 部分步骤完成
    NOT_EVALUATED = "not_evaluated"  # 缺失证据——绝不等于成功


class OutcomeRecorder:
    """终态恰好一次记录器（first-wins），线程安全。

    用于 turn 级 outcome：多路径（成功路径 / 异常 / 取消 / 超时）都可能尝试结算，
    只有第一个终态生效；后续结算返回 False。避免「取消后被异常覆盖成 failed」或
    「成功后又标失败」。
    """

    __slots__ = ("_outcome", "_failure_class", "_detail", "_lock")

    def __init__(self) -> None:
        self._outcome: Optional[Outcome] = None
        self._failure_class: Optional[str] = None
        self._detail: Optional[str] = None
        self._lock = threading.Lock()

    def settle(
        self,
        outcome: Outcome,
        *,
        failure_class: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> bool:
        """记录终态。返回 True 表示本次调用成功结算（first-wins），False 表示已有终态。"""
        with self._lock:
            if self._outcome is not None:
                return False
            self._outcome = outcome
            self._failure_class = failure_class
            self._detail = detail
            return True

    @property
    def outcome(self) -> Optional[Outcome]:
        with self._lock:
            return self._outcome

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._outcome is not None

    @property
    def failure_class(self) -> Optional[str]:
        with self._lock:
            return self._failure_class

    def as_dict(self) -> Dict[str, Optional[str]]:
        with self._lock:
            return {
                "outcome": self._outcome.value if self._outcome else None,
                "failure_class": self._failure_class,
                "detail": self._detail,
            }


@dataclass
class _MapActionTrack:
    issued_at: float  # monotonic
    status: Optional[str] = None
    ack_wait_ms: Optional[float] = None


class TurnEvidence:
    """一次 turn 的进程内证据累加器（线程安全，按 turn_id 注册）。

    计数/计时字段为标量；map 动作按 action_id 跟踪 issued_at 以计算 ack_wait。
    warning 有界。``to_summary()`` 输出诊断 dict（redacted，无敏感数据）。
    """

    def __init__(self, *, request_id: Optional[str], session_id: Optional[str],
                 turn_id: str, run_id: Optional[str]) -> None:
        self.request_id = request_id
        self.session_id = session_id
        self.turn_id = turn_id
        self.run_id = run_id
        self.started_monotonic = time.monotonic()
        self._first_event_monotonic: Optional[float] = None
        self._ended_monotonic: Optional[float] = None
        # 计时（ms，浮点）
        self.context_ms: float = 0.0
        self.llm_total_ms: float = 0.0
        self.llm_ttft_ms: Optional[float] = None  # legacy call_llm stream first-token
        self.tool_ms: float = 0.0                  # 工具执行墙钟累计
        # 计数
        self.llm_rounds = 0
        self.tool_calls = 0
        self.tool_retries = 0
        self.deduped_tool_calls = 0
        self.sse_events = 0
        self.map_actions_issued = 0
        self.map_actions_acked = 0
        self.artifacts = 0
        # audit4 #985: provider token usage 记账（此前全链路丢弃，成本不可观测）
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.llm_usage_reports = 0
        # 结构（有界）
        self._map_actions: "OrderedDict[str, _MapActionTrack]" = OrderedDict()
        self._warnings: Deque[Dict[str, str]] = deque(maxlen=_MAX_WARNINGS)
        self.outcome = OutcomeRecorder()
        self._lock = threading.Lock()

    # ── timing ──────────────────────────────────────────────────────────────
    def mark_first_event(self) -> None:
        """记录首个 SSE 事件（token/content）到达——TTFT 代理（两条路径都可观测）。"""
        with self._lock:
            if self._first_event_monotonic is None:
                self._first_event_monotonic = time.monotonic()

    def mark_ended(self) -> None:
        with self._lock:
            self._ended_monotonic = time.monotonic()

    def add_context_ms(self, ms: float) -> None:
        if ms <= 0:
            return
        with self._lock:
            self.context_ms += ms

    def add_llm_round(self, *, total_ms: Optional[float] = None,
                      ttft_ms: Optional[float] = None) -> None:
        with self._lock:
            self.llm_rounds += 1
            if total_ms is not None:
                self.llm_total_ms += total_ms
            if ttft_ms is not None and self.llm_ttft_ms is None:
                self.llm_ttft_ms = ttft_ms

    def add_llm_usage(self, usage: Optional[dict]) -> None:
        """audit4 #985: 累计一次 LLM 调用的 provider usage（None/缺字段安全跳过）。"""
        if not isinstance(usage, dict):
            return
        try:
            p = int(usage.get("prompt_tokens") or 0)
            c = int(usage.get("completion_tokens") or 0)
            t = int(usage.get("total_tokens") or (p + c))
        except (TypeError, ValueError):
            return
        with self._lock:
            self.prompt_tokens += p
            self.completion_tokens += c
            self.total_tokens += t
            self.llm_usage_reports += 1

    def add_tool_call(self, *, duration_ms: Optional[float] = None,
                      failure_class: Optional[str] = None) -> None:
        with self._lock:
            self.tool_calls += 1
            if duration_ms and duration_ms > 0:
                self.tool_ms += duration_ms

    def add_tool_retry(self) -> None:
        with self._lock:
            self.tool_retries += 1

    def add_deduped_tool_call(self) -> None:
        """重复/被 dedup 拦截的工具调用（dispatch_service 早返回，不进 registry）。"""
        with self._lock:
            self.deduped_tool_calls += 1

    def inc_sse_event(self, n: int = 1) -> None:
        with self._lock:
            self.sse_events += n

    def add_artifacts(self, n: int = 1) -> None:
        with self._lock:
            self.artifacts += n

    # ── map action（跨请求 join：issued 在 dispatch_task，ack 在 ack_task）─────
    def record_map_action_issued(self, action_id: str) -> None:
        if not action_id:
            return
        with self._lock:
            if action_id in self._map_actions:
                return
            if len(self._map_actions) >= _MAX_TRACKED_MAP_ACTIONS:
                self._map_actions.popitem(last=False)  # FIFO 丢弃最旧
            self._map_actions[action_id] = _MapActionTrack(issued_at=time.monotonic())
            self.map_actions_issued += 1

    def record_map_action_acked(self, action_id: str, status: str) -> None:
        if not action_id:
            return
        with self._lock:
            self.map_actions_acked += 1
            track = self._map_actions.get(action_id)
            if track is not None:
                track.status = status
                track.ack_wait_ms = (time.monotonic() - track.issued_at) * 1000.0

    # ── warnings / outcome ───────────────────────────────────────────────────
    def add_warning(self, code: str, detail: str = "") -> None:
        with self._lock:
            self._warnings.append({"code": code, "detail": detail[:200]})

    def settle(self, outcome: Outcome, **kwargs) -> bool:
        return self.outcome.settle(outcome, **kwargs)

    # ── summary ──────────────────────────────────────────────────────────────
    def _finalize_stale_warnings(self) -> None:
        """汇总前：issued 超过 TTL 仍无终态的 map 动作 → 孤儿 warning（不伪造终态）。"""
        now = time.monotonic()
        with self._lock:
            orphans = [
                aid for aid, t in self._map_actions.items()
                if t.status is None and (now - t.issued_at) > _STALE_ACTION_TTL_S
            ]
        for aid in orphans:
            self.add_warning("orphan_map_action", f"action_id={aid} 未收到 ACK 终态")

    def to_summary(self) -> Dict[str, object]:
        """诊断汇总（redacted；用于 turn 结束时的结构化日志，/goal §8）。"""
        self._finalize_stale_warnings()
        with self._lock:
            end = self._ended_monotonic if self._ended_monotonic is not None else time.monotonic()
            total_ms = (end - self.started_monotonic) * 1000.0
            first_event_ms: Optional[float] = None
            if self._first_event_monotonic is not None:
                first_event_ms = (self._first_event_monotonic - self.started_monotonic) * 1000.0
            ack_waits = [t.ack_wait_ms for t in self._map_actions.values() if t.ack_wait_ms is not None]
            map_ack_wait_ms = max(ack_waits) if ack_waits else None
            unacked = sum(1 for t in self._map_actions.values() if t.status is None)
            warnings = list(self._warnings)
        return {
            "correlation": {
                "request_id": self.request_id,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "run_id": self.run_id,
            },
            "outcome": self.outcome.as_dict(),
            "timing_ms": {
                "total": round(total_ms, 1),
                "first_event": round(first_event_ms, 1) if first_event_ms is not None else None,
                "llm_total": round(self.llm_total_ms, 1) if self.llm_total_ms else None,
                "llm_ttft": round(self.llm_ttft_ms, 1) if self.llm_ttft_ms is not None else None,
                "context": round(self.context_ms, 1) if self.context_ms else None,
                "tool": round(self.tool_ms, 1) if self.tool_ms else None,
                "map_ack_wait_max": round(map_ack_wait_ms, 1) if map_ack_wait_ms is not None else None,
            },
            "work": {
                "llm_rounds": self.llm_rounds,
                "tool_calls": self.tool_calls,
                "tool_retries": self.tool_retries,
                "deduped_tool_calls": self.deduped_tool_calls,
                "sse_events": self.sse_events,
                "map_actions_issued": self.map_actions_issued,
                "map_actions_acked": self.map_actions_acked,
                "map_actions_unacked": unacked,
                "artifacts": self.artifacts,
            },
            "llm_usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "reports": self.llm_usage_reports,
            },
            "warnings": warnings,
        }


class TurnEvidenceRegistry:
    """turn_id 键控的进程内注册表（有界 FIFO，线程安全）。

    所有 adapter（stream_prompt / dispatch_tool / ack endpoint）按 turn_id 写入同一
    ``TurnEvidence``，从而跨 HTTP 请求 task 关联。turn 结束时由流任务 ``remove``。
    """

    def __init__(self, max_entries: int = _MAX_ACTIVE_TURNS) -> None:
        self._entries: "OrderedDict[str, TurnEvidence]" = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def register(self, ev: TurnEvidence) -> TurnEvidence:
        with self._lock:
            if ev.turn_id in self._entries:
                # 已存在（例如 resume）：返回既有，避免双注册
                self._entries.move_to_end(ev.turn_id)
                return self._entries[ev.turn_id]
            self._entries[ev.turn_id] = ev
            self._entries.move_to_end(ev.turn_id)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)  # FIFO 丢弃最旧 turn
            return ev

    def get(self, turn_id: Optional[str]) -> Optional[TurnEvidence]:
        if not turn_id:
            return None
        with self._lock:
            return self._entries.get(turn_id)

    def remove(self, turn_id: Optional[str]) -> None:
        if not turn_id:
            return
        with self._lock:
            self._entries.pop(turn_id, None)

    def active_turn_ids(self) -> List[str]:
        with self._lock:
            return list(self._entries.keys())


# 进程单例注册表
TURN_EVIDENCE = TurnEvidenceRegistry()


# ── 流任务内的便利 ContextVar（仅 in-task LLM/round/SSE 计数用）──────────────────
_CURRENT_TURN: contextvars.ContextVar[Optional[TurnEvidence]] = contextvars.ContextVar(
    "webgis_current_turn_evidence", default=None
)


def current_turn_evidence() -> Optional[TurnEvidence]:
    """流任务内当前 turn 的证据（便利指针；跨 task 请用 TURN_EVIDENCE.get(turn_id)）。"""
    return _CURRENT_TURN.get()


@contextlib.contextmanager
def bind_turn_evidence(ev: TurnEvidence) -> Iterator[TurnEvidence]:
    token = _CURRENT_TURN.set(ev)
    try:
        yield ev
    finally:
        # Tolerate a cross-context reset (see context._safe_reset): an async
        # generator driven across multiple asyncio tasks can have set/reset in
        # different copied Contexts. Skipping is benign — the value is isolated
        # in the copied Context.
        try:
            _CURRENT_TURN.reset(token)
        except (ValueError, LookupError):
            pass


def emit_turn_summary(ev: TurnEvidence) -> Dict[str, object]:
    """唯一的证据汇聚 sink（P0#3）：turn 结束时输出一条结构化 INFO 日志。

    dev-level，无攻击面（仅关联 id + 聚合计数/耗时 + 有界 warning，无 prompt/GeoJSON）。
    返回 summary dict 供调用方按需附加（如 SSE done 事件）。
    """
    summary = ev.to_summary()
    try:
        logger.info("[turn] %s", json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    except Exception:  # noqa: BLE001
        logger.info("[turn] turn_id=%s outcome=%s (summary serialize failed)", ev.turn_id,
                    ev.outcome.outcome.value if ev.outcome.outcome else None)
    return summary


def record_map_action_acked_by_turn(turn_id: Optional[str], action_id: str, status: str) -> None:
    """ACK endpoint 便利入口：按 turn_id 找到证据并记录 ack（查不到则 graceful 跳过）。"""
    ev = TURN_EVIDENCE.get(turn_id)
    if ev is not None:
        ev.record_map_action_acked(action_id, status)
