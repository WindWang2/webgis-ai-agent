"""ReplayTrace v1 契约测试（B1）：打包、消毒、digest 稳定性、版本演进。"""
from __future__ import annotations

import pytest

from app.lib.harness.replay.determinism import behavior_digest, canonical_json
from app.lib.harness.replay.metrics import project_metrics
from app.lib.harness.replay.schema import (
    REPLAY_TRACE_SCHEMA_VERSION,
    ReplayTrace,
    build_trace,
)
from app.lib.runtime.evidence import Outcome, TurnEvidence
from app.lib.runtime.gis_trace import GisTraceChain, Stage

pytestmark = pytest.mark.cartography


def _synthetic_chain() -> dict:
    chain = GisTraceChain(turn_id="turn-test0001", session_id="sess-r10")
    chain.record(Stage.USER_INTENT, prompt="把 A 区人均公园面积做成分布图")
    chain.record(Stage.PARSED_INTENT, goal="point_distribution", confidence=0.9)
    chain.record(
        Stage.SELECTED_WORKFLOW, workflow="point_density_map", candidates=["a", "b"],
    )
    chain.record(
        Stage.TOOL_CALLS, tool_call_id="call-1", tool_name="webgis_layer_upsert",
        arguments={"layer_id": "parks", "api_key": "sk-should-vanish"},
    )
    chain.record(
        Stage.TOOL_RESULTS, tool_call_id="call-1", tool_name="webgis_layer_upsert",
        status="ok", duration_ms=12.5,
        result={"geojson_ref": "ref://abc", "mapspec_fingerprint": "fp-1"},
    )
    chain.record(Stage.MAP_MUTATIONS, intent="UpsertLayerIntent", revision=3)
    chain.record(
        Stage.FINAL_VERDICT, status="complete", final_map_status="verified",
        product_verdict="ready",
    )
    return chain.as_dict()


def _synthetic_summary() -> dict:
    ev = TurnEvidence(request_id="req-1", session_id="sess-r10", turn_id="turn-test0001", run_id="run-1")
    ev.add_llm_usage({"prompt_tokens": 100, "completion_tokens": 20})
    ev.add_tool_call(duration_ms=12.5)
    ev.settle(Outcome.SUCCEEDED)
    ev.mark_ended()
    return ev.to_summary()


def _synthetic_product() -> dict:
    return {
        "status": "complete",
        "viewport_status": "ok",
        "result_bbox": [1.0, 2.0, 3.0, 4.0],
        "summary": "done",
        "issues": [],
        "repairs": [],
        "task_complete": True,
    }


def _build(**overrides):
    kwargs = dict(
        session_id="sess-r10",
        turn_id="turn-test0001",
        chain_dict=_synthetic_chain(),
        turn_summary=_synthetic_summary(),
        map_product=_synthetic_product(),
        final_text="图已生成",
    )
    kwargs.update(overrides)
    return build_trace(**kwargs)


class TestBuildTrace:
    def test_core_fields_populated(self):
        trace = _build()
        assert trace.schema_version == REPLAY_TRACE_SCHEMA_VERSION
        assert trace.user_input.startswith("把 A 区")
        assert trace.normalized_goal == "point_distribution"
        assert trace.selected_workflow == "point_density_map"
        assert trace.plan_digest
        assert trace.behavior_digest
        assert trace.outcome.get("outcome") == "succeeded"
        assert trace.work.get("tool_calls") == 1
        assert trace.final_text == "图已生成"

    def test_tool_calls_extracted_and_sanitized(self):
        trace = _build()
        assert len(trace.tool_calls) == 1
        call = trace.tool_calls[0]
        assert call["tool_name"] == "webgis_layer_upsert"
        assert call["status"] == "ok"
        assert call["arguments"].get("layer_id") == "parks"
        assert call["arguments"]["api_key"] == "[REDACTED]"
        assert call["result_ref"]["geojson_ref"] == "ref://abc"
        assert "digest" in call["result_ref"]
        assert "features" not in call["result_ref"]

    def test_verdict_blocks(self):
        trace = _build()
        assert trace.verdict["map_product"]["task_complete"] is True
        assert trace.verdict["final_verdict"]["status"] == "complete"

    def test_mutations_count(self):
        trace = _build()
        assert trace.mutations["count"] == 1
        assert trace.mutations["mutation_revision"] is None  # 载荷未带 revision


class TestBehaviorDigest:
    def test_stable_across_wall_time_and_ids(self):
        t1 = _build()
        t2 = _build(session_id="sess-other", turn_id="turn-other")
        t2.created_at_epoch = 123.456
        assert t1.behavior_digest == t2.behavior_digest

    def test_digest_ignores_timing_precision(self):
        s1 = _synthetic_summary()
        t1 = _build(turn_summary=s1)
        s2 = _synthetic_summary()
        s2["timing_ms"]["total"] = (s1["timing_ms"]["total"] or 1) * 7.77
        t2 = _build(turn_summary=s2)
        assert t1.behavior_digest == t2.behavior_digest

    def test_digest_changes_on_semantic_drift(self):
        t1 = _build()
        product = _synthetic_product()
        product["task_complete"] = False
        t2 = _build(map_product=product)
        assert t1.behavior_digest != t2.behavior_digest

    def test_digest_changes_on_tool_result_drift(self):
        t1 = _build()
        chain = _synthetic_chain()
        for rec in chain["stages"]:
            if rec.get("stage") == "TOOL_RESULTS":
                rec["result"] = {"geojson_ref": "ref://CHANGED"}
        t2 = _build(chain_dict=chain)
        assert t1.behavior_digest != t2.behavior_digest

    def test_digest_independent_of_user_text(self):
        """LLM 文本属 nondeterministic_text 类：内容变化不影响 digest。"""
        t1 = _build(final_text="图已生成")
        t2 = _build(final_text="已完成制图，请查看")
        assert t1.behavior_digest == t2.behavior_digest


class TestSanitizeBounded:
    def test_forbidden_value_key_becomes_digest(self):
        chain = _synthetic_chain()
        for rec in chain["stages"]:
            if rec.get("stage") == "TOOL_CALLS":
                rec["arguments"] = {"llm_payload": "x" * 10000, "layer_id": "parks"}
        trace = _build(chain_dict=chain)
        call = trace.tool_calls[0]
        block = call["arguments"]["llm_payload"]
        assert set(block.keys()) == {"digest", "bytes"}
        assert "xxxx" not in canonical_json(trace.to_dict())

    def test_oversized_arguments_degrade_to_digest_only(self):
        chain = _synthetic_chain()
        for rec in chain["stages"]:
            if rec.get("stage") == "TOOL_CALLS":
                # bound_meta 对超长 repr 的折叠形态 —— literal_eval 不可还原。
                rec["arguments"] = "<dict len=9>"
        trace = _build(chain_dict=chain)
        call = trace.tool_calls[0]
        assert "_digest_only" in call["arguments"]
        assert call["args_truncated"] is True

    def test_trace_json_bounded(self):
        chain = _synthetic_chain()
        for rec in chain["stages"]:
            if rec.get("stage") == "USER_INTENT":
                rec["prompt"] = "z" * 200000
        trace = _build(chain_dict=chain)
        blob = canonical_json(trace.to_dict())
        assert len(blob) < 400_000  # 全 trace 有界（诚实降级，不静默膨胀）
        # prompt 是禁值键 → 值被 digest 块替换（体积归零、尺寸可追溯）。
        for stage in trace.to_dict()["chain"]["stages"]:
            if stage.get("stage") == "USER_INTENT":
                assert set(stage["prompt"].keys()) == {"digest", "bytes"}
                assert stage["prompt"]["bytes"] == 200002


class TestVersioning:
    def test_roundtrip_and_unknown_field_preserved(self):
        trace = _build()
        data = trace.to_dict()
        data["future_optional_field"] = {"governor": {"decision": "admit"}}
        restored = ReplayTrace.from_dict(data)
        assert restored.to_dict()["schema_version"] == REPLAY_TRACE_SCHEMA_VERSION
        assert getattr(restored, "_unknown_fields", {}).get("future_optional_field") == {
            "governor": {"decision": "admit"}
        }

    def test_reserved_optional_fields_default_none(self):
        trace = _build()
        assert trace.situation_revision is None
        assert trace.governor is None
        assert trace.skill_id is None


class TestMetricsProjection:
    def test_projection_rows_and_direction_naming(self):
        trace = _build().to_dict()
        gate_result = {
            "checks": {
                "ToolChoiceAccuracy": {"score": 100.0, "evaluated": True},
                "MapSpecValidity": {"score": 0.0, "evaluated": False,
                                    "reason": "not_evaluated_policy_fail"},
            },
        }
        rows = project_metrics(
            trace, scene_id="scenario-x", gate_result=gate_result,
            goal_satisfaction={"status": "pass"}, plan_stable=True,
        )
        by_id = {r["check_id"]: r["value"] for r in rows}
        assert by_id["gate.replay.task_complete"] == 1.0
        assert by_id["gate.replay.goal_pass"] == 1.0
        assert by_id["gate.replay.plan_stability"] == 1.0
        assert by_id["gate.ToolChoiceAccuracy"] == 100.0
        # not_evaluated 维度诚实缺席，不伪造 0 分行。
        assert "gate.MapSpecValidity" not in by_id
        assert by_id["replay.tool_calls"] == 1.0
        assert by_id["replay.tool_error_rate"] == 0.0
        assert all(r["scene_id"] == "scenario-x" for r in rows)

    def test_absence_honesty(self):
        trace = _build().to_dict()
        rows = project_metrics(trace, scene_id="s")
        ids = {r["check_id"] for r in rows}
        assert "gate.replay.goal_pass" not in ids   # 无 goal 证据 → 缺席
        assert "gate.replay.plan_stability" not in ids

    def test_behavior_digest_function_direct(self):
        assert behavior_digest({"a": 1}) == behavior_digest({"a": 1})
        assert behavior_digest({"a": 1}) != behavior_digest({"a": 2})
