"""Unit tests for MapSpecLifecycleEngine."""
import pytest
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
)


@pytest.mark.asyncio
async def test_lifecycle_init_project():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_init_1"
    intent = InitProjectIntent(view={"center": [116.4, 39.9], "zoom": 12.0})
    res = await engine.apply_mutation(session_id, intent)

    assert res.is_error is False
    assert res.mapspec is not None
    assert res.mapspec["view"]["center"] == [116.4, 39.9]
    assert res.mapspec["view"]["zoom"] == 12.0


@pytest.mark.asyncio
async def test_lifecycle_set_view():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_view_1"

    # Auto-inits if absent
    intent = SetViewIntent(center=[120.0, 30.0], zoom=10.0)
    res = await engine.apply_mutation(session_id, intent)

    assert res.is_error is False
    assert res.mapspec["view"]["center"] == [120.0, 30.0]
    assert res.mapspec["view"]["zoom"] == 10.0


@pytest.mark.asyncio
async def test_lifecycle_upsert_and_remove_layer():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_layer_1"

    # Upsert layer
    layer_def = {
        "id": "test-layer-1",
        "type": "circle",
        "source": "src-1",
        "paint": {"circle-color": "#ff0000"},
    }
    source_data = {"type": "FeatureCollection", "features": []}

    res = await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=layer_def, source_data=source_data)
    )
    assert res.is_error is False
    assert len(res.mapspec["layers"]) == 1
    assert res.mapspec["layers"][0]["id"] == "test-layer-1"
    assert res.checkpoint_id is not None  # Auto-checkpoint on layer mutation

    # Remove layer
    res_remove = await engine.apply_mutation(
        session_id, RemoveLayerIntent(layer_id="test-layer-1")
    )
    assert res_remove.is_error is False
    assert len(res_remove.mapspec["layers"]) == 0


@pytest.mark.asyncio
async def test_lifecycle_set_layout():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_layout_1"

    intent = SetLayoutIntent(legend={"visible": False, "position": "bottom-left"})
    res = await engine.apply_mutation(session_id, intent)

    assert res.is_error is False
    assert res.mapspec["layout"]["legend"]["visible"] is False
    assert res.mapspec["layout"]["legend"]["position"] == "bottom-left"


@pytest.mark.asyncio
async def test_lifecycle_checkpoint_and_rollback():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_ckpt_1"

    # Set initial view
    await engine.apply_mutation(session_id, SetViewIntent(center=[100.0, 20.0], zoom=5.0))

    # Checkpoint
    ckpt_res = await engine.apply_mutation(session_id, CheckpointIntent())
    ckpt_id = ckpt_res.checkpoint_id
    assert ckpt_id is not None

    # Mutate view
    await engine.apply_mutation(session_id, SetViewIntent(center=[110.0, 30.0], zoom=10.0))

    # Rollback
    rb_res = await engine.apply_mutation(session_id, RollbackIntent(checkpoint_id=ckpt_id))
    assert rb_res.is_error is False
    assert rb_res.mapspec["view"]["center"] == [100.0, 20.0]
    assert rb_res.mapspec["view"]["zoom"] == 5.0
