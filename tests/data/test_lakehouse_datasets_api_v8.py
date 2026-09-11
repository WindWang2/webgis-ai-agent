"""Lakehouse V8 — dataset 版本层 REST 面行为契约（ADR-0130）。

最小 app + 保真所有权守卫 fake（V7 API 测试同款）。覆盖：
- dataset 注册（幂等）；describe（refs/head）；版本历史；
- commit（typed 404/400）；branch/tag 创建；rollback；
- retention plan/execute（跨 dataset plan → 400；owner 404）。

注：本模块经 ``app.api.routes.lakehouse`` 导入 auth 链 —— Windows
本机缺 fcntl 时 importorskip 诚实跳过（与 CI/Linux 无关）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("app.api.routes.lakehouse_datasets")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.routes.lakehouse_datasets import router

_OWNED_SESSIONS = {"sess-v8"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    reset_filesystem_blob_store()
    reset_content_store_root_cache()

    from app.core.database import Base, Engine

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    # 持久 sqlite（./data/webgis.db）跨测试运行残留 → 重建域表。
    _tables = [t for t in Base.metadata.sorted_tables if t.name in (
        "lakehouse_datasets", "lakehouse_dataset_versions",
        "lakehouse_dataset_refs",
    )]
    for t in reversed(_tables):
        t.drop(bind=Engine, checkfirst=True)
    for t in _tables:
        t.create(bind=Engine, checkfirst=True)

    async def fake_verify(db, session_id, **kw):
        if session_id not in _OWNED_SESSIONS:
            raise HTTPException(status_code=404, detail="Session not found")
        return SimpleNamespace(session_id=session_id)

    import app.api.routes.lakehouse_datasets as mod

    monkeypatch.setattr(mod, "verify_session_owner", fake_verify)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.auth import get_async_db

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_async_db] = _fake_db
    with TestClient(app) as c:
        yield c
    reset_filesystem_blob_store()
    reset_content_store_root_cache()


def _owned_object(tag: bytes = b"rest"):
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    return publish_data_object(
        {"data.bin": tag},
        kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-v8"),
    ).data_object_id


def _create_dataset(client, name="ds-rest"):
    resp = client.post(
        "/api/v1/lakehouse/datasets",
        json={"session_id": "sess-v8", "name": name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_dataset_create_and_describe(client):
    body = _create_dataset(client)
    assert body["created"] is True
    did = body["dataset"]["dataset_id"]
    # 幂等重注册。
    again = _create_dataset(client)
    assert again["created"] is False
    # describe：refs 空（无提交）。
    resp = client.get(
        f"/api/v1/lakehouse/datasets/{did}",
        params={"session_id": "sess-v8"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["descriptor"]["name"] == "ds-rest"
    assert payload["head"] is None
    # 非 owner → 404（不泄漏存在性）。
    resp = client.get(
        f"/api/v1/lakehouse/datasets/{did}",
        params={"session_id": "intruder"},
    )
    assert resp.status_code == 404
    # 非法名 → 400。
    resp = client.post(
        "/api/v1/lakehouse/datasets",
        json={"session_id": "sess-v8", "name": "bad name!"},
    )
    assert resp.status_code == 400


def test_list_datasets_endpoint(client):
    _create_dataset(client, "list-a")
    _create_dataset(client, "list-b")
    resp = client.get(
        "/api/v1/lakehouse/datasets",
        params={"session_id": "sess-v8"},
    )
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["datasets"]]
    assert "list-a" in names and "list-b" in names
    # 非 owner → 守卫 404（无清单泄漏）。
    resp = client.get(
        "/api/v1/lakehouse/datasets",
        params={"session_id": "intruder"},
    )
    assert resp.status_code == 404


def test_commit_and_versions_flow(client):
    did = _create_dataset(client, "flow-ds")["dataset"]["dataset_id"]
    obj = _owned_object(b"flow-v1")
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/commit",
        json={
            "session_id": "sess-v8", "branch": "main",
            "data_object_id": obj,
            "provenance": {"algorithm": "ingest"},
        },
    )
    assert resp.status_code == 200, resp.text
    v1 = resp.json()["version_id"]
    assert resp.json()["parent_version_id"] is None
    # 版本历史。
    resp = client.get(
        f"/api/v1/lakehouse/datasets/{did}/versions",
        params={"session_id": "sess-v8"},
    )
    assert resp.status_code == 200
    assert [v["version_id"] for v in resp.json()["versions"]] == [v1]
    # 版本解析（含 commit record）。
    resp = client.get(
        f"/api/v1/lakehouse/datasets/{did}/versions/{v1}",
        params={"session_id": "sess-v8"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit"]["kind"] == "dataset_commit"
    assert body["content_available"] is True
    # 未知内容提交 → 404（DATASET_CONTENT_UNRESOLVED 归 404 族）。
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/commit",
        json={"session_id": "sess-v8", "branch": "main",
              "data_object_id": "f" * 64},
    )
    assert resp.status_code == 404


def test_branch_tag_rollback_endpoints(client):
    did = _create_dataset(client, "branch-ds")["dataset"]["dataset_id"]
    obj = _owned_object(b"base")
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/commit",
        json={"session_id": "sess-v8", "branch": "main",
              "data_object_id": obj},
    )
    v1 = resp.json()["version_id"]
    # 开分支。
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/branches",
        json={"session_id": "sess-v8", "name": "exp",
              "from_version_id": v1},
    )
    assert resp.status_code == 200 and resp.json()["created"] is True
    # tag（不可变；重复 → 409）。
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/tags",
        json={"session_id": "sess-v8", "name": "rel-1",
              "version_id": v1},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/tags",
        json={"session_id": "sess-v8", "name": "rel-1",
              "version_id": v1},
    )
    assert resp.status_code == 409
    # rollback 到当前 head → 400（NOOP）。
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/rollback",
        json={"session_id": "sess-v8", "branch": "main",
              "to_version_id": v1},
    )
    assert resp.status_code == 400
    # rollback 到未知版本 → 404。
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/rollback",
        json={"session_id": "sess-v8", "branch": "main",
              "to_version_id": "a" * 64},
    )
    assert resp.status_code == 404
    # lineage 端点。
    resp = client.get(
        f"/api/v1/lakehouse/datasets/{did}/lineage",
        params={"session_id": "sess-v8", "version_id": v1},
    )
    assert resp.status_code == 200
    assert resp.json()["chain"][0]["version_id"] == v1


def test_retention_endpoints(client):
    did = _create_dataset(client, "ret-ds")["dataset"]["dataset_id"]
    obj = _owned_object(b"ret")
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/commit",
        json={"session_id": "sess-v8", "branch": "main",
              "data_object_id": obj},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/retention/plan",
        json={"session_id": "sess-v8", "max_versions": 1,
              "min_age_hours": 0.0},
    )
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["candidate_count"] == 0  # head 版本受指针+窗口保护
    plan["dataset_row_id"] = "wrong-row"
    resp = client.post(
        f"/api/v1/lakehouse/datasets/{did}/retention/execute",
        json={"session_id": "sess-v8", "plan": plan},
    )
    assert resp.status_code == 400
