"""Unit tests for SessionStoreProtocol 100% interface parity (ADR-0025)."""
import pytest
from app.services.session_data_protocol import SessionStoreProtocol, SessionDataProtocol
from app.services.session_data import SessionDataManager
from app.services.session_data_redis import RedisSessionDataManager


def test_protocol_runtime_checkable():
    """Verify runtime_checkable isinstance assertions on both backends."""
    mem_mgr = SessionDataManager()
    redis_mgr = RedisSessionDataManager("redis://localhost:6379/0")

    assert isinstance(mem_mgr, SessionStoreProtocol)
    assert isinstance(mem_mgr, SessionDataProtocol)
    assert isinstance(redis_mgr, SessionStoreProtocol)
    assert isinstance(redis_mgr, SessionDataProtocol)


@pytest.mark.asyncio
async def test_session_data_manager_all_16_methods():
    """Test full 16-method suite on SessionDataManager."""
    mgr = SessionDataManager()
    sid = "sess_test_16"

    # 1. store
    ref1 = await mgr.store(sid, {"foo": "bar"}, prefix="data")
    assert ref1.startswith("ref:data-")

    # 2. get
    val1 = await mgr.get(sid, ref1)
    assert val1 == {"foo": "bar"}

    # 3. overwrite existing ref
    ok = await mgr.overwrite(sid, ref1, {"foo": "bar_updated"})
    assert ok is True
    val1_updated = await mgr.get(sid, ref1)
    assert val1_updated == {"foo": "bar_updated"}

    # overwrite non-existent ref
    bad_ok = await mgr.overwrite(sid, "ref:data-nonexistent", {"a": 1})
    assert bad_ok is False

    # 4. set_alias & 5. resolve_alias
    await mgr.set_alias(sid, ref1, "alias_ref1")
    resolved = await mgr.resolve_alias(sid, "alias_ref1")
    assert resolved == ref1

    # 6. list_refs
    refs = await mgr.list_refs(sid)
    assert ref1 in refs
    assert refs[ref1] == "alias_ref1"

    # 7. set_map_state & 8. get_map_state
    await mgr.set_map_state(sid, "zoom", 12)
    state = await mgr.get_map_state(sid)
    assert state.get("zoom") == 12

    # 9. update_layer_in_state
    await mgr.update_layer_in_state(sid, "layer_1", {"color": "blue"})
    state = await mgr.get_map_state(sid)
    assert len(state.get("layers", [])) == 1
    assert state["layers"][0]["id"] == "layer_1"

    # 10. remove_layer_from_state
    await mgr.remove_layer_from_state(sid, "layer_1")
    state = await mgr.get_map_state(sid)
    assert len(state.get("layers", [])) == 0

    # 11. append_event & 12. get_event_log
    await mgr.append_event(sid, "click", {"x": 100})
    events = await mgr.get_event_log(sid)
    assert len(events) == 1
    assert events[0]["event"] == "click"

    # 13. get_started_at
    started_at = await mgr.get_started_at(sid)
    assert started_at is not None

    # 14. get_session_metadata
    meta = await mgr.get_session_metadata(sid)
    assert "map_state" in meta
    assert "list_refs" in meta
    assert "event_log" in meta
    assert "started_at" in meta

    # 15. cleanup_idle_sessions
    await mgr.cleanup_idle_sessions(max_sessions=50)

    # 16. clear_session
    await mgr.clear_session(sid)
    cleared_state = await mgr.get_map_state(sid)
    assert cleared_state == {}
