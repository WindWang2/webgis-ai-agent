"""Cross-boundary journey evidence projection contracts."""
from __future__ import annotations

from app.lib.harness.replay.journey import project_journey_evidence


def _trace_without_render() -> dict:
    return {
        "session_id": "sess-journey",
        "turn_id": "turn-journey",
        "request_id": "req-journey",
        "run_id": "run-journey",
        "chain": {
            "session_id": "sess-journey",
            "turn_id": "turn-journey",
            "stages": [
                {"stage": "USER_INTENT", "query": "绘制学校分布图"},
                {"stage": "SELECTED_WORKFLOW", "recipe_id": "school_distribution"},
                {"stage": "TOOL_CALLS", "call_id": "call-1", "tool": "spatial_aggregate"},
                {"stage": "TOOL_RESULTS", "call_id": "call-1", "tool": "spatial_aggregate",
                 "status": "ok", "geojson_ref": "ref:geojson-result"},
                {"stage": "ARTIFACT_CREATION", "ref": "ref:geojson-result",
                 "tool": "spatial_aggregate"},
                {"stage": "MAP_MUTATIONS", "revision": 4,
                 "mapspec_fingerprint": "fp-map"},
                {"stage": "FINAL_VERDICT", "status": "complete",
                 "final_map_status": "verified"},
            ],
        },
        "artifacts": {"ref": "ref:geojson-result", "count": 1},
        "verdict": {"map_product": {"task_complete": True}},
        "outcome": {"outcome": "succeeded"},
        "env": {"registry_digest": "registry-v1", "python_version": "3.13"},
    }


def test_task_complete_without_render_observation_is_contradiction():
    report = project_journey_evidence(
        _trace_without_render(), mode="offline_replay")

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["render_observation"]["status"] == "not_evaluated"
    assert stages["settlement"]["status"] == "fail"
    assert report["completion_claims"]["map_rendered_and_observed"] is False
    assert report["completion_claims"]["goal_satisfied"] is False
    assert report["overall"] == "fail"
    assert "task_complete_without_render_observation" in {
        finding["code"] for finding in report["findings"]
    }


def test_cross_session_chain_cannot_settle_or_leak_identity():
    import copy

    trace = copy.deepcopy(_trace_without_render())
    stages = trace["chain"]["stages"]
    stages.insert(-1, {
        "stage": "MAP_OBSERVATION", "session_id": "sess-third",
        "sequence": 9, "map_idle": True,
    })
    trace["chain"]["session_id"] = "sess-other"

    report = project_journey_evidence(trace, mode="offline_replay")

    codes = {finding["code"] for finding in report["findings"]}
    assert "session_mismatch" in codes
    assert report["overall"] == "fail"
    assert report["completion_claims"]["goal_satisfied"] is False
    assert "sess-other" not in str(report["findings"])
    assert "sess-third" not in str(report["findings"])


def test_tool_result_with_unknown_call_id_is_dangling_edge():
    import copy

    trace = copy.deepcopy(_trace_without_render())
    trace["chain"]["stages"] = [
        record for record in trace["chain"]["stages"]
        if record["stage"] not in ("TOOL_CALLS", "ARGUMENTS")
    ]

    report = project_journey_evidence(trace, mode="offline_replay")

    assert "dangling_tool_result" in {
        finding["code"] for finding in report["findings"]
    }
    assert report["completion_claims"]["tool_dispatched"] is False
    assert report["completion_claims"]["tool_result_validated"] is False
    assert report["overall"] == "fail"


def test_noncanonical_stage_order_is_reported_without_inventing_timestamps():
    import copy

    trace = copy.deepcopy(_trace_without_render())
    observation = {
        "stage": "MAP_OBSERVATION", "sequence": 3, "map_idle": True,
    }
    trace["chain"]["stages"].insert(0, observation)

    report = project_journey_evidence(trace, mode="offline_replay")

    assert "noncanonical_stage_order" in {
        finding["code"] for finding in report["findings"]
    }
    assert report["overall"] == "fail"
    assert report["sequence_basis"] == "canonical_stage_order_only"
    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["render_observation"]["observed_ordinal"] == 1
    assert stages["intent"]["observed_ordinal"] == 2


def test_fully_observed_journey_can_pass_without_model_text_evidence():
    import copy

    trace = copy.deepcopy(_trace_without_render())
    trace["chain"]["stages"].insert(-1, {
        "stage": "MAP_OBSERVATION", "sequence": 8, "map_idle": True,
        "mapspec_fingerprint": "fp-map",
    })
    export_index = next(
        index for index, record in enumerate(trace["chain"]["stages"])
        if record["stage"] == "MAP_MUTATIONS"
    )
    trace["chain"]["stages"].insert(export_index, {
        "stage": "ARTIFACT_CREATION", "kind": "pdf",
        "ref": "ref:pdf-export", "digest": "sha256:export",
    })
    trace["final_text"] = "模型这句话不参与完成判定"

    report = project_journey_evidence(trace, mode="browser_renderer")

    assert report["overall"] == "pass"
    assert all(report["completion_claims"].values())
    assert report["last_valid_stage"] == "settlement"
    assert report["mode"] == "browser_renderer"


def test_ref_result_without_artifact_persistence_cannot_pass():
    from tests.harness_replay.test_replay_schema import _build
    from app.lib.runtime.gis_trace import Stage

    trace = _build().to_dict()
    trace["chain"]["stages"].insert(-1, {
        "stage": Stage.MAP_OBSERVATION.name, "sequence": 3, "map_idle": True,
    })

    report = project_journey_evidence(trace)

    assert report["overall"] == "fail"
    assert report["completion_claims"]["artifact_persisted"] is False
    assert "artifact_persistence_unverified" in {
        finding["code"] for finding in report["findings"]
    }


def test_inline_result_without_artifact_cannot_claim_full_journey():
    from tests.harness_replay.test_journey_faults import _complete_trace

    trace = _complete_trace()
    trace["chain"]["stages"] = [
        record for record in trace["chain"]["stages"]
        if record["stage"] != "ARTIFACT_CREATION"
    ]
    result = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "TOOL_RESULTS"
    )
    result.pop("geojson_ref", None)
    result["result"] = {"value": 42}

    report = project_journey_evidence(trace)

    assert report["overall"] == "fail"
    assert report["completion_claims"]["artifact_persisted"] is False
    assert "artifact_persistence_unverified" in {
        finding["code"] for finding in report["findings"]
    }
