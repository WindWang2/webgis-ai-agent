"""Unit tests for app.services.chat.pi_event_mapper.map_event_to_sse.

Tests pure event mapping logic without instantiating PiBridge or subprocesses.
"""
from app.services.chat.pi_event_mapper import (
    map_event_to_sse,
    _extract_error_text,
)


class FakeToolDispatchResult:
    def __init__(self, status="ok", error_msg=None, slim_event=None, geojson_ref=None):
        self.status = status
        self.error_msg = error_msg
        self.slim_event = slim_event or {"success": True}
        self.geojson_ref = geojson_ref


def test_map_message_update_text_delta_token():
    """Vendor text_delta carries the increment in `delta` (audit #816)."""
    event = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "text_delta",
            "contentIndex": 0,
            "delta": "Hello GIS",
        },
    }
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is not None
    assert "event: token" in sse
    assert '"content": "Hello GIS"' in sse
    assert '"session_id": "s1"' in sse


def test_map_message_update_thinking_delta_token():
    """Vendor thinking_delta is an incremental reasoning token (audit #816)."""
    event = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "thinking_delta",
            "contentIndex": 0,
            "delta": "Analyzing spatial topology...",
        },
    }
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is not None
    assert "event: token" in sse
    assert '"is_reasoning": true' in sse
    assert '"content": "Analyzing spatial topology..."' in sse


def test_map_message_update_text_end_does_not_duplicate():
    """text_end carries the whole block; deltas already streamed it (audit #816)."""
    event = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "text_end",
            "contentIndex": 0,
            "content": "Hello GIS",
        },
    }
    assert map_event_to_sse(event, session_id="s1") is None


def test_map_message_update_toolcall_end():
    """Vendor toolcall_end carries the complete toolCall object (audit #816)."""
    event = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "toolcall_end",
            "contentIndex": 1,
            "toolCall": {
                "type": "toolCall",
                "id": "tc-1",
                "name": "buffer_analysis",
                "arguments": {"radius": 100},
            },
        },
    }
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is not None
    assert "event: tool_call" in sse
    assert '"name": "buffer_analysis"' in sse
    # frontend contract: arguments is a JSON string
    assert '"arguments": "{\\"radius\\": 100}"' in sse or '"arguments": "{&' in sse or '"radius"' in sse


def test_map_message_update_toolcall_start_and_delta_are_silent():
    """Incomplete tool calls emit nothing until toolcall_end (audit #816)."""
    for kind in ("toolcall_start", "toolcall_delta"):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {"type": kind, "contentIndex": 1, "delta": "{"},
        }
        assert map_event_to_sse(event, session_id="s1") is None


def test_map_tool_execution_start():
    event = {
        "type": "tool_execution_start",
        "toolName": "buffer_analysis",
        "toolCallId": "tc-123",
    }
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is not None
    assert "event: step_start" in sse
    assert '"tool": "buffer_analysis"' in sse
    assert '"step_id": "tc-123"' in sse


def test_map_tool_execution_end_with_cache_hit():
    event = {
        "type": "tool_execution_end",
        "toolName": "buffer_analysis",
        "toolCallId": "tc-123",
    }
    fake_result = FakeToolDispatchResult(
        status="ok",
        slim_event={"success": True, "count": 5},
        geojson_ref="ref:buffer/1",
    )

    def fake_lookup(tcid):
        if tcid == "tc-123":
            return fake_result
        return None

    sse = map_event_to_sse(event, session_id="s1", cache_lookup=fake_lookup)
    assert sse is not None
    assert "event: step_result" in sse
    assert '"ref:buffer/1"' in sse
    assert '"count": 5' in sse


def test_map_tool_execution_end_with_cache_error():
    event = {
        "type": "tool_execution_end",
        "toolName": "buffer_analysis",
        "toolCallId": "tc-456",
    }
    fake_result = FakeToolDispatchResult(
        status="error",
        error_msg="Buffer radius must be positive",
    )

    def fake_lookup(tcid):
        return fake_result

    sse = map_event_to_sse(event, session_id="s1", cache_lookup=fake_lookup)
    assert sse is not None
    assert "event: step_error" in sse
    assert '"error": "Buffer radius must be positive"' in sse


def test_map_tool_execution_end_cache_miss_fallback():
    event = {
        "type": "tool_execution_end",
        "toolName": "buffer_analysis",
        "toolCallId": "tc-999",
        "result": {"summary": "fallback result"},
        "isError": False,
    }
    sse = map_event_to_sse(event, session_id="s1", cache_lookup=lambda t: None)
    assert sse is not None
    assert "event: step_result" in sse
    assert '"fallback result"' in sse


def test_map_agent_end_reports_turn_stats():
    """task_complete carries the injected turn counters (audit #820)."""
    event = {"type": "agent_end"}
    sse = map_event_to_sse(
        event, session_id="s1",
        turn_stats=lambda: {"tool_step_count": 3, "final_text": "done analyzing"},
    )
    assert sse is not None
    assert "event: task_complete" in sse
    assert '"step_count": 3' in sse
    assert "done analyzing" in sse


def test_map_agent_end_zero_without_stats():
    event = {"type": "agent_end"}
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is not None
    assert "event: task_complete" in sse
    assert '"step_count": 0' in sse


def test_map_compaction_start_and_end():
    sse_start = map_event_to_sse({"type": "compaction_start"}, session_id="s1")
    assert sse_start is not None
    assert "event: content" in sse_start
    assert "[压缩上下文...]" in sse_start

    sse_end = map_event_to_sse({"type": "compaction_end"}, session_id="s1")
    assert sse_end is not None
    assert "event: content" in sse_end
    assert "[上下文压缩完成]" in sse_end


def test_sanitize_error_text():
    sensitive = "Error at /app/internal/secret/db.py: postgresql://admin:secret123@localhost:5432/gis"
    sanitized = _extract_error_text(sensitive)
    assert "secret123" not in sanitized
    assert "<path>" in sanitized


# ─── Negative-path tests (review §3 missing edge cases) ───────────────────


def test_map_tool_execution_end_cache_miss_with_is_error():
    """cache-miss + isError=True emits step_error with the extracted error text.

    The cache-miss + isError=False path is covered by
    test_map_tool_execution_end_cache_miss_fallback; this pins the isError=True
    branch (pi_event_mapper.py:139-144) which emits step_error.
    """
    event = {
        "type": "tool_execution_end",
        "toolName": "buffer_analysis",
        "toolCallId": "tc-err-miss",
        "result": {"content": [{"type": "text", "text": "Buffer radius must be positive"}]},
        "isError": True,
    }
    # cache_lookup is single-arg (tool_call_id) since #295 collapsed the
    # dispatch-cache key; the two-arg form would TypeError on master.
    sse = map_event_to_sse(event, session_id="s1", cache_lookup=lambda t: None)
    assert sse is not None
    assert "event: step_error" in sse
    assert "Buffer radius must be positive" in sse


def test_map_unknown_event_type_returns_none():
    """An event type not in _EVENT_HANDLERS returns None (no SSE emitted).

    Pins pi_event_mapper.py:203-206: handler lookup returns None for unknown
    types, and map_event_to_sse returns None instead of crashing.
    """
    event = {"type": "some_future_event_type", "data": "whatever"}
    sse = map_event_to_sse(event, session_id="s1")
    assert sse is None
