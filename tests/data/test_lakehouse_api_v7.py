"""Lakehouse V7 Wave 18 — REST 面行为契约（owner 门禁 / 预算 / 脱敏）。

最小 app + 保真所有权守卫 fake（V6 API 测试同款）。覆盖：
- RS cube 构建（typed 网格闸 → 400/404）；labeled 窗口读（无选择 422、
  触达块证据随响应）；发布（owner 链 / 未知对象清单）；
- catalog 双域（session 守卫 deny 404 / project 非 owner 404）；
- GC plan/execute（stale → 409）；scrub（非 owner 404）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("zarr")
pytest.importorskip("rasterio")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.routes import lakehouse as lakehouse_mod
from app.api.routes.lakehouse import router

_OWNED_SESSIONS = {"sess-v7"}


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

    async def fake_verify(db, session_id, **kw):
        if session_id not in _OWNED_SESSIONS:
            raise HTTPException(status_code=404, detail="Session not found")
        return SimpleNamespace(session_id=session_id)

    monkeypatch.setattr(lakehouse_mod, "verify_session_owner", fake_verify)
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


def _owned_object():
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    identity = publish_data_object(
        {"data.bin": b"rest"},
        kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-v7"),
    )
    return identity.data_object_id


def test_scrub_requires_owner(client):
    ghost = "f" * 64
    resp = client.post(
        f"/api/v1/lakehouse/objects/{ghost}/scrub",
        json={"session_id": "sess-v7", "mode": "sample"},
    )
    assert resp.status_code == 404  # 不可解析/非 owner 一律 404（不泄漏）
    resp = client.post(
        f"/api/v1/lakehouse/objects/{ghost}/scrub",
        json={"session_id": "sess-attacker", "mode": "sample"},
    )
    assert resp.status_code == 404


def test_labeled_window_requires_selection(client):
    resp = client.post(
        "/api/v1/lakehouse/cubes/labeled/window",
        json={"session_id": "sess-v7", "ref": "ref:cube/x"},
    )
    assert resp.status_code == 422


def test_rs_cube_grid_mismatch_is_typed_400(client, tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    src = tmp_path / "data" / "src"
    src.mkdir(parents=True, exist_ok=True)

    def _tif(name, transform, value=1.0):
        path = src / name
        with rasterio.open(
            path, "w", driver="GTiff", width=8, height=8, count=1,
            dtype="float32", crs="EPSG:4326", transform=transform,
        ) as ds:
            ds.write(np.full((8, 8), value, dtype="float32"), 1)
        return str(path)

    tf_ok = from_origin(0, 8, 1, 1)
    tf_shift = from_origin(0.5, 8, 1, 1)
    a = _tif("a.tif", tf_ok)
    b = _tif("b.tif", tf_shift)
    resp = client.post(
        "/api/v1/lakehouse/cubes/rs",
        json={
            "session_id": "sess-v7",
            "title": "bad",
            "sources": [
                {"time": "t0", "source": a, "role": "optical", "band": "B02"},
                {"time": "t1", "source": b, "role": "optical", "band": "B02"},
            ],
        },
    )
    assert resp.status_code == 400
    assert "grid differs" in resp.json()["detail"]


def test_catalog_dual_scope_fail_closed(client, monkeypatch):
    # session 域：非 owner → 404（守卫 deny）。
    resp = client.get(
        "/api/v1/lakehouse/catalog",
        params={"owner_type": "session", "owner_id": "sess-attacker"},
    )
    assert resp.status_code == 404

    # project 域：DB 查无项目 → 404（不泄漏存在性；保真 fake 返回查无）。
    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()

    from app.core.auth import get_async_db

    async def _fake_db():
        yield _FakeDB()

    client.app.dependency_overrides[get_async_db] = _fake_db
    resp = client.get(
        "/api/v1/lakehouse/catalog",
        params={"owner_type": "project", "owner_id": "proj-ghost"},
    )
    assert resp.status_code == 404
    # 非法 owner_type → 400。
    resp = client.get(
        "/api/v1/lakehouse/catalog",
        params={"owner_type": "global", "owner_id": "x"},
    )
    assert resp.status_code == 400


def test_gc_plan_and_execute_owner_gated(client):
    resp = client.post(
        "/api/v1/lakehouse/gc/plan",
        json={"session_id": "sess-attacker"},
    )
    assert resp.status_code == 404
    # owner 通过 → plan 只读（本会话 blob store 可能空）。
    resp = client.post(
        "/api/v1/lakehouse/gc/plan",
        json={"session_id": "sess-v7", "grace_hours": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "token" in body and "candidates" in body
    # 伪造 plan（token 不匹配）→ 409 stale。
    resp = client.post(
        "/api/v1/lakehouse/gc/execute",
        json={"session_id": "sess-v7", "plan": {
            "candidates": ["f" * 64], "deletable_blobs": [],
            "watermark": 0.0, "token": "forged", "grace_hours": 0,
        }},
    )
    assert resp.status_code == 409


def test_publish_endpoint_requires_owned_session(client):
    resp = client.post(
        "/api/v1/lakehouse/publish",
        json={
            "session_id": "sess-attacker",
            "project_id": "proj-x",
            "object_ids": ["a" * 64],
        },
    )
    assert resp.status_code == 404
