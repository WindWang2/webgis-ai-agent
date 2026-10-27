"""G3D-V2-A A4：会话 terrain TileJSON 端点 —— 四段链闭环（materialization→tile）。

覆盖：receipt 驱动的 bounds/maxzoom/terrain 扩展块；tiles URL 直接可取瓦片；
鉴权（跨会话 404、匿名 ?token=、错误 token 404）；ETag 304；无 CRS 非
摄入 ref 422 fail-closed；退役 ref 404。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
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

_MOCK_USER = {"user_id": "tilejson-user-1"}
_ANON_USER: dict = {}


def _write_dem(path: Path) -> Path:
    elev = np.linspace(500.0, 1200.0, _SHAPE[0] * _SHAPE[1]).reshape(_SHAPE)
    with rasterio.open(
        path, "w", driver="GTiff", width=_SHAPE[1], height=_SHAPE[0], count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0,
        transform=from_bounds(*_BBOX, width=_SHAPE[1], height=_SHAPE[0]),
    ) as dst:
        dst.write(elev.astype("float32"), 1)
    return path


async def _seed_conversation(db_factory, session_id: str, *, user_id=None, owner_token=None):
    from app.models.db_model import Conversation, User

    async with db_factory() as db:
        if user_id is not None:
            user = await db.get(User, user_id)
            if user is None:
                db.add(User(
                    id=user_id, username=user_id, email=f"{user_id}@example.com",
                    password_hash="scrypt$16384$8$1$00$00", role="editor",
                    is_active=True, token_version=0,
                ))
        db.add(Conversation(id=session_id, user_id=user_id, owner_token=owner_token))
        await db.commit()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """app + sqlite DB + 内存 session store + DATA_DIR 隔离。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tilejson.db'}"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    db_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    from app.core.auth import get_current_user_optional
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
    # 每个用例按需切换认证/匿名
    app.dependency_overrides[get_current_user_optional] = lambda: _MOCK_USER
    try:
        yield {
            "app": app, "db": db_factory, "tmp": tmp_path,
            "manager": fresh_manager, "monkeypatch": monkeypatch,
            "layer": layer_routes,
        }
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_dem(env, session_id: str, *, datum="EGM96") -> str:
    from app.services.terrain_ingest import register_session_terrain_source

    dem = _write_dem(env["tmp"] / f"dem-{session_id}.tif")
    out = await register_session_terrain_source(
        session_id, dem,
        declared_vertical_datum=datum, declared_vertical_unit="m",
        content_sha256=hashlib64(dem), manager=env["manager"],
    )
    assert out["ok"], out["error"]
    return out["ref_id"]


def hashlib64(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


SESSION = "sess-tilejson-auth-0001"


class TestTileJsonContract:
    async def test_receipt_driven_tilejson_and_tile_chain(self, client, env):
        await _seed_conversation(env["db"], SESSION, user_id=_MOCK_USER["user_id"])
        ref_id = await _register_dem(env, SESSION)

        r = await client.get(
            f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id={SESSION}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tilejson"] == "3.0.0"
        assert body["minzoom"] == 0 and 0 <= body["maxzoom"] <= 20
        assert body["bounds"] == pytest.approx(list(_BBOX), abs=1e-4)
        assert body["attribution"].startswith("User-provided elevation data")
        terrain = body["terrain"]
        assert terrain["encoding"] == "terrarium"
        assert terrain["qualification"] == "analysis_grade"
        assert terrain["vertical_datum"] == "EGM96"
        assert terrain["synthetic"] is False

        # 四段链闭环：tiles URL 直接取回 terrarium PNG
        tiles_url = body["tiles"][0]
        assert f"session_id={SESSION}" in tiles_url
        z = body["maxzoom"]
        import math

        n = 1 << z
        lat, lon = (_BBOX[1] + _BBOX[3]) / 2, (_BBOX[0] + _BBOX[2]) / 2
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        rt = await client.get(tiles_url.format(z=z, x=x, y=y))
        assert rt.status_code == 200, rt.text
        assert rt.headers["content-type"] == "image/png"

    async def test_etag_304(self, client, env):
        await _seed_conversation(env["db"], SESSION + "b", user_id=_MOCK_USER["user_id"])
        ref_id = await _register_dem(env, SESSION + "b")
        url = f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id={SESSION}b"
        r1 = await client.get(url)
        assert r1.status_code == 200
        etag = r1.headers["ETag"]
        r2 = await client.get(url, headers={"If-None-Match": etag})
        assert r2.status_code == 304

    async def test_cross_session_404(self, client, env):
        await _seed_conversation(env["db"], SESSION + "c", user_id=_MOCK_USER["user_id"])
        ref_id = await _register_dem(env, SESSION + "c")
        # 另一会话（无行）→ 404；不泄漏存在性
        r = await client.get(
            f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id=sess-other-user-999"
        )
        assert r.status_code == 404

    async def test_anonymous_query_token(self, client, env):
        sid = "sess-tilejson-anon-001"
        await _seed_conversation(env["db"], sid, user_id=None, owner_token="tok-anon-123")
        ref_id = await _register_dem(env, sid)
        from app.core.auth import get_current_user_optional

        env["app"].dependency_overrides[get_current_user_optional] = lambda: _ANON_USER
        base = f"/api/v1/layers/data/{ref_id}/terrain-tiles.json"
        # 无 token → 404（匿名会话必须有 owner token）
        r0 = await client.get(f"{base}?session_id={sid}")
        assert r0.status_code == 404
        # 错 token → 404
        r1 = await client.get(f"{base}?session_id={sid}&token=wrong")
        assert r1.status_code == 404
        # 对 token → 200 且 tiles URL 携带 token（MapLibre 可直接消费）
        r2 = await client.get(f"{base}?session_id={sid}&token=tok-anon-123")
        assert r2.status_code == 200, r2.text
        assert f"&token=tok-anon-123" in r2.json()["tiles"][0]

    async def test_retired_ref_404(self, client, env):
        sid = "sess-tilejson-retire-01"
        await _seed_conversation(env["db"], sid, user_id=_MOCK_USER["user_id"])
        ref_id = await _register_dem(env, sid)
        await env["manager"].delete_ref(sid, ref_id)
        r = await client.get(
            f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id={sid}"
        )
        assert r.status_code == 404

    async def test_non_ingested_no_crs_ref_422(self, client, env):
        sid = "sess-tilejson-nocrs-01"
        await _seed_conversation(env["db"], sid, user_id=_MOCK_USER["user_id"])
        # 无 CRS 的裸 GeoTIFF ref（模拟工具产物/旧路径注册），TileJSON fail-closed
        p = env["tmp"] / "bare.tif"
        with rasterio.open(
            p, "w", driver="GTiff", width=64, height=64, count=1, dtype="float32",
            transform=from_bounds(0, 0, 1, 1, width=64, height=64),
        ) as dst:
            dst.write(np.zeros((64, 64), dtype="float32"), 1)
        ref_id = await env["manager"].store(sid, {"file_path": str(p)}, prefix="raster")
        r = await client.get(
            f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id={sid}"
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "TERRAIN_REQUIRES_CRS"

    async def test_display_only_receipt_passthrough(self, client, env):
        sid = "sess-tilejson-disp-001"
        await _seed_conversation(env["db"], sid, user_id=_MOCK_USER["user_id"])
        # 不声明 datum → display_only；TileJSON 原样透传资格与 reasons
        ref_id = await _register_dem(env, sid, datum=None)
        r = await client.get(
            f"/api/v1/layers/data/{ref_id}/terrain-tiles.json?session_id={sid}"
        )
        assert r.status_code == 200
        terrain = r.json()["terrain"]
        assert terrain["qualification"] == "display_only"
        codes = [x["code"] for x in terrain["reasons"]]
        assert "UNKNOWN_VERTICAL_DATUM" in codes
