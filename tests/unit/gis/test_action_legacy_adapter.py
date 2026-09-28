"""H10：legacy adapter / 收敛指标 / service replan 测试。"""
from __future__ import annotations

from app.lib.gis.action_ir import GISAction, GISActionPlan
from app.services.gis_action.legacy_adapter import (
    project_tool_call_to_action,
    project_tool_call_to_plan,
    record_usage,
    reset_usage_counters,
    usage_snapshot,
)
from app.services.gis_action.service import GISActionService


class TestLegacyAdapter:
    def test_state_mutation_tool_projects_mutate_presentation(self):
        meta = {"side_effect": "state_mutation", "latency_class": "medium",
                "memory_class": "medium", "scale_class": "medium"}
        action = project_tool_call_to_action(
            "webgis_layer_upsert", {"layer": {"id": "L1"}}, meta)
        assert action.kind == "mutate_presentation"
        assert action.side_effect == "session_state"
        assert action.idempotency == "duplicate_safe"
        assert action.resource_class == {
            "latency": "medium", "memory": "medium", "scale": "medium"}
        assert action.compensation.kind == "restore_layer_style"
        assert action.compensation.target == "L1"
        assert action.reason_codes == ["LEGACY_ADAPTER_V1"]

    def test_analysis_family_projects_analyze(self):
        action = project_tool_call_to_action(
            "hotspot_analysis", {"ref": "ref:abc"},
            {"side_effect": "deterministic_compute", "deterministic": True})
        assert action.kind == "analyze"
        assert action.side_effect == "pure"
        assert action.idempotency == "idempotent"

    def test_acquire_family_and_ref_inputs(self):
        action = project_tool_call_to_action(
            "query_dataset", {"dataset": "ref:d1", "limit": 10},
            {"side_effect": "pure"})
        assert action.kind == "data_acquire"
        assert [i.ref for i in action.inputs] == ["ref:d1"]
        assert action.preconditions[0].kind == "data_ref_alive"
        assert action.params.get("limit") == 10

    def test_export_and_observe_families(self):
        assert project_tool_call_to_action(
            "export_map", {}, {"side_effect": "external_side_effect"}).kind == "export"
        assert project_tool_call_to_action(
            "webgis_validate", {}, {"side_effect": "pure"}).kind == "observe"

    def test_default_is_conservative_inspect(self):
        action = project_tool_call_to_action(
            "something_unknown", {}, {"side_effect": "cacheable_read"})
        assert action.kind == "inspect"

    def test_inline_geojson_never_enters_params(self):
        fc = {"type": "FeatureCollection",
              "features": [{"properties": {"v": i}} for i in range(200)]}
        action = project_tool_call_to_action(
            "webgis_layer_upsert", {"layer": {"id": "L1"}, "source_data": fc},
            {"side_effect": "state_mutation"})
        assert "source_data" not in action.params
        assert action.params["source_data__sha"]  # 指纹占位

    def test_deterministic_projection(self):
        meta = {"side_effect": "state_mutation"}
        args = {"layer": {"id": "L1"}}
        a1 = project_tool_call_to_action("webgis_layer_upsert", args, meta)
        a2 = project_tool_call_to_action("webgis_layer_upsert", args, meta)
        assert a1.action_fingerprint() == a2.action_fingerprint()

    def test_single_call_plan_shape(self):
        plan = project_tool_call_to_plan(
            "hotspot_analysis", {"ref": "ref:abc"},
            {"side_effect": "deterministic_compute"})
        assert plan.origin == "tool_call"
        assert len(plan.actions) == 1
        assert plan.plan_id.startswith("gap-")


class TestUsageTelemetry:
    def setup_method(self):
        reset_usage_counters()

    def test_record_and_snapshot(self):
        record_usage("direct_routed", "analyze")
        record_usage("direct_routed", "analyze")
        record_usage("plan_routed", "mutate_presentation")
        snap = usage_snapshot()
        assert snap["direct_routed"]["analyze"] == 2
        assert snap["plan_routed"]["mutate_presentation"] == 1

    def test_unknown_channel_ignored(self):
        record_usage("bogus_channel", "analyze")
        assert usage_snapshot()["direct_routed"] == {}

    def test_snapshot_is_sorted_deterministic(self):
        record_usage("direct_routed", "export")
        record_usage("direct_routed", "analyze")
        keys = list(usage_snapshot()["direct_routed"].keys())
        assert keys == sorted(keys)
