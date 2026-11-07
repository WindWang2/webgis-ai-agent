"""Journey evidence rows for the existing replay ratchet."""
from __future__ import annotations

import copy

from app.lib.harness.replay.metrics import project_metrics
from app.lib.runtime.gis_trace import Stage
from tests.harness_replay.test_replay_schema import _build


def _rows(trace):
    return {row["check_id"]: row["value"] for row in project_metrics(
        trace, scene_id="journey-metrics",
    )}


def test_missing_render_has_no_pass_score_and_records_integrity_findings():
    rows = _rows(_build().to_dict())

    assert rows["gate.replay.journey_overall"] == 0.0
    assert "gate.replay.render_observed" not in rows
    assert rows["replay.journey_findings"] >= 1.0


def test_observed_map_emits_record_only_pass_rows():
    trace = _build().to_dict()
    trace["chain"]["stages"].insert(-1, {
        "stage": Stage.MAP_OBSERVATION.name,
        "sequence": 3, "map_idle": True,
    })
    artifact_index = next(
        index for index, record in enumerate(trace["chain"]["stages"])
        if record["stage"] == Stage.MAP_MUTATIONS.name
    )
    trace["chain"]["stages"].insert(artifact_index, {
        "stage": Stage.ARTIFACT_CREATION.name, "ref": "ref://abc",
    })

    rows = _rows(trace)

    assert rows["gate.replay.journey_overall"] == 1.0
    assert rows["gate.replay.render_observed"] == 1.0
    assert rows["replay.cross_session_violations"] == 0.0


def test_cross_session_evidence_is_a_hard_count_not_averaged_away():
    trace = copy.deepcopy(_build().to_dict())
    trace["chain"]["session_id"] = "sess-other"

    rows = _rows(trace)

    assert rows["gate.replay.journey_overall"] == 0.0
    assert rows["replay.cross_session_violations"] >= 1.0
