"""Workflow Runtime V5 —— API 契约测试（Wave 11）。

TestClient smoke：端点可达、认证强制（401）、owner 隔离（404）、
输入界（422）。持久层用临时 SQLite 工厂注入 service 单例。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    SV.reset_service()
    monkeypatch.setattr(
        SV, "_service",
        SV.WorkflowRuntimeService(
            store=InstanceStore(factory=fac),
            registry=SV.PackageRegistry(factory=fac),
            reuse_index=ReuseIndex(factory=fac)))

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "alice"}
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    SV.reset_service()


_AUTH = {"Authorization": "Bearer test"}


def test_all_endpoints_require_auth(client, monkeypatch):
    """无认证 → 401（安全红线）。"""
    from app.core.auth import get_current_user

    # 移除 fixture 的认证 override，恢复真实依赖
    client.app.dependency_overrides.pop(get_current_user, None)
    r = client.get("/api/v1/workflow-runtime/packages")
    assert r.status_code == 401, r.status_code
    r2 = client.post("/api/v1/workflow-runtime/instances", json={})
    assert r2.status_code in (401, 422)


def test_register_publish_resolve_flow(client):
    """注册 → 发布 → 版本清单（编译真实走 V4 编译器）。"""
    r = client.post("/api/v1/workflow-runtime/packages/register", json={
        "query": "对工厂污染源周边做缓冲区分析并评估影响范围",
    }, headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["package"]["status"] == "draft"
    pkg = body["package"]
    r2 = client.post(
        f"/api/v1/workflow-runtime/packages/{pkg['package_id']}/publish",
        json={"version": pkg["version"]}, headers=_AUTH)
    assert r2.status_code == 200 and r2.json()["package"]["status"] == "published"
    r3 = client.get(
        f"/api/v1/workflow-runtime/packages/{pkg['package_id']}/versions",
        headers=_AUTH)
    assert r3.status_code == 200 and len(r3.json()["versions"]) == 1


def test_register_rejects_unmapped_query(client):
    r = client.post("/api/v1/workflow-runtime/packages/register", json={
        "query": "",
    }, headers=_AUTH)
    assert r.status_code == 422  # pydantic 输入界


def test_instance_not_found_is_404(client):
    r = client.get("/api/v1/workflow-runtime/instances/wi-nonexistent",
                   headers=_AUTH)
    assert r.status_code == 404


def test_changes_input_bound(client):
    """changes 超界 → 422（≤16 硬界）。"""
    r = client.post("/api/v1/workflow-runtime/instances/wi-x/changes", json={
        "changes": [{"dimension": "data", "target_kind": "data_role",
                     "target": "x"}] * 17,
    }, headers=_AUTH)
    assert r.status_code == 422


def test_owner_isolation_between_users(client, monkeypatch):
    """alice 的包对 bob 不可见（owner 隔离，404 不泄漏存在性）。"""
    from app.core.auth import get_current_user

    r = client.post("/api/v1/workflow-runtime/packages/register", json={
        "query": "对工厂污染源周边做缓冲区分析并评估影响范围",
    }, headers=_AUTH)
    pkg = r.json()["package"]
    # 切换为 bob
    client.app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "bob"}
    r2 = client.get(
        f"/api/v1/workflow-runtime/packages/{pkg['package_id']}/versions",
        headers=_AUTH)
    assert r2.status_code == 404
    r3 = client.get("/api/v1/workflow-runtime/instances", headers=_AUTH)
    assert r3.json()["instances"] == []
