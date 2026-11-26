"""共享拓扑提案重放与 G14 plan/commit 的层级守卫一致性。"""
from __future__ import annotations

from app.services.feature_proposals.candidate import evaluate_candidate


def test_candidate_replay_consumes_envelope_level_field() -> None:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[116.4, 39.9], [116.4002, 39.9]]},
                "properties": {"_wfid": "wf-road", "level": 0},
            },
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[116.4002, 39.9], [116.4004, 39.9]]},
                "properties": {"_wfid": "wf-ramp", "level": 1},
            },
        ],
    }
    ops = [{"op": "move_shared_vertex", "at": [116.4002, 39.9], "dx_m": 2.0, "dy_m": 0.0}]

    deltas, findings, lineage = evaluate_candidate(
        fc, ops, ["wf-road"], op_seed="fpr-level-guard", shared_level_field="level",
    )
    assert findings == []
    assert {d["wfid"] for d in deltas} == {"wf-road"}
    assert lineage == []

    deltas_unguarded, findings_unguarded, _ = evaluate_candidate(
        fc, ops, ["wf-road"], op_seed="fpr-level-guard",
    )
    assert findings_unguarded == []
    assert {d["wfid"] for d in deltas_unguarded} == {"wf-road", "wf-ramp"}
