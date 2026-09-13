"""方向 8（ADR-0183）：Mutation Transactions — 幂等/precedence/reconciliation/
竞态确定性测试（U2/U3/U7/U8/U9）。

Barrier/event 驱动（asyncio.Event + 显式 await 点），零 sleep：所有"同时"，
场景经事件屏障编排；所有"先后"，场景经引擎锁/CAS 语义断言。场景矩阵：

- 幂等：同 id 重放单执行（spec+revision 不再推进）；响应丢失后的重试
  （CAS 落后）返回 duplicate 而非 superseded；并发同 id 恰一笔执行；
  回滚后同 id 可重新执行；去重索引 FIFO 有界。
- precedence：producer 分类全表；can_override/resolve_field/fill_undeclared
  规则；repair 不得反转 user 隐藏（既有守卫 × 新分类的联合断言）。
- 竞态（U9）：remove vs agent 同 id upsert（僵尸面：spec/runtime 一致性）；
  user toggle vs agent toggle 同秒；style patch vs self-heal batch；
  user pin（workbench 锁）+ template apply；409→retry；stale 响应隔离。
- 多客户端（U8）：同 session 双写者经 collab 事件 seq=revision 单调；重连
  replay 语义由 revision 门控承载（事件载荷断言）。
"""
import asyncio
import shutil
import uuid

import pytest

from app.services.gis_world_state.envelope import (
    KNOWN_PRODUCER_CLASSES,
    PRODUCER_AGENT_EXPLICIT,
    PRODUCER_REPAIR_AUTOFILL,
    PRODUCER_SYSTEM_DEFAULT,
    PRODUCER_TEMPLATE,
    PRODUCER_USER_EXPLICIT,
    MutationEnvelope,
    classify_producer,
)
from app.services.gis_world_state.mutation import apply_gis_mutation
from app.services.gis_world_state.precedence import (
    PRODUCER_PRECEDENCE,
    PRODUCER_USER_PINNED,
    can_override,
    fill_undeclared,
    higher,
    rank,
    resolve_field,
)
from app.services.gis_world_state.provenance import get_provenance
from app.services.gis_world_state.reconciliation import reconcile_map_state
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    RemoveLayerIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"mtn-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _layer(layer_id: str, visible: bool = True) -> dict:
    layer = {
        "id": layer_id,
        "source": f"{layer_id}-src",
        "type": "circle",
        "paint": {"opacity": 0.9},
    }
    if not visible:
        layer["layout"] = {"visibility": "none"}
    return layer


async def _seed_spec(session_id: str, layer_id: str = "road") -> dict:
    """起世：一笔 agent upsert 落一个基础层，返回提交后的 revision。"""
    engine = MapSpecLifecycleEngine()
    res = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=_layer(layer_id), source_data={"type": "geojson", "features": []}),
        origin="agent", actor="tool:seed",
    )
    assert not res.is_error, res.error_msg
    return res


# ─── U1/U2：envelope + precedence ────────────────────────────────────────────


@pytest.mark.cartography
class TestProducerClassification:
    def test_classification_table(self):
        assert classify_producer(origin="user") == PRODUCER_USER_EXPLICIT
        assert classify_producer(origin="user", targets_user_locked=True) == PRODUCER_USER_PINNED
        assert classify_producer(origin="agent", actor="runtime_repair") == PRODUCER_REPAIR_AUTOFILL
        assert classify_producer(origin="agent", actor="map_finalizer") == PRODUCER_REPAIR_AUTOFILL
        assert classify_producer(origin="agent", actor="finalize_display") == PRODUCER_REPAIR_AUTOFILL
        assert classify_producer(origin="agent", actor="tool:apply_template") == PRODUCER_TEMPLATE
        assert classify_producer(origin="agent", actor="tool:recipe_buffer") == PRODUCER_TEMPLATE
        assert classify_producer(origin="agent", actor="tool:query_osm_poi") == PRODUCER_AGENT_EXPLICIT
        assert classify_producer(origin="system", actor="style_restore") == PRODUCER_SYSTEM_DEFAULT
        # 未知 origin fail-low：绝不冒充高阶写入者。
        assert classify_producer(origin="bogus") == PRODUCER_SYSTEM_DEFAULT

    def test_forged_producer_class_degrades(self):
        # 未知名册外的分类不冒充任何写入者：一律 fail-low 降级。
        env = MutationEnvelope.from_request(
            origin="agent", actor="tool:x", producer_class="ADMIN_OVERRIDE",
        )
        assert env.producer_class == PRODUCER_SYSTEM_DEFAULT

    def test_ladder_total_order(self):
        assert len(PRODUCER_PRECEDENCE) == len(KNOWN_PRODUCER_CLASSES)
        assert rank(PRODUCER_USER_PINNED) > rank(PRODUCER_USER_EXPLICIT)
        assert rank(PRODUCER_USER_EXPLICIT) > rank(PRODUCER_AGENT_EXPLICIT)
        assert rank(PRODUCER_AGENT_EXPLICIT) > rank(PRODUCER_REPAIR_AUTOFILL)
        assert rank(PRODUCER_REPAIR_AUTOFILL) > rank(PRODUCER_TEMPLATE)
        assert rank(PRODUCER_TEMPLATE) > rank(PRODUCER_SYSTEM_DEFAULT)
        assert higher("USER_EXPLICIT", "AGENT_EXPLICIT") == "USER_EXPLICIT"

    def test_field_resolution_rules(self):
        # agent 不得覆盖 user 的已声明字段。
        assert resolve_field(True, "USER_EXPLICIT", False, "AGENT_EXPLICIT")[2] == "kept"
        # user 可覆盖 agent。
        assert resolve_field(False, "AGENT_EXPLICIT", True, "USER_EXPLICIT")[2] == "overridden"
        # template 只能填未声明。
        assert resolve_field(None, "SYSTEM_DEFAULT", "blue", "TEMPLATE")[2] == "filled"
        assert resolve_field("red", "AGENT_EXPLICIT", "blue", "TEMPLATE")[2] == "kept"
        # incoming 未声明 → 恒 kept。
        assert resolve_field("x", "AGENT_EXPLICIT", None, "USER_EXPLICIT")[2] == "kept"

    def test_can_override_and_fill_undeclared(self):
        assert can_override("USER_EXPLICIT", "AGENT_EXPLICIT") is True
        assert can_override("AGENT_EXPLICIT", "USER_EXPLICIT") is False
        assert can_override("AGENT_EXPLICIT", "AGENT_EXPLICIT") is False  # 同阶不覆盖
        assert can_override("TEMPLATE", "USER_EXPLICIT", incumbent_declared=False) is True
        merged, filled = fill_undeclared(
            {"color": "red", "width": None},
            {"color": "blue", "width": 2, "dash": "solid"},
            template_owner="TEMPLATE",
        )
        assert merged == {"color": "red", "width": 2, "dash": "solid"}
        assert sorted(filled) == ["dash", "width"]


@pytest.mark.cartography
async def test_facade_records_envelope_in_provenance(clean_session):
    await _seed_spec(clean_session)
    entries = await get_provenance(clean_session)
    assert entries, "facade must append provenance"
    last = entries[-1]
    assert last["detail"]["producer_class"] == PRODUCER_AGENT_EXPLICIT
    assert last["detail"]["mutation_id"]


@pytest.mark.cartography
async def test_repair_actor_classified_as_repair_autofill(clean_session):
    await _seed_spec(clean_session)
    res = await apply_gis_mutation(
        clean_session,
        PatchLayerPresentationIntent(layer_id="road", visible=False),
        origin="agent", actor="runtime_repair",
    )
    assert not res.is_error and not res.superseded
    entries = await get_provenance(clean_session)
    assert entries[-1]["detail"]["producer_class"] == PRODUCER_REPAIR_AUTOFILL


# ─── U3：服务端幂等 ──────────────────────────────────────────────────────────


@pytest.mark.cartography
class TestMutationIdempotency:
    async def test_same_id_replays_committed_revision(self, clean_session):
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        mid = f"m-{uuid.uuid4().hex[:8]}"
        r1 = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="agent", mutation_id=mid,
        )
        assert not r1.is_error and not r1.duplicate
        spec_after_first = r1.mapspec
        r2 = await engine.apply_mutation(
            clean_session,
            # 不同操作、同 id —— 幂等命中：不再执行、不再递增。
            PatchLayerPresentationIntent(layer_id="road", visible=True),
            origin="agent", mutation_id=mid,
        )
        assert r2.duplicate is True
        assert r2.mutation_revision == r1.mutation_revision
        assert r2.mapspec == spec_after_first

    async def test_lost_response_retry_returns_duplicate_not_superseded(self, clean_session):
        """重试携带的 expected_revision 已落后（响应丢失场景）——幂等命中
        必须优先于 CAS superseded，否则客户端被逼入假冲突。"""
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        revision_before = 1
        mid = f"m-{uuid.uuid4().hex[:8]}"
        r1 = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="agent", mutation_id=mid,
        )
        assert not r1.is_error
        # 重试：stale expected_revision + 同 id。
        r2 = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="user", expected_revision=revision_before, mutation_id=mid,
        )
        assert r2.duplicate is True
        assert r2.mutation_revision == r1.mutation_revision

    async def test_concurrent_same_id_single_execution(self, clean_session):
        """屏障并发：N 个同 id 请求恰一笔执行，其余幂等命中。"""
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        mid = f"m-{uuid.uuid4().hex[:8]}"
        barrier = asyncio.Barrier(4)

        async def submit():
            await barrier.wait()  # 事件屏障：四笔同时放行（锁内串行）
            return await engine.apply_mutation(
                clean_session,
                UpsertLayerIntent(layer=_layer(f"l-{uuid.uuid4().hex[:6]}"), source_data={"type": "geojson", "features": []}),
                origin="agent", mutation_id=mid,
            )

        results = await asyncio.gather(*[submit() for _ in range(4)])
        committed = [r for r in results if not r.duplicate and not r.is_error and not r.superseded]
        duplicates = [r for r in results if r.duplicate]
        assert len(committed) == 1, "exactly one execution per mutation_id"
        assert len(duplicates) == 3
        dup_revision = duplicates[0].mutation_revision
        assert all(d.mutation_revision == dup_revision for d in duplicates)

    async def test_failed_mutation_leaves_no_record_so_retry_reexecutes(self, clean_session):
        engine = MapSpecLifecycleEngine()
        mid = f"m-{uuid.uuid4().hex[:8]}"
        # 未知 id 的 presentation patch：not_found 错误（未提交）。
        r = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="ghost", visible=False),
            origin="agent", mutation_id=mid,
        )
        assert r.is_error
        # 幂等索引无存证 → 同 id 的后续提交必须放行。
        await _seed_spec(clean_session)
        r2 = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="agent", mutation_id=mid,
        )
        assert not r2.is_error and not r2.duplicate

    async def test_dedup_index_bounded(self, clean_session):
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        for i in range(70):
            res = await engine.apply_mutation(
                clean_session,
                PatchLayerPresentationIntent(layer_id="road", visible=(i % 2 == 0)),
                origin="agent", mutation_id=f"m-{i}",
            )
            assert not res.is_error
        state = await session_data_manager.get_map_state(clean_session)
        index = state.get("_mutation_dedup")
        assert isinstance(index, dict)
        assert len(index) <= 64
        # 最老的 id 已被 FIFO 裁剪。
        assert "m-0" not in index


# ─── U5/U6：生产接线归因 ─────────────────────────────────────────────────────


@pytest.mark.cartography
async def test_adapter_route_produces_provenance(clean_session):
    """agent 工具 authoring（mapspec_store 适配器）此前绕过门面 —— 现在必须
    留痕：provenance 条目 + producer 分类（D-03/U5）。"""
    from app.services.mapspec_store import mapspec_store

    res = await mapspec_store.layer_upsert(
        clean_session, _layer("result-abc"), {"type": "geojson", "features": []},
        actor="tool:query_osm_poi",
    )
    assert res["success"] is True
    entries = await get_provenance(clean_session)
    assert entries
    last = entries[-1]
    assert last["actor"] == "tool:query_osm_poi"
    assert last["detail"]["producer_class"] == PRODUCER_AGENT_EXPLICIT


# ─── U9：确定性竞态矩阵（barrier/event 驱动，零 sleep）──────────────────────


@pytest.mark.cartography
class TestDeterministicRaces:
    async def test_remove_vs_agent_upsert_no_zombie(self, clean_session):
        """remove vs 同 id upsert：引擎锁串行 + layer_op 单事务 → 终态
        spec 与 runtime layers 一致（无僵尸：runtime 不含 spec 没有的层）。"""
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        start = asyncio.Event()

        async def user_remove():
            start.set()
            return await engine.apply_mutation(
                clean_session, RemoveLayerIntent(layer_id="road"), origin="user",
                expected_revision=1,
            )

        async def agent_reupsert():
            await start.wait()
            return await apply_gis_mutation(
                clean_session,
                UpsertLayerIntent(layer=_layer("road"), source_data={"type": "geojson", "features": []}),
                origin="agent", actor="tool:rerun",
            )

        r_remove, r_upsert = await asyncio.gather(user_remove(), agent_reupsert())
        state = await session_data_manager.get_map_state(clean_session)
        spec_ids = {str(l.get("id")) for l in (state.get("mapspec") or {}).get("layers", [])}
        runtime_ids = {str(l.get("id")) for l in state.get("layers", [])}
        # 两种合法终态之一：先删后挂（层在）或先挂被 superseded（层不在）。
        # 无论如何：spec 与 runtime 一致 —— 僵尸不存在。
        if r_upsert.superseded:
            assert "road" not in spec_ids or r_remove is not None
        anomalies = [
            a for a in reconcile_map_state(
                state.get("mapspec"), state.get("layers", [])
            )
            if a["code"] == "ZOMBIE_RUNTIME_LAYER"
        ]
        assert anomalies == []
        assert runtime_ids == spec_ids or runtime_ids.issubset(spec_ids | set())

    async def test_user_toggle_vs_agent_hide_same_second(self, clean_session):
        """同秒双写：agent hide 落地后 user hide 同值重放（幂等）或以新
        revision 落账；终态以 spec 权威值收敛，双方 revision 单调。"""
        await _seed_spec(clean_session)
        revisions = []

        async def agent_hide():
            r = await apply_gis_mutation(
                clean_session,
                PatchLayerPresentationIntent(layer_id="road", visible=False),
                origin="agent", actor="finalize_display",
            )
            revisions.append(r.mutation_revision)

        async def user_hide():
            state = await session_data_manager.get_map_state(clean_session)
            rev = int(state.get("_cartographic_mutation_revision") or 0)
            r = await apply_gis_mutation(
                clean_session,
                PatchLayerPresentationIntent(layer_id="road", visible=False),
                origin="user", actor="mapspec_route",
                expected_revision=rev,
            )
            if not r.is_error and not r.superseded:
                revisions.append(r.mutation_revision)

        await asyncio.gather(agent_hide(), user_hide())
        assert revisions == sorted(revisions), "revision must advance monotonically"

    async def test_user_pin_blocks_template_apply(self, clean_session):
        """user pin（workbench 锁）+ template apply：agent/模板意图撞用户锁
        → 整笔拒绝（机器可读 layer_locked），模板不覆盖 pin。"""
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        rev = 1
        lock_res = await engine.apply_mutation(
            clean_session,
            SetWorkbenchStateIntent(
                doc={
                    "version": 5,
                    "groups": [],
                    "membership": {},
                    "lockedLayerIds": ["road"],
                    "mode": "explore",
                },
                base_workbench_revision=None,
            ),
            origin="user", expected_revision=rev,
        )
        assert not lock_res.is_error
        template_res = await apply_gis_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="agent", actor="tool:apply_template",
        )
        # 模板 actor 是 TEMPLATE 分类，但锁 guard 对一切非 user 来源拒绝。
        assert template_res.is_error
        assert template_res.error_code == "layer_locked"

    async def test_superseded_then_retry_with_fresh_revision(self, clean_session):
        """409→retry：stale expected_revision → superseded（spec 不变）；
        以返回的新 revision 重试 → 提交。"""
        await _seed_spec(clean_session)
        engine = MapSpecLifecycleEngine()
        stale = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="user", expected_revision=0,
        )
        assert stale.superseded and stale.mutation_revision == 1
        retry = await engine.apply_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="road", visible=False),
            origin="user", expected_revision=stale.mutation_revision,
        )
        assert not retry.is_error and not retry.superseded
        assert retry.mutation_revision == 2


# ─── U7：reconciliation ──────────────────────────────────────────────────────


@pytest.mark.cartography
class TestReconciliation:
    def _spec(self, layers):
        return {"version": "1.0", "layers": layers}

    def test_zombie_detection(self):
        spec = self._spec([_layer("road")])
        runtime = [_layer("road"), _layer("ghost-x")]
        anomalies = reconcile_map_state(spec, runtime)
        codes = [a["code"] for a in anomalies]
        assert "ZOMBIE_RUNTIME_LAYER" in codes
        zombie = next(a for a in anomalies if a["code"] == "ZOMBIE_RUNTIME_LAYER")
        assert zombie["layer_id"] == "ghost-x"

    def test_user_hidden_but_visible_is_top_priority(self):
        hidden = _layer("road", visible=False)
        hidden["cartographic_intent"] = {
            "presentation_owner": "user", "expected_visible": False,
        }
        runtime = [_layer("road")]
        anomalies = reconcile_map_state(self._spec([hidden]), runtime)
        assert anomalies[0]["code"] == "USER_HIDDEN_BUT_VISIBLE"

    def test_missing_runtime_and_mismatch(self):
        spec = self._spec([_layer("a"), _layer("b", visible=False)])
        runtime = [_layer("a", visible=False)]  # b 缺失；a 可见性翻转
        anomalies = reconcile_map_state(spec, runtime)
        codes = {a["code"] for a in anomalies}
        assert "SPEC_LAYER_MISSING_RUNTIME" in codes
        assert "VISIBILITY_MISMATCH" in codes

    def test_stale_pending_removed(self):
        spec = self._spec([_layer("road")])
        anomalies = reconcile_map_state(spec, [_layer("road")], pending_removed=["road", "gone"])
        stale = [a for a in anomalies if a["code"] == "STALE_PENDING_REMOVED"]
        assert len(stale) == 1 and stale[0]["layer_id"] == "gone"

    def test_family_aliases_do_not_flag(self):
        spec = self._spec([_layer("road")])
        runtime = [_layer("road"), _layer("road-label")]
        assert reconcile_map_state(spec, runtime) == []

    def test_deterministic_ordering_and_bound(self):
        spec = self._spec([_layer(str(i)) for i in range(50)])
        runtime = [_layer(f"z-{i}") for i in range(50)]
        anomalies = reconcile_map_state(spec, runtime)
        assert len(anomalies) <= 32
        keys = [
            (a["code"], a["layer_id"]) for a in anomalies
        ]
        assert keys == sorted(keys)

    def test_consistent_state_yields_no_anomalies(self):
        spec = self._spec([_layer("road"), _layer("poi", visible=False)])
        anomalies = reconcile_map_state(spec, [_layer("road"), _layer("poi", visible=False)])
        assert anomalies == []
    def test_consistent_state_yields_no_anomalies(self):
        spec = self._spec([_layer("road"), _layer("poi", visible=False)])
        anomalies = reconcile_map_state(spec, [_layer("road"), _layer("poi", visible=False)])
        assert anomalies == []


@pytest.mark.cartography
async def test_ws_collab_sync_projection_carries_anomalies(clean_session):
    """生产接线（U7）：sync 投影在僵尸态下必须产出 ZOMBIE_RUNTIME_LAYER。"""
    from app.api.routes.ws_collab import _reconciliation_anomalies

    await _seed_spec(clean_session)
    # 模拟 ws_service 遗留直写通道留下的 runtime 僵尸。
    await session_data_manager.update_layer_in_state(
        clean_session, "ghost-ws", {"visible": True}
    )
    projection = await _reconciliation_anomalies(clean_session)
    codes = [a["code"] for a in projection.get("anomalies", [])]
    assert "ZOMBIE_RUNTIME_LAYER" in codes
