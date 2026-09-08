"""Lakehouse V6 Wave 13/Review — REST 面行为契约（含跨租户回归）。

最小 app + 独立 router。所有权守卫用**保真 fake**（复刻 verify_session_owner
的 allow/deny 语义 —— 拒绝时抛同样的 404），不整体覆盖路由逻辑：POST 端点
的 body session_id 与守卫的绑定关系由此真实受测（review B1 的回归锁）。
覆盖：manifest 读取（非 owner 404 不泄漏）、矢量窗口扫描（剪枝证据 + 跨租户
404）、cube 构建/窗口读/修订、无界窗口拒绝、project_id 拒绝。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app.api.routes import lakehouse as lakehouse_mod
from app.api.routes.lakehouse import router

pytest.importorskip("zarr")
pytest.importorskip("pyarrow")

_OWNED_SESSIONS = {
    "sess-api", "sess-cube", "sess-obj", "sess-win",
}


@pytest.fixture()
def client():
    """最小 app + 保真所有权守卫 fake：deny 抛 404（同 verify_session_owner
    语义），allow 返回带 session_id 的 conv —— POST 的 body session_id 与
    守卫的绑定关系真实受测。"""
    async def fake_verify(db, session_id, **kw):
        if session_id not in _OWNED_SESSIONS:
            raise HTTPException(status_code=404, detail="Session not found")
        return SimpleNamespace(session_id=session_id)

    lakehouse_mod.verify_session_owner = fake_verify
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.auth import get_async_db

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_async_db] = _fake_db
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


def _seed_parquet(data_dir):
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.0 + i * 1e-4, 0.0]},
         "properties": {"tag": "a"}} for i in range(4)
    ] + [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [90.0, 0.0]},
         "properties": {"tag": "b"}} for _ in range(4)
    ]
    table = features_to_arrow(feats, crs="EPSG:4326")
    import asyncio

    return asyncio.get_event_loop().run_until_complete(
        materialization_service.materialize_geoparquet(
            "sess-api", table, "T", row_group_size=4,
        )
    )


def test_scan_endpoint_prunes_and_honest_404(client, data_dir):
    res = _seed_parquet(data_dir)
    resp = client.post("/api/v1/lakehouse/vector/scan", json={
        "session_id": "sess-api", "ref": res["ref"],
        "bbox": [-0.01, -0.01, 0.01, 0.01],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["properties"]["row_groups_read"] < body["properties"]["row_groups_total"]
    assert [f["properties"]["tag"] for f in body["features"]] == ["a"] * 4

    # 跨租户：body 的 victim session 不为调用方所有 → 守卫 404（B1 回归锁：
    # 守卫绑定 body session_id，而非可伪造的旁路参数）。
    resp = client.post("/api/v1/lakehouse/vector/scan", json={
        "session_id": "victim-session", "ref": res["ref"],
        "bbox": [0, 0, 1, 1],
    })
    assert resp.status_code == 404
    # 非法窗口 → schema 校验 422。
    resp = client.post("/api/v1/lakehouse/vector/scan", json={
        "session_id": "sess-api", "ref": res["ref"], "bbox": [0, 1],
    })
    assert resp.status_code == 422


def test_cube_endpoints(client, data_dir):
    src = data_dir / "src"
    src.mkdir(parents=True)
    resp = client.post("/api/v1/lakehouse/cubes", json={
        "session_id": "sess-cube",
        "title": "api cube",
        "time_sources": [
            {"time": "2024-01", "source": _write_slice(src / "t1.tif", 1.0)},
            {"time": "2024-02", "source": _write_slice(src / "t2.tif", 2.0)},
        ],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ref"].startswith("ref:cube/")
    assert body["durable"] == "published"

    resp = client.post("/api/v1/lakehouse/cubes/window", json={
        "session_id": "sess-cube", "ref": body["ref"], "time": [1, 2],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["bands"]["b1"][0][0][0]) == 2.0
    assert data["times"] == ["2024-02"]

    # 无界窗口（全 cube 读）→ 422（review M-2）。
    resp = client.post("/api/v1/lakehouse/cubes/window", json={
        "session_id": "sess-cube", "ref": body["ref"],
    })
    assert resp.status_code == 422
    # 负切片 → 422。
    resp = client.post("/api/v1/lakehouse/cubes/window", json={
        "session_id": "sess-cube", "ref": body["ref"], "y": [-2, 2],
    })
    assert resp.status_code == 422
    # 跨租户 → 404。
    resp = client.post("/api/v1/lakehouse/cubes/window", json={
        "session_id": "other", "ref": body["ref"], "time": [0, 1],
    })
    assert resp.status_code == 404

    # 修订端点（fork 的生产调用方）：t=0 以新源重写。
    resp = client.post("/api/v1/lakehouse/cubes/revise", json={
        "session_id": "sess-cube",
        "ref": body["ref"],
        "title": "rev1",
        "updates": [
            {"band": "b1", "time_index": 0,
             "source": _write_slice(src / "new0.tif", 9.0)},
        ],
    })
    assert resp.status_code == 200, resp.text
    rev = resp.json()
    assert rev["ref"] != body["ref"]
    assert rev["revision_of"] == body["ref"]
    assert float(rev["first_step_preview"]["b1"][0][0][0]) == 9.0


def test_object_manifest_and_verify_owner_gated(client, data_dir):
    from app.services.lakehouse.data_object import publish_data_object

    identity = publish_data_object(
        {"d.bin": b"x"}, kind="vector_parquet",
        owner_scope={"session_id": "sess-obj"},
    )
    resp = client.get(f"/api/v1/lakehouse/objects/{identity.data_object_id}"
                      f"?session_id=sess-obj")
    assert resp.status_code == 200
    assert resp.json()["manifest"]["kind"] == "vector_parquet"

    # 非 owner → 404（不泄漏存在性）。
    resp = client.get(f"/api/v1/lakehouse/objects/{identity.data_object_id}"
                      f"?session_id=sess-obj2")
    assert resp.status_code == 404

    # project_id 在 REST 面显式拒绝（review M1/M-5）。
    resp = client.get(f"/api/v1/lakehouse/objects/{identity.data_object_id}"
                      f"?project_id=p1")
    assert resp.status_code == 400

    resp = client.post(f"/api/v1/lakehouse/objects/{identity.data_object_id}/verify",
                       json={"session_id": "sess-obj"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "verified"

    resp = client.post(f"/api/v1/lakehouse/objects/{identity.data_object_id}/verify",
                       json={"session_id": "sess-obj2"})
    assert resp.status_code == 404
