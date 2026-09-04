"""GeoCompute REST 路由（additive，ADR-0096）：校验/执行/run 查询。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FILTER_NODE = {
    "node_id": "f1",
    "category": "filter",
    "parameters": {
        "predicate": {"op": "eq", "field": "kind", "value": "a"},
        "features": [
            {"type": "Feature", "geometry": None,
             "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i}}
            for i in range(4)
        ],
    },
}


def _plan_body(**overrides):
    node = dict(FILTER_NODE)
    node.update(overrides)
    return {"plan": {"plan_id": "p-rest", "nodes": [node]}}


def test_validate_returns_fingerprint_and_waves():
    body = _plan_body()
    resp = client.post("/api/v1/geocompute/plans/validate", json=body["plan"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan_id"] == "p-rest"
    assert data["waves"] == [["f1"]]
    assert data["node_fingerprints"]["f1"]
    assert "materialize" in data["wired_categories"]


def test_validate_rejects_unknown_input():
    bad = _plan_body(inputs=["ghost"])
    resp = client.post("/api/v1/geocompute/plans/validate", json=bad["plan"])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "PLAN_INVALID"


def test_execute_plan_and_get_run():
    resp = client.post("/api/v1/geocompute/plans/execute", json=_plan_body())
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    assert run["evidence"]["f1"]["status"] in {"completed", "reused"}
    assert run["evidence"]["f1"]["rows_emitted"] == 2

    got = client.get(f"/api/v1/geocompute/runs/{run['run_id']}")
    assert got.status_code == 200
    assert got.json()["run_id"] == run["run_id"]

    summary = client.get(f"/api/v1/geocompute/runs/{run['run_id']}/summary")
    assert summary.status_code == 200
    assert any("f1" in line for line in summary.json()["lines"])


def test_get_missing_run_404():
    assert client.get("/api/v1/geocompute/runs/does-not-exist").status_code == 404


def test_overbudget_admission_rejected_via_rest():
    body = _plan_body(
        category="query",
        estimate={"rows": 10_000_000, "confidence": "high"},
        parameters={"dataset_id": "d"},
    )
    body["plan"]["budget"] = {"max_rows": 1000}
    resp = client.post("/api/v1/geocompute/plans/execute", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "RESOURCE_BUDGET_EXCEEDED"
    assert detail["details"]["suggestions"]


def test_unsupported_category_is_typed_failure():
    body = _plan_body(node_id="n9", category="network_operation", parameters={})
    resp = client.post("/api/v1/geocompute/plans/execute", json=body)
    assert resp.status_code == 200  # 执行成功受理；节点级类型化失败在 run 证据里
    run = resp.json()
    assert run["status"] == "failed"
    assert run["evidence"]["n9"]["error_code"] == "OPERATION_UNSUPPORTED"
