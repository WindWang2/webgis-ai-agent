"""Lakehouse V6 Wave 13 — REST 面行为契约。

最小 app + 独立 router（test_session_api.py 同款 harness）；所有权守卫
经 patch AsyncHistoryService.get_session_meta 通过（正向/负向跨租户
由 test_cross_tenant_isolation 端到端覆盖）。覆盖：

- manifest 读取（owner 校验：非 owner = 404，不泄漏存在性）；
- 矢量窗口扫描（剪枝证据 + 死 ref 404 + 非法窗口 400）；
- cube 构建 / 窗口读（session 域）；
- DR verify（state 透传）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
import rasterio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app.api.routes.lakehouse import router

pytest.importorskip("zarr")
pytest.importorskip("pyarrow")


@pytest.fixture()
def client():
    """最小 app + 所有权守卫放行（FastAPI dependency_overrides —— 路由
    装饰时已捕获 require_owned_session，patch 模块属性无效）。"""
    from app.core.auth import require_owned_session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_owned_session] = lambda: MagicMock()
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

    # 死 ref → 404（不泄漏）。
    resp = client.post("/api/v1/lakehouse/vector/scan", json={
        "session_id": "other", "ref": res["ref"],
        "bbox": [0, 0, 1, 1],
    })
    assert resp.status_code == 404
    # 非法窗口 → schema 校验 422（bbox 长度契约在请求模型层强制）。
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

    # 跨会话 → 404。
    resp = client.post("/api/v1/lakehouse/cubes/window", json={
        "session_id": "other", "ref": body["ref"],
    })
    assert resp.status_code == 404


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
                      f"?session_id=sess-other")
    assert resp.status_code == 404

    resp = client.post(f"/api/v1/lakehouse/objects/{identity.data_object_id}/verify",
                       json={"session_id": "sess-obj"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "verified"

    resp = client.post(f"/api/v1/lakehouse/objects/{identity.data_object_id}/verify",
                       json={"session_id": "sess-other"})
    assert resp.status_code == 404
