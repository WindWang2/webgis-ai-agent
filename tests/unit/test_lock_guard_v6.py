"""Harness V6 Wave 15 锁下沉（Human-Agent 状态收敛）契约测试。

覆盖 E2E Scenario 8（三路径：tool 直调 + repair planner + batch）与
W15 状态收敛面：

- 被锁图层：tool 直调 / repair planner / batch 全部拒绝 + 机器可读披露
  （含 layer_locked token，对齐前端 failed/layer_locked）；
- component 锁同理（lockedComponentIds：缺席=空，非法类型同门校验）；
- unlocked 部分照常执行，披露精确到被锁 id；
- override 三分类 + 来源记录（user/agent）；
- transient（pan/zoom/hover/selection）永不持久。

全确定性，不依赖 LLM 与网络。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.repair_planner import (
    classify_repair,
    plan_repairs,
)
from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.runtime_repair import classify_runtime_repairs
from app.services.gis_world_state.mutation import (
    apply_gis_mutation,
    apply_gis_mutation_batch,
)
from app.services.gis_world_state.provenance import get_provenance
from app.services.mapspec.lifecycle_engine import (
    LOCK_CONFLICT_CODE,
    OVERRIDE_PRESENTATION,
    OVERRIDE_SEMANTIC,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    SetLayoutIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
    classify_override,
    guard_locked_partitions,
    strip_transient_state,
)
from app.services.session_data import session_data_manager


def _sid(tag: str) -> str:
    return f"w15-lock-{tag}-{uuid.uuid4().hex[:6]}"


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [104.0, 30.6]}, "properties": {}}
        ],
    }


def _layer(layer_id: str, *, visible: bool = True):
    return {
        "id": layer_id, "type": "circle", "source": "s-w15",
        "layout": {"visibility": "visible" if visible else "none"},
        "paint": {"circle-color": "#ff0000"},
    }


def _wb_doc(locked_layers=(), locked_components=None):
    doc = {
        "version": 5, "groups": [], "membership": {},
        "mode": "explore", "lockedLayerIds": list(locked_layers),
    }
    if locked_components is not None:
        doc["lockedComponentIds"] = list(locked_components)
    return doc


async def _seed_locked_session(engine, sid, layer_ids,
                               locked_layers=(), locked_components=()):
    """先 agent 建层，再 user 落 workbench 锁（CAS 链；锁引用既有层 id）。"""
    rev = 0
    for lid in layer_ids:
        up = await engine.apply_mutation(
            sid, UpsertLayerIntent(layer=_layer(lid), source_data=_geojson()),
        )
        assert up.is_error is False, up.error_msg
        rev = up.mutation_revision
    res = await engine.apply_mutation(
        sid, SetWorkbenchStateIntent(
            doc=_wb_doc(locked_layers, locked_components)),
        origin="user", expected_revision=rev,
    )
    assert res.is_error is False, res.error_msg
    return sid


def _spec_layer(spec, layer_id):
    assert isinstance(spec, dict)
    for layer in spec.get("layers", []) or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    raise AssertionError(f"layer {layer_id} missing")


# ── 统一 guard 分区单元 ──────────────────────────────────────────────────


def test_guard_partition_and_disclosure():
    mapspec = {"workbench": _wb_doc(["locked-a", "locked-b"])}
    part = guard_locked_partitions(
        mapspec, layer_ids=["locked-a", "free-c", "locked-a"])
    assert part.locked_layer_ids == ["locked-a"]
    assert part.allowed_layer_ids == ["free-c"]
    assert part.has_locked
    d = part.disclosure()
    assert d["code"] == LOCK_CONFLICT_CODE == "layer_locked"
    assert d["locked_layer_ids"] == ["locked-a"]
    assert "layer_locked" in d["message"]
    # 层族双向命中：锁逻辑层拦物理层，锁物理层也拦逻辑层意图。
    fam = guard_locked_partitions(mapspec, layer_ids=["locked-a__fill"])
    assert fam.locked_layer_ids == ["locked-a"]
    fam2 = guard_locked_partitions(
        {"workbench": _wb_doc(["locked-a__fill"])}, layer_ids=["locked-a"])
    assert fam2.locked_layer_ids == ["locked-a__fill"]
    # 无锁 → 全放行。
    empty = guard_locked_partitions({}, layer_ids=["x"])
    assert not empty.has_locked and empty.allowed_layer_ids == ["x"]
    none_spec = guard_locked_partitions(None, layer_ids=["x"])
    assert not none_spec.has_locked


def test_component_lock_partition_and_disclosure():
    mapspec = {"workbench": _wb_doc([], ["legend-main"])}
    part = guard_locked_partitions(
        mapspec, component_ids=["legend-main", "title-main"])
    assert part.locked_component_ids == ["legend-main"]
    assert part.allowed_component_ids == ["title-main"]
    d = part.disclosure()
    # 单码契约（B/Q1）：前端无 component_locked 消费端，组件拒绝复用
    # layer_locked，载荷 locked_component_ids 指明被锁组件。
    assert d["code"] == LOCK_CONFLICT_CODE == "layer_locked"
    assert d["locked_component_ids"] == ["legend-main"]
    assert d["locked_layer_ids"] == []
    assert "layer_locked" in d["message"]
    assert "legend-main" in d["message"]
    # 缺席=空：无 lockedComponentIds 键 → 无组件锁。
    assert not guard_locked_partitions(
        {"workbench": {"version": 5}}, component_ids=["legend-main"]).has_locked


def test_workbench_locked_component_ids_validation():
    from app.services.mapspec.lifecycle_engine import _workbench_doc_error
    assert _workbench_doc_error(_wb_doc(["a"], ["c1"])) is None
    doc_no_key = {"version": 5, "groups": [], "mode": "explore"}
    assert _workbench_doc_error(doc_no_key) is None  # 缺席=空，版本兼容
    bad = {"version": 5, "groups": [], "mode": "explore",
           "lockedComponentIds": [1]}
    err = _workbench_doc_error(bad)
    assert err is not None and "lockedComponentIds" in err  # 同门校验披露


# ── E2E Scenario 8 三路径 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario8_tool_direct_locked_layer_refused():
    """tool 直调：agent 改被锁层 → 拒绝 + layer_locked，spec 不动，未锁层照常。"""
    engine = MapSpecLifecycleEngine()
    sid = _sid("direct")
    await _seed_locked_session(
        engine, sid, ["locked-lyr", "free-lyr"], locked_layers=["locked-lyr"])

    refused = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
        origin="agent", actor="test",
    )
    assert refused.is_error is True
    assert "layer_locked" in refused.error_msg
    assert refused.origin == "agent"
    spec = await engine.store.get_mapspec(sid)
    assert _spec_layer(spec, "locked-lyr")["layout"]["visibility"] == "visible"

    ok = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="free-lyr", visible=False),
        origin="agent", actor="test",
    )
    assert ok.is_error is False
    spec = await engine.store.get_mapspec(sid)
    assert _spec_layer(spec, "free-lyr")["layout"]["visibility"] == "none"
    assert _spec_layer(spec, "locked-lyr")["layout"]["visibility"] == "visible"
    await session_data_manager.clear_session(sid)


def _uf(code, *, entity="", scope="layer"):
    return UnifiedFinding(
        domain="harness_finalizer", code=code, severity="warning", source="t",
        scope=scope, affected_entity=entity, evidence="ev",
        blocks_completion=False,
    )


def test_scenario8_repair_planner_locked_refused():
    """repair planner：被锁实体 → not_allowed（语义不变，底走统一 guard）。"""
    action = classify_repair(
        _uf("layer_hidden", entity="locked-lyr"),
        locked_entities=frozenset({"locked-lyr"}))
    assert action.safety == "not_allowed"
    assert action.executor == "none"
    assert "layer_locked" in action.detail
    assert "user-wins" in action.detail
    plan = plan_repairs(
        [_uf("layer_hidden", entity="locked-lyr"),
         _uf("layer_hidden", entity="free-lyr")],
        locked_entities=frozenset({"locked-lyr"}))
    assert [a.target for a in plan.refused] == ["locked-lyr"]
    assert [a.target for a in plan.actions] == ["free-lyr"]


@pytest.mark.asyncio
async def test_scenario8_batch_partition_locked_refused_rest_applied():
    """batch：被锁 refused（披露精确到被锁 id），未锁照常提交，revision 恰 +1。"""
    engine = MapSpecLifecycleEngine()
    sid = _sid("batch")
    await _seed_locked_session(
        engine, sid, ["locked-lyr", "free-lyr"], locked_layers=["locked-lyr"])
    before = await session_data_manager.get_map_state(sid)
    rev_before = int(before.get("_cartographic_mutation_revision") or 0)

    batch = await apply_gis_mutation_batch(
        sid,
        [PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
         PatchLayerPresentationIntent(layer_id="free-lyr", visible=False)],
        origin="agent", actor="test",
    )
    by_id = {o.layer_id: o for o in batch.outcomes}
    assert by_id["locked-lyr"].status == "refused"
    assert "layer_locked" in (by_id["locked-lyr"].error_msg or "")
    assert "locked-lyr" in (by_id["locked-lyr"].error_msg or "")
    assert by_id["free-lyr"].status == "applied"
    assert batch.committed is True
    assert batch.mutation_revision == rev_before + 1
    spec = await engine.store.get_mapspec(sid)
    assert _spec_layer(spec, "locked-lyr")["layout"]["visibility"] == "visible"
    assert _spec_layer(spec, "free-lyr")["layout"]["visibility"] == "none"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_scenario8_component_lock_refused():
    """component 锁：agent 改被锁组件 → 拒绝 + layer_locked 单码披露."""
    engine = MapSpecLifecycleEngine()
    sid = _sid("comp")
    await _seed_locked_session(
        engine, sid, [], locked_components=["legend-main"])
    refused = await engine.apply_mutation(
        sid, PatchComponentIntent(component_id="legend-main", enabled=False),
    )
    assert refused.is_error is True
    # 单码契约：组件拒绝亦为 layer_locked，载荷 locked_component_ids 区分。
    assert refused.error_code == LOCK_CONFLICT_CODE == "layer_locked"
    assert LOCK_CONFLICT_CODE in refused.error_msg
    assert refused.locked_component_ids == ["legend-main"]
    assert refused.locked_layer_ids == []
    payload = refused.to_dict()
    assert payload["success"] is False
    assert payload["error_code"] == "layer_locked"
    assert payload["locked_component_ids"] == ["legend-main"]
    assert "locked_layer_ids" not in payload  # 空载荷不透出
    await session_data_manager.clear_session(sid)


def test_lock_refusal_error_code_contract():
    """B/M1 契约：拒绝结果的精确码经 error_code + to_dict 原样透出."""
    from app.services.mapspec.lifecycle_engine import guard_intent_locks
    layer_refusal = guard_intent_locks(
        {"workbench": _wb_doc(["locked-lyr"])},
        PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
        origin="agent",
    )
    assert layer_refusal is not None and layer_refusal.is_error is True
    assert layer_refusal.error_code == "layer_locked"
    assert layer_refusal.locked_layer_ids == ["locked-lyr"]
    wire = layer_refusal.to_dict()
    assert wire["success"] is False
    assert wire["error_code"] == "layer_locked"
    assert wire["locked_layer_ids"] == ["locked-lyr"]
    # user 意图不受自有锁约束 → 放行（无拒绝结果）。
    assert guard_intent_locks(
        {"workbench": _wb_doc(["locked-lyr"])},
        PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
        origin="user",
    ) is None


@pytest.mark.asyncio
async def test_user_origin_bypasses_own_lock():
    """用户自己的锁不拦用户操作（用户解锁/操作是唯一 override）。"""
    engine = MapSpecLifecycleEngine()
    sid = _sid("userbypass")
    await _seed_locked_session(
        engine, sid, ["locked-lyr"], locked_layers=["locked-lyr"])
    state = await session_data_manager.get_map_state(sid)
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    ok = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
        origin="user", actor="panel", expected_revision=rev,
    )
    assert ok.is_error is False, ok.error_msg
    spec = await engine.store.get_mapspec(sid)
    assert _spec_layer(spec, "locked-lyr")["layout"]["visibility"] == "none"
    await session_data_manager.clear_session(sid)


# ── override 三分类 + 来源记录 ───────────────────────────────────────────


def test_override_classification_kinds():
    assert classify_override(
        UpsertLayerIntent(layer={"id": "x"}))["kind"] == OVERRIDE_SEMANTIC
    assert classify_override(
        PatchLayerPresentationIntent(layer_id="x"), "user") == {
            "kind": OVERRIDE_PRESENTATION, "source": "user"}
    assert classify_override(SetLayoutIntent())["kind"] == OVERRIDE_PRESENTATION
    assert classify_override(SetViewIntent(), "system") == {
        "kind": OVERRIDE_SEMANTIC, "source": "system"}
    assert classify_override(
        UpsertLayerIntent(layer={"id": "x"}), "agent")["source"] == "agent"


@pytest.mark.asyncio
async def test_override_source_recorded_in_provenance():
    """provenance 记录来源 user/agent + override_kind（既有模式推广）。"""
    engine = MapSpecLifecycleEngine()
    sid = _sid("prov")
    await _seed_locked_session(engine, sid, ["free-lyr"])
    state = await session_data_manager.get_map_state(sid)
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="free-lyr", visible=False),
        origin="user", actor="panel", expected_revision=rev,
    )
    entries = await get_provenance(sid)
    mine = [e for e in entries
            if e.get("kind") == "PatchLayerPresentationIntent"
            and e.get("target") == "free-lyr"]
    assert mine, "user 呈现操作必须进 provenance"
    last = mine[-1]
    assert last["origin"] == "user"
    assert last["detail"].get("override_kind") == OVERRIDE_PRESENTATION
    assert last["detail"].get("visible") is False
    await session_data_manager.clear_session(sid)


# ── transient 永不持久 ─────────────────────────────────────────────────


def test_strip_transient_state_unit():
    dirty = {"version": "1.0", "view": {}, "pan": {"dx": 1}, "zoom": {},
             "hover": "l1", "selection": ["l1"]}
    cleaned = strip_transient_state(dirty)
    assert cleaned == {"version": "1.0", "view": {}}
    assert dirty["hover"] == "l1"  # 输入不动（纯函数）
    assert strip_transient_state(cleaned) is cleaned  # 无键零拷贝
    assert strip_transient_state(None) is None
    assert strip_transient_state("x") == "x"


@pytest.mark.asyncio
async def test_transient_keys_stripped_at_commit_boundary():
    """提交边界剥离瞬态键：脏 spec 经一次 mutation 落盘后不再含 transient。"""
    from app.services.mapspec.lifecycle_engine import TRANSIENT_INTERACTION_KEYS
    engine = MapSpecLifecycleEngine()
    sid = _sid("transient")
    await _seed_locked_session(engine, sid, ["lyr-t"])
    spec = await engine.store.get_mapspec(sid)
    assert isinstance(spec, dict)
    state = await session_data_manager.get_map_state(sid)
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    dirty = dict(spec)
    dirty["hover"] = {"layer_id": "lyr-t"}
    dirty["selection"] = ["lyr-t"]
    saved = await engine.store.save_mapspec(
        sid, dirty, mutation_revision=rev)
    assert saved.get("success", True)
    res = await engine.apply_mutation(
        sid, SetViewIntent(center=[104.0, 30.6], zoom=9.0))
    assert res.is_error is False, res.error_msg
    fresh = await engine.store.get_mapspec(sid)
    assert not (set(fresh.keys()) & set(TRANSIENT_INTERACTION_KEYS)), (
        sorted(set(fresh.keys()) & set(TRANSIENT_INTERACTION_KEYS)))
    assert fresh["view"]["center"] == [104.0, 30.6]  # 语义照常提交
    await session_data_manager.clear_session(sid)


# ── repair 执行面走统一 guard ────────────────────────────────────────────


def _rr_chapter(layer_id="poi-main"):
    return {
        "plan_id": "plan-w15",
        "query": "锁下沉",
        "map_layers": [{"role": "primary", "layer_id": layer_id, "enabled": True,
                        "source_capability": "poi_query"}],
    }


def _rr_mapspec(layer_id="poi-main", *, locked=()):
    return {
        "layers": [{"id": layer_id, "source": "s-poi", "type": "circle",
                    "layout": {"visibility": "visible"}}],
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-a"}},
        "layout": {"components": []},
        "workbench": _wb_doc(locked),
    }


def _rr_observation(revision):
    return {"source": "frontend_runtime", "mapspec_revision": revision,
            "mapspec_fingerprint": "fp-w15", "layers": [], "components": []}


def test_gis_runtime_repair_locked_layer_not_planned():
    """gis runtime repair：被锁层不进修复动作，进 locked_refused + 披露。"""
    plan = classify_runtime_repairs(
        _rr_chapter(), _rr_mapspec(locked=["poi-main"]),
        descriptors={"ref:geojson-a": {"feature_count": 1}},
        observation=_rr_observation(3), current_revision=3,
    )
    assert plan.reassert_layers == []
    assert plan.locked_refused == ["poi-main"]
    assert not plan.has_actions
    disclosures = plan.locked_disclosures()
    assert disclosures[0]["code"] == "layer_locked"
    assert "layer_locked" in disclosures[0]["message"]
    # 对照：无锁 → 照常 reassert。
    free = classify_runtime_repairs(
        _rr_chapter(), _rr_mapspec(),
        descriptors={"ref:geojson-a": {"feature_count": 1}},
        observation=_rr_observation(3), current_revision=3,
    )
    assert free.reassert_layers == ["poi-main"]


def test_lib_runtime_repair_locked_patch_suppressed():
    """cartographic runtime repair：被锁层 patch 被过滤 + locked_refused。"""
    from app.lib.cartography.runtime_repair import plan_runtime_repairs
    mapspec = _rr_mapspec("locked-lyr", locked=["locked-lyr"])
    observation = {"layers": [{"id": "rt-1", "intent_generation": 1,
                               "visible": False}],
                   "mapspec_fingerprint": "fp-w15"}
    cartography = {"checks": [{"status": "fail",
                               "rule": "RUNTIME_RESULT_VISIBILITY",
                               "evidence": {"layer_id": "locked-lyr",
                                            "runtime_layer_id": "rt-1"}}]}
    plan = plan_runtime_repairs(mapspec, observation, cartography)
    assert plan is not None
    assert plan["patches"] == []
    assert plan["locked_refused"][0]["code"] == "layer_locked"
    assert plan["locked_refused"][0]["layer_id"] == "locked-lyr"
    free = plan_runtime_repairs(_rr_mapspec("locked-lyr"), observation,
                                cartography)
    assert len(free["patches"]) == 1 and free["locked_refused"] == []


def test_quality_loop_locked_repair_suppressed():
    """quality_loop：被锁层的 AUTO_SAFE 修复不执行，进 locked_suppressed。"""
    from app.lib.cartography.quality_loop import review_and_repair_cartography
    base_layer = {"id": "locked-lyr", "type": "circle", "source": "s",
                  "layout": {"visibility": "none"},
                  "paint": {"circle-color": "#ff0000"},
                  "cartographic_intent": {"expected_visible": True}}
    base = {"version": "1.0", "view": {}, "sources": {"s": {"type": "geojson"}},
            "layers": [dict(base_layer)],
            "layout": {"legend": {"visible": True, "position": "top-right"},
                       "controls": []},
            "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000}}
    locked = dict(base)
    locked["workbench"] = _wb_doc(["locked-lyr"])
    res = review_and_repair_cartography(dict(locked), max_iterations=2)
    codes = [e["code"] for e in res.locked_suppressed]
    assert "layer_locked" in codes
    assert res.locked_suppressed[0]["layer_id"] == "locked-lyr"
    assert res.mapspec["layers"][0]["layout"]["visibility"] == "none"
    # 对照：无锁 → AUTO_SAFE 照常翻回可见。
    free = review_and_repair_cartography(dict(base), max_iterations=2)
    assert free.locked_suppressed == []
    assert free.mapspec["layers"][0]["layout"]["visibility"] == "visible"


def _family_quality_base(logical_id: str, physical_id: str):
    """B/C1 族变体夹具：物理层 id 挂逻辑层后缀，锁只记逻辑层。"""
    base_layer = {"id": physical_id, "type": "fill", "source": "s",
                  "layout": {"visibility": "none"},
                  "paint": {"fill-color": "#ff0000"},
                  "cartographic_intent": {"expected_visible": True}}
    base = {"version": "1.0", "view": {}, "sources": {"s": {"type": "geojson"}},
            "layers": [dict(base_layer)],
            "layout": {"legend": {"visible": True, "position": "top-right"},
                       "controls": []},
            "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000}}
    locked = dict(base)
    locked["workbench"] = _wb_doc([logical_id])
    return locked


def test_quality_loop_locked_repair_suppressed_family_variant():
    """B/C1：锁逻辑层拦住物理层修复（精确匹配旁路已堵死）。"""
    from app.lib.cartography.quality_loop import review_and_repair_cartography
    res = review_and_repair_cartography(
        _family_quality_base("locked-lyr", "locked-lyr__fill"),
        max_iterations=2)
    assert [e["layer_id"] for e in res.locked_suppressed] == ["locked-lyr__fill"]
    assert res.mapspec["layers"][0]["layout"]["visibility"] == "none"


def test_quality_loop_locked_repair_suppressed_family_reverse():
    """B/C1 反向：锁物理层同样拦住逻辑层修复（双向语义）。"""
    from app.lib.cartography.quality_loop import review_and_repair_cartography
    base_layer = {"id": "locked-lyr", "type": "circle", "source": "s",
                  "layout": {"visibility": "none"},
                  "paint": {"circle-color": "#ff0000"},
                  "cartographic_intent": {"expected_visible": True}}
    base = {"version": "1.0", "view": {}, "sources": {"s": {"type": "geojson"}},
            "layers": [dict(base_layer)],
            "layout": {"legend": {"visible": True, "position": "top-right"},
                       "controls": []},
            "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000}}
    locked = dict(base)
    locked["workbench"] = _wb_doc(["locked-lyr__fill"])
    res = review_and_repair_cartography(dict(locked), max_iterations=2)
    assert [e["layer_id"] for e in res.locked_suppressed] == ["locked-lyr"]
    assert res.mapspec["layers"][0]["layout"]["visibility"] == "none"


def test_lib_runtime_repair_locked_patch_suppressed_family_variant():
    """B/C1：lib runtime repair 族变体 —— 锁逻辑层拦物理层 patch。"""
    from app.lib.cartography.runtime_repair import plan_runtime_repairs
    mapspec = _rr_mapspec("locked-lyr__fill", locked=["locked-lyr"])
    observation = {"layers": [{"id": "rt-1", "intent_generation": 1,
                               "visible": False}],
                   "mapspec_fingerprint": "fp-w15"}
    cartography = {"checks": [{"status": "fail",
                               "rule": "RUNTIME_RESULT_VISIBILITY",
                               "evidence": {"layer_id": "locked-lyr__fill",
                                            "runtime_layer_id": "rt-1"}}]}
    plan = plan_runtime_repairs(mapspec, observation, cartography)
    assert plan is not None
    assert plan["patches"] == []
    assert plan["locked_refused"][0]["layer_id"] == "locked-lyr__fill"
    assert plan["locked_refused"][0]["code"] == "layer_locked"


@pytest.mark.asyncio
async def test_batch_provenance_records_per_intent_overrides():
    """B/Q4：batch provenance 逐 intent 记录 override（批级单 kind 仅兼容）。"""
    engine = MapSpecLifecycleEngine()
    sid = _sid("batchprov")
    await _seed_locked_session(engine, sid, ["free-a", "free-b"])
    batch = await apply_gis_mutation_batch(
        sid,
        [PatchLayerPresentationIntent(layer_id="free-a", visible=False),
         PatchLayerPresentationIntent(layer_id="free-b", visible=False)],
        origin="agent", actor="test",
    )
    assert batch.committed is True
    entries = await get_provenance(sid)
    mine = [e for e in entries if e.get("kind") == "GISMutationBatch"]
    assert mine, "batch 提交必须进 provenance"
    detail = mine[-1].get("detail", {})
    assert detail.get("override_kind") == OVERRIDE_PRESENTATION  # 批级兼容
    per_intent = detail.get("intent_overrides")
    assert per_intent == [
        {"target": "free-a", "override_kind": OVERRIDE_PRESENTATION,
         "source": "agent"},
        {"target": "free-b", "override_kind": OVERRIDE_PRESENTATION,
         "source": "agent"},
    ]
    await session_data_manager.clear_session(sid)
