"""Unit tests for coalesced session metadata retrieval"""
from unittest.mock import MagicMock
import pytest
import redis

from app.services.session_data import SessionDataManager
from app.services.session_data_redis import RedisSessionDataManager


def test_in_memory_session_metadata():
    manager = SessionDataManager()
    sid = "test-mem-sid"

    # Set up some state
    manager.set_map_state(sid, "base_layer", "Google Satellite")
    manager.store(sid, {"type": "FeatureCollection"}, prefix="layer1")
    manager.append_event(sid, "tool_executed", {"tool": "buffer"})

    # Get metadata
    metadata = manager.get_session_metadata(sid)

    assert metadata["started_at"] is not None
    assert metadata["map_state"]["base_layer"] == "Google Satellite"
    assert len(metadata["list_refs"]) == 1
    assert any(ref.startswith("ref:layer1") for ref in metadata["list_refs"])
    assert metadata["event_log"][0]["event"] == "tool_executed"


def test_redis_session_metadata():
    manager = RedisSessionDataManager("redis://localhost:6379")
    mock_redis = MagicMock()
    manager._r = mock_redis

    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe

    # Mock the pipeline execution result: [state_raw, ref_ids_bytes, raw_refs, events_raw]
    state_raw = {
        b"base_layer": b'"Satellite"',
        b"_started_at": b'"2026-05-22T22:00:00"'
    }
    ref_ids_bytes = [b"ref:layer1-abc12345"]
    raw_refs = {
        b"ref:layer1-abc12345": b"my-layer-alias"
    }
    events_raw = [
        b'{"event": "tool_executed", "data": {"tool": "buffer"}, "timestamp": "2026-05-22T22:01:00"}'
    ]

    mock_pipe.execute.return_value = [state_raw, ref_ids_bytes, raw_refs, events_raw]

    metadata = manager.get_session_metadata("session-xyz")

    # Verify pipeline calls
    mock_redis.pipeline.assert_called_once()
    from unittest.mock import call
    mock_pipe.hgetall.assert_has_calls([
        call(manager._state_key("session-xyz")),
        call(manager._refs_key("session-xyz")),
    ], any_order=True)
    mock_pipe.zrange.assert_called_with(manager._refs_order_key("session-xyz"), 0, -1)
    mock_pipe.lrange.assert_called_with(manager._events_key("session-xyz"), 0, -1)
    mock_pipe.execute.assert_called_once()

    # Verify parsed metadata
    assert metadata["started_at"] == "2026-05-22T22:00:00"
    assert metadata["map_state"] == {"base_layer": "Satellite", "_started_at": "2026-05-22T22:00:00"}
    assert metadata["list_refs"] == {"ref:layer1-abc12345": "my-layer-alias"}
    assert metadata["event_log"] == [{"event": "tool_executed", "data": {"tool": "buffer"}, "timestamp": "2026-05-22T22:01:00"}]


def test_redis_session_metadata_error_fallback():
    manager = RedisSessionDataManager("redis://localhost:6379")
    mock_redis = MagicMock()
    manager._r = mock_redis

    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe

    mock_pipe.execute.side_effect = redis.RedisError("Connection timed out")

    # Stub the individual fallback methods on the manager
    manager.get_map_state = MagicMock(return_value={"base_layer": "FallbackMap"})
    manager.list_refs = MagicMock(return_value={"ref:layer-fallback": "alias123"})
    manager.get_event_log = MagicMock(return_value=[])
    manager.get_started_at = MagicMock(return_value="2026-05-22T22:00:00")

    metadata = manager.get_session_metadata("session-xyz")

    # Should fallback successfully to individual direct calls
    assert metadata["map_state"] == {"base_layer": "FallbackMap"}
    assert metadata["list_refs"] == {"ref:layer-fallback": "alias123"}
    assert metadata["event_log"] == []
    assert metadata["started_at"] == "2026-05-22T22:00:00"
