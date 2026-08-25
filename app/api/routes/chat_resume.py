"""SSE 断线续传生成器（E-8 / #899 拆分：自 api/routes/chat.py 原样搬移）。

DUP-1 resume 契约的自包含实现：重放 ring buffer 中错过的事件后干净收尾。
路由层保留薄包装 ``_resume_generator``（绑定 request 关联上下文后委托）。
"""
from __future__ import annotations

import logging

from app.services.chat.event_resume import TurnResumeRegistry
from app.utils.sse import sse_event, sse_event_id, sse_event_type

logger = logging.getLogger(__name__)

_RESUME_LIVE_HOLD_S = 30.0

def _synthesize_terminal_event(session_key: str, terminal_event: str) -> str:
    """Synthesize truthful terminal event matching the original terminal type (INV-4)."""
    term_type = sse_event_type(terminal_event)
    if term_type == "task_cancelled":
        return sse_event("task_cancelled", {"session_id": session_key, "resumed": True})
    if term_type in ("task_error", "error", "step_error"):
        return sse_event(term_type, {
            "session_id": session_key,
            "error": "本轮执行失败；请重试。",
            "resumed": True,
        })
    return sse_event("done", {"session_id": session_key, "resumed": True})


_turn_resume_registry: TurnResumeRegistry | None = None


def bind_resume_registry(registry: TurnResumeRegistry) -> None:
    """路由模块装配时注入其 TurnResumeRegistry 单例（进程内 ring buffer）。"""
    global _turn_resume_registry
    _turn_resume_registry = registry


async def _resume_generator_impl(
    session_key: str,
    last_event_id: int,
    message: str,
):
    """DUP-1 resume stream: replay missed events, then terminate cleanly.

    A POST /chat/stream carrying ``Last-Event-ID`` / ``last_event_id`` is a
    RESUME — a read, never a new execution. The route sends no prompt RPC and
    dispatches no tool; it replays what the dropped connection missed from the
    per-session ring buffers and then ends:

      * unique buffer hit (message match + Last-Event-ID valid for that turn)
        → replay every buffered event with ``id > last_event_id`` in order;
        then terminate with the buffered terminal event if it was replayed, a
        synthesized ``done {resumed: true}`` if the client already consumed
        the terminal (``last_event_id >= terminal id`` — the stream must never
        hang), or a synthesized ``error`` if the turn was interrupted (aborted
        on disconnect; no terminal exists).
      * the matched turn is still LIVE (or queued, never started) → after the
        replay the stream HOLDS (F20): new events are forwarded as the turn
        records them, until the real terminal arrives or the turn ends. A live
        turn NEVER yields a fabricated ``done {resumed: true}``. If the turn
        outlives ``_RESUME_LIVE_HOLD_S`` (stalled), the resume ends with a
        truthful terminal ``error {resumed: true, pending: true}`` — the
        client may reconnect again with a newer Last-Event-ID. Anonymous
        (``""``-key) sessions never hold (P1): the shared anonymous key skips
        ownership checks, so holding would let anyone who knows the message +
        Last-Event-ID tail a stranger's live turn — an anonymous resume of a
        live turn replays the already-buffered tail and immediately ends with
        that truthful pending error instead.
      * ambiguous hit (multiple concurrent turns on this session key match) →
        terminal ``error {resumed: false}``: fail safe, never replay the WRONG
        turn's events into this client's stream (cross-turn leak).
      * buffer miss (server restart / LRU eviction / message mismatch) → a
        terminal ``error {resumed: false}``; no new turn starts, so a stale
        reconnect can never double-execute the message. The client stops
        auto-retrying on any terminal event.

    Synthesized terminal events carry no ``id:`` (they are not part of the
    original turn's id sequence); the client consumes them for status and
    stops, so ids are not needed for dedup there.

    Anonymous ``""``-key resumes are always refused. Last-Event-ID is an
    ordering cursor, not an ownership capability; treating it as one permits
    cross-user replay. Pi turns receive a canonical UUID before execution and
    can resume by that id. Legacy anonymous turns are deliberately
    non-resumable until the client has a canonical session identity.
    """
    if session_key == "":
        yield sse_event("error", {
            "session_id": session_key,
            "error": (
                "匿名会话不能安全续传；请使用服务端签发的会话编号重新连接。"
            ),
            "resumed": False,
        })
        return
    buffered, ambiguous = _turn_resume_registry.find(session_key, message, last_event_id)
    if buffered is None:
        if ambiguous:
            logger.warning(
                "ambiguous resume for session key %r: multiple concurrent turns "
                "match — refusing to replay (possible cross-turn leak)",
                session_key,
            )
        yield sse_event("error", {
            "error": (
                "Resume unavailable: the turn buffer is gone (server restarted "
                "or the session expired)."
                if not ambiguous else
                "Resume ambiguous: multiple concurrent turns on this session "
                "match this request — refusing to replay the wrong one. Wait "
                "for the in-flight turn to finish and reconnect, or start a "
                "new message."
            ),
            "resumed": False,
        })
        return

    # Replay (catching up to the live tail), then — while the turn is still
    # live — hold for newly recorded events instead of terminating (F20).
    # Single-threaded asyncio: replay + ended check are await-free, so no
    # recorded event can be missed between iterations.
    cursor = last_event_id
    while True:
        for event in buffered.replay_after(cursor):
            yield event
        if buffered.last_id is not None and buffered.last_id > cursor:
            cursor = buffered.last_id
        if buffered.ended:
            break
        if not await buffered.wait_for_update(timeout=_RESUME_LIVE_HOLD_S):
            # Stalled live turn: end truthfully — the turn is still in
            # progress and can be picked up by another resume. Never a fake done.
            yield sse_event("error", {
                "session_id": session_key,
                "error": (
                    "本轮仍在执行中（续传等待超时）；请携带 Last-Event-ID "
                    "重新连接继续接收。"
                ),
                "resumed": True,
                "pending": True,
            })
            return

    if buffered.terminal_event is not None:
        terminal_id = sse_event_id(buffered.terminal_event)
        if terminal_id is None or terminal_id <= last_event_id:
            # Client already consumed the terminal; reconnect for nothing but
            # a clean close — synthesize matching terminal type so the stream terminates truthfully.
            yield _synthesize_terminal_event(session_key, buffered.terminal_event)
        # Otherwise the terminal was replayed above — nothing more to add.
    else:
        # Ended without a terminal: the turn was interrupted (client
        # disconnect → server aborted the prompt). Replayed partial content
        # must NOT look like a complete answer.
        yield sse_event("error", {
            "session_id": session_key,
            "error": "连接已中断，本轮未完成且不会自动续跑；请重新发送消息。",
            "resumed": True,
        })
