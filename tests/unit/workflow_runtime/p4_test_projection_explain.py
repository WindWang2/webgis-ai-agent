"""Workflow V6 — projection 有界投影与解释（P4 补强：W8 inspector 投影面）。"""
from __future__ import annotations

from app.services.workflow_runtime import projection as PJ


def _instance() -> dict:
    return {
        "instance_id": "wi-proj",
        "package_id": "pkg-proj",
        "package_version": "1.0.0",
        "package_fingerprint": "pf" * 16,
        "status": "running",
        "revision": 3,
        "cancel_requested": True,
        "error_code": "",
        "error_detail": "",
    }


def _node(node_id: str, state: str, **extra) -> dict:
    base = {
        "node_id": node_id, "state": state, "attempts": 0,
        "error_code": "", "bound_ref": "", "output_ref": "",
        "binding": {}, "reuse": {},
    }
    base.update(extra)
    return base


def test_instance_projection_is_bounded_and_sorted() -> None:
    nodes = [_node(f"n:{i:02d}", "PENDING") for i in range(80)]
    proj = PJ.instance_projection(_instance(), nodes)
    assert len(proj["nodes"]) <= 64  # node_count_cap
    ids = [n["node_id"] for n in proj["nodes"]]
    assert ids == sorted(ids)


def test_projection_counts_by_state() -> None:
    nodes = [
        _node("a", "SUCCEEDED"), _node("b", "SUCCEEDED"),
        _node("c", "RUNNING"),
    ]
    proj = PJ.instance_projection(_instance(), nodes)
    assert proj["counts"] == {"RUNNING": 1, "SUCCEEDED": 2}


def test_projection_exposes_binding_violation_codes_only() -> None:
    nodes = [_node("a", "BLOCKED", binding={"violations": [
        {"code": "MISSING_BINDING", "detail": "secret-detail"},
    ]})]
    proj = PJ.instance_projection(_instance(), nodes)
    assert proj["nodes"][0]["binding_violations"] == ["MISSING_BINDING"]


def test_explain_reports_reuse_and_recompute_reasons() -> None:
    nodes = [
        {"node_id": "n:keep", "state": "SUCCEEDED",
         "reuse": {"reused": True, "artifact_ref": "ref:keep",
                   "verified_inputs": ["fp-a", "fp-b"]},
         "binding_violations": [], "error_code": ""},
        {"node_id": "n:drift", "state": "STALE",
         "reuse": {}, "binding_violations": [], "error_code": ""},
        {"node_id": "n:block", "state": "BLOCKED",
         "reuse": {}, "binding_violations": ["BINDING_BLOCKED"],
         "error_code": ""},
    ]
    out = PJ.explain(nodes, [], )
    assert any("n:keep" in w for w in out["why_reused"])
    assert any("n:drift" in w for w in out["why_recomputed"])
    assert out["blocked"] == [{"node": "n:block", "codes": ["BINDING_BLOCKED"]}]


def test_explain_node_filter_scopes_to_one_node() -> None:
    nodes = [
        {"node_id": "n:x", "state": "STALE", "reuse": {},
         "binding_violations": [], "error_code": ""},
        {"node_id": "n:y", "state": "SUCCEEDED", "reuse": {},
         "binding_violations": [], "error_code": ""},
    ]
    out = PJ.explain(nodes, [], node_id="n:x")
    assert out["why_recomputed"] and "n:y" not in "".join(out["why_reused"])


def test_projection_tolerates_missing_optional_keys() -> None:
    proj = PJ.instance_projection(_instance(), [])
    assert proj["nodes"] == []
    assert proj["counts"] == {}
    assert proj["decisions"] == []
