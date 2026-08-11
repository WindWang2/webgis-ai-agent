"""Tests for SSE serialization + the batching/throttling buffer."""
import pytest

from app.utils.sse import (
    sse_event,
    SSEBatcher,
    _serialize_sse_data,
    _is_terminal_event,
    sse_event_id,
    sse_event_id_scope,
    sse_event_type,
)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_sse_event_basic():
    s = sse_event("token", {"content": "hi"})
    assert s.startswith("event: token\ndata: ")
    assert s.endswith("\n\n")
    assert '"content": "hi"' in s


def test_sse_event_utf8():
    # 中文 must round-trip (ensure_ascii=False)
    s = sse_event("content", {"content": "你好"})
    assert "你好" in s


# ---------------------------------------------------------------------------
# DUP-1: per-turn monotonic event ids (sse_event_id / sse_event_id_scope)
# ---------------------------------------------------------------------------

def test_sse_event_no_id_outside_scope():
    # Backward compat: calls outside a turn scope emit no id: line (unit tests,
    # non-chat streams like explorer are unchanged).
    s = sse_event("token", {"content": "x"})
    assert "id:" not in s
    assert sse_event_id(s) is None


def test_sse_event_ids_monotonic_inside_scope():
    with sse_event_id_scope():
        ids = [sse_event_id(sse_event("token", {"i": i})) for i in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_sse_event_id_scope_resets_on_exit():
    with sse_event_id_scope():
        sse_event("token", {})
    assert sse_event_id(sse_event("token", {})) is None


def test_sse_event_explicit_event_id_overrides_scope():
    with sse_event_id_scope():
        s = sse_event("token", {"c": 1}, event_id=7)
    assert sse_event_id(s) == 7


def test_sse_event_id_parses_comment_as_none():
    assert sse_event_id(": keepalive\n\n") is None


def test_sse_event_ids_monotonic_across_mixed_events_and_comments():
    """Ids stay monotonic across batched tokens, structural events AND the Pi
    keepalive comments interleaved between them; comments consume no id."""
    with sse_event_id_scope():
        a = sse_event("task_start", {"session_id": "s"})
        b = sse_event("token", {"content": "t"})
        c = ": keepalive\n\n"  # heartbeat — raw comment, no id
        d = sse_event("step_result", {"tool": "t", "result": {}})
        e = sse_event("done", {"session_id": "s"})
    assert sse_event_id(a) == 1
    assert sse_event_id(b) == 2
    assert sse_event_id(c) is None
    assert sse_event_id(d) == 3
    assert sse_event_id(e) == 4


def test_sse_event_id_line_keeps_event_type_and_terminal_detection():
    with sse_event_id_scope():
        done = sse_event("done", {"session_id": "s"})
        token = sse_event("token", {"content": "x"})
        keep = ": keepalive\n\n"
    # sse_event_type/_is_terminal_event parse the FIRST line (event: X), which
    # the id: line must not disturb (event: X is emitted before id: N).
    assert sse_event_type(done) == "done"
    assert _is_terminal_event(done) is True
    assert sse_event_type(token) == "token"
    assert _is_terminal_event(token) is False
    assert sse_event_type(keep) == ""
    # ... and with no scope the event type parsing is unchanged too.
    assert _is_terminal_event(sse_event("done", {})) is True


def test_serialize_dict():
    assert _serialize_sse_data({"a": 1}) == '{"a": 1}'


def test_serialize_list():
    assert _serialize_sse_data([1, 2, 3]) == "[1, 2, 3]"


def test_serialize_pydantic_v2_like():
    class V2:
        def model_dump(self):
            return {"x": 1}
    assert _serialize_sse_data(V2()) == '{"x": 1}'


def test_serialize_pydantic_v1_like():
    """v1 models expose .dict() not .model_dump(); must call the right method."""
    class V1:
        def dict(self):
            return {"x": 2}
        # deliberately NO model_dump — proves we hit the v1 branch
    assert _serialize_sse_data(V1()) == '{"x": 2}'


def test_serialize_fallback_on_error():
    class Boom:
        def __getattribute__(self, name):
            if name in ("model_dump", "dict"):
                raise RuntimeError("boom")
            raise AttributeError(name)
    out = _serialize_sse_data(Boom())
    assert "Internal serialization error" in out


# ---------------------------------------------------------------------------
# Terminal-event detection (must NOT substring-match the whole payload)
# ---------------------------------------------------------------------------

def test_is_terminal_event_done():
    assert _is_terminal_event("event: done\ndata: {}\n\n") is True


def test_is_terminal_event_step_result_not_terminal():
    # The word "done" appears in data but the event type is step_result.
    payload = sse_event("step_result", {"msg": "all done!"})
    assert _is_terminal_event(payload) is False


def test_is_terminal_event_comment_not_terminal():
    assert _is_terminal_event(": keep-alive\n\n") is False


# ---------------------------------------------------------------------------
# Batcher
# ---------------------------------------------------------------------------

def test_batcher_count_threshold():
    b = SSEBatcher(max_events=3, max_delay_s=10)
    b.push(sse_event("token", {"i": 1}))
    b.push(sse_event("token", {"i": 2}))
    assert len(b) == 2  # not yet at threshold
    b.push(sse_event("token", {"i": 3}))
    # drain() should now emit 1 coalesced batch of all 3.
    out = []
    for chunk in b.flush():  # threshold tripped -> flush yields it
        out.append(chunk)
    assert len(out) == 1
    assert out[0].count("event: token") == 3
    assert len(b) == 0  # flushed, buffer reset


def test_batcher_terminal_flushes_immediately():
    import asyncio

    async def run():
        b = SSEBatcher(max_events=100, max_delay_s=10)
        b.push(sse_event("token", {"i": 1}))
        b.push(sse_event("token", {"i": 2}))
        # neither tripped yet
        out_before = [c async for c in b.drain()]
        assert out_before == []
        b.push(sse_event("done", {"session_id": "s1"}))
        out = [c async for c in b.drain()]
        return out

    out = asyncio.run(run())
    # Terminal forces flush: the 2 tokens + done all coalesced into one batch.
    assert len(out) == 1
    assert out[0].count("event: token") == 2
    assert "event: done" in out[0]


def test_batcher_coalesces_into_single_string():
    b = SSEBatcher(max_events=2)
    b.push("AAA\n\n")
    b.push("BBB\n\n")
    out = list(b.flush())
    assert out == ["AAA\n\nBBB\n\n"]


def test_batcher_reset_after_flush():
    b = SSEBatcher(max_events=2)
    b.push(sse_event("x", {}))
    list(b.flush())
    assert len(b) == 0
    # second flush is no-op
    assert list(b.flush()) == []


def test_batcher_rejects_invalid_args():
    with pytest.raises(ValueError):
        SSEBatcher(max_events=0)
    with pytest.raises(ValueError):
        SSEBatcher(max_delay_s=0)


def test_batcher_time_threshold():
    """max_delay_s=0 + sleep(0) should trip the time threshold on drain."""
    import asyncio

    async def run():
        b = SSEBatcher(max_events=1000, max_delay_s=0.001)
        b.push(sse_event("token", {"i": 1}))
        # exceed the window
        import time as _t
        _t.sleep(0.005)
        out = []
        async for chunk in b.drain():
            out.append(chunk)
        return out

    out = asyncio.run(run())
    assert len(out) == 1
    assert "event: token" in out[0]


def test_batcher_drain_async_yields_pending():
    """drain() flushes the whole buffer when threshold trips (coalesced)."""
    import asyncio

    async def run():
        b = SSEBatcher(max_events=1)  # trips on the very first real event
        b.push(sse_event("token", {"i": 1}))
        # drain after each push mirrors the production streaming loop.
        out = []
        async for chunk in b.drain():
            out.append(chunk)
        # second push + drain
        b.push(sse_event("token", {"i": 2}))
        async for chunk in b.drain():
            out.append(chunk)
        return out

    out = asyncio.run(run())
    # drain-between-pushes yields one batch per push.
    assert len(out) == 2
    assert all("event: token" in c for c in out)


def test_batcher_comments_dont_count_toward_threshold():
    b = SSEBatcher(max_events=2)
    b.push(": keep-alive\n\n")
    b.push(": keep-alive\n\n")
    assert len(b) == 0  # comments don't count
    b.push(sse_event("token", {"i": 1}))
    assert len(b) == 1
    out = list(b.flush())
    assert out == [": keep-alive\n\n: keep-alive\n\nevent: token\ndata: {\"i\": 1}\n\n"]
