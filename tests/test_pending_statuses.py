"""Tests for all pending tool statuses.

Part of P3-3: _PENDING_STATUSES 全状态测试覆盖.
"""
import pytest

from app.services.chat.context_builder import _split_events, _PENDING_STATUSES


def test_pending_statuses_contains_exact_set():
    """Assert that _PENDING_STATUSES contains all the expected pending status strings."""
    expected = {
        "export_task_created",
        "export_batch_task_created",
        "change_detection_task_started",
        "analysis_task_started",
        "started",
    }
    assert _PENDING_STATUSES == expected


@pytest.mark.parametrize("status", [
    "export_task_created",
    "export_batch_task_created",
    "change_detection_task_started",
    "analysis_task_started",
    "started",
])
def test_split_events_recognizes_each_pending_status(status):
    """Verify that each of the 5 pending statuses is correctly split as a pending task."""
    log = [
        {
            "event": "tool_executed",
            "data": {
                "tool": "spatial_analysis",
                "status": status,
                "command": "run"
            }
        }
    ]
    tools, users, pending = _split_events(log)
    assert len(tools) == 1
    assert len(users) == 0
    assert len(pending) == 1
    assert pending[0]["data"]["status"] == status
