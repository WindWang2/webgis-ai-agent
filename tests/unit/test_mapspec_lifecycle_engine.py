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


@pytest.mark.asyncio
async def test_rollback_fails_loudly_when_ref_unrestorable(monkeypatch):
    """#1072: 回滚的 ref 恢复 overwrite() 返回 False（内存后端 ref 已被
    LRU 逐出 / Redis 不可用）时必须响亮失败 —— 此前静默"成功"留下指向
    无负载 ref 的图层。"""
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_ckpt_unrestorable_1"

    # ref-backed source：checkpoint 才会物化 blob（planned 非空 → 恢复走
    # overwrite 路径）。
    from app.services.session_data import session_data_manager as sdm

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
         "properties": {"v": 1}}]}
    ref_id = await sdm.store(session_id, fc, prefix="geojson")
    layer_def = {"id": "ly-1", "type": "circle", "source": "src-1"}
    source_data = {"type": "geojson", "ref_id": ref_id}
    await engine.apply_mutation(
        session_id, UpsertLayerIntent(layer=layer_def, source_data=source_data)
    )
    # 显式命名 checkpoint 才是自包含（物化 ref blob）；自动 checkpoint 是
    # presentation_only（refs 留存活动态，无需 overwrite）。
    ckpt = await engine.apply_mutation(
        session_id, CheckpointIntent(checkpoint_id="ckpt_audit1072")
    )
    assert ckpt.checkpoint_id

    # 模拟恢复期存储不可用：checkpoint.py 的 overwrite 全部失败。
    # 直接在 checkpoint 模块命名空间打桩（它 import 的是模块级单例）。
    from app.services.session_data import session_data_manager

    async def _failing_overwrite(sid, ref_id, data):
        return False

    # rollback 从 lifecycle_engine 收到的就是该单例对象 —— 直接打桩其方法
    monkeypatch.setattr(session_data_manager, "overwrite", _failing_overwrite)
    rb = await engine.apply_mutation(
        session_id, RollbackIntent(checkpoint_id=ckpt.checkpoint_id)
    )
    # 会话无过期 ref 时走 refs_reused 快路径（无 overwrite）—— 用 materialized
    # blob 的 planned 路径验证；若本会话未触发 planned 非空，则强制经由
    # monkeypatch 的失败 overwrite 至少被调用一次的分支无法覆盖，
    # 断言降级为回滚不静默丢失（success 或显式 error 二选一，绝不
    # "成功但 ref 缺失"）。
    assert rb.is_error is True, "ref 无法恢复时回滚必须失败（不得静默成功）"
    assert "could not restore" in (rb.error_msg or "")


@pytest.mark.asyncio
async def test_remove_layer_family_predicate_matches_runtime_layers():
    """#1074(F-12): spec 删层族（x / x-label）与运行时 map_state.layers 的
    可见集必须一致 —— 此前运行时只删精确 id。"""
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_family_rm_1"
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection", "features": []}
    await engine.apply_mutation(session_id, UpsertLayerIntent(
        layer={"id": "roads", "type": "circle", "source": "src-1"},
        source_data={"type": "geojson", "inlineData": fc},
    ))
    await engine.apply_mutation(session_id, UpsertLayerIntent(
        layer={"id": "roads-label", "type": "symbol", "source": "src-1"},
        source_data={"type": "geojson", "inlineData": fc},
    ))
    await engine.apply_mutation(session_id, RemoveLayerIntent(layer_id="roads"))
    state = await session_data_manager.get_map_state(session_id)
    runtime_ids = {ly.get("id") for ly in state.get("layers", [])}
    assert "roads" not in runtime_ids
    assert "roads-label" not in runtime_ids, "伴生子层必须随族移除（与 spec 一致）"


@pytest.mark.asyncio
async def test_mutation_revision_restored_when_redis_state_expires():
    """#1074(F-9): Redis 状态过期而磁盘 spec 存活时，CAS 令牌随复活路径
    恢复（不再 N→0→1 破坏单调性）。"""
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_rev_restore_1"
    from app.services.session_data import session_data_manager

    res1 = await engine.apply_mutation(session_id, SetViewIntent(center=[104.0, 30.6], zoom=10.0))
    res2 = await engine.apply_mutation(session_id, SetViewIntent(center=[104.1, 30.7], zoom=11.0))
    assert res2.mutation_revision == 2
    # 模拟 Redis 状态过期：仅清内存状态哈希（等价 TTL 到期），保留磁盘
    # spec + revision sidecar。注意 clear_session 在内存后端会连磁盘一起
    # 清（会话删除语义），不能用它模拟过期。
    session_data_manager._map_state.pop(session_id, None)
    spec = await engine.store.get_mapspec(session_id)
    assert spec is not None, "磁盘兜底应复活 spec"
    state = await session_data_manager.get_map_state(session_id)
    assert state.get("_cartographic_mutation_revision") == 2, \
        "revision 必须随磁盘复活恢复（sidecar）"
