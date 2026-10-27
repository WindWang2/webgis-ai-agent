"""G3D-V2-A A2：上传 raster/DEM 摄入车道 —— 四段链第一段（upload/registration）。

覆盖：声明 datum+unit → analysis_grade + terrain ref；无声明 → display_only
如实披露；结构不合格（无 CRS）→ unavailable 且上传本体不受阻断；矢量车道
回归零变更；幂等重注册复用 ref。
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

_MOCK_USER = {"user_id": "g3dv2a-raster-lane-user"}

_BBOX = (103.90, 30.50, 104.25, 30.80)
_SHAPE = (128, 128)

_GEOJSON_BYTES = (
    b'{"type":"FeatureCollection","features":[{"type":"Feature",'
    b'"geometry":{"type":"Point","coordinates":[116.4,39.9]},"properties":{"name":"a"}}]}'
)


def _dem_bytes(*, crs="EPSG:4326", elevation=None, unit_tag=None, synthetic=False) -> bytes:
    import io

    if elevation is None:
        elevation = np.linspace(500.0, 1200.0, _SHAPE[0] * _SHAPE[1]).reshape(_SHAPE)
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", width=_SHAPE[1], height=_SHAPE[0], count=1,
        dtype="float32", crs=crs,
        transform=from_bounds(*_BBOX, width=_SHAPE[1], height=_SHAPE[0]),
    ) as dst:
        dst.write(np.asarray(elevation, dtype="float32"), 1)
        if unit_tag:
            dst.set_band_unit(1, unit_tag)
        if synthetic:
            dst.update_tags(SYNTHETIC_DEM="1")
    return buf.getvalue()


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'raster_lane.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    from app.core.auth import get_current_user
    from app.api.routes import upload as upload_routes
    from app.models.db_model import Base  # noqa: F401
    import app.models.upload  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from app.tools import _utils

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr(upload_routes, "async_db_session", override_async_db_session)
    monkeypatch.setattr(upload_routes.settings, "DATA_DIR", str(tmp_path))

    app = FastAPI()
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    app.include_router(upload_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: _MOCK_USER
    try:
        yield app, test_session, tmp_path
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRasterTerrainLane:
    async def test_declared_datum_unit_registers_analysis_grade(self, client, app_and_db):
        _, _, tmp_path = app_and_db
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(), "image/tiff")},
            data={
                "session_id": "sess-terrain-lane-1",
                "register_ref": "true",
                "vertical_datum": "EGM96",
                "vertical_unit": "m",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["file_type"] == "raster"
        assert body["session_ref"] and body["session_ref"].startswith("ref:terrain-")
        assert body["ref_registration_error"] is None
        terrain = body["terrain"]
        assert terrain["qualification"] == "analysis_grade"
        assert terrain["vertical_datum"] == "EGM96"
        assert terrain["tiles_url_template"].startswith("/api/v1/layers/data/ref:terrain-")
        assert terrain["tilejson_url"].endswith("/terrain-tiles.json")
        # 上传本体元数据剖析（V4 回归）不受车道影响
        assert body["meta"] and body["meta"].get("band_count") == 1

    async def test_missing_datum_declaration_is_display_only(self, client, app_and_db):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(), "image/tiff")},
            data={
                "session_id": "sess-terrain-lane-2",
                "register_ref": "true",
                "vertical_unit": "m",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        terrain = body["terrain"]
        assert terrain["qualification"] == "display_only"
        codes = [x["code"] for x in terrain["reasons"]]
        assert "UNKNOWN_VERTICAL_DATUM" in codes
        assert any("仅限显示" in w for w in body["warnings"])

    async def test_structural_failure_unavailable_upload_survives(self, client, app_and_db):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(crs=None), "image/tiff")},
            data={
                "session_id": "sess-terrain-lane-3",
                "register_ref": "true",
                "vertical_datum": "EGM96",
            },
        )
        # 无 CRS 栅格本身仍被上传接受（crs_missing 披露）——但 terrain 车道诚实拒绝
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_ref"] is None
        assert "TERRAIN_INGEST_NO_CRS" in body["ref_registration_error"]
        assert body["terrain"]["qualification"] == "unavailable"
        assert body["terrain"]["tiles_url_template"] is None

    async def test_synthetic_tag_display_only(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(synthetic=True), "image/tiff")},
            data={
                "session_id": "sess-terrain-lane-4",
                "register_ref": "true",
                "vertical_datum": "EGM96",
                "vertical_unit": "m",
            },
        )
        body = r.json()
        assert body["terrain"]["qualification"] == "display_only"
        assert body["terrain"]["synthetic"] is True

    async def test_idempotent_reregistration_reuses_ref(self, client, app_and_db):
        payload = {
            "session_id": "sess-terrain-lane-5",
            "dedup": "false",  # 绕过上传记录级 dedup，验证车道级 sha256 幂等
            "register_ref": "true",
            "vertical_datum": "EGM96",
            "vertical_unit": "m",
        }
        r1 = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(), "image/tiff")},
            data=payload,
        )
        r2 = await client.post(
            "/api/v1/upload",
            files={"files": ("dem-again.tif", _dem_bytes(), "image/tiff")},
            data=payload,
        )
        assert r1.status_code == r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        assert b2["session_ref"] == b1["session_ref"]
        assert any("复用" in w for w in b2["warnings"])

    async def test_lane_off_by_default_for_raster(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("dem.tif", _dem_bytes(), "image/tiff")},
            data={"session_id": "sess-terrain-lane-6"},
        )
        body = r.json()
        assert body["session_ref"] is None
        assert body["terrain"] is None  # 未 opt-in → 车道不跑，行为与旧版一致


class TestVectorLaneRegression:
    async def test_vector_lane_unchanged(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-vector-lane-1", "register_ref": "true"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["file_type"] == "vector"
        assert body["terrain"] is None
        # 矢量车道照旧产出 session_ref（V3 管线）
        assert body["session_ref"] is not None

    async def test_no_register_ref_no_lane_error(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-vector-lane-2"},
        )
        assert r.status_code == 200
        assert r.json()["ref_registration_error"] is None
