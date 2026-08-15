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
  - buffer hit + UNAMBIGUOUS message match: replay every buffered event with
    ``id > Last-Event-ID`` in order; if the turn is still live (or queued, not
    yet started), the resume HOLDS — replaying new events as they are recorded
    — until the real terminal arrives or the hold cap expires. It terminates
    with the buffered terminal event, a synthesized ``done {resumed: true}``
    if the terminal was already consumed, a synthesized ``error`` if the turn
    was interrupted (aborted on disconnect — no terminal was ever produced),
    or a truthful ``error {resumed: true, pending: true}`` if the live turn
    outlived the hold cap. A live/never-started turn NEVER yields a fabricated
    ``done`` (F20). Anonymous (``""``-key) sessions NEVER hold (P1): the
    shared anonymous key skips ownership checks, so holding would let anyone
    who knows the victim's message + Last-Event-ID tail a stranger's live turn
    — an anonymous resume of a live turn replays the already-buffered tail and
    immediately ends with that truthful pending error instead;
  - ambiguous match (multiple concurrent turns on the same session key match
    the message + Last-Event-ID — e.g. two anonymous ``""``-key turns with the
    same message, F2): emit ``error {resumed: false}`` and close. Failing safe
    is the whole point: replaying the WRONG turn would leak one user's events
    into another's stream;
  - buffer miss (process restart, LRU eviction, message mismatch): emit
    ``error {resumed: false}`` and close — the client stops auto-retrying and
    surfaces the error instead of silently re-executing the message.
  - P2 (round-2 review): anonymous (``""``-key) resumes require
    ``Last-Event-ID > 0`` — id 0 (no proof of prior contact with that turn) is
    refused with a terminal ``error {resumed: false}``, no replay and no
    fabricated done. RESIDUAL (documented, by design): anonymous + no-auth
    still share the ``""`` namespace, so a nonzero id is only weak ownership —
    this guard removes the whole-turn replay, not the namespace sharing.

Bounds: ``RESUME_MAX_EVENTS`` per turn (ring buffer — only the tail survives,
which is exactly what a dropped connection missed). Entries are the route's
COALESCED wire chunks (each holding up to ~32 token events — the route
records merged batches whole and re-splits only at replay time, #398), so the
256-slot ring comfortably covers multi-thousand-token turns; when the ring
still evicts events a client has not seen, a replay STARTS with an explicit
``resume_gap`` marker event instead of a silently truncated turn (#398).
``RESUME_MAX_BUFFERS_PER_SESSION`` recent turn buffers per session key (F4: a
queued second POST registers its buffer WITHOUT clobbering the live turn's),
and ``RESUME_MAX_SESSIONS`` process-wide (oldest buffered session evicted
first). Cross-restart resume is explicitly out of scope (non-goal): the buffer
lives in this process only.

The registry is asyncio-single-threaded (plain dicts/deques): every access
happens inside a request/stream task, so no locking is needed.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

from app.utils.sse import _is_terminal_event, sse_event, sse_event_id

# Ring-buffer depth per turn: the tail a dropped client missed. Entries are
# COALESCED wire chunks (the route records merged batches whole and re-splits
# only at replay time — #398), so 256 slots hold up to ~256×32 token events
# plus structural events: a multi-thousand-token turn fits without evicting
# its own head (Round-2 P3: was 64 SINGLE events, which could silently evict
# un-replayed step_results under a long multi-command turn). If the ring is
# STILL too small for a pathological turn, :meth:`TurnEventBuffer.replay_after`
# emits an explicit ``resume_gap`` marker instead of silently truncating.
RESUME_MAX_EVENTS = 256
# Process-wide cap on buffered sessions; oldest evicted first.
RESUME_MAX_SESSIONS = 32
# F4: recent turn buffers kept per session key. A queued second POST registers
# its buffer alongside the live turn's instead of clobbering it, so a resume
# of the live turn still finds it (matched by message + Last-Event-ID). Small
# bound: a client only ever resumes the live turn or the most recent ones.
RESUME_MAX_BUFFERS_PER_SESSION = 4

# #398: event type of the synthesized gap marker a replay emits FIRST when the
# ring evicted events the client has not seen (the turn's head was truncated).
# The marker carries no ``id:`` — it is produced at replay time, not part of
# the original turn's id sequence. The client may ignore it, but it can now
# TELL a truncated replay from a complete turn (seenEventIds dedup can't).
RESUME_GAP_EVENT_TYPE = "resume_gap"


def _event_blocks(event_str: str) -> list[str]:
    """Split a (possibly coalesced) SSE chunk into individual event blocks,
    terminator stripped, empty parts dropped."""
    return [p for p in event_str.split("\n\n") if p]


def _highest_event_id(event_str: str) -> int | None:
    """Highest ``id:`` across all event blocks inside a (possibly coalesced)
    SSE chunk; ``None`` when the chunk carries no id at all."""
    highest: int | None = None
    for block in _event_blocks(event_str):
        eid = sse_event_id(block)
        if eid is not None and (highest is None or eid > highest):
            highest = eid
    return highest


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
        "_version",
        "_waiters",
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
        # F20 live-hold support: monotonic change counter + one-shot waiter
        # Events so a resume racing this live turn can park until new events
        # are recorded or the turn ends (instead of fabricating a terminal).
        self._version: int = 0
        self._waiters: list[asyncio.Event] = []

    def record(self, event_str: str, *, force_terminal: bool = False) -> None:
        """Record one emitted event (call before yielding it to the wire).

        ``event_str`` may be a COALESCED chunk holding several ``id:``-stamped
        events (the engine/route batchers merge token bursts; #398: the merged
        chunk is recorded WHOLE so a token-heavy turn cannot evict its own
        head from the ring — replay re-splits per event). ``last_id`` tracks
        the HIGHEST id across the chunk. A terminal event may sit at the END
        of a coalesced chunk (the batchers flush the batch that contains the
        terminal); it is detected per block and stored as the turn's terminal.
        ``force_terminal=True`` marks a non-`_TERMINAL_EVENTS` event (e.g. the
        route's synthesized ``error``) as the turn's terminal anyway, so a
        resume replays it as the ending instead of treating the turn as
        aborted.
        """
        event_id = _highest_event_id(event_str)
        if event_id is not None:
            self.last_id = event_id
        self._events.append(event_str)
        if len(self._events) > self.max_events:
            self._events.popleft()
        if force_terminal:
            self.ended = True
            self.terminal_event = event_str
        else:
            terminal_block: Optional[str] = None
            for block in _event_blocks(event_str):
                if _is_terminal_event(block):
                    terminal_block = block
            if terminal_block is not None:
                self.ended = True
                self.terminal_event = terminal_block + "\n\n"
        self._version += 1
        self._notify()

    def mark_ended(self, aborted: bool) -> None:
        """Called in the route generator's ``finally`` when no terminal was
        produced (upstream exception, client disconnect). ``aborted=True`` means
        the turn was cut by a disconnect and must NOT be presented as complete.
        """
        if not self.ended:
            self.ended = True
            self.aborted = aborted
            self._version += 1
            self._notify()

    def _notify(self) -> None:
        for waiter in self._waiters:
            waiter.set()

    async def wait_for_update(self, timeout: float) -> bool:
        """Wait until the buffer changes (events recorded or turn ended).

        Returns True on change, False on ``timeout``. Used by a resume that
        raced this still-live turn: it holds for the real terminal instead of
        fabricating a ``done`` (F20). Single-threaded asyncio: the version
        snapshot and waiter subscription are await-free, so no change can be
        missed between them — a ``record``/``mark_ended`` after the snapshot
        always fires ``_notify`` on this waiter (no redundant re-check is
        needed before the await). ``timeout <= 0`` returns immediately
        (``asyncio.wait_for`` with an unset event raises TimeoutError at the
        next loop checkpoint), which the stalled-turn tests use instead of a
        wall-clock hold.
        """
        seen = self._version
        if self.ended:
            return True
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        try:
            try:
                await asyncio.wait_for(waiter.wait(), timeout)
            except TimeoutError:
                return self._version != seen or self.ended
            return True
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def replay_after(self, last_event_id: int) -> list[str]:
        """Buffered events with ``id > last_event_id``, in original order.

        Buffered entries are (possibly coalesced) wire chunks — see
        :meth:`record`; #398 records merged batches whole so a token-heavy
        turn cannot evict its own head. Each entry is re-split here and
        filtered PER EVENT, so resumption granularity is unchanged: a resume
        still starts at exactly the missed id, ids intact, blocks re-framed
        with their ``\\n\\n`` terminator. Events without an ``id:`` line
        (keepalive comments) are never replayed — they carry no resume value.

        When the ring evicted events the client has not seen (the first
        retained event id exceeds ``last_event_id + 1``), the returned list
        STARTS with a synthesized ``resume_gap`` marker event (no ``id:`` — it
        is not part of the turn) describing the missing id range, so a resumed
        client can tell a truncated replay from a complete turn instead of
        silently missing the head (#398).
        """
        out: list[str] = []
        first_id: int | None = None
        for entry in self._events:
            for block in _event_blocks(entry):
                eid = sse_event_id(block)
                if eid is None:
                    continue
                if first_id is None:
                    first_id = eid
                if eid > last_event_id:
                    out.append(block + "\n\n")
        if first_id is not None and first_id > last_event_id + 1:
            out.insert(0, sse_event(RESUME_GAP_EVENT_TYPE, {
                "session_id": self.session_id,
                "resumed": True,
                "gap": True,
                "missing_from": last_event_id + 1,
                "missing_to": first_id - 1,
            }))
        return out

    def __len__(self) -> int:
        return len(self._events)


class TurnResumeRegistry:
    """Session-keyed registry of the most recent turn buffers per session.

    Keeps a small bounded list of recent buffers per session key
    (``session_id or ""`` — the Pi path runs first anonymous turns under the
    empty key, shared by ALL anonymous sessions). F4: registering a new turn
    APPENDS — a queued second POST can no longer clobber the live turn's
    buffer. Resumes locate their turn via :meth:`find` (message +
    Last-Event-ID match) and fail safe on ambiguity. The registry is
    LRU-capped at ``max_sessions`` session keys.
    """

    def __init__(
        self,
        max_sessions: int = RESUME_MAX_SESSIONS,
        max_buffers_per_session: int = RESUME_MAX_BUFFERS_PER_SESSION,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        if max_buffers_per_session < 1:
            raise ValueError("max_buffers_per_session must be >= 1")
        self._max_sessions = max_sessions
        self._max_buffers_per_session = max_buffers_per_session
        self._buffers: dict[str, deque[TurnEventBuffer]] = {}
        self._order: deque[str] = deque()

    def register(self, session_id: str, buffer: TurnEventBuffer) -> None:
        if session_id not in self._buffers:
            self._order.append(session_id)
            self._buffers[session_id] = deque()
        else:
            # P2: 重新注册已有 key 时刷新 LRU 序（MRU bump）—— 否则一个活跃
            # 会话的 buffer 会被 32 个「只注册过一次」的其它会话挤出缓存，
            # 造成活跃会话的续传不可用。
            self._order.remove(session_id)
            self._order.append(session_id)
        buffers = self._buffers[session_id]
        buffers.append(buffer)
        while len(buffers) > self._max_buffers_per_session:
            # P2 (review): over cap — evict an ENDED buffer first (its turn is
            # complete; a resume of it already has the terminal), so N
            # concurrent registrations on one key (e.g. five anonymous POSTs
            # sharing the '' key) can't evict a still-LIVE turn's buffer before
            # its turn runs — that would make a resume fail, the client resend,
            # and the tool side effects re-execute. A live buffer is only
            # evicted when every buffer in the deque is live (oldest first).
            for i, candidate in enumerate(buffers):
                if candidate.ended:
                    del buffers[i]
                    break
            else:
                buffers.popleft()
        while len(self._order) > self._max_sessions:
            oldest = self._order.popleft()
            if oldest in self._buffers:
                del self._buffers[oldest]

    def get(self, session_id: str) -> Optional[TurnEventBuffer]:
        """Most recently registered buffer for the key (introspection/tests)."""
        buffers = self._buffers.get(session_id)
        return buffers[-1] if buffers else None

    def find(
        self, session_id: str, message: str, last_event_id: int
    ) -> tuple[Optional[TurnEventBuffer], bool]:
        """Locate the UNIQUE buffer a resume refers to.

        A buffer matches when the message is identical (same turn — the client
        re-POSTs the same message) and the Last-Event-ID is meaningful for it:

          * ``0`` means "from the beginning of this turn" — it matches ONLY
            turns that are still ACTIVE (live, or queued and never started;
            #398). An ended buffer matched by 0 would replay a PREVIOUS turn's
            complete stream as if it were the current answer (the reconnect
            may target a newer turn whose buffer never registered, or be
            simply stale) — refuse instead of replaying the wrong turn's
            events;
          * otherwise the id must not lie beyond the buffer's highest recorded
            id (per-turn id scopes make ids from a LATER turn meaningless for
            an earlier one).

        Returns ``(buffer, False)`` on a unique match, ``(None, True)`` when
        MULTIPLE buffers match (ambiguous — e.g. two concurrent anonymous
        same-message turns sharing the ``""`` key, F2): the caller must fail
        safe rather than replay the wrong turn's events (cross-turn leak).
        ``(None, False)`` = plain miss.
        """
        matches: list[TurnEventBuffer] = []
        for b in self._buffers.get(session_id, ()):
            if b.message != message:
                continue
            if last_event_id == 0:
                # #398: id 0 only addresses an ACTIVE turn (live or queued);
                # an ended buffer must never be replayed from its start.
                if not b.ended:
                    matches.append(b)
            elif b.last_id is not None and last_event_id <= b.last_id:
                matches.append(b)
        if len(matches) == 1:
            return matches[0], False
        return None, len(matches) > 1

    def clear(self) -> None:
        """Drop all buffered turns (test isolation; also safe on shutdown)."""
        self._buffers.clear()
        self._order.clear()

    def clear_session(self, session_id: str) -> None:
        """Drop all buffered turns for ONE session key (round-2 P2).

        Called from the ``clear_session`` route on DELETE /sessions/{id}: a
        deleted session must not stay replayable through its stale resume
        buffers (anyone holding the session id + the message could otherwise
        replay its events until LRU eviction). The key is removed from both
        ``_buffers`` and ``_order`` so a re-registration starts a fresh LRU
        slot instead of appending a duplicate order entry. Unknown keys are a
        no-op; other sessions' buffers are untouched (the shared ``""`` key is
        never purged here — it is shared by ALL anonymous sessions).
        """
        if session_id in self._buffers:
            del self._buffers[session_id]
            if session_id in self._order:
                self._order.remove(session_id)

    def __len__(self) -> int:
        return len(self._buffers)
