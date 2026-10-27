"""G3D-V2-A A6：地图资源可用性 typed 摘要 —— 契约锁定 + 三腿合成。

覆盖：静态画像部署投影（cloud/air_gapped）；会话 DEM 清单与 receipt 透传；
3D 总判（available / available_with_limits / unavailable + 解释）；跨会话
隔离；response 形状契约锁（MapResourceAvailabilitySummary）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
import rasterio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from rasterio.transform import from_bounds
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_BBOX = (103.90, 30.50, 104.25, 30.80)
_SHAPE = (128, 128)
_MOCK_USER = {"user_id": "summary-user-1"}


def _write_dem(path: Path) -> Path:
    elev = np.linspace(500.0, 1200.0, _SHAPE[0] * _SHAPE[1]).reshape(_SHAPE)
    with rasterio.open(
        path, "w", driver="GTiff", width=_SHAPE[1], height=_SHAPE[0], count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0,
        transform=from_bounds(*_BBOX, width=_SHAPE[1], height=_SHAPE[0]),
    ) as dst:
        dst.write(elev.astype("float32"), 1)
    return path


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'summary.db'}"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    db_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    from app.core.auth import get_current_user_optional, get_owner_token
    from app.core.database import get_async_db
    from app.api.routes import layer as layer_routes
    from app.models.db_model import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_db():
        async with db_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from app.services.session_data import SessionDataManager
    from app.core.config import settings as global_settings

    fresh_manager = SessionDataManager(capacity=8)
    monkeypatch.setattr(layer_routes, "session_data_manager", fresh_manager)
    monkeypatch.setattr(global_settings, "DATA_DIR", str(tmp_path))

    app = FastAPI()
    app.include_router(layer_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_db
    app.dependency_overrides[get_current_user_optional] = lambda: _MOCK_USER
    try:
        yield {"app": app, "db": db_factory, "tmp": tmp_path, "manager": fresh_manager,
               "settings": global_settings, "monkeypatch": monkeypatch}
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(env, session_id: str):
    from app.models.db_model import Conversation, User

    async with env["db"]() as db:
        uid = _MOCK_USER["user_id"]
        if await db.get(User, uid) is None:
            db.add(User(
                id=uid, username=uid, email=f"{uid}@example.com",
                password_hash="scrypt$16384$8$1$00$00", role="editor",
                is_active=True, token_version=0,
            ))
        db.add(Conversation(id=session_id, user_id=uid))
        await db.commit()


async def _register(env, session_id: str, *, datum="EGM96") -> str:
    from app.services.terrain_ingest import register_session_terrain_source

    dem = _write_dem(env["tmp"] / f"dem-{session_id}.tif")
    out = await register_session_terrain_source(
        session_id, dem, declared_vertical_datum=datum, declared_vertical_unit="m",
        content_sha256="f" * 64, manager=env["manager"], name=f"{session_id}.tif",
    )
    assert out["ok"], out["error"]
    return out["ref_id"]


class TestSummaryContract:
    async def test_response_shape_locked(self, client, env):
        sid = "sess-summary-shape-01"
        await _seed(env, sid)
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {
            "session_id", "elevation_profiles", "session_terrain_sources",
            "terrain_3d_available", "terrain_3d_status", "terrain_3d_reason_code",
            "terrain_3d_detail",
        }
        assert body["session_id"] == sid

    async def test_cloud_no_session_dem_fallback_only(self, client, env):
        sid = "sess-summary-cloud-01"
        await _seed(env, sid)
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        body = r.json()
        assert body["terrain_3d_available"] is True
        assert body["terrain_3d_status"] == "available_with_limits"
        assert body["terrain_3d_reason_code"] == "REMOTE_FALLBACK_ONLY"
        assert body["session_terrain_sources"] == []
        # 静态腿：远端兜底在 cloud 可用；合成演示源永久 display_only
        by_id = {p["source_id"]: p for p in body["elevation_profiles"]}
        assert by_id["aws-terrarium-public"]["deployment_available"] is True
        assert by_id["aws-terrarium-public"]["qualification"] == "display_only"
        assert by_id["synthetic-demo-dem"]["synthetic"] is True
        assert by_id["synthetic-demo-dem"]["qualification"] == "display_only"

    async def test_session_dem_makes_available(self, client, env):
        sid = "sess-summary-dem-01"
        await _seed(env, sid)
        await _register(env, sid)
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        body = r.json()
        assert body["terrain_3d_status"] == "available"
        assert body["terrain_3d_reason_code"] == "SESSION_TERRAIN_SOURCE"
        sources = body["session_terrain_sources"]
        assert len(sources) == 1
        src = sources[0]
        assert src["ref_id"].startswith("ref:terrain-")
        assert src["name"] == f"{sid}.tif"
        assert src["receipt"]["qualification"] == "analysis_grade"
        assert src["receipt"]["vertical_datum"] == "EGM96"
        assert src["tiles_url_template"].startswith("/api/v1/layers/data/ref:terrain-")
        assert src["tilejson_url"].endswith("/terrain-tiles.json")

    async def test_display_only_receipt_passthrough(self, client, env):
        sid = "sess-summary-disp-01"
        await _seed(env, sid)
        await _register(env, sid, datum=None)
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        src = r.json()["session_terrain_sources"][0]
        assert src["receipt"]["qualification"] == "display_only"
        codes = [x["code"] for x in src["receipt"]["reasons"]]
        assert "UNKNOWN_VERTICAL_DATUM" in codes

    async def test_air_gapped_no_dem_unavailable_with_explanation(self, client, env):
        sid = "sess-summary-airgap-01"
        await _seed(env, sid)
        env["monkeypatch"].setattr(env["settings"], "DEPLOYMENT_PROFILE", "air_gapped")
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        body = r.json()
        assert body["terrain_3d_available"] is False
        assert body["terrain_3d_status"] == "unavailable"
        assert body["terrain_3d_reason_code"] == "NO_TERRAIN_SOURCE"
        assert "air_gapped" in body["terrain_3d_detail"]
        by_id = {p["source_id"]: p for p in body["elevation_profiles"]}
        assert by_id["aws-terrarium-public"]["deployment_available"] is False
        assert by_id["aws-terrarium-public"]["unavailable_reason"] == "deployment_not_permitted"

    async def test_air_gapped_with_session_dem_available(self, client, env):
        sid = "sess-summary-airgap-02"
        await _seed(env, sid)
        await _register(env, sid)
        env["monkeypatch"].setattr(env["settings"], "DEPLOYMENT_PROFILE", "air_gapped")
        r = await client.get(f"/api/v1/layers/terrain-sources?session_id={sid}")
        body = r.json()
        # 本地 DEM 不受 air_gapped 影响（离线可用正是 A6 受限样例）
        assert body["terrain_3d_status"] == "available"
        assert body["terrain_3d_reason_code"] == "SESSION_TERRAIN_SOURCE"

    async def test_cross_session_isolation(self, client, env):
        await _seed(env, "sess-summary-a-01")
        await _seed(env, "sess-summary-b-01")
        await _register(env, "sess-summary-a-01")
        rb = await client.get("/api/v1/layers/terrain-sources?session_id=sess-summary-b-01")
        assert rb.json()["session_terrain_sources"] == []
        # 无会话行 → 404（不泄漏存在性）
        r404 = await client.get("/api/v1/layers/terrain-sources?session_id=sess-unknown-9999")
        assert r404.status_code == 404
