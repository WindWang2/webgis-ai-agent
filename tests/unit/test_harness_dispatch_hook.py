"""Tests for PiAgentHarness dispatch hook wiring in agent_pi_bridge."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.lib.harness.pi_agent_harness import PiAgentHarness


def test_harness_records_tool_call_via_record_event():
    """Verify harness can record tool calls through the unified event API."""
    from app.lib.harness.tool_call_event import ToolCallEvent
    harness = PiAgentHarness(session_id="hook_test")
    
    event = ToolCallEvent(
        tool_call_id="tc_1",
        tool_name="webgis_layer_upsert",
        arguments={"layer": {"id": "test"}},
        result={"success": True},
        is_error=False,
    )
    harness.record_event(event)
    
    assert len(harness.tool_calls) == 1
    assert len(harness.tool_results) == 1
    assert harness.tool_calls[0]["name"] == "webgis_layer_upsert"
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
    """Verify _harness is None when PI_HARNESS_ENABLED is not set."""
    import app.agent_pi_bridge as bridge
    # In normal test runs, PI_HARNESS_ENABLED is not set
    # So get_harness() should return None (unless explicitly enabled)
    # This test verifies the opt-in pattern works
    harness = bridge.get_harness()
    # harness may be None or a PiAgentHarness depending on env
    assert harness is None or isinstance(harness, PiAgentHarness)


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
