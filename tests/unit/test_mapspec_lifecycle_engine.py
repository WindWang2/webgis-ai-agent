"""Unit tests for MapSpecLifecycleEngine."""
import uuid

import pytest

import app.services.mapspec.store as mapspec_store_module
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
    ReorderLayersIntent,
)
from app.services.session_data import session_data_manager


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
async def test_mutation_revision_restored_when_redis_state_expires(tmp_path, monkeypatch):
    """#1074(F-9): Redis 状态过期而磁盘 spec 存活时，CAS 令牌随复活路径
    恢复（不再 N→0→1 破坏单调性）。

    v2(F1): 会话目录必须隔离 —— 固定 id 的跨运行磁盘 sidecar 残留会让
    prior 继承上一运行的令牌（这正是 F1 修复的新语义：复活令牌参与
    CAS 计算），旧断言因此不确定。
    """
    import shutil
    import uuid
    from app.services.mapspec.store import BASE_STORAGE_DIR

    engine = MapSpecLifecycleEngine()
    session_id = f"test_session_rev_restore_{uuid.uuid4().hex[:8]}"
    session_dir = BASE_STORAGE_DIR / session_id
    shutil.rmtree(session_dir, ignore_errors=True)
    try:
        from app.services.session_data import session_data_manager

        await engine.apply_mutation(session_id, SetViewIntent(center=[104.0, 30.6], zoom=10.0))
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
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_mutation_single_full_state_read(monkeypatch):
    """#1082(F-10): 每次 mutation 恰一次全量 get_map_state（此前 2-3 次：
    pre_state + get_mapspec 二读 + 每次旧 spec 全量重校验）。"""
    import app.services.mapspec.lifecycle_engine as le

    engine = MapSpecLifecycleEngine()
    session_id = "test_session_single_read_1"
    calls = {"n": 0}
    monkeypatch.setattr(
        le, "session_data_manager", _CountingSDM(le.session_data_manager, calls)
    )
    await engine.apply_mutation(
        session_id, SetViewIntent(center=[104.0, 30.6], zoom=10.0)
    )
    calls["n"] = 0
    for i in range(3):
        await engine.apply_mutation(
            session_id, SetViewIntent(center=[104.0 + i * 0.01, 30.6], zoom=10.5)
        )
    assert calls["n"] == 3, f"3 次 mutation 应恰 3 次全量读，实际 {calls['n']}"


class _CountingSDM:
    """包裹 session_data_manager：仅计数 get_map_state，其余透传。"""

    def __init__(self, inner, counter):
        self._inner = inner
        self._counter = counter

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def get_map_state(self, sid):
        self._counter["n"] += 1
        return await self._inner.get_map_state(sid)


# ─── Workbench V5：SetWorkbenchStateIntent（W3 组织态持久化）─────────────────
from app.services.mapspec.lifecycle_engine import SetWorkbenchStateIntent


def _valid_workbench_doc():
    return {
        "version": 5,
        "groups": [
            {"id": "wg-1", "name": "东部", "collapsed": False, "parentId": None},
            {"id": "wg-2", "name": "子组", "collapsed": False, "parentId": "wg-1"},
        ],
        "membership": {"layer-a": "wg-2"},
        "lockedLayerIds": ["layer-b"],
        "mode": "analyze",
    }


@pytest.mark.asyncio
async def test_workbench_state_commit_and_persist():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_wb_1"
    res = await engine.apply_mutation(
        session_id, SetWorkbenchStateIntent(doc=_valid_workbench_doc()),
        origin="user", expected_revision=0,
    )
    assert res.is_error is False
    assert res.mapspec["workbench"]["version"] == 5
    assert res.mapspec["workbench"]["membership"] == {"layer-a": "wg-2"}
    # 磁盘域可恢复（store.get_mapspec 含 workbench 分支）
    restored = await engine.store.get_mapspec(session_id)
    assert restored is not None and restored["workbench"]["mode"] == "analyze"


@pytest.mark.asyncio
async def test_workbench_state_requires_matching_revision():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_wb_2"
    first = await engine.apply_mutation(
        session_id, SetWorkbenchStateIntent(doc=_valid_workbench_doc()),
        origin="user", expected_revision=0,
    )
    assert first.is_error is False
    stale = await engine.apply_mutation(
        session_id, SetWorkbenchStateIntent(doc=_valid_workbench_doc()),
        origin="user", expected_revision=0,
    )
    assert stale.superseded is True
    assert stale.mutation_revision == 1


@pytest.mark.asyncio
async def test_workbench_state_rejects_invalid_docs():
    engine = MapSpecLifecycleEngine()
    session_id = "test_session_wb_3"
    bad_docs = [
        {"version": 4},
        {"version": 5, "groups": [{"id": "a"}, {"id": "a"}], "mode": "explore"},
        # 环：a→b→a
        {"version": 5, "groups": [
            {"id": "a", "parentId": "b"}, {"id": "b", "parentId": "a"}], "mode": "explore"},
        # 超深：5 层链
        {"version": 5, "groups": [
            {"id": "n1", "parentId": None}, {"id": "n2", "parentId": "n1"},
            {"id": "n3", "parentId": "n2"}, {"id": "n4", "parentId": "n3"},
            {"id": "n5", "parentId": "n4"}], "mode": "explore"},
        {"version": 5, "groups": [], "membership": {"l": 1}, "mode": "explore"},
        {"version": 5, "groups": [], "lockedLayerIds": [1], "mode": "explore"},
        {"version": 5, "groups": [], "mode": "hacker"},
    ]
    for doc in bad_docs:
        res = await engine.apply_mutation(
            session_id, SetWorkbenchStateIntent(doc=doc),
            origin="user", expected_revision=0,
        )
        assert res.is_error is True, f"doc should be rejected: {doc}"
        assert res.error_msg


def test_workbench_doc_validator_messages():
    from app.services.mapspec.lifecycle_engine import _workbench_doc_error

    assert _workbench_doc_error(_valid_workbench_doc()) is None
    assert "version" in _workbench_doc_error({"version": 3})
    assert "object" in _workbench_doc_error([1, 2, 3])
    assert "unique" in _workbench_doc_error({
        "version": 5, "groups": [{"id": "a"}, {"id": "a"}], "mode": "explore"})


# ─── P3 review fixes: 读失败回执 / 批量 CAS 令牌 / 双 fault / 组件排序 ─────────


@pytest.mark.asyncio
async def test_presentation_batch_persists_revision_without_atomic_backend(
    tmp_path, monkeypatch,
):
    """批量路径不得丢弃 save_mapspec 的 revision_persisted 回退。

    后端缺 commit_mapspec_state / set_map_state_fields（测试替身）时 save
    只写 spec；CAS 令牌必须由引擎补写，否则回执报 prior+1 而令牌仍旧值。
    """
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(session_data_manager, "commit_mapspec_state", None)
    monkeypatch.setattr(session_data_manager, "set_map_state_fields", None)
    engine = MapSpecLifecycleEngine()
    session_id = f"test-batch-rev-{uuid.uuid4().hex[:8]}"
    try:
        seed = await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer={"id": "L", "source": "s", "type": "circle", "paint": {}},
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )
        assert seed.is_error is False, seed.error_msg
        batch = await engine.apply_presentation_batch(
            session_id,
            [PatchLayerPresentationIntent(layer_id="L", visible=False)],
            origin="agent",
        )
        assert batch.is_error is False, batch.error_msg
        state = await session_data_manager.get_map_state(session_id)
        assert int(state.get("_cartographic_mutation_revision") or 0) == (
            batch.mutation_revision
        )
    finally:
        await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_state_read_failure_returns_is_error_receipt(monkeypatch):
    """get_map_state 失败必须返回 is_error 回执，而非裸异常逸出。"""
    async def boom(_sid):
        raise RuntimeError("redis down")

    monkeypatch.setattr(session_data_manager, "get_map_state", boom)
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        f"test-read-fail-{uuid.uuid4().hex[:8]}",
        SetViewIntent(center=[100.0, 20.0], zoom=5.0),
    )
    assert res.is_error is True
    assert "MapSpec 意图更新失败" in (res.error_msg or "")


@pytest.mark.asyncio
async def test_mapspec_load_failure_returns_is_error_receipt(monkeypatch):
    """get_mapspec 失败同样须回执，不得以裸异常逸出锁段。"""
    engine = MapSpecLifecycleEngine()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("disk read down")

    monkeypatch.setattr(engine.store, "get_mapspec", boom)
    res = await engine.apply_mutation(
        f"test-load-fail-{uuid.uuid4().hex[:8]}",
        SetViewIntent(center=[100.0, 20.0], zoom=5.0),
    )
    assert res.is_error is True
    assert "MapSpec 意图更新失败" in (res.error_msg or "")


def test_review_failure_never_raises_on_secondary_fault(monkeypatch):
    """review 兜底表示本身不得再抛（双 fault 逸出会跳过回滚/is_error）。"""
    from app.services.mapspec import lifecycle_engine as le

    def boom_fp(_spec):
        raise ValueError("fingerprint boom")

    monkeypatch.setattr(le, "cartographic_fingerprint", boom_fp)
    review = MapSpecLifecycleEngine._review_failure(
        {"sources": 1}, RuntimeError("review boom"),
    )
    assert review["status"] == "not_evaluated"
    assert review["review"]["status"] == "not_evaluated"
    assert review["counters"]["metadata_sources"] == 0


@pytest.mark.asyncio
async def test_reorder_layers_preserves_non_dict_entries():
    """reorder 的 leftover 过滤不得丢弃 spec 中的非 dict 图层条目。"""
    engine = MapSpecLifecycleEngine()
    session_id = f"test-reorder-nondict-{uuid.uuid4().hex[:8]}"
    try:
        base = await engine.apply_mutation(
            session_id, SetViewIntent(center=[0.0, 0.0], zoom=2.0)
        )
        assert base.is_error is False, base.error_msg
        spec = dict(base.mapspec)
        spec["sources"] = {"s": {"type": "geojson"}}
        spec["layers"] = [
            {"id": "a", "source": "s", "type": "circle"},
            "opaque-layer-entry",
            {"id": "b", "source": "s", "type": "circle"},
        ]
        await session_data_manager.set_map_state(session_id, "mapspec", spec)
        res = await engine.apply_mutation(
            session_id,
            ReorderLayersIntent(layer_ids=["b"]),
            expected_revision=base.mutation_revision,
        )
        assert res.is_error is False, res.error_msg
        assert "opaque-layer-entry" in res.mapspec["layers"]
    finally:
        await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_set_layout_rejects_non_numeric_component_priority():
    """priority 类型混用（"high" + 缺省）须确定性拒绝，而非 sorted TypeError。"""
    engine = MapSpecLifecycleEngine()
    session_id = f"test-layout-priority-{uuid.uuid4().hex[:8]}"
    try:
        bad = await engine.apply_mutation(
            session_id,
            SetLayoutIntent(components=[
                {"id": "a", "type": "title", "priority": "high"},
                {"id": "b", "type": "legend"},
            ]),
        )
        assert bad.is_error is True
        assert "must be numeric" in (bad.error_msg or "")

        # None 与数值混用（schema Optional[Number]）也不得 TypeError。
        ok = await engine.apply_mutation(
            session_id,
            SetLayoutIntent(components=[
                {"id": "a", "type": "title", "priority": None},
                {"id": "b", "type": "legend", "priority": 10},
            ]),
        )
        assert ok.is_error is False, ok.error_msg
    finally:
        await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_workbench_state_rejects_dangling_membership_group():
    """membership 的 groupId 必须引用声明组（与 parentId 同门校验）。"""
    engine = MapSpecLifecycleEngine()
    session_id = f"test-wb-dangling-{uuid.uuid4().hex[:8]}"
    try:
        res = await engine.apply_mutation(
            session_id,
            SetWorkbenchStateIntent(doc={
                "version": 5,
                "groups": [{"id": "g1", "parentId": None}],
                "membership": {"layer-a": "gone"},
                "mode": "explore",
            }),
            origin="user", expected_revision=0,
        )
        assert res.is_error is True
        assert "membership" in (res.error_msg or "")
    finally:
        await session_data_manager.clear_session(session_id)
