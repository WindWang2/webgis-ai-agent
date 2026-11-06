"""S1 — AnalysisScope：selection → 受控分析输入（ADR-0285 D1）。

声称（ClaimedScope，纯函数）与验证（ValidatedScope，IO 壳）分离：
- 声称从 IR 投影 + pre-turn 快照纯派生，编译期零 IO；
- 验证链封闭 reason 词表：ref 所有权 → revision 一致 → 要素存活 →
  宿主层在架；失败不猜默认（unusable/stale 分级）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import apply_event
from app.services.gis_situation import analysis_scope
from app.services.gis_situation.analysis_scope import (
    derive_claimed_scope,
    validate_analysis_scope,
)
from tests.data.situation_fakes import FakeMapspecStore, FakeSituationStore


def _fold(events):
    state = LedgerState()
    for e in events:
        apply_event(state, e)
    return state


def _ev(seq, kind, payload, **extra):
    return {
        "event_id": f"c-{seq:08d}", "client_instance_id": "t-aaaa0001",
        "client_seq": seq, "kind": kind, "payload": payload, **extra,
    }


def _snapshot(events):
    from app.services.gis_harness.interaction_ir.projection import (
        project_interaction_snapshot,
    )
    return project_interaction_snapshot(events)


def _geojson_ref_payload() -> Dict[str, Any]:
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "sch-1", "properties": {"name": "A"}},
        {"type": "Feature", "id": "sch-2", "properties": {"name": "B"}},
        {"type": "Feature", "id": "sch-3", "properties": {"name": "C"}},
    ]}}


class ScopeStore(FakeSituationStore):
    """FakeSituationStore + ref 载荷解析（get）——验证链的最小接口面。"""

    def __init__(self, payloads: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._payloads = dict(payloads or {})

    async def get(self, session_id: str, ref_id: str) -> Any:
        return self._payloads.get(ref_id)


def _spec_with_schools_layer() -> Dict[str, Any]:
    return {
        "revision": 7,
        "sources": {"src-1": {"type": "geojson", "url": "ref:geojson-aaa"}},
        "layers": [{"id": "schools", "type": "circle", "source": "src-1",
                    "visible": True}],
    }


# ── ClaimedScope（纯函数）──────────────────────────────────────────────


def test_claimed_scope_from_ir_multi_select():
    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2"],
            "id_field": "OBJECTID",
        }),
    ])
    claimed = derive_claimed_scope(_snapshot(state.events), None)
    assert claimed["kind"] == "feature_ids"
    assert claimed["dataset_ref"] == "ref:geojson-aaa"
    assert claimed["feature_ids"] == ["sch-1", "sch-2"]
    assert claimed["layer_id"] == "schools"
    assert claimed["sources"] == ["ir_ledger"]
    assert claimed["id_field"] == "OBJECTID"


def test_claimed_scope_single_feature_and_pre_turn_fallback():
    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "feature_id": "sch-1",
        }),
    ])
    claimed = derive_claimed_scope(
        _snapshot(state.events), None,
    )
    assert claimed["kind"] == "feature_single"
    assert claimed["feature_ids"] == ["sch-1"]
    # Explicit deselect wins over an older pre-turn snapshot.
    state2 = _fold([_ev(1, "feature_deselected", {})])
    pre_turn = {"layer_id": "schools", "feature_id": "sch-9",
                "dataset_ref": "ref:geojson-aaa"}
    claimed2 = derive_claimed_scope(_snapshot(state2.events), pre_turn)
    assert claimed2["kind"] == "none"
    assert derive_claimed_scope(None, pre_turn)["feature_ids"] == ["sch-9"]
    assert derive_claimed_scope(_snapshot([]), pre_turn)["feature_ids"] == ["sch-9"]


def test_claimed_scope_none_when_no_evidence():
    claimed = derive_claimed_scope(_snapshot([]), None)
    assert claimed["kind"] == "none"
    assert claimed.get("feature_ids") is None


def test_claimed_scope_aoi_from_canvas_affordance_ring():
    ring = [{
        "action_id": "a-1", "kind": "box_select", "seq": 1,
        "geometry": {"type": "Polygon", "coordinates": [
            [[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7], [104.0, 30.6]],
        ]},
        "bbox": [104.0, 30.6, 104.1, 30.7],
    }]
    claimed = analysis_scope.derive_claimed_scope(
        _snapshot([]), None, affordance_ring=ring,
    )
    assert claimed["kind"] == "aoi"
    assert claimed["bbox"] == [104.0, 30.6, 104.1, 30.7]
    assert claimed["geometry"]["type"] == "Polygon"


def test_claimed_scope_feature_ids_prefer_latest_of_ir():
    state = _fold([
        _ev(1, "feature_selected", {"layer_id": "schools", "feature_id": "old"}),
        _ev(2, "feature_selected", {
            "layer_id": "schools", "feature_id": "new",
            "dataset_ref": "ref:geojson-aaa",
        }),
    ])
    claimed = derive_claimed_scope(_snapshot(state.events), None)
    assert claimed["feature_ids"] == ["new"]


# ── ValidatedScope（验证链，IO 壳）─────────────────────────────────────


def _usable_claim() -> Dict[str, Any]:
    return {
        "kind": "feature_ids", "layer_id": "schools",
        "dataset_ref": "ref:geojson-aaa",
        "feature_ids": ["sch-1", "sch-2"],
        "claimed_at_seq": 3, "sources": ["ir_ledger"],
    }


def test_validate_no_selection():
    result = validate_analysis_scope_sync_wrapper("s1", {"kind": "none"})
    assert result["status"] == "unusable"
    assert result["reason"] == "no_selection"


def test_validate_no_dataset_ref():
    claimed = _usable_claim()
    claimed["dataset_ref"] = None
    result = validate_analysis_scope_sync_wrapper("s1", claimed)
    assert result["status"] == "unusable"
    assert result["reason"] == "layer_unavailable"


def test_validate_ref_unresolvable_cross_session():
    store = ScopeStore(payloads={}, map_state={}, descriptors={})
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "unusable"
    assert result["reason"] == "ref_unresolvable"


def test_validate_layer_removed():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {"content_revision": 2, "feature_count": 3}},
    )
    spec = _spec_with_schools_layer()
    spec["layers"] = []  # 宿主层已被移除
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(spec))
    assert result["status"] == "unusable"
    assert result["reason"] == "layer_removed"


def test_validate_revision_drifted():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {"content_revision": 5, "feature_count": 3}},
    )
    spec = _spec_with_schools_layer()
    spec["layers"][0]["provenance"] = {"content_revision": 2}  # 层还钉在旧版
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(spec))
    assert result["status"] == "stale"
    assert result["reason"] == "revision_drifted"


def test_validate_revision_check_unavailable_disclosed():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {"content_revision": 5, "feature_count": 3}},
    )
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["checks"]["revision"] == "unavailable"  # 层无 provenance → 诚实披露


def test_validate_features_liveness():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {"content_revision": 2, "feature_count": 3}},
    )
    claimed = _usable_claim()
    claimed["feature_ids"] = ["sch-1", "sch-x"]  # 一个存活一个缺失
    result = run_validate("s1", claimed, store=store,
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["feature_ids"] == ["sch-1"]
    assert result["checks"]["features"] == "partial_missing"
    assert result["missing_ids"] == ["sch-x"]

    claimed["feature_ids"] = ["sch-x", "sch-y"]  # 全部缺失
    result2 = run_validate("s1", claimed, store=store,
                           mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result2["status"] == "stale"
    assert result2["reason"] == "features_missing"


def test_validate_features_check_skipped_for_large_dataset():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {
            "content_revision": 2, "feature_count": analysis_scope.MAX_LIVENESS_FEATURES + 1,
        }},
    )
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["checks"]["features"] == "skipped_large_dataset"


def test_validate_happy_path_usable():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {
            "content_revision": 2, "feature_count": 3,
            "crs": "EPSG:4326", "bbox": [104.0, 30.6, 104.1, 30.7],
        }},
    )
    result = run_validate("s1", _usable_claim(), store=store,
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["dataset_ref"] == "ref:geojson-aaa"
    assert result["content_revision"] == 2
    assert result["feature_ids"] == ["sch-1", "sch-2"]
    assert result["layer_id"] == "schools"
    assert result["checks"] == {"ownership": "ok", "revision": "unavailable",
                                "features": "ok", "layer": "ok"}
    assert result["crs"] == "EPSG:4326"


def test_validate_aoi_scope_bounded_geometry():
    ring = [{
        "action_id": "a-1", "kind": "box_select", "seq": 1,
        "geometry": {"type": "Polygon", "coordinates": [
            [[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7], [104.0, 30.6]],
        ]},
        "bbox": [104.0, 30.6, 104.1, 30.7],
    }]
    claimed = analysis_scope.derive_claimed_scope(
        _snapshot([]), None, affordance_ring=ring,
    )
    result = run_validate("s1", claimed, store=ScopeStore(),
                          mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["kind"] == "aoi"
    assert result["bbox"] == [104.0, 30.6, 104.1, 30.7]
    # AOI 不需要 dataset_ref/要素存活——检验面按 kind 分支。
    assert result["checks"]["features"] == "not_applicable"


# ── 辅助：async 驱动（asyncio_mode=auto 下也可直接 async def；这里显式）


def run_validate(session_id, claimed, *, store, mapspec_store):
    import asyncio
    return asyncio.run(
        validate_analysis_scope(
            session_id, claimed, store=store, mapspec_store=mapspec_store,
        )
    )


def validate_analysis_scope_sync_wrapper(session_id, claimed):
    import asyncio
    return asyncio.run(validate_analysis_scope(session_id, claimed))


# ── situation fact 接线 + 投影行（ADR-0285 D1）─────────────────────────


@pytest.mark.asyncio
async def test_compile_situation_exposes_claimed_scope_fact():
    from app.services.gis_harness.interaction_ir.ingest import LEDGER_KEY
    from app.services.gis_situation.compiler import compile_situation
    from app.services.gis_situation.projection import render_situation_for_context

    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2", "sch-3", "sch-4", "sch-5"],
        }),
    ])
    store = ScopeStore(map_state={LEDGER_KEY: state.events})
    situation = await compile_situation("s1", turn_id="t1", store=store,
                                        mapspec_store=FakeMapspecStore({}))
    fact = situation.analysis.selection_scope
    assert fact.status == "known"
    assert fact.value["kind"] == "feature_ids"
    assert fact.value["dataset_ref"] == "ref:geojson-aaa"
    # 投影行：有界单行 + ids 截断披露 + 明示「未验证」。
    projection = render_situation_for_context(situation)
    assert "选择 scope(未验证)" in projection.text
    assert "kind=feature_ids" in projection.text
    assert "ids=5[" in projection.text


@pytest.mark.asyncio
async def test_compile_situation_scope_unknown_without_evidence():
    from app.services.gis_situation.compiler import compile_situation

    situation = await compile_situation(
        "s1", store=ScopeStore(), mapspec_store=FakeMapspecStore({}))
    assert situation.analysis.selection_scope.status == "unknown"


def test_review_reason_vocabulary_closed():
    """review P3：SCOPE_REASONS 封闭性 + 工具层补充 reason 显式登记。"""
    from app.services.gis_situation import analysis_scope as mod
    assert mod.SCOPE_REASONS == frozenset({
        "ok", "no_selection", "no_dataset_ref", "ref_unresolvable",
        "layer_removed", "revision_drifted", "features_missing",
        "source_ref_mismatch", "layer_unavailable",
    })


def test_review_dataset_ref_falls_back_to_layer_source():
    """review P2-4：选择不带 dataset_ref 时从 desired spec 层 source 解析。"""
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {"content_revision": 2, "feature_count": 3}},
    )
    spec = {
        "revision": 3,
        "sources": {"src-1": {"type": "geojson", "url": "ref:geojson-aaa"}},
        "layers": [{"id": "schools", "type": "circle", "source": "src-1",
                    "visible": True}],
    }
    claimed = {
        "kind": "feature_single", "layer_id": "schools", "dataset_ref": None,
        "feature_ids": ["sch-1"], "claimed_at_seq": 1, "sources": ["ir_ledger"],
    }
    result = run_validate("s1", claimed, store=store,
                          mapspec_store=FakeMapspecStore(spec))
    assert result["status"] == "usable"
    assert result["dataset_ref"] == "ref:geojson-aaa"
    assert result["feature_ids"] == ["sch-1"]



def test_claimed_ref_must_match_actual_host_source_even_with_matching_ids_and_revision():
    claimed = _usable_claim()
    claimed["dataset_ref"] = "ref:dataset-b"
    store = ScopeStore(payloads={"ref:dataset-b": _geojson_ref_payload()}, descriptors={"ref:dataset-b": {"content_revision": 1}})
    result = run_validate("s1", claimed, store=store, mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "unusable" and result["reason"] == "source_ref_mismatch"


@pytest.mark.parametrize("key", ["ref", "ref_id", "url"])
def test_table_missing_ref_uses_authoritative_source_shape(key):
    spec = _spec_with_schools_layer()
    spec["sources"]["src-1"] = {"type": "geojson", key: "ref:geojson-aaa", "content_revision": 2}
    claimed = _usable_claim()
    claimed["dataset_ref"] = None
    store = ScopeStore(payloads={"ref:geojson-aaa": _geojson_ref_payload()}, descriptors={"ref:geojson-aaa": {"content_revision": 2}})
    result = run_validate("s1", claimed, store=store, mapspec_store=FakeMapspecStore(spec))
    assert result["status"] == "usable" and result["dataset_ref"] == "ref:geojson-aaa"
    assert result["checks"]["revision"] == "ok"


def test_alias_equality_is_resolved_by_the_current_session():
    class Aliased(ScopeStore):
        async def resolve_alias(self, sid, value):
            assert sid == "s1"
            return "ref:geojson-aaa" if value == "ref:alias" else value
    claimed = _usable_claim()
    claimed["dataset_ref"] = "ref:alias"
    store = Aliased(payloads={"ref:geojson-aaa": _geojson_ref_payload()})
    result = run_validate("s1", claimed, store=store, mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable" and result["dataset_ref"] == "ref:geojson-aaa"


def test_current_aoi_deletion_does_not_reuse_an_old_affordance_geometry():
    snapshot = _snapshot(_fold([_ev(1, "aoi_deleted", {})]).events)
    claimed = derive_claimed_scope(snapshot, None, affordance_ring=[{
        "kind": "box_select",
        "geometry": {"type": "Polygon", "coordinates": []}, "bbox": [0, 0, 1, 1],
    }])
    assert claimed["kind"] == "none"


def test_aoi_deletion_remains_current_after_recent_interaction_window_rolls_over():
    events = _fold([_ev(1, "aoi_deleted", {})] + [
        _ev(seq, "comparison_mode_changed", {"enabled": bool(seq % 2)})
        for seq in range(2, 16)
    ]).events
    snapshot = _snapshot(events)
    assert all(event["kind"] != "aoi_deleted" for event in snapshot["recent_interactions"])
    claimed = derive_claimed_scope(snapshot, None, affordance_ring=[{
        "kind": "box_select", "geometry": {"type": "Polygon", "coordinates": []},
        "bbox": [0, 0, 1, 1], "sequence": 7,
    }])
    assert claimed["kind"] == "none"


@pytest.mark.parametrize("kind", ["measure", "highlight", "snap_pick", "widget_reply"])
def test_unrelated_canvas_geometry_is_not_an_analysis_area(kind):
    claimed = derive_claimed_scope(_snapshot([]), None, affordance_ring=[{
        "kind": kind, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "bbox": [0, 0, 1, 1],
    }])
    assert claimed["kind"] == "none"
