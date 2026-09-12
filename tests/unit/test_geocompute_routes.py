"""GeoCompute REST 路由（additive，ADR-0096）：校验/执行/run 查询。

SEC 评审后：execute / runs / drift-check 强制认证（无 Bearer → 401）；
validate 保持可选认证。非 401 断言一律携带同用户 Bearer token。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _global_db_ready():
    """全局 sqlite 库就绪（与应用启动同路径的 init_db）。

    ``GET /runs/{id}`` 内存未命中时会回读 cluster run store（全局库）+
    tenancy org 解析。生产/全量套件里全局库已建表，run 缺席 → 404；
    单文件直跑时库缺席会把 store 不可用诚实映射为 503（CLUSTER_UNAVAILABLE），
    与被测语义（missing run → 404）错位。这里走一次 init_db() 消除环境差。
    """
    from app.core.database import init_db

    init_db()
    yield

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


def _auth(user_id: str = "gc-rest-user") -> dict[str, str]:
    token = create_access_token({"sub": user_id, "role": "editor"})
    return {"Authorization": f"Bearer {token}"}


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
    assert resp.json()["data"]["code"] == "PLAN_INVALID"


def test_execute_plan_and_get_run():
    resp = client.post("/api/v1/geocompute/plans/execute", json=_plan_body(),
                       headers=_auth())
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    assert run["evidence"]["f1"]["status"] in {"completed", "reused"}
    assert run["evidence"]["f1"]["rows_emitted"] == 2

    got = client.get(f"/api/v1/geocompute/runs/{run['run_id']}", headers=_auth())
    assert got.status_code == 200
    assert got.json()["run_id"] == run["run_id"]

    summary = client.get(f"/api/v1/geocompute/runs/{run['run_id']}/summary",
                         headers=_auth())
    assert summary.status_code == 200
    assert any("f1" in line for line in summary.json()["lines"])


def test_get_missing_run_404():
    got = client.get("/api/v1/geocompute/runs/does-not-exist", headers=_auth())
    assert got.status_code == 404


def test_overbudget_admission_rejected_via_rest():
    body = _plan_body(
        category="query",
        estimate={"rows": 10_000_000, "confidence": "high"},
        parameters={"dataset_id": "d"},
    )
    body["plan"]["budget"] = {"max_rows": 1000}
    resp = client.post("/api/v1/geocompute/plans/execute", json=body,
                       headers=_auth())
    assert resp.status_code == 422
    # ADR-0138：路由 raise HTTPException(422, detail=<dict>) 时，统一处理器把
    # 结构化 detail 移入信封 data（message 为状态码缺省文案）—— 同上面
    # test_validate_rejects_unknown_input 的 data["code"] 断言。
    payload = resp.json()["data"]
    assert payload["code"] == "RESOURCE_BUDGET_EXCEEDED"
    assert payload["details"]["suggestions"]


def test_unsupported_category_is_typed_failure():
    body = _plan_body(node_id="n9", category="network_operation", parameters={})
    resp = client.post("/api/v1/geocompute/plans/execute", json=body,
                       headers=_auth())
    assert resp.status_code == 200  # 执行成功受理；节点级类型化失败在 run 证据里
    run = resp.json()
    assert run["status"] == "failed"
    assert run["evidence"]["n9"]["error_code"] == "OPERATION_UNSUPPORTED"
