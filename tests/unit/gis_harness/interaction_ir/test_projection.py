"""Interaction IR V1 投影测试（ADR-0225 F/I）：有界 / 诚实结局 / 风暴合并 /
确定性（无 wall-clock）。"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import apply_event
from app.services.gis_harness.interaction_ir.projection import (
    MAX_RECENT_INTERACTIONS,
    project_interaction_snapshot,
)


def _fold(events, dedup=None):
    state = LedgerState()
    for e in events:
        apply_event(state, e, mutation_dedup=dedup)
    return state


def _viewport(seq, zoom, instance="tab-aaaa0001"):
    return {
        "event_id": f"vp-{seq:08d}", "client_instance_id": instance,
        "client_seq": seq, "kind": "viewport_settled",
        "payload": {"center": [116.0 + seq / 100, 39.9], "zoom": zoom},
    }


def test_pan_zoom_storm_projects_single_settled_fact():
    """500 次 pan/zoom → 投影只保留最后 settled 视口；recent 内 viewport 只 1 条。"""
    state = _fold([_viewport(i, 10 + i / 100) for i in range(1, 501)])
    snap = project_interaction_snapshot(state.events)
    assert snap["viewport"]["zoom"] == pytest.approx(10 + 5.0)
    recent_kinds = [r["kind"] for r in snap["recent_interactions"]]
    assert recent_kinds.count("viewport_settled") == 1
    assert snap["event_count"] <= 128  # 环淘汰
    assert state.dropped == 500 - 128


def test_selection_then_deselect_clears():
    events = [
        {"event_id": "s-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "feature_selected", "payload": {"layer_id": "roads", "feature_id": "f9"}},
        {"event_id": "s-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "feature_deselected", "payload": {}},
    ]
    snap = project_interaction_snapshot(_fold(events).events)
    assert snap["selection"] is None


def test_user_override_only_committed():
    """superseded / failed 的尝试进 attempted_not_applied，绝不进 overrides。"""
    events = [
        {"event_id": "m-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "layer_visibility_changed", "payload": {"layer_id": "roads", "visible": False},
         "client_mutation_id": "mut-11111111", "mutation_outcome": "superseded"},
        {"event_id": "m-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "layer_opacity_changed", "payload": {"layer_id": "roads", "opacity": 0.4},
         "client_mutation_id": "mut-22222222", "mutation_outcome": "failed"},
    ]
    snap = project_interaction_snapshot(_fold(events).events)
    assert snap["user_overrides"] == []
    assert [a["outcome"] for a in snap["attempted_not_applied"]] == ["superseded", "failed"]


def test_committed_override_carries_causality_chain():
    dedup = {"c:mut-33333333": {"revision": 7}}
    events = [
        {"event_id": "m-00000003", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "layer_visibility_changed", "payload": {"layer_id": "roads", "visible": False},
         "client_mutation_id": "mut-33333333", "mutation_outcome": "committed"},
    ]
    snap = project_interaction_snapshot(_fold(events, dedup).events)
    (override,) = snap["user_overrides"]
    assert override["visible"] is False
    # 链条：InteractionEvent → client_mutation_id → 引擎存证 revision。
    assert override["link"]["mutation_verified"] is True
    assert override["link"]["verified_revision"] == 7


def test_layer_removed_clears_override():
    events = [
        {"event_id": "m-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "layer_visibility_changed", "payload": {"layer_id": "roads", "visible": False},
         "client_mutation_id": "mut-11111111", "mutation_outcome": "committed"},
        {"event_id": "m-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "layer_removed", "payload": {"layer_id": "roads"},
         "client_mutation_id": "mut-44444444", "mutation_outcome": "committed"},
    ]
    snap = project_interaction_snapshot(_fold(events).events)
    assert snap["user_overrides"] == []


def test_aoi_lifecycle():
    events = [
        {"event_id": "a-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "aoi_drawn", "payload": {"geometry_type": "Polygon", "vertex_count": 4,
                                          "geometry_digest": "d1"}},
        {"event_id": "a-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "aoi_edited", "payload": {"geometry_type": "Polygon", "vertex_count": 5,
                                           "geometry_digest": "d2"}},
    ]
    snap = project_interaction_snapshot(_fold(events).events)
    assert snap["active_aoi"]["geometry_digest"] == "d2"
    events.append({
        "event_id": "a-00000003", "client_instance_id": "t-aaaa0001", "client_seq": 3,
        "kind": "aoi_deleted", "payload": {},
    })
    snap2 = project_interaction_snapshot(_fold(events).events)
    assert snap2["active_aoi"] is None


def test_recent_interactions_bounded_and_ordered_newest_first():
    events = [
        _viewport(i, 10) for i in range(1, 30)
    ] + [
        {"event_id": f"f-{i:04d}", "client_instance_id": "t-aaaa0001",
         "client_seq": 30 + i, "kind": "feature_selected",
         "payload": {"layer_id": "roads", "feature_id": f"f{i}"}}
        for i in range(1, 15)
    ]
    snap = project_interaction_snapshot(_fold(events).events)
    assert len(snap["recent_interactions"]) == MAX_RECENT_INTERACTIONS
    seqs = [r.get("client_seq") for r in snap["recent_interactions"]]
    assert seqs == sorted(seqs, reverse=True)


def test_projection_is_pure_function_of_ledger():
    events = [_viewport(1, 10)]
    snap1 = project_interaction_snapshot(events)
    snap2 = project_interaction_snapshot(json.loads(json.dumps(events)))
    assert snap1 == snap2
    # created_at 等 advisory 字段不影响投影（确定性：不读时钟）。
    events[0]["created_at"] = "2099-01-01T00:00:00Z"
    assert project_interaction_snapshot(events) == snap1


def test_token_estimate_present_and_bounded():
    state = _fold([_viewport(i, 10 + i) for i in range(1, 140)])
    snap = project_interaction_snapshot(state.events)
    assert snap["token_estimate"] > 0
    serialized = json.dumps(snap, ensure_ascii=False)
    assert len(serialized) < 32 * 1024  # 有界：投影远小于 ledger 本体
