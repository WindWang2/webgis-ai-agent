import pytest
from app.services.session_data import SessionDataManager
from app.services.session_data_protocol import SessionContext, SessionStoreProtocol


@pytest.mark.asyncio
async def test_session_store_interface_and_load_context():
    store = SessionDataManager()
    session_id = "test-session-123"

    # Store some initial layer state
    await store.set_map_state(session_id, "current_view", {"center": [30.0, 104.0], "zoom": 12})
    await store.update_layer_in_state(session_id, "layer-1", {"name": "Test Layer", "type": "geojson"})

    # Store a data ref
    ref_id = await store.store(session_id, {"type": "FeatureCollection", "features": []}, prefix="geojson")
    await store.set_alias(session_id, ref_id, "my_layer")

    # Commit a dispatch result
    await store.commit_dispatch(
        session_id=session_id,
        tool_name="buffer_analysis",
        geojson_ref=ref_id,
        event_payload={"feature_count": 5},
    )

    # Test load_context
    ctx: SessionContext = await store.load_context(session_id)
    assert ctx.session_id == session_id
    assert ctx.map_state["current_view"] == {"center": [30.0, 104.0], "zoom": 12}
    assert len(ctx.layers) == 1
    assert ctx.layers[0]["id"] == "layer-1"
    assert len(ctx.event_log) == 1
    assert ctx.event_log[0]["data"]["tool"] == "buffer_analysis"
    assert ctx.event_log[0]["data"]["ref"] == ref_id
    assert ctx.refs[ref_id] == "my_layer"

    # Test get_ref_data (Fetch-on-Demand)
    data = await store.get_ref_data(session_id, "my_layer")
    assert data == {"type": "FeatureCollection", "features": []}
