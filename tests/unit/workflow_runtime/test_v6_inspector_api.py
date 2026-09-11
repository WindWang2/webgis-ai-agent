"""Workflow V6 —— Phase G：Inspector/Debugger API 契约测试。

mini FastAPI 应用挂 workflow_runtime router（不 import app.main —— 其
RAG 链在 Windows 缺 fcntl，属环境限制而非本域）。覆盖：journal 分页、
节点明细、人工重试（预算耗尽 409）、节点级取消、克隆、debug 聚合面、
owner 隔离（404 语义）。
"""
from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    SV.reset_service()
    svc = SV.WorkflowRuntimeService(
        store=InstanceStore(factory=fac),
        registry=SV.PackageRegistry(factory=fac),
        reuse_index=ReuseIndex(factory=fac))
    monkeypatch.setattr(SV, "_service", svc)

    from types import SimpleNamespace

    yield_extra = {}
    owner = SV.owner_scope_for({"user_id": "alice"}, None)
    svc.registry.register(SimpleNamespace(
        package_id="recipe-x", version="1.0.0", schema_version="1",
        compiler_version="4.0.0", methodology_family="m",
        recipe_fingerprint="", methodology_fingerprint="",
        fingerprint="pf" * 16,
        to_bounded_dict=lambda: {"compiled_form": {"typed_dag": _DAG}}),
        owner_scope=owner)
    yield_extra["owner"] = owner

    from app.core.auth import get_current_user

    # 直接加载路由模块（绕过 app.api.routes.__init__ —— 它拖全量路由，
    # 其中 rag 链在 Windows 缺 fcntl，属环境限制而非本域）
    import importlib.util
    import os

    _route_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "app", "api", "routes", "workflow_runtime.py")
    spec = importlib.util.spec_from_file_location(
        "wf_runtime_routes_standalone", _route_path)
    routes = importlib.util.module_from_spec(spec)
    # FastAPI 解析 postponed annotations 需要 sys.modules 注册
    import sys

    sys.modules["wf_runtime_routes_standalone"] = routes
    spec.loader.exec_module(routes)

    mini = FastAPI()
    mini.include_router(routes.router, prefix="/api/v1")
    mini.dependency_overrides[get_current_user] = lambda: {
        "user_id": "alice"}
    with TestClient(mini, raise_server_exceptions=False) as c:
        yield {"client": c, "svc": svc, "owner": yield_extra["owner"]}
    SV.reset_service()


_DAG = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False},
        {"node_id": "output:zone", "kind": "output", "optional": False},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


def _make(svc, owner):
    return svc.store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope=owner, session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]])


_AUTH = {"Authorization": "Bearer test"}


def _fail_node(svc, iid, node_id="transform:buffer:subject", attempts=0):
    svc.store.transition_node(iid, node_id, C.NodeState.READY,
                              expected_from=C.NodeState.PENDING)
    svc.store.transition_node(iid, node_id, C.NodeState.RUNNING,
                              expected_from=C.NodeState.READY, claim=True,
                              claimed_by="rt-x")
    svc.store.transition_node(iid, node_id, C.NodeState.FAILED,
                              require_claim=True, claimed_by="rt-x",
                              complete=True, reason="EXEC_FAIL:NODE_TIMEOUT",
                              patch={"error_code": "NODE_TIMEOUT",
                                     "attempts_increment": True})


def test_events_endpoint_pagination(env):
    client, svc = env["client"], env["svc"]
    inst = _make(svc, env["owner"])
    for i in range(5):
        svc.store.append_event(inst["instance_id"],
                               kind=C.EventKind.RETRY_SCHEDULED,
                               reason=f"B{i}")
    r = client.get(
        f"/api/v1/workflow-runtime/instances/{inst['instance_id']}/events",
        headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 5
    r2 = client.get(
        f"/api/v1/workflow-runtime/instances/{inst['instance_id']}/events",
        headers=_AUTH, params={"limit": 2})
    assert len(r2.json()["events"]) == 2
    # owner 隔离
    client.app.dependency_overrides[  # noqa: S105 — 换身份
        __import__("app.core.auth", fromlist=["get_current_user"])
        .get_current_user] = lambda: {"user_id": "mallory"}
    r3 = client.get(
        f"/api/v1/workflow-runtime/instances/{inst['instance_id']}/events",
        headers=_AUTH)
    assert r3.status_code == 404


def test_node_detail_and_debug_endpoint(env):
    client, svc = env["client"], env["svc"]
    inst = _make(svc, env["owner"])
    _fail_node(svc, inst["instance_id"])
    iid = inst["instance_id"]
    r = client.get(f"/api/v1/workflow-runtime/instances/{iid}/nodes/"
                   f"transform:buffer:subject", headers=_AUTH)
    assert r.status_code == 200
    node = r.json()["node"]
    assert node["state"] == C.NodeState.FAILED
    assert node["attempts"] == 1
    assert node["error_code"] == "NODE_TIMEOUT"
    r2 = client.get(f"/api/v1/workflow-runtime/instances/{iid}/debug",
                    headers=_AUTH)
    assert r2.status_code == 200
    dbg = r2.json()
    assert "explain" in dbg["instance"]
    assert isinstance(dbg["recent_events"], list)


def test_retry_node_endpoint_budget_and_force(env):
    client, svc = env["client"], env["svc"]
    inst = _make(svc, env["owner"])
    iid = inst["instance_id"]
    _fail_node(svc, iid)  # attempts=1（预算 2 内）
    r = client.post(f"/api/v1/workflow-runtime/instances/{iid}/nodes/"
                    f"transform:buffer:subject/retry", headers=_AUTH)
    assert r.status_code == 200
    assert r.json()["state"] == C.NodeState.READY
    # 再打回 FAILED 并耗尽预算（attempts=2）→ 409
    svc.store.transition_node(iid, "transform:buffer:subject",
                              C.NodeState.RUNNING,
                              expected_from=C.NodeState.READY, claim=True,
                              claimed_by="rt-y")
    svc.store.transition_node(iid, "transform:buffer:subject",
                              C.NodeState.FAILED, require_claim=True,
                              claimed_by="rt-y", complete=True,
                              reason="EXEC_FAIL:NODE_TIMEOUT",
                              patch={"attempts_increment": True})
    r2 = client.post(f"/api/v1/workflow-runtime/instances/{iid}/nodes/"
                     f"transform:buffer:subject/retry", headers=_AUTH)
    assert r2.status_code == 409
    # DAG 外节点 → 404 语义
    r3 = client.post(f"/api/v1/workflow-runtime/instances/{iid}/nodes/"
                     f"nope:node/retry", headers=_AUTH)
    assert r3.status_code == 404


def test_cancel_nodes_endpoint(env):
    client, svc = env["client"], env["svc"]
    inst = _make(svc, env["owner"])
    iid = inst["instance_id"]
    r = client.post(
        f"/api/v1/workflow-runtime/instances/{iid}/nodes/cancel",
        headers=_AUTH, json={"node_ids": ["transform:buffer:subject"],
                             "include_descendants": True})
    assert r.status_code == 200
    flagged = set(r.json()["flagged"])
    assert "output:zone" in flagged  # 后代闭包传播
    assert "data:subject" not in flagged


def test_clone_endpoint(env):
    client, svc = env["client"], env["svc"]
    inst = _make(svc, env["owner"])
    # 克隆只对终态实例开放
    svc.store.update_instance(inst["instance_id"],
                              fields={"status": C.InstanceStatus.SUCCEEDED,
                                      "terminal_at": __import__("datetime")
                                      .datetime.utcnow()})
    r = client.post(f"/api/v1/workflow-runtime/instances/"
                    f"{inst['instance_id']}/clone", headers=_AUTH,
                    json={"skip_nodes": ["output:zone"]})
    assert r.status_code == 200
    body = r.json()
    assert body["instance"]["instance_id"] != inst["instance_id"]
    states = svc.store.get_node_states(body["instance"]["instance_id"])
    assert states["output:zone"] == C.NodeState.SKIPPED
