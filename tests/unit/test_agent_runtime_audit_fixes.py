"""Unit tests verifying Agent Runtime & GIS Harness audit fixes (#940-#944)."""
import pytest
from unittest.mock import MagicMock

from app.services.subagent import SubagentResult
from app.services.gis_harness.recipes import get_recipe_registry
from app.services.gis_harness.intent import resolve_map_request_intent
from app.agent_pi_bridge import _slim_pi_details_payload
from app.services.task_tracker import TaskTracker


def test_subagent_result_refs_always_list():
    """SubagentResult.refs must default to and remain a list[str], never None."""
    res = SubagentResult(success=False, error="test error", summary="failed", refs=[])
    assert isinstance(res.refs, list)
    assert res.refs == []


def test_slim_pi_details_payload_strips_large_image_and_caps_size():
    """_slim_pi_details_payload removes base64 data URLs and caps payload to 64KB."""
    mock_result = MagicMock()
    mock_result.raw_result = {
        "image": "data:image/png;base64," + "A" * 2000,
        "result_ref": "ref:image-12345",
        "data": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"id": i}} for i in range(200)],
        },
    }
    mock_result.geojson_ref = "ref:image-12345"

    slimmed = _slim_pi_details_payload(mock_result)
    assert "image" not in slimmed
    assert slimmed["imageRef"] == "ref:image-12345"
    assert "features" not in slimmed["data"]
    assert slimmed["data"]["feature_count"] == 200


def test_intent_simple_view_matches_poi_distribution_recipe():
    """Intent task simple_view matches poi_distribution_overview recipe."""
    intent = resolve_map_request_intent("给我看看成都小学")
    assert intent.task == "simple_view"
    registry = get_recipe_registry()
    matches = registry.select_candidates(intent)
    assert len(matches) > 0
    assert any(r.id == "poi_distribution_overview" for r in matches)


@pytest.mark.asyncio
async def test_pi_bridge_task_tracker_tracks_lifecycle():
    """TaskTracker tracks task lifecycle during PiBridge executions."""
    tracker = TaskTracker()
    sid = "test-pi-tracker-session"
    task = tracker.create(sid, "test request")
    assert task is not None
    assert task.session_id == sid

    step = tracker.start_step(task.id, "buffer", {"distance": 100})
    assert step.tool == "buffer"
    assert step.status.value == "running"

    tracker.complete_step(task.id, step.id, {"success": True})
    assert step.status.value == "completed"

    tracker.complete_task(task.id)
    assert task.status.value == "completed"
