"""Descriptor facts for local knowledge, planning, and bounded data tools.

Local session-store access follows the existing composition/temporal tool
convention: network describes an external provider dependency, not the store's
optional Redis implementation. Mutating evidence/plan stores are not pure reads.
"""

import json

import pytest

from app.services.gis_harness.knowledge_tools import register_knowledge_tools
from app.tools.data_preparation_tools import register_data_preparation_tools
from app.tools.descriptor import SideEffectClass
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.flow_tools import register_flow_tools
from app.tools.layer_browse_tools import register_layer_browse_tools
from app.tools.registry import ToolRegistry
from app.tools.scene_tools import register_scene_tools
from app.tools.skill_library_tools import register_skill_library_tools


@pytest.fixture(scope="module")
def registry():
    registry = ToolRegistry()
    for register in (
        register_knowledge_tools,
        register_skill_library_tools,
        register_feature_edit_tools,
        register_flow_tools,
        register_scene_tools,
        register_layer_browse_tools,
        register_data_preparation_tools,
    ):
        register(registry)
    return registry


@pytest.mark.parametrize(
    "name,side_effect,deterministic,result_size_policy",
    [
        ("gis_task_classify", "pure", True, "bounded"),
        ("gis_method_qualify", "pure", True, "bounded"),
        ("gis_method_rank", "pure", True, "bounded"),
        ("gis_method_explain", "pure", True, "bounded"),
        ("gis_template_plan", "pure", True, "bounded"),
        ("gis_component_query", "pure", True, "bounded"),
        ("gis_skill_search", "state_mutation", True, "bounded"),
        ("gis_skill_detail", "pure", True, "bounded"),
        ("gis_skill_replay_check", "pure", True, "bounded"),
        ("gis_skill_policy", "state_mutation", True, "bounded"),
        ("vector_feature_edit_commit", "state_mutation", False, "unknown"),
        ("vector_feature_edit_history", "cacheable_read", False, "unknown"),
        ("od_flow_edges", "deterministic_compute", True, "unknown"),
        ("webgis_layer_features_page", "cacheable_read", True, "inline_small"),
        ("webgis_layer_aggregate", "cacheable_read", True, "inline_small"),
        ("webgis_layer_feature_detail", "cacheable_read", True, "inline_small"),
        ("prepare_dataset", "artifact_creation", False, "ref_offload"),
    ],
)
def test_local_tool_descriptor_matches_implementation(
    registry, name, side_effect, deterministic, result_size_policy,
):
    descriptor = registry.descriptor(name)
    assert descriptor.network is False
    assert descriptor.side_effect == SideEffectClass(side_effect)
    assert descriptor.deterministic is deterministic
    assert descriptor.result_size_policy == result_size_policy


@pytest.mark.parametrize(
    "name",
    ["gis_skill_search", "gis_skill_policy", "vector_feature_edit_preview",
     "vector_feature_edit_commit"],
)
def test_store_writes_are_not_cached_or_blindly_retried(registry, name):
    from app.services.chat.pi_surface_receipt import qualify_surface
    from app.services.chat.pi_tool_spec import compile_tool_spec

    descriptor = registry.descriptor(name)
    assert descriptor.side_effect == SideEffectClass.STATE_MUTATION
    assert descriptor.cacheable is False
    assert descriptor.retry_safe is False
    spec = compile_tool_spec(registry, name)
    assert spec is not None
    assert spec.side_effect == "state_mutation"
    assert spec.idempotent is None
    # Local writes remain eligible offline; commit's existing policy gate still
    # decides whether a plan may execute (covered by feature_edit/test_tools.py).
    assert qualify_surface([spec], situation={"offline": True}) == ((name,), ())


async def test_preview_creates_distinct_stored_plans(registry, monkeypatch):
    from app.services.feature_edit import executor, policy, service
    from app.services.session_data import MemorySessionStore
    from app.tools import feature_edit_tools

    store = MemorySessionStore()
    for module in (executor, policy, service, feature_edit_tools):
        monkeypatch.setattr(module, "session_data_manager", store)
    session_id = "metadata-preview"
    payload = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "id": "school-1",
            "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
            "properties": {"capacity": 500},
        }],
    }
    ref = await store.store(session_id, payload, prefix="data")
    args = {"ref_id": ref, "feature_ids": ["school-1"],
            "ops": [{"op": "set_fields", "fields": {"capacity": 600}}]}
    first = await registry.dispatch(
        "vector_feature_edit_preview", args, session_id=session_id)
    second = await registry.dispatch(
        "vector_feature_edit_preview", args, session_id=session_id)
    assert first["success"] is True and second["success"] is True
    assert first["plan_id"] != second["plan_id"]
    stored = await executor.load_plans(session_id)
    assert {first["plan_id"], second["plan_id"]} <= set(stored)
    assert await store.get(session_id, ref) == payload
    descriptor = registry.descriptor("vector_feature_edit_preview")
    assert descriptor.network is False
    assert descriptor.deterministic is False
    # Target counts are capped, but field diffs/findings are not fully bounded.
    assert descriptor.result_size_policy == "unknown"


@pytest.mark.parametrize("name", ["gis_skill_search", "gis_skill_policy"])
async def test_skill_output_is_repeatable_but_records_each_call(registry, name):
    from app.tools.skill_library_tools import (
        get_skill_evidence_recorder,
        reset_skill_evidence_recorder,
    )

    reset_skill_evidence_recorder()
    try:
        args = {"query": "成都小学分布情况"}
        first = await registry.dispatch(name, args)
        recorder = get_skill_evidence_recorder()
        count = len(recorder.records)
        assert count > 0
        second = await registry.dispatch(name, args)
        assert first == second
        assert len(recorder.records) == 2 * count
        assert registry.descriptor(name).cacheable is False
    finally:
        reset_skill_evidence_recorder()


async def test_history_output_honors_hard_entry_cap(registry, monkeypatch):
    from app.services.feature_edit import executor
    from app.services.feature_edit.contracts import JOURNALS_STATE_KEY
    from app.services.session_data import MemorySessionStore
    from app.tools import feature_edit_tools

    store = MemorySessionStore()
    monkeypatch.setattr(executor, "session_data_manager", store)
    monkeypatch.setattr(feature_edit_tools, "session_data_manager", store)
    session_id = "metadata-history"
    ref = await store.store(session_id, {"type": "FeatureCollection",
                                         "features": []}, prefix="data")
    await store.set_map_state_fields(session_id, {
        JOURNALS_STATE_KEY: {ref: {
            "head_seq": 75,
            "entries": [{"seq": i, "deltas": []} for i in range(1, 76)],
        }},
    })
    result = await registry.dispatch(
        "vector_feature_edit_history", {"ref_id": ref, "limit": 10000},
        session_id=session_id,
    )
    assert result["success"] is True
    assert len(result["entries"]) == 50
    assert result["entries"][0]["seq"] == 75


async def test_scene_plan_does_not_claim_small_output_for_unbounded_source(registry):
    terrain_source = "x" * 100_000
    result = await registry.dispatch("plan_map_scene", {
        "has_elevation_evidence": True,
        "terrain_intent": True,
        "terrain_source": terrain_source,
    })
    assert result["success"] is True
    assert result["suggested_scene"]["terrain"]["source"] == terrain_source
    assert registry.descriptor("plan_map_scene").result_size_policy == "unknown"


async def test_od_edge_count_cap_does_not_imply_a_byte_bound(registry, monkeypatch):
    from app.services import session_data

    store = session_data.MemorySessionStore()
    monkeypatch.setattr(session_data, "session_data_manager", store)
    session_id = "metadata-long-od"
    long_id = "x" * 400_000
    payload = {"rows": [{
        "origin_id": long_id, "destination_id": long_id,
        "origin_lng": 104.0, "origin_lat": 30.0,
        "destination_lng": 104.1, "destination_lat": 30.1, "weight": 1,
    }]}
    ref = await store.store(session_id, payload, prefix="data")
    result = await registry.dispatch("od_flow_edges", {
        "od_table_ref": ref, "top_n": 1,
    }, session_id=session_id)
    assert len(result["features"]) == 1
    assert result["features"][0]["properties"]["origin_id"] == long_id
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 1024 * 1024
    assert registry.descriptor("od_flow_edges").result_size_policy == "unknown"


async def test_history_preserves_unbounded_old_field_name(registry, monkeypatch):
    from app.services.feature_edit import executor
    from app.services.feature_edit.attr_ops import validate_rename_field
    from app.services.feature_edit.contracts import JOURNALS_STATE_KEY
    from app.services.feature_edit.journal import append_entry, new_journal
    from app.services.session_data import MemorySessionStore
    from app.tools import feature_edit_tools

    old_field = "x" * 1_100_000
    assert validate_rename_field(old_field, "short", {old_field: {}}) == []
    journal = append_entry(new_journal(), {
        "kind": "apply", "actor": "agent", "origin": "agent",
        "deltas": [], "schema_ops": [{"from": old_field, "to": "short"}],
        "content_revision_after": 1,
    })
    store = MemorySessionStore()
    monkeypatch.setattr(executor, "session_data_manager", store)
    monkeypatch.setattr(feature_edit_tools, "session_data_manager", store)
    session_id = "metadata-long-history"
    ref = await store.store(session_id, {"type": "FeatureCollection",
                                         "features": []}, prefix="data")
    await store.set_map_state_fields(session_id, {JOURNALS_STATE_KEY: {ref: journal}})
    result = await registry.dispatch("vector_feature_edit_history", {
        "ref_id": ref, "limit": 1,
    }, session_id=session_id)
    assert result["entries"][0]["schema_ops"][0]["from"] == old_field
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 1024 * 1024
    assert registry.descriptor("vector_feature_edit_history").result_size_policy == "unknown"
