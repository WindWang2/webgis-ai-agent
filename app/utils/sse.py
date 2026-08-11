"""SSE 事件封装。

新增于 plan-in-chat 设计（2026-05-20）的事件契约：

  - plan_ready      由 chat_engine.chat_stream 在 _maybe_plan 成功后发出
                    data: {session_id, task_id, intent, domains, steps[]}
  - plan_step_done  每次 planner.mark_step_done 返回非空时发出
                    data: {session_id, task_id, step_n}
  - plan_finalized  task_complete / task_cancelled / task_error 之前发出
                    data: {session_id, task_id, skipped: [step_n, ...]}

前端类型定义见 frontend/lib/types/agent-plan.ts::AgentPlanState
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

def _serialize_sse_data(data: Any) -> str:
    """
    安全地将数据序列化为 JSON 字符串，防止序列化失败导致流中断。
    支持 dict, list, Pydantic v1 (dict()) 和 Pydantic v2 (model_dump())。
    """
    try:
        # Pydantic v2
        if hasattr(data, "model_dump"):
            return json.dumps(data.model_dump(), ensure_ascii=False)
        # Pydantic v1 — must call .dict() (v1 method). The prior code called
        # the v2-only .model_dump() here, so v1 models silently fell through
        # to the generic json.dumps branch and could fail on non-JSON types.
        if hasattr(data, "dict") and callable(getattr(data, "dict")):
            return json.dumps(data.dict(), ensure_ascii=False)
        # Standard types
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"SSE serialization error: {e}, data type: {type(data)}")
        # 尝试提取 session_id 或 task_id 用于基本的错误追踪
        info = {}
        if isinstance(data, dict):
            if "session_id" in data:
                info["session_id"] = data["session_id"]
            if "task_id" in data:
                info["task_id"] = data["task_id"]
        
        return json.dumps({
            "error": "Internal serialization error",
            "detail": str(e),
            **info
        }, ensure_ascii=False)


def sse_event(event_type: str, data: Any) -> str:
    """
    构造标准 SSE 格式事件字符串。

    格式:
    event: {event_type}
    data: {json_string}
    \n\n
    """
    return f"event: {event_type}\ndata: {_serialize_sse_data(data)}\n\n"


# ---------------------------------------------------------------------------
# SSE 事件批处理 / 节流缓冲区
#
# 高频事件（token 流式输出、并行工具调度产生的 step_result 爆发）若逐条
# flush 到 StreamingResponse 会造成每 token 一次 HTTP write，压垮前端。
# SSEBatcher 按 "时间窗口 (≤ max_delay_s) 或 数量阈值 (max_events)" 聚合事件，
# 到阈值或显式 flush() 时一次性 yield 出去，将 write 次数大幅降低。
#
# 设计要点：
#   - 被动 flush：push 时若已到阈值会立刻把当前批次返回，调用方负责 yield。
#   - 不持有 asyncio loop 引用也不自建定时任务：流式生成器本身的 await 点
#     就是天然驱动循环，避免在 disconnect 时残留后台 timer 造成泄漏。
#   - 终结事件（done/task_complete/task_error/task_cancelled）总是立刻 flush，
#     保证前端不卡在末尾事件上。
# ---------------------------------------------------------------------------

# 事件类型 -> 是否 "终态"（必须立即 flush，不允许被批处理延迟）。
# 这些事件标志一次会话的结束，前端依赖其及时到达来关闭连接 / 停止 spinner。
_TERMINAL_EVENTS = frozenset({
    "done", "task_complete", "task_error", "task_cancelled",
})

# 注释行（keep-alive 心跳）不参与计数，但会和已缓冲事件一起 flush。
_COMMENT_PREFIX = ":"


def sse_event_type(event_str: str) -> str:
    """Return the event type carried by an SSE string; "" for comment lines.

    Parsed defensively: the event name is read from the first line, which for
    a typed event is exactly ``event: <name>\\n``. Comment/keepalive lines
    (``: ...``) and anything not shaped as ``event: `` return "" — matching
    how :func:`_is_terminal_event` avoids substring-matching payloads (a
    ``step_result`` whose data contains the word "done" must not read as a
    "done" event). Callers use this to classify events without re-parsing.
    """
    first_line = event_str.split("\n", 1)[0]
    if not first_line.startswith("event: "):
        return ""
    return first_line[len("event: "):].strip()


def _is_terminal_event(event_str: str) -> bool:
    """True if this SSE string carries a terminal event type.

    Parsed defensively: a terminal event's first non-empty line is exactly
    ``event: <name>\\n`` where ``<name>`` ∈ :data:`_TERMINAL_EVENTS`. We do NOT
    substring-match the whole payload (a ``step_result`` whose data happens to
    contain the word "done" must not be treated as terminal).
    """
    return sse_event_type(event_str) in _TERMINAL_EVENTS


class SSEBatcher:
    """Time- and count-based coalescing buffer for SSE events.

    High-frequency events (token streaming, ``step_result`` bursts from
    parallel tool dispatch) are accumulated and flushed as a single coalesced
    HTTP write when either:

      * ``max_events`` real events have been buffered, OR
      * ``max_delay_s`` seconds have elapsed since the first buffered event.

    Terminal events (``done`` / ``task_complete`` / ``task_error`` /
    ``task_cancelled``) bypass both thresholds and flush immediately — the
    frontend relies on them to close the connection.

    The batcher owns no background timer; flush is driven by the streaming
    generator's own await points via :meth:`drain`, so there is nothing to
    leak on client disconnect. Always call :meth:`flush` once at stream end to
    emit any tail.

    Usage in an async generator::

        batcher = SSEBatcher(max_events=32, max_delay_s=0.08)
        ...
        batcher.push(sse_event("token", {...}))
        async for chunk in batcher.drain():
            yield chunk
        ...
        for chunk in batcher.flush():       # 收尾
            yield chunk
    """

    __slots__ = ("_buffer", "_count", "_max_events", "_max_delay_s", "_first_ts")

    def __init__(self, max_events: int = 32, max_delay_s: float = 0.08) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        if max_delay_s <= 0:
            raise ValueError("max_delay_s must be > 0")
        self._buffer: list[str] = []
        self._count = 0  # 不含纯 comment 行的"真实事件"计数
        self._max_events = max_events
        self._max_delay_s = max_delay_s
        self._first_ts: float | None = None  # 首个真实事件入队时间 (perf_counter)

    def __len__(self) -> int:  # 已缓冲（未 flush）的真实事件数
        return self._count

    def push(self, event_str: str) -> None:
        """Buffer one SSE event string (synchronous, side-effect only).

        Does NOT flush — call :meth:`drain` (or :meth:`flush` at stream end)
        to emit. This keeps the streaming loop linear: push, drain, repeat.
        """
        is_comment = event_str.startswith(_COMMENT_PREFIX)
        if not is_comment and self._first_ts is None:
            import time as _t
            self._first_ts = _t.perf_counter()
        self._buffer.append(event_str)
        if not is_comment:
            self._count += 1

    def _should_flush(self) -> bool:
        """True if a threshold tripped or the last buffered event is terminal."""
        if not self._buffer:
            return False
        if self._count >= self._max_events:
            return True
        if self._first_ts is not None:
            import time as _t
            if (_t.perf_counter() - self._first_ts) >= self._max_delay_s:
                return True
        # Terminal events bypass thresholds so the frontend can close promptly.
        last = self._buffer[-1]
        if not last.startswith(_COMMENT_PREFIX) and _is_terminal_event(last):
            return True
        return False

    async def drain(self):
        """Async generator yielding ripe batches.

        Typical use: ``async for chunk in batcher.drain(): yield chunk`` right
        after a :meth:`push`. Yields the coalesced batch string when a
        threshold tripped (or a terminal event forced it); otherwise yields
        nothing. The single ``await asyncio.sleep(0)`` is the cheap cooperation
        point that lets the event loop interleave other streams.
        """
        if self._should_flush():
            await asyncio.sleep(0)
            for chunk in self.flush():
                yield chunk

    def flush(self):
        """Yield the entire buffered batch as one coalesced string; reset.

        After this call the batcher is empty and its time window is reset.
        Safe to call when empty (yields nothing).
        """
        if not self._buffer:
            return
        yield "".join(self._buffer)
        self._buffer.clear()
        self._count = 0
        self._first_ts = None


import asyncio  # noqa: E402  (late import: pure-serialization path stays loop-free)
