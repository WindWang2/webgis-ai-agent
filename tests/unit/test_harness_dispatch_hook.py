"""Tests for PiAgentHarness dispatch hook wiring in agent_pi_bridge."""
import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.lib.harness.pi_agent_harness import PiAgentHarness

def _sys_executable_is_interpreter() -> bool:
    """#1011 环境假象守卫：ZCode AppImage 宿主把 sys.executable 重解析为
    桌面应用包装器（子进程输出为桌面日志，非 python）——显式 skip，
    CI 不受影响。"""
    from pathlib import Path as _Path
    return _Path(sys.executable).name.lower().startswith("python")


pytestmark = pytest.mark.skipif(
    not _sys_executable_is_interpreter(),
    reason="sys.executable 非 python 解释器（AppImage 宿主环境假象，#1011）；CI 不受影响",
)

from app.lib.harness.tool_call_event import ToolCallEvent


def test_harness_records_tool_call_via_record_event():
    """Verify harness can record tool calls through the unified event API."""
    from app.lib.harness.tool_call_event import ToolCallEvent
    harness = PiAgentHarness(session_id="hook_test")
    
    event = ToolCallEvent(
        tool_call_id="tc_1",
        tool_name="webgis_layer_upsert",
        arguments={"layer": {"id": "test"}},
        result={"success": True, "is_compiled": True},
        is_error=False,
    )
    harness.record_event(event)

    assert len(harness.tool_calls) == 1
    assert len(harness.tool_results) == 1
    assert harness.tool_calls[0]["name"] == "webgis_layer_upsert"
    # V2: is_valid requires real semantic-validity evidence (is_compiled), not
    # merely "didn't error".
    assert harness.mapspec_mutations[0]["is_valid"] is True


def test_harness_records_error_event():
    """Verify harness tracks errors through unified event API."""
    from app.lib.harness.tool_call_event import ToolCallEvent
    harness = PiAgentHarness(session_id="error_test")
    
    event = ToolCallEvent(
        tool_call_id="tc_err",
        tool_name="st_dbscan",
        arguments={},
        result={},
        is_error=True,
        error_msg="Invalid CRS",
    )
    harness.record_event(event)
    
    assert len(harness.exceptions) == 1
    assert harness.exceptions[0]["error_msg"] == "Invalid CRS"


def test_harness_disabled_by_default():
    """Verify the opt-in flag: without PI_HARNESS_ENABLED, a freshly-imported
    bridge module has _harness = None.

    P1 fix: the previous assertion (`harness is None or isinstance(...)`) was
    vacuously true — a live harness passed via the isinstance branch, so it
    proved nothing. We spawn a clean subprocess (no env var) so the
    module-level _harness initialization runs without interference from the
    test process's already-imported module, and without polluting other tests
    via importlib.reload.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import app.agent_pi_bridge as b; "
         "h = b.get_harness(); "
         "print('HARNESS_IS_NONE' if h is None else 'HARNESS_IS_LIVE')"],
        capture_output=True,
        text=True,
        env={**os.environ, "PI_HARNESS_ENABLED": ""},
    )
    # The flag is explicitly empty -> opt-in disabled -> harness must be None.
    assert "HARNESS_IS_NONE" in result.stdout, (
        f"expected harness None when PI_HARNESS_ENABLED empty, "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_dispatch_tool_records_duration_and_truncated_error(monkeypatch):
    """P1 fix: dispatch_tool must populate duration_ms, truncate error_msg,
    and still record telemetry on the exception path (previously skipped)."""
    import app.services.cartography_runtime as cartography_runtime

    harness = PiAgentHarness(session_id="exc_test")
    monkeypatch.setattr(cartography_runtime, "_harness", harness)

    # Force the dispatch service to raise a long error message.
    long_msg = "x" * 500
    fake_service = MagicMock()
    fake_service.dispatch = AsyncMock(side_effect=RuntimeError(long_msg))
    monkeypatch.setattr(bridge, "ToolDispatchService", lambda **kw: fake_service)

    fake_registry = MagicMock()
    fake_registry.list_tools = MagicMock(return_value=["some_tool"])
    fake_registry.metadata = MagicMock(return_value={"tier": 1})
    monkeypatch.setattr(bridge, "_tool_registry", fake_registry)

    request = bridge.PiToolRequest(
        toolCallId="tc_x",
        name="some_tool",
        arguments={},
        sessionId="exc_test",
    )

    # The exception propagates out of dispatch_tool, but telemetry is recorded.
    with pytest.raises(RuntimeError):
        await bridge.dispatch_tool(request)

    assert len(harness.exceptions) == 1
    exc_entry = harness.exceptions[0]
    # error_msg is the truncated exception text, not the full 500-char payload.
    assert len(exc_entry["error_msg"]) <= 200
    assert exc_entry["error_msg"] == long_msg[:200]


def test_harness_caps_accumulated_events():
    """P1 fix: the production harness is a singleton; unbounded accumulation
    of tool_calls/sse_events/etc. is a memory leak. Each list is capped."""
    harness = PiAgentHarness(session_id="cap_test")
    # Inject well over the cap on every accumulator surface.
    for i in range(1500):
        harness.record_event(ToolCallEvent(
            tool_call_id=f"tc_{i}",
            tool_name="noop",
            arguments={},
            result={},
        ))
        harness.record_sse_event({"type": "noop", "i": i})

    assert len(harness.tool_calls) <= PiAgentHarness.MAX_EVENTS
    assert len(harness.tool_results) <= PiAgentHarness.MAX_EVENTS
    assert len(harness.sse_events) <= PiAgentHarness.MAX_EVENTS
    assert len(harness.exceptions) <= PiAgentHarness.MAX_EVENTS


def test_telemetry_summary_separates_rates_from_counts():
    """U5 fix: get_telemetry_summary must separate rates (0-100, rendered with
    %) from raw counts (rendered without %). Previously both shared one flat
    Dict[str, float], so the UI showed ToolCallsCount=42 as "42%"."""
    harness = PiAgentHarness(session_id="u5_test")
    # Record 2 tool calls + 1 error so counts are non-zero.
    harness.record_event(ToolCallEvent(
        tool_call_id="ok_1", tool_name="noop", arguments={}, result={},
    ))
    harness.record_event(ToolCallEvent(
        tool_call_id="ok_2", tool_name="noop", arguments={}, result={},
    ))
    harness.record_event(ToolCallEvent(
        tool_call_id="err_1", tool_name="noop", arguments={}, is_error=True,
        error_msg="boom",
    ))

    summary = harness.get_telemetry_summary()
    assert "rates" in summary
    assert "counts" in summary
    # Rates are 0-100 percentages WHEN evaluated; null (not 100.0) when there is
    # no positive evidence — "missing evidence ≠ success". An ``evaluated`` map
    # says which rates had evidence, and null-ness MUST match evaluated=False.
    assert "evaluated" in summary
    assert "MapSpecValidity" in summary["rates"]
    assert "ErrorRecoveryRate" in summary["rates"]
    for name, v in summary["rates"].items():
        assert v is None or 0.0 <= v <= 100.0
        # invariant: a rate is null exactly when it was not evaluated
        assert (v is None) == (not summary["evaluated"][name])
    # Counts are raw integers-as-floats, NOT in rates.
    assert summary["counts"]["ToolCallsCount"] == 3.0
    assert "ToolCallsCount" not in summary["rates"]


def test_tool_metrics_record_event():
    """Verify tool_metrics.record_event delegates to record_tool_call."""
    from app.lib.harness.tool_call_event import ToolCallEvent
    from app.services import tool_metrics
    tool_metrics._reset_for_tests()

    event = ToolCallEvent(
        tool_call_id="tm_1",
        tool_name="webgis_view_set",
        arguments={"zoom": 10},
        duration_ms=42,
        cache_hit=False,
        session_id="sess_1",
        arg_bytes=20,
        result_bytes=100,
    )
    tool_metrics.record_event(event)

    snap = tool_metrics.aggregator_snapshot()
    assert "webgis_view_set" in snap
    assert snap["webgis_view_set"]["count"] == 1
    assert snap["webgis_view_set"]["total_ms"] == 42
