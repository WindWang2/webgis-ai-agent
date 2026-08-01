import pytest
from app.services.session_data import SessionDataManager


@pytest.mark.asyncio
async def test_session_store_granular_surface():
    """Exercise the granular methods ADR-0018 keeps as load-bearing.

    The former deep-method layer (load_context / commit_dispatch / get_ref_data)
    is gone (ADR-0018 Trigger 2): load_context + get_ref_data had zero callers,
    and commit_dispatch was an anemic wrapper inlined at its sole call site
    (tool_dispatch_service._record_event → append_event). This test now covers
    the granular surface directly — the same state the deleted methods built on.
    """
    store = SessionDataManager()
    session_id = "test-session-123"

    # map-state + layer mutations (frontend-perception surface)
    await store.set_map_state(session_id, "current_view", {"center": [30.0, 104.0], "zoom": 12})
    await store.update_layer_in_state(session_id, "layer-1", {"name": "Test Layer", "type": "geojson"})

    # ref store + alias (Fetch-on-Demand write + alias surface)
    ref_id = await store.store(session_id, {"type": "FeatureCollection", "features": []}, prefix="geojson")
    await store.set_alias(session_id, ref_id, "my_layer")

    # commit_dispatch is gone; the inlined caller does append_event directly
    await store.append_event(
        session_id,
        "tool_executed",
        {"tool": "buffer_analysis", "ref": ref_id, "feature_count": 5},
    )

    # get_ref_data is gone; Fetch-on-Demand reads cross the granular get() seam
    # (get_ref_data was a 2-line `return await self.get(...)` rename of it)
    data = await store.get(session_id, "my_layer")
    assert data == {"type": "FeatureCollection", "features": []}

    # map_state round-trips
    map_state = await store.get_map_state(session_id)
    assert map_state["current_view"] == {"center": [30.0, 104.0], "zoom": 12}
    assert len(map_state.get("layers", [])) == 1
    assert map_state["layers"][0]["id"] == "layer-1"

    # event_log carries the tool_executed event
    event_log = await store.get_event_log(session_id)
    assert len(event_log) == 1
    assert event_log[0]["data"]["tool"] == "buffer_analysis"
    assert event_log[0]["data"]["ref"] == ref_id

    # alias resolves
    refs = await store.list_refs(session_id)
    assert refs[ref_id] == "my_layer"
