"""B1：/upload 路由必须把同步 parse_* 调用扔给 executor，不阻塞事件循环。

不打真实文件，只验证 parse_vector / parse_raster 通过 run_in_executor 调用，
线程已切换。
"""
import threading
from io import BytesIO
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user

_mock_user = {"user_id": "test-user"}


@pytest_asyncio.fixture
async def app_and_signals(tmp_path, monkeypatch):
    # 把 settings.DATA_DIR 指向 tmp，避免污染生产 data/
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "DATA_DIR", str(tmp_path))

    from app.api.routes import upload as upload_routes

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _mock_user
    app.include_router(upload_routes.router, prefix="/api/v1")

    # 记录调用 parse_vector 时所在的线程 id
    signals = {"main_thread": threading.get_ident(), "parse_thread": None}

    def fake_parse_vector(temp_path, upload_dir, upload_id):
        signals["parse_thread"] = threading.get_ident()
        return {
            "output_path": str(temp_path),
            "file_type": "vector",
            "format": "geojson",
            "crs": "EPSG:4326",
            "feature_count": 0,
            "bbox": None,
            "geometry_type": "Point",
        }

    # 让数据库写入也走 patch — 测试不关心 ORM 落盘
    async def fake_async_session():
        class _Db:
            def add(self, _):
                pass
            async def flush(self):
                pass
            async def refresh(self, _):
                pass
        from contextlib import asynccontextmanager
        return _Db()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_ctx():
        class _Db:
            def add(self, rec):
                # FastAPI 期望 record.id 可读，给一个
                rec.id = 1
            async def flush(self):
                pass
            async def refresh(self, _):
                pass
        yield _Db()

    monkeypatch.setattr(upload_routes, "parse_vector", fake_parse_vector)
    monkeypatch.setattr(upload_routes, "async_db_session", fake_ctx)
    monkeypatch.setattr(upload_routes, "save_meta", lambda *a, **k: None)

    yield app, signals


@pytest_asyncio.fixture
async def client(app_and_signals):
    app, _ = app_and_signals
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_upload_runs_parse_in_executor_thread(app_and_signals, client):
    _, signals = app_and_signals

    # 构造一个最小 .geojson 上传
    files = {"files": ("test.geojson", b'{"type":"FeatureCollection","features":[]}', "application/geo+json")}
    resp = await client.post("/api/v1/upload", files=files)

    assert resp.status_code == 200, resp.text
    assert signals["parse_thread"] is not None
    assert signals["parse_thread"] != signals["main_thread"], (
        "parse_vector 必须在 worker 线程执行，不能阻塞主事件循环"
    )
