"""模型作者域 REST 契约测试（TestClient smoke）。

端点可达、认证强制（401）、slug 冲突（409）、CAS 冲突（409）、验证
findings 投影。注意：本文件的 fixture 把 `_authorize_project_write`
monkeypatch 成放行 —— 项目写权限（403）路径由
`tests/unit/model_authoring/test_r1_r2_regressions.py`（服务层项目派生）
与真实 route 闸的探针覆盖；此处专注契约形状。发布/实例化链依赖 compile
模块（同域），随本域单测一起回归。持久层用临时 SQLite 工厂注入 service
单例。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.model_authoring.service import (
    ModelAuthoringService,
)
from app.services.model_authoring.store import ModelDraftStore


def _fac():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _doc_payload(slug_name: str = "设施统计模型") -> dict:
    return {
        "schema_version": "1",
        "name": slug_name,
        "description": "按行政区统计设施",
        "source": {"kind": "manual", "ref": ""},
        "params": [],
        "nodes": [
            {"node_id": "src", "kind": "data_input", "role": "facility"},
            {"node_id": "agg", "kind": "analysis",
             "capability": "admin_aggregation"},
            {"node_id": "cart", "kind": "cartography"},
            {"node_id": "out", "kind": "output"},
        ],
        "edges": [
            {"from_node": "src", "to_node": "agg"},
            {"from_node": "agg", "to_node": "cart"},
            {"from_node": "cart", "to_node": "out"},
        ],
        "output_node_ids": ["out"],
        "layout": {"src": {"x": 100, "y": 100}},
    }


@pytest.fixture
def client(monkeypatch):
    fac = _fac()
    from app.services.workflow_runtime import service as SV
    from app.services.workflow_runtime.registry import PackageRegistry
    from app.services.workflow_runtime.store import InstanceStore
    from app.services.workflow_runtime.reuse import ReuseIndex

    SV.reset_service()
    monkeypatch.setattr(
        SV, "_service",
        SV.WorkflowRuntimeService(
            store=InstanceStore(factory=fac),
            registry=PackageRegistry(factory=fac),
            reuse_index=ReuseIndex(factory=fac)))
    from app.core.database import SessionLocal  # noqa: F401

    svc = ModelAuthoringService(
        store=ModelDraftStore(session_factory=fac))
    import app.services.model_authoring.service as MS

    monkeypatch.setattr(MS, "_service", svc)
    # 项目写权限：测试用户是 owner（跳过真实 project 归属——routes 用
    # ProjectService；此处 monkeypatch 判定为放行）
    import app.api.routes.model_authoring as MR

    async def _allow(project_id, user):
        return None

    monkeypatch.setattr(MR, "_authorize_project_write", _allow)
    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "alice"}
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    SV.reset_service()


_AUTH = {"Authorization": "Bearer test"}


def test_endpoints_require_auth(client):
    from app.core.auth import get_current_user

    client.app.dependency_overrides.pop(get_current_user, None)
    r = client.get("/api/v1/model-authoring/projects/p1/models")
    assert r.status_code == 401, r.status_code
    r = client.post("/api/v1/model-authoring/projects/p1/models", json={})
    assert r.status_code == 401, r.status_code


def test_crud_and_slug_conflict(client):
    r = client.post(
        "/api/v1/model-authoring/projects/p1/models",
        json={"model_slug": "poi_stats", "document": _doc_payload()},
        headers=_AUTH)
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["revision_no"] == 1
    assert draft["model_slug"] == "poi_stats"
    # 同 slug 冲突 → 409
    r2 = client.post(
        "/api/v1/model-authoring/projects/p1/models",
        json={"model_slug": "poi_stats", "document": _doc_payload()},
        headers=_AUTH)
    assert r2.status_code == 409, r2.text
    assert r2.json()["data"]["code"] == "DRAFT_EXISTS"
    # 列表与读取
    r3 = client.get("/api/v1/model-authoring/projects/p1/models",
                    headers=_AUTH)
    assert r3.status_code == 200 and len(r3.json()["models"]) == 1
    r4 = client.get("/api/v1/model-authoring/projects/p1/models/poi_stats",
                    headers=_AUTH)
    assert r4.status_code == 200
    # 不存在 → 404
    r5 = client.get("/api/v1/model-authoring/projects/p1/models/ghost",
                    headers=_AUTH)
    assert r5.status_code == 404


def test_update_cas_conflict(client):
    client.post(
        "/api/v1/model-authoring/projects/p1/models",
        json={"model_slug": "m01", "document": _doc_payload()},
        headers=_AUTH)
    r = client.put(
        "/api/v1/model-authoring/projects/p1/models/m01",
        json={"document": _doc_payload(), "expected_revision": 99},
        headers=_AUTH)
    assert r.status_code == 409
    assert r.json()["data"]["code"] == "REVISION_CONFLICT"
    r2 = client.put(
        "/api/v1/model-authoring/projects/p1/models/m01",
        json={"document": _doc_payload("改名"), "expected_revision": 1},
        headers=_AUTH)
    assert r2.status_code == 200
    assert r2.json()["draft"]["revision_no"] == 2


def test_validate_reports_findings(client):
    client.post(
        "/api/v1/model-authoring/projects/p1/models",
        json={"model_slug": "m01", "document": _doc_payload()},
        headers=_AUTH)
    r = client.post(
        "/api/v1/model-authoring/projects/p1/models/m01/validate",
        headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert isinstance(body["findings"], list)


def test_schema_invalid_rejected(client):
    r = client.post(
        "/api/v1/model-authoring/projects/p1/models",
        json={"model_slug": "bad1", "document": {"schema_version": "1"}},
        headers=_AUTH)
    assert r.status_code == 422
    assert r.json()["data"]["code"] == "MODEL_SCHEMA_INVALID"


def test_capabilities_catalog(client):
    r = client.get("/api/v1/model-authoring/capabilities", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "buffer" in body["transform_ops"]
    assert "facility" in body["data_roles"]
    assert isinstance(body["capabilities"], list)
