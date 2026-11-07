"""Terminal-state fault contracts for the journey evidence projection."""
from __future__ import annotations

import copy

import pytest

from app.lib.harness.replay.journey import project_journey_evidence
from tests.harness_replay.test_journey_evidence import _trace_without_render


def _complete_trace() -> dict:
    trace = copy.deepcopy(_trace_without_render())
    stages = trace["chain"]["stages"]
    mutation_index = next(
        index for index, record in enumerate(stages)
        if record["stage"] == "MAP_MUTATIONS"
    )
    stages.insert(mutation_index, {
        "stage": "ARTIFACT_CREATION", "kind": "pdf",
        "ref": "ref:pdf-export", "digest": "sha256:export",
    })
    stages.insert(-1, {
        "stage": "MAP_OBSERVATION", "sequence": 8, "map_idle": True,
        "mapspec_fingerprint": "fp-map",
    })
    return trace


def test_lock_loss_warning_invalidates_otherwise_complete_journey():
    trace = _complete_trace()
    trace["warnings"] = [{"code": "lock_lost", "detail": "ownership lost"}]

    report = project_journey_evidence(trace)

    assert report["overall"] == "fail"
    assert report["completion_claims"]["goal_satisfied"] is False
    assert "lock_lost" in {finding["code"] for finding in report["findings"]}


@pytest.mark.parametrize(("outcome", "failure_class", "overall", "reason"), [
    ("cancelled", "user_cancel", "blocked", "cancelled"),
    ("failed", "timeout", "fail", "timeout"),
    ("cancelled", "client_disconnect", "blocked", "cancelled"),
])
def test_terminal_outcomes_never_fake_completion(outcome, failure_class, overall, reason):
    trace = _complete_trace()
    trace["outcome"] = {
        "outcome": outcome, "failure_class": failure_class,
        "detail": failure_class,
    }

    report = project_journey_evidence(trace)

    assert report["overall"] == overall
    assert report["completion_claims"]["goal_satisfied"] is False
    assert reason in {finding["code"] for finding in report["findings"]}


def test_webgl_context_loss_is_not_a_rendered_map():
    trace = _complete_trace()
    observation = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "MAP_OBSERVATION"
    )
    observation["runtime_errors"] = [{"code": "WEBGL_CONTEXT_LOST"}]

    report = project_journey_evidence(trace, mode="browser_renderer")

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["render_observation"]["status"] == "fail"
    assert report["overall"] == "fail"
    assert "renderer_context_lost" in {
        finding["code"] for finding in report["findings"]
    }


def test_missing_tiles_are_partial_never_publication_complete():
    trace = _complete_trace()
    observation = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "MAP_OBSERVATION"
    )
    observation["missing_tiles"] = 3

    report = project_journey_evidence(trace, mode="browser_renderer")

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["render_observation"]["status"] == "partial"
    assert report["overall"] == "fail"
    assert "tiles_missing" in {finding["code"] for finding in report["findings"]}


def test_export_failure_cannot_be_hidden_by_artifact_reference():
    trace = _complete_trace()
    export = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "ARTIFACT_CREATION"
        and record.get("kind") == "pdf"
    )
    export["status"] = "failed"
    export["error_code"] = "EXPORT_ABORTED"

    report = project_journey_evidence(trace)

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["export"]["status"] == "fail"
    assert report["overall"] == "fail"
    assert report["completion_claims"]["publication_export_verified"] is False
    assert "export_failed" in {finding["code"] for finding in report["findings"]}


def test_postgis_failure_is_typed_at_tool_result_boundary():
    trace = _complete_trace()
    result = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "TOOL_RESULTS"
    )
    result["status"] = "error"
    result["is_error"] = True
    result["error_code"] = "POSTGIS_UNAVAILABLE"

    report = project_journey_evidence(trace)

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["data_result"]["status"] == "fail"
    assert report["overall"] == "fail"
    assert "postgis_unavailable" in {
        finding["code"] for finding in report["findings"]
    }


def test_identical_duplicate_callback_is_at_least_once_not_full_success():
    trace = _complete_trace()
    result = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "TOOL_RESULTS"
    )
    trace["chain"]["stages"].insert(
        trace["chain"]["stages"].index(result) + 1, copy.deepcopy(result),
    )

    report = project_journey_evidence(trace)

    assert report["overall"] == "partial"
    assert report["completion_claims"]["goal_satisfied"] is False
    assert "duplicate_tool_callback" in {
        finding["code"] for finding in report["findings"]
    }


def test_conflicting_duplicate_callback_fails_closed():
    trace = _complete_trace()
    result = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "TOOL_RESULTS"
    )
    conflict = copy.deepcopy(result)
    conflict["geojson_ref"] = "ref:geojson-conflict"
    trace["chain"]["stages"].insert(
        trace["chain"]["stages"].index(result) + 1, conflict,
    )

    report = project_journey_evidence(trace)

    assert report["overall"] == "fail"
    assert "conflicting_tool_result" in {
        finding["code"] for finding in report["findings"]
    }


def test_unloaded_tool_cannot_be_masked_by_success_result():
    trace = _complete_trace()
    trace["dispatch_evidence"] = [{
        "call_id": "call-1", "tool": "spatial_aggregate",
        "allowed": False, "reason": "TOOL_UNREGISTERED",
    }]

    report = project_journey_evidence(trace)

    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["tool_dispatch"]["status"] == "fail"
    assert report["overall"] == "fail"
    assert report["completion_claims"]["tool_dispatched"] is False
    assert "tool_unregistered" in {
        finding["code"] for finding in report["findings"]
    }


def test_late_observation_from_previous_turn_is_rejected():
    trace = _complete_trace()
    observation = next(
        record for record in trace["chain"]["stages"]
        if record["stage"] == "MAP_OBSERVATION"
    )
    observation["turn_id"] = "turn-previous"

    report = project_journey_evidence(trace)

    assert report["overall"] == "fail"
    assert report["completion_claims"]["goal_satisfied"] is False
    assert "turn_mismatch" in {finding["code"] for finding in report["findings"]}
