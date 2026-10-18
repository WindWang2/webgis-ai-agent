"""Interaction IR → GIS Situation 投影集成（ADR-0225 E/F）。

compile_interaction 的 recent_interactions 优先读 IR ledger（存在时），
旧 ring（/ws 死通道）仅缺省回退；复合 interaction revision 并入 IR
server_seq —— 纯交互变化也推进快照。
"""
from __future__ import annotations

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import LEDGER_KEY, META_KEY, apply_event
from app.services.gis_situation.compiler import (
    _interaction_sequence,
    compile_interaction,
)


def _fold(events):
    state = LedgerState()
    for e in events:
        apply_event(state, e)
    return state


def _ir_events(state) -> list:
    return state.events


def test_compile_prefers_ir_ledger_when_present():
    state = _fold([
        {"event_id": "c-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "feature_selected", "payload": {"layer_id": "roads", "feature_id": "f1"}},
        {"event_id": "c-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "layer_visibility_changed", "payload": {"layer_id": "roads", "visible": False},
         "client_mutation_id": "mut-99999999", "mutation_outcome": "committed"},
    ])
    map_state = {
        LEDGER_KEY: _ir_events(state),
        "_situation_interactions": [
            {"sequence": 1, "kind": "viewport_change", "payload": {"zoom": 9},
             "payload_hash": "legacy"},
        ],
    }
    ctx = compile_interaction(map_state, [])
    fact = ctx.recent_interactions
    assert fact.status == "known"
    assert fact.source == "map_state._interaction_ir_ledger"
    # IR 投影结构（kind + causality 链）而非 legacy 环条目。
    kinds = [entry["kind"] for entry in fact.value]
    assert kinds == ["layer_visibility_changed", "feature_selected"]  # 新→旧


def test_compile_falls_back_to_legacy_ring_without_ledger():
    map_state = {
        "_situation_interactions": [
            {"sequence": 3, "kind": "viewport_change", "payload": {"zoom": 9},
             "payload_hash": "x"},
        ],
    }
    ctx = compile_interaction(map_state, [])
    assert ctx.recent_interactions.source == "map_state._situation_interactions"
    assert ctx.recent_interactions.value[0]["sequence"] == 3


def test_interaction_sequence_includes_ir_server_seq():
    state = _fold([
        {"event_id": "c-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "viewport_settled", "payload": {"center": [1, 2], "zoom": 5}},
        {"event_id": "c-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "feature_selected", "payload": {"layer_id": "l", "feature_id": "f"}},
    ])
    map_state = {
        LEDGER_KEY: state.events,
        META_KEY: {"server_seq": state.server_seq, "dropped": 0},
        "_situation_interactions": [],
    }
    assert _interaction_sequence(map_state) == 2
    # legacy 单独存在时不受影响。
    legacy_only = {"_situation_interactions": [{"sequence": 7}]}
    assert _interaction_sequence(legacy_only) == 7
    # 两者并存取 max。
    both = {
        LEDGER_KEY: state.events,
        META_KEY: {"server_seq": state.server_seq},
        "_situation_interactions": [{"sequence": 7}],
    }
    assert _interaction_sequence(both) == 7
