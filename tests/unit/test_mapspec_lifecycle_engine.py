"""Unit tests for MapSpecLifecycleEngine."""
import pytest
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    PatchLayerPresentationIntent,
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


@pytest.mark.asyncio
async def test_upsert_preserves_user_durable_presentation():
    """ST-P2-2（user wins）：用户 durable 隐藏/透明度不被 agent 重跑同 id upsert 静默重置。"""
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_user_pres_1"

    layer_def = {
        "id": "poi-layer",
        "type": "circle",
        "source": "src-poi",
        "paint": {"circle-color": "#ff0000"},
    }
    source_data = {"type": "FeatureCollection", "features": []}
    res = await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=layer_def, source_data=source_data)
    )
    assert res.is_error is False

    # 用户 durable 决策：隐藏 + 透明度 0.3（origin=user 强制 CAS）
    res_patch = await engine.apply_mutation(
        session_id,
        PatchLayerPresentationIntent(layer_id="poi-layer", visible=False, opacity=0.3),
        origin="user",
        expected_revision=res.mutation_revision,
    )
    assert res_patch.is_error is False

    # Agent 重跑同 id upsert（新数据/新颜色，未显式给出显隐与透明度）
    reup = dict(layer_def)
    reup["paint"] = {"circle-color": "#00ff00"}
    res_reup = await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=reup, source_data=source_data)
    )
    assert res_reup.is_error is False
    layer = res_reup.mapspec["layers"][0]
    assert layer["paint"]["circle-color"] == "#00ff00"  # agent 样式以新为准
    assert layer["layout"]["visibility"] == "none"  # 用户隐藏决策保留
    assert layer["paint"]["opacity"] == 0.3  # 用户透明度决策保留
    assert layer["paint"]["circle-opacity"] == 0.3


@pytest.mark.asyncio
async def test_upsert_explicit_agent_visibility_wins():
    """Agent 显式给出 visibility 时以 agent 为准（合法语义：分析中间层默认隐藏）。"""
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_agent_vis_1"

    layer_def = {"id": "layer-a", "type": "fill", "source": "src-a", "paint": {}}
    source_data = {"type": "FeatureCollection", "features": []}
    await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=layer_def, source_data=source_data)
    )
    await engine.apply_mutation(
        session_id, PatchLayerPresentationIntent(layer_id="layer-a", visible=False)
    )

    reup = dict(layer_def)
    reup["layout"] = {"visibility": "visible"}
    res = await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=reup, source_data=source_data)
    )
    assert res.is_error is False
    assert res.mapspec["layers"][0]["layout"]["visibility"] == "visible"
