"""SituationCompiler + diff/快照守卫测试（方向 2 S2/S3，ADR-0180）。

覆盖：有界扇出（单次 map_state 读）、partial source unavailable 降级、
descriptor-first（绝不 resolve payload）、viewport 取数优先级、确定性、
diff 变更分组与 revision 单调守卫、快照只前进。
"""
import uuid

import pytest

from app.services.gis_situation.compiler import compile_situation
from app.services.gis_situation.diff import (
    advance_snapshot,
    diff_situation,
    load_snapshot,
)
from app.services.gis_situation.facts import STATUS_UNAVAILABLE
from tests.data.situation_fakes import (
    FakeMapspecStore,
    FakeSituationStore,
    FailingStore,
)


def _sid() -> str:
    return f"s-{uuid.uuid4().hex[:10]}"


def _spec() -> dict:
    return {
        "layers": [
            {"id": "lyr-heat", "type": "circle", "layout": {"visibility": "visible"},
             "context_role": "base", "source": "src-1"},
            {"id": "lyr-bound", "type": "line", "layout": {"visibility": "none"}},
        ],
        "sources": {
            "src-1": {"type": "geojson", "ref_id": "ref:data-heat",
                      "profile": {"featureCount": 331}},
        },
        "view": {"center": [116.3, 39.9], "zoom": 10.5, "framed": True},
    }


def _full_store(session_id: str) -> FakeSituationStore:
    return FakeSituationStore(
        map_state={
            "_cartographic_mutation_revision": 4,
            "viewport": {"center": [116.4, 39.9], "zoom": 11},
            "base_layer": "OSM 地图",
            "_cartographic_context_observation": {
                "sequence": 2,
                "viewport": {"center": [116.41, 39.91], "zoom": 12},
                "selected_feature": {"layer_id": "lyr-heat", "name": "某区"},
                "focus_layer_id": "lyr-heat",
                "is_3d": False,
            },
            "_cartographic_observation": {
                "sequence": 5, "mapspec_revision": 4,
                "layers": {"lyr-heat": {"mounted": True, "render_complete": True,
                                        "feature_count": 331}},
                "observed_at": "2026-09-13T00:00:00+00:00",
            },
            "_gis_provenance": [
                {"seq": 1, "origin": "agent", "kind": "PatchLayerPresentationIntent",
                 "target": "lyr-heat", "revision": 2, "detail": {}},
                {"seq": 2, "origin": "user", "kind": "PatchLayerPresentationIntent",
                 "target": "lyr-heat", "revision": 3, "detail": {"visible": False}},
            ],
        },
        refs={"ref:data-heat": "人口栅格"},
        event_log=[
            {"event": "tool_executed",
             "data": {"tool": "webgis_map_intent", "status": "started",
                      "command": "热力图"}},
        ],
        descriptors={
            "ref:data-heat": {
                "ref_id": "ref:data-heat", "feature_count": 331,
                "geometry_types": ["Point"], "crs": "EPSG:4326",
                "raster_capable": False, "content_revision": 3,
                "bbox": [115.0, 39.0, 117.0, 41.0],
            },
        },
    )


@pytest.mark.asyncio
async def test_compile_full_source_facts_have_authority():
    session_id = _sid()
    store = _full_store(session_id)
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
        compiled_at="2026-09-13 00:00:00",
    )
    assert situation.identity.revision.mutation_revision == 4
    assert situation.identity.revision.observation_sequence == 5
    # viewport 取 pre-turn 前端快照（非 WS 键），来源如实标注。
    assert situation.geographic.viewport.value["center"] == [116.41, 39.91]
    assert situation.geographic.viewport.source == "_cartographic_context_observation.viewport"
    assert situation.geographic.crs.value == "EPSG:4326"
    assert situation.geographic.scope_name.status in ("known", "unknown")
    # 图层摘要无 payload、可见语义来自 layout。
    layers = situation.map.layers.value
    assert layers[0]["id"] == "lyr-heat" and layers[0]["visible"] is True
    assert layers[1]["visible"] is False
    assert situation.map.layer_count.value == 2
    # role→ref 派生。
    assert situation.data.active_roles.value == {"base": "ref:data-heat"}
    assert situation.data.freshness.value == 3
    # 用户 durable 隐藏决策（provenance 裁决）。
    assert situation.interaction.user_hidden_layers.value == ["lyr-heat"]
    # 选中要素来自活通道（D2 失联修复的证据）。
    assert situation.interaction.selected_feature.value["layer_id"] == "lyr-heat"
    # 进行中后台任务。
    assert situation.interaction.pending_mutations.value[0]["tool"] == "webgis_map_intent"
    # 无未获源。
    assert situation.evidence.sources_unavailable == []


@pytest.mark.asyncio
async def test_compile_is_deterministic_and_single_map_state_read():
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    a = await compile_situation(session_id, store=store, mapspec_store=spec_store,
                                compiled_at="T")
    b = await compile_situation(session_id, store=store, mapspec_store=spec_store,
                                compiled_at="T")
    assert a.to_dict() == b.to_dict()
    # PERF 纪律：整个编译只做一次全量 get_map_state。
    assert store.map_state_reads == 2  # 每次编译各 1 次


@pytest.mark.asyncio
async def test_compile_partial_source_unavailable_degrades_not_fails():
    session_id = _sid()
    store = FailingStore("refs", map_state={"_cartographic_mutation_revision": 1})
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    assert "refs" in situation.evidence.sources_unavailable
    # refs 失败 → 描述符派生事实 unavailable（可归因的降级，不是笼统 unknown）。
    assert situation.data.datasets.status == STATUS_UNAVAILABLE
    # 其余源不受牵连。
    assert situation.map.desired_revision.value == 1


@pytest.mark.asyncio
async def test_compile_map_state_source_failure_flags_unavailable_facts():
    session_id = _sid()
    store = FailingStore("map_state", refs={})
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    assert "map_state" in situation.evidence.sources_unavailable
    # map_state 派生事实全部降级为 unavailable（不是 unknown —— 有明确原因）。
    unavailable_sources = {
        f.source for _c, _n, f in situation.iter_facts()
        if f.status == STATUS_UNAVAILABLE
    }
    assert any("base_layer" in s for s in unavailable_sources)


@pytest.mark.asyncio
async def test_compile_never_resolves_payload(monkeypatch):
    """descriptor-first：store.get（payload 读口）若被调用即失败。"""
    session_id = _sid()
    store = _full_store(session_id)

    async def _forbidden(*a, **k):
        raise AssertionError("compiler must not resolve payloads")

    monkeypatch.setattr(store, "get", _forbidden, raising=False)
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    blob = situation.to_dict()
    assert "ref:data-heat" in str(blob)
    assert "features" not in blob  # 无 FeatureCollection


@pytest.mark.asyncio
async def test_compile_empty_session_all_unknown():
    session_id = _sid()
    situation = await compile_situation(
        session_id, store=FakeSituationStore(), mapspec_store=FakeMapspecStore({}),
    )
    for _ctx, _name, fact in situation.iter_facts():
        assert fact.status in ("unknown", "known")
    assert situation.map.layers.status == "unknown"
    assert situation.evidence.sources_unavailable == []


# ── diff / snapshot（S3）──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_reports_grouped_changes_with_coordinates():
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    s1 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    store._map_state["_cartographic_mutation_revision"] = 5
    store._map_state["base_layer"] = "天地图"
    s2 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    delta = diff_situation(s1, s2)
    assert not delta.regressed
    coords = delta.changed_coordinates()
    assert ("map", "basemap") in coords
    assert ("map", "desired_revision") in coords
    grouped = delta.by_context()
    assert set(grouped) <= {"map", "geographic"}
    assert all(c.kind in ("value_changed", "status_changed", "added", "removed")
               for c in delta.changes)


def test_diff_detects_regressed_revision():
    from tests.test_gis_situation_contract import _sit

    old = _sit("x")
    new = _sit("x")
    new.identity.revision.mutation_revision = old.identity.revision.mutation_revision - 1
    delta = diff_situation(old, new)
    assert delta.regressed
    # regressed 的变更集不可作为增量消费。
    assert delta.changed_coordinates() == set()


def test_diff_first_turn_no_spurious_added_noise():
    from tests.test_gis_situation_contract import _sit

    delta = diff_situation(None, _sit("x"))
    assert delta.changes == []
    assert delta.changed_coordinates() == set()


@pytest.mark.asyncio
async def test_snapshot_only_advances_forward():
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    s1 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    assert await advance_snapshot(session_id, s1, store=store) is True
    stored = await load_snapshot(session_id, store=store)
    assert stored.identity.revision.mutation_revision == 4

    # 人为构造"更旧"的编译（迟到观察）：revision 更低 → 拒收。
    store._map_state["_cartographic_mutation_revision"] = 2
    stale_sit = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    assert await advance_snapshot(session_id, stale_sit, store=store) is False
    stored = await load_snapshot(session_id, store=store)
    assert stored.identity.revision.mutation_revision == 4  # 快照没有倒退


@pytest.mark.asyncio
async def test_snapshot_same_revision_idempotent():
    session_id = _sid()
    store = FakeSituationStore(map_state={})
    spec_store = FakeMapspecStore({})
    s1 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    assert await advance_snapshot(session_id, s1, store=store) is True
    assert await advance_snapshot(session_id, s1, store=store) is True


@pytest.mark.asyncio
async def test_load_snapshot_corrupt_data_returns_none():
    session_id = _sid()
    store = FakeSituationStore(map_state={"_situation_snapshot": {"identity": "garbage"}})
    assert await load_snapshot(session_id, store=store) is None
