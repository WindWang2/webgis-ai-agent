"""Project Runtime Snapshot 路由接线测试（ADR-0226）。

生产面：鉴权三重门（项目 auth → 会话所有权 → 服务侧 created_by）、
freeze → inspect → resume → delete 的 HTTP 契约、跨项目/跨会话 404
不泄露存在性。种子经 ``asyncio.run`` 预置（与 app portal loop 顺序复用
session store 单例 —— Python 3.10+ asyncio 原语不在创建期绑 loop）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SID = "sess-rt-a"


def _auth_headers(sub: str = "rt-owner"):
    from app.core.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": "viewer"})}


def _seed_mapspec(sid: str) -> str:
    """同步包装：在临时 loop 内预置 ref + 账本 + MapSpec（返回 ref id）。"""
    from app.services.artifact_registry import register_artifact
    from app.services.mapspec.store import MapSpecStore
    from app.services.session_data import session_data_manager

    async def _seed() -> str:
        fc = {"type": "FeatureCollection", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.3, 39.9]},
             "properties": {"name": "x"}},
        ]}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await MapSpecStore().save_mapspec(sid, {
            "sources": {"poi": {"type": "geojson", "ref": ref, "ref_id": ref}},
            "layers": [{"id": "l1", "source": "poi"}],
            "view": {"zoom": 10},
        }, mutation_revision=1)
        return ref

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.database import Base, Engine, SessionLocal

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    from app.services.project_runtime.store import reset_project_runtime_store
    from app.services.workspace.snapshot import reset_workspace_snapshot_service

    reset_project_runtime_store()
    reset_workspace_snapshot_service()
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as s:
        from app.models.db_model import Conversation, User
        from app.models.project import Project, ProjectDataset

        s.merge(User(id="rt-owner", username="rt-owner",
                     email="rt-owner@example.com", password_hash="x",
                     role="viewer", is_active=True))
        s.merge(Project(id="proj-rt-route", name="route", owner_id="rt-owner"))
        s.merge(ProjectDataset(
            id="ds-rt-route", project_id="proj-rt-route", name="poi",
            source_type="upload", source_ref="up_1",
            version_fingerprint="fp-v1",
        ))
        # 会话所有权面：Conversation.user_id == 调用者
        s.merge(Conversation(id=SID, user_id="rt-owner", title="seed"))
        s.commit()
    yield
    reset_project_runtime_store()
    reset_workspace_snapshot_service()


def _freeze(label: str = "route-flow") -> str:
    res = client.post("/api/v1/projects/proj-rt-route/runtime-snapshots", json={
        "session_id": SID, "label": label, "freeze_reason": "explicit_save",
    }, headers=_auth_headers())
    assert res.status_code == 200, res.text
    return res.json()["snapshot_id"]


def test_freeze_requires_project_auth():
    _seed_mapspec(SID)
    res = client.post("/api/v1/projects/proj-rt-route/runtime-snapshots", json={
        "session_id": SID, "label": "x",
    }, headers=_auth_headers("someone-else"))
    assert res.status_code == 404  # 越权/不存在：一律 404 不泄露


def test_freeze_requires_session_ownership():
    _seed_mapspec(SID)
    res = client.post("/api/v1/projects/proj-rt-route/runtime-snapshots", json={
        "session_id": "sess-not-owned",  # 无 Conversation 行 / 非本人
    }, headers=_auth_headers())
    assert res.status_code == 404


def test_freeze_requires_authentication():
    res = client.post("/api/v1/projects/proj-rt-route/runtime-snapshots", json={
        "session_id": SID,
    })
    assert res.status_code == 401


def test_full_route_flow_freeze_inspect_resume_delete():
    ref = _seed_mapspec(SID)
    snapshot_id = _freeze()
    body = client.get(
        "/api/v1/projects/proj-rt-route/runtime-snapshots",
        headers=_auth_headers()).json()
    assert any(x["snapshot_id"] == snapshot_id for x in body["items"])

    inspect = client.get(
        f"/api/v1/projects/proj-rt-route/runtime-snapshots/{snapshot_id}",
        headers=_auth_headers())
    assert inspect.status_code == 200
    assert inspect.json()["status"] in (
        "ready", "ready_with_stale_inputs", "dataset_version_changed")
    assert inspect.json()["recovery_plan"]["steps"]

    resumed = client.post(
        f"/api/v1/projects/proj-rt-route/runtime-snapshots/{snapshot_id}/resume",
        json={"allow_stale": True}, headers=_auth_headers())
    assert resumed.status_code == 200, resumed.text
    receipt = resumed.json()
    assert receipt["session_id"] != SID
    assert receipt["mapspec_rebound"] is True
    assert ref in receipt["ref_map"]

    deleted = client.delete(
        f"/api/v1/projects/proj-rt-route/runtime-snapshots/{snapshot_id}",
        headers=_auth_headers())
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(
        f"/api/v1/projects/proj-rt-route/runtime-snapshots/{snapshot_id}",
        headers=_auth_headers()).status_code == 404


def test_resume_missing_snapshot_404():
    res = client.post(
        "/api/v1/projects/proj-rt-route/runtime-snapshots/prs-none/resume",
        json={"allow_stale": True}, headers=_auth_headers())
    assert res.status_code == 404


def test_cross_project_snapshot_404():
    """快照按项目域解析：别的项目 id 摸不到本项目快照（存在性不泄露）。"""
    _seed_mapspec(SID)
    snapshot_id = _freeze()
    res = client.get(
        f"/api/v1/projects/proj-rt-route2/runtime-snapshots/{snapshot_id}",
        headers=_auth_headers())
    assert res.status_code == 404
