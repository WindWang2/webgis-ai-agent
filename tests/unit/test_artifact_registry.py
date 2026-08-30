"""Artifact Runtime（ADR-0082）单元测试：registry / graph / lifecycle / GC。

覆盖：
- 注册 + alias 持久化 round-trip（SessionPlan 同款 envelope 模式）；
- 同 capability 换 ref → 旧产物 superseded + replacement 链；
- ArtifactGraph：consumers / lineage / dependents（实例级血缘）；
- 生命周期巡检：expired（store 探测缺失）/ stale（无活引用）/ valid；
- 孤儿 GC：只删 GC 态且无活引用的 ref（活引用绝不删）；
- 类型推断（capability 输出 > result 形状 > ref 前缀）；
- plan-apply seam：capability/tool/inputs 上下文注册；
- finalizer 集成：MapSpec source ref 过期 → 不再假 complete（review C-2）；
- 有界账本（128 上限，先淘汰 GC 态）。
"""
import shutil
import time
import uuid

import pytest

from app.services.artifact_registry import (
    A_EXPIRED,
    A_STALE,
    A_SUPERSEDED,
    A_VALID,
    MAX_ARTIFACT_RECORDS,
    ArtifactGraph,
    collect_orphan_refs,
    get_artifact,
    infer_artifact_type,
    list_artifacts,
    register_artifact,
    register_tool_artifact,
    sweep_statuses,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"art-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _store_geojson(sid: str, n: int = 3) -> str:
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0 + i, 30.6]},
         "properties": {}}
        for i in range(n)
    ]
    return await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": feats}, prefix="geojson"
    )


# ── 注册与持久化 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_and_roundtrip(clean_session):
    ref = await _store_geojson(clean_session)
    rec = await register_artifact(
        clean_session,
        artifact_id=ref,
        artifact_type="poi_collection",
        producer_capability="poi_query",
        producer_tool="query_local_poi",
        producer_node="poi_query",
        descriptor={"feature_count": 3, "bbox": [104.0, 30.6, 104.2, 30.8], "crs": "EPSG:4326"},
        metadata={"seam": "test"},
    )
    assert rec is not None
    assert rec.status == A_VALID
    assert rec.feature_count == 3 and rec.empty is False
    assert rec.bbox == [104.0, 30.6, 104.2, 30.8]

    loaded = await get_artifact(clean_session, ref)
    assert loaded is not None
    assert loaded.producer_capability == "poi_query"
    assert loaded.artifact_type == "poi_collection"
    assert loaded.expires_at is not None  # Redis TTL 已知时填（best-effort）

    listing = await list_artifacts(clean_session)
    assert [r.artifact_id for r in listing] == [ref]


@pytest.mark.asyncio
async def test_rebind_supersedes_previous(clean_session):
    old_ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=old_ref, producer_capability="poi_query"
    )
    new_ref = await _store_geojson(clean_session)
    rec = await register_artifact(
        clean_session, artifact_id=new_ref, producer_capability="poi_query"
    )
    assert rec.replaces == old_ref

    old = await get_artifact(clean_session, old_ref)
    assert old.status == A_SUPERSEDED
    new = await get_artifact(clean_session, new_ref)
    assert new.status == A_VALID

    graph = ArtifactGraph(
        {r.artifact_id: r for r in await list_artifacts(clean_session)}
    )
    assert graph.replacement_chain(old_ref) == [new_ref]
    assert graph.latest_for_capability("poi_query").artifact_id == new_ref


# ── ArtifactGraph 血缘 ────────────────────────────────────────────────


def test_graph_lineage_and_dependents():
    records = {}
    for aid, cap, inputs in [
        ("ref:geojson-poi", "poi_query", []),
        ("ref:geojson-stats", "admin_aggregate", ["ref:geojson-poi"]),
        ("ref:geojson-density", "density_surface", ["ref:geojson-poi"]),
        ("ref:geojson-combined", "overlay_analysis", ["ref:geojson-stats", "ref:geojson-density"]),
    ]:
        from app.services.artifact_registry import ArtifactRecord

        records[aid] = ArtifactRecord(
            artifact_id=aid, producer_capability=cap, inputs=inputs,
            created_at=time.time(), updated_at=time.time(),
        )
    graph = ArtifactGraph(records)

    assert sorted(graph.consumers("ref:geojson-poi")) == [
        "ref:geojson-density", "ref:geojson-stats",
    ]
    # lineage：overlay 的上游闭包（stats + density + poi）
    assert graph.lineage("ref:geojson-combined") == [
        "ref:geojson-density", "ref:geojson-poi", "ref:geojson-stats",
    ]
    # dependents：poi 的下游闭包
    assert graph.dependents("ref:geojson-poi") == [
        "ref:geojson-combined", "ref:geojson-density", "ref:geojson-stats",
    ]
    assert graph.producers("ref:geojson-stats") == ["ref:geojson-poi"]


# ── 生命周期巡检与 GC ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_marks_expired_and_stale(clean_session):
    live_ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=live_ref, producer_capability="poi_query"
    )
    # 未注册到 store 的 ref → 探测缺失 → expired
    ghost_ref = "ref:geojson-ghost-not-stored"
    await register_artifact(
        clean_session, artifact_id=ghost_ref, producer_capability="density_surface"
    )
    # 活 ref 但章节/spec 都不引用 → stale
    orphan_ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=orphan_ref, producer_capability="admin_boundary"
    )

    chapter = {
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": live_ref}
        ],
        "analysis_steps": [],
    }
    result = await sweep_statuses(clean_session, chapter=chapter, mapspec={})
    assert ghost_ref in result["expired"]
    assert orphan_ref in result["stale"]
    assert live_ref in result["valid"]
    assert (await get_artifact(clean_session, ghost_ref)).status == A_EXPIRED
    assert (await get_artifact(clean_session, orphan_ref)).status == A_STALE
    assert (await get_artifact(clean_session, live_ref)).status == A_VALID


@pytest.mark.asyncio
async def test_gc_deletes_only_orphans(clean_session):
    # superseded 旧 ref（不再被引用）+ 活 ref
    old_ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=old_ref, producer_capability="poi_query"
    )
    new_ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=new_ref, producer_capability="poi_query"
    )
    chapter = {
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": new_ref}
        ],
        "analysis_steps": [],
    }
    deleted = await collect_orphan_refs(clean_session, chapter=chapter, mapspec={})
    assert deleted == [old_ref]
    assert await session_data_manager.get_ref_descriptor(clean_session, old_ref) is None
    # 活 ref 绝不删
    assert await session_data_manager.get_ref_descriptor(clean_session, new_ref) is not None


@pytest.mark.asyncio
async def test_gc_never_deletes_live_binding(clean_session):
    """活引用保护：即使记录态是 stale（巡检滞后），行仍引用的 ref 不删。"""
    ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=ref, producer_capability="poi_query"
    )
    chapter = {
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": ref}
        ],
        "analysis_steps": [],
    }
    # 强行标 stale（模拟巡检滞后）
    from app.services.artifact_registry import mark_status

    await mark_status(clean_session, ref, A_STALE)
    deleted = await collect_orphan_refs(clean_session, chapter=chapter, mapspec={})
    assert deleted == []
    assert await session_data_manager.get_ref_descriptor(clean_session, ref) is not None


# ── 类型推断与 dispatch/chart seam ───────────────────────────────────


def test_infer_artifact_type_priority():
    # capability 输出优先
    assert infer_artifact_type(
        "ref:geojson-x", capability_outputs=["density_surface"]
    ) == "density_surface"
    # result 形状（heatmap_raster）
    assert infer_artifact_type(
        "ref:heatmap-x", result={"type": "heatmap_raster"}
    ) == "density_surface"
    # ref 前缀兜底
    assert infer_artifact_type("ref:geojson-x") == "feature_collection"
    assert infer_artifact_type("ref:chart-x") == "chart_spec"
    assert infer_artifact_type("ref:raster/x") == "raster_surface"


@pytest.mark.asyncio
async def test_register_tool_artifact_dispatch_seam(clean_session):
    ref = await _store_geojson(clean_session)
    rec = await register_tool_artifact(
        clean_session, ref, tool="query_local_poi", result={"type": "FeatureCollection"}
    )
    assert rec is not None
    assert rec.producer_tool == "query_local_poi"
    assert rec.artifact_type == "feature_collection"
    assert rec.metadata.get("seam") == "dispatch"
    # 非 ref 字符串拒绝
    assert await register_tool_artifact(clean_session, "not-a-ref") is None


# ── plan-apply seam ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_apply_seam_registers_with_lineage(clean_session):
    from app.services.session_plan import SessionPlan, _register_plan_artifacts
    from app.services.session_plan import _init_progress

    poi_ref = await _store_geojson(clean_session)
    density_ref = await _store_geojson(clean_session)
    chapter = {
        "plan_id": "p1",
        "query": "成都小学分布",
        "data_requirements": [
            {
                "capability": "poi_query", "status": "available",
                "bound_ref": poi_ref,
            }
        ],
        "analysis_steps": [
            {
                "capability": "density_surface", "status": "done",
                "bound_ref": density_ref,
                "depends_on": ["poi_query"],
            }
        ],
        "map_layers": [],
        "components": [],
        "template_selection": {},
    }
    plan = SessionPlan(
        envelope_id=f"env-{uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal="成都小学分布",
        gis_chapter=chapter,
        progress=_init_progress(chapter),
    )
    await _register_plan_artifacts(
        clean_session, plan, ["density_surface"], "kde_density", density_ref
    )
    rec = await get_artifact(clean_session, density_ref)
    assert rec is not None
    assert rec.producer_capability == "density_surface"
    assert rec.producer_tool == "kde_density"
    # 实例级血缘：inputs 指向 poi 的具体产物 ref（非类型名）
    assert rec.inputs == [poi_ref]
    # capability 输出类型 → artifact_type
    assert rec.artifact_type == "density_surface"


# ── finalizer 集成：source ref 存活校验（review C-2）────────────────


@pytest.mark.asyncio
async def test_dead_source_ref_blocks_complete(clean_session):
    from app.services.gis_harness.map_completion import (
        F_ARTIFACT_EXPIRED,
        STATUS_FAILED,
        run_map_finalization,
    )
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        PatchComponentIntent,
        UpsertLayerIntent,
    )

    dead_ref = "ref:geojson-evicted-by-ttl"
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={
                "id": "poi-main",
                "source": "s-poi-main",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
            },
            source_data={
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                     "properties": {}}
                ],
            },
        ),
    )
    # 直接把 source 的 ref 换成已驱逐的 ref（模拟 TTL 过期后 spec 仍引用；
    # sources 是 {id: def} dict —— 大载荷才落 ref，这里手工注入）
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(clean_session)
    sources = spec.get("sources")
    assert isinstance(sources, dict) and "s-poi-main" in sources
    sources["s-poi-main"]["ref"] = dead_ref
    sources["s-poi-main"]["ref_id"] = dead_ref
    await mapspec_store.save_mapspec(clean_session, spec)
    for comp in ({"id": "title", "type": "title", "position": "top-center"},
                 {"id": "scale-bar", "type": "scale_bar", "position": "bottom-right"}):
        await engine.apply_mutation(
            clean_session,
            PatchComponentIntent(
                component_id=comp["id"], component_type=comp["type"],
                enabled=True, position=comp["position"], upsert=True,
            ),
        )

    live_ref = await _store_geojson(clean_session)
    chapter = {
        "plan_id": "p1",
        "query": "q",
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": live_ref}
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": "poi-main", "enabled": True}],
        "components": [],
        "template_selection": {},
    }
    result = await run_map_finalization(clean_session, chapter=chapter)
    expired = [f for f in result.findings if f.code == F_ARTIFACT_EXPIRED]
    assert expired and expired[0].target == "poi-main"
    assert result.status == STATUS_FAILED


# ── 有界账本 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_bounded(clean_session):
    ids = []
    for i in range(MAX_ARTIFACT_RECORDS + 10):
        ref = await _store_geojson(clean_session, n=1)
        ids.append(ref)
        await register_artifact(
            clean_session,
            artifact_id=ref,
            producer_capability=f"cap-{i}",
            # 前 10 个标 superseded → 优先淘汰
            metadata={"i": i},
        )
        if i < 10:
            from app.services.artifact_registry import mark_status

            await mark_status(clean_session, ref, A_SUPERSEDED)
    records = await list_artifacts(clean_session)
    assert len(records) <= MAX_ARTIFACT_RECORDS
    # superseded 的前 10 个被优先淘汰
    remaining = {r.artifact_id for r in records}
    assert all(a not in remaining for a in ids[:10])


@pytest.mark.asyncio
async def test_gc_with_default_chapter_loads_plan_fresh(clean_session):
    """终审 F3：chapter 缺省 → 实时加载计划 —— 行绑定的 ref 不因
    默认参数被排除在活集合外而遭 GC 误删。"""
    from app.services.artifact_registry import collect_orphan_refs
    from app.services.session_plan import SessionPlan, _init_progress, save_session_plan
    import uuid as _uuid

    ref = await _store_geojson(clean_session)
    await register_artifact(
        clean_session, artifact_id=ref, producer_capability="poi_query"
    )
    chapter = {
        "plan_id": "p1",
        "query": "q",
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": ref}
        ],
        "analysis_steps": [],
        "map_layers": [],
        "components": [],
        "template_selection": {},
    }
    plan = SessionPlan(
        envelope_id=f"env-{_uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal="q",
        gis_chapter=chapter,
        progress=_init_progress(chapter),
    )
    await save_session_plan(plan)
    # chapter=None（默认）→ 必须实时重载，行绑定保护 ref
    deleted = await collect_orphan_refs(clean_session)  # mapspec 缺省同样重载
    assert deleted == []
    assert await session_data_manager.get_ref_descriptor(clean_session, ref) is not None
