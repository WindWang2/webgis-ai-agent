"""DUP-1: process-local SSE turn-resume buffer and registry.

A chat SSE stream is a *read* of one turn: every event carries a per-turn
monotonic ``id:`` (see ``app.utils.sse.sse_event_id_scope``), and the route
records each emitted event into a per-session ring buffer so a client whose
connection dropped can resume with ``Last-Event-ID`` instead of losing the
turn (there was no ``id:``/resume anywhere before DUP-1 — finding B-2.8).

Why a buffer, not a live splice: when a client disconnects, Starlette cancels
the StreamingResponse body generator, so the server-side turn (Pi prompt /
legacy engine) is aborted within milliseconds — there is no live stream left
to splice into. The buffer is therefore the source of truth for what the
client missed. A resume replays the buffered events after ``Last-Event-ID``
and then terminates; it NEVER starts a new turn (no prompt RPC, no tool
dispatch), which is the DUP-1 no-duplicate-execution guarantee.

Semantics (documented contract, see ``app.api.routes.chat._resume_generator``):

  - a POST /chat/stream carrying ``Last-Event-ID`` / ``last_event_id`` is a
    RESUME (read-only), never a new execution;
  - buffer hit + matching message: replay every buffered event with
    ``id > Last-Event-ID`` in order, then terminate with the buffered terminal
    event if it was replayed, a synthesized ``done {resumed: true}`` if the
    terminal was already consumed, or a synthesized ``error`` if the turn was
    interrupted (aborted on disconnect — no terminal was ever produced);
  - buffer miss (process restart, LRU eviction, message mismatch): emit
    ``error {resumed: false}`` and close — the client stops auto-retrying and
    surfaces the error instead of silently re-executing the message.

Bounds: ``RESUME_MAX_EVENTS`` per turn (ring buffer — only the tail survives,
which is exactly what a dropped connection missed), and
``RESUME_MAX_SESSIONS`` process-wide (oldest buffered turn evicted first).
Cross-restart resume is explicitly out of scope (non-goal): the buffer lives
in this process only.

The registry is asyncio-single-threaded (a plain dict): every access happens
inside a request/stream task, so no locking is needed.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from app.utils.sse import _is_terminal_event, sse_event_id

# Ring-buffer depth per turn: the tail a dropped client missed. 64 events ≈ two
# batched token bursts + their structural events, comfortably more than a
# reconnect backoff window (500ms–2s) can produce.
RESUME_MAX_EVENTS = 64
# Process-wide cap on buffered turns (one per session); oldest evicted first.
RESUME_MAX_SESSIONS = 32


class TurnEventBuffer:
    """Ring buffer of the last ``max_events`` SSE events of one turn.

    Records events as the route emits them (before the wire write), tracks the
    highest id seen, whether a terminal event was produced, and whether the
    turn ended by abort (client disconnect) rather than normally.
    """

    __slots__ = (
        "session_id",
        "message",
        "max_events",
        "_events",
        "last_id",
        "ended",
        "terminal_event",
        "aborted",
    )

    def __init__(
        self,
        session_id: str,
        message: str,
        max_events: int = RESUME_MAX_EVENTS,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        self.session_id = session_id
        self.message = message
        self.max_events = max_events
        self._events: deque[str] = deque()
        self.last_id: int | None = None  # highest event id recorded
        self.ended: bool = False  # a terminal was recorded (or mark_ended called)
        self.terminal_event: Optional[str] = None
        self.aborted: bool = False  # turn ended by client disconnect, not normally

    def record(self, event_str: str, *, force_terminal: bool = False) -> None:
        """Record one emitted event (call before yielding it to the wire).

        ``force_terminal=True`` marks a non-`_TERMINAL_EVENTS` event (e.g. the
        route's synthesized ``error``) as the turn's terminal anyway, so a
        resume replays it as the ending instead of treating the turn as aborted.
        """
        event_id = sse_event_id(event_str)
        if event_id is not None:
            self.last_id = event_id
        self._events.append(event_str)
        if len(self._events) > self.max_events:
            self._events.popleft()
        if force_terminal or _is_terminal_event(event_str):
            self.ended = True
            self.terminal_event = event_str

    def mark_ended(self, aborted: bool) -> None:
        """Called in the route generator's ``finally`` when no terminal was
        produced (upstream exception, client disconnect). ``aborted=True`` means
        the turn was cut by a disconnect and must NOT be presented as complete.
        """
        if not self.ended:
            self.ended = True
            self.aborted = aborted

    def replay_after(self, last_event_id: int) -> list[str]:
        """Buffered events with ``id > last_event_id``, in original order.

        Events without an ``id:`` line (keepalive comments) are never replayed
        — they carry no resume value and would be un-ordered in the sequence.
        """
        return [
            e for e in self._events
            if (eid := sse_event_id(e)) is not None and eid > last_event_id
        ]

    def __len__(self) -> int:
        return len(self._events)


class TurnResumeRegistry:
    """Session-keyed registry of the most recent turn buffer per session.

    One buffer per session key (``session_id or ""`` — the Pi path runs first
    anonymous turns under the empty key). Registering a new turn replaces the
    previous one; the registry is LRU-capped at ``max_sessions``.
    """

    def __init__(self, max_sessions: int = RESUME_MAX_SESSIONS) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self._max_sessions = max_sessions
        self._buffers: dict[str, TurnEventBuffer] = {}
        self._order: deque[str] = deque()

    def register(self, session_id: str, buffer: TurnEventBuffer) -> None:
        if session_id not in self._buffers:
            self._order.append(session_id)
        self._buffers[session_id] = buffer
        while len(self._order) > self._max_sessions:
            oldest = self._order.popleft()
            if oldest in self._buffers:
                del self._buffers[oldest]

    def get(self, session_id: str) -> Optional[TurnEventBuffer]:
        return self._buffers.get(session_id)

    def clear(self) -> None:
        """Drop all buffered turns (test isolation; also safe on shutdown)."""
        self._buffers.clear()
        self._order.clear()

    def __len__(self) -> int:
        return len(self._buffers)
