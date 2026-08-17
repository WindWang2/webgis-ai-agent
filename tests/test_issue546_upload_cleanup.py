"""#546 回归：上传失败路径不得遗留孤儿目录。

修复前：`get_upload_dir` 在解析/入库之前就 eager 创建目录，而 ParseError(400)、
(OSError, RuntimeError)(500)、未捕获的 SQLAlchemy DBAPIError、save_meta OSError
这些分支都不会删除目录 → 持久卷上积累无法通过 API 删除（无 DB 行）的孤儿目录。

修复后：单一清理纪律 —— 目录创建后任何失败分支都 rmtree 本次上传目录。
"""
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError

from app.api.routes import upload as _mod
from app.core.auth import get_current_user

_mock_user = {"user_id": "test-user"}


@pytest.fixture
async def app(tmp_path, monkeypatch):
    """路由 fixture：DATA_DIR 指向临时目录（避免污染仓库 data/），
    并把路由模块的 async_db_session 换成一次性 sqlite 会话
    （否则成功路径会写仓库 data/webgis.db）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.db_model import Base

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'uploads.db'}",
        connect_args={"check_same_thread": False},
    )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def test_db_session():
        async with session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
            finally:
                await s.close()

    # 路由在自身模块命名空间里查找 async_db_session（import 时绑定），
    # 因此必须 patch _mod 的属性；按测试覆盖时用 patch.object(_mod, ...) 叠加。
    monkeypatch.setattr(_mod, "async_db_session", test_db_session)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _mock_user
    app.include_router(_mod.router, prefix="/api/v1")

    yield app

    await engine.dispose()


@pytest.fixture
async def client(app):
    # raise_app_exceptions=False —— 模拟生产环境 ServerErrorMiddleware：
    # 未捕获异常转成 500 响应而不是把原始异常抛给测试。
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _upload_dirs(tmp_path):
    root = tmp_path / "data" / "uploads"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _fake_meta(tmp_path):
    """正常解析产物（不含 output_path —— 路由会 fallback 到 upload_dir/filename）。"""
    return {
        "file_type": "vector",
        "format": "geojson",
        "crs": "EPSG:4326",
        "geometry_type": "Point",
        "feature_count": 0,
        "bbox": None,
    }


@pytest.mark.asyncio
async def test_parse_error_cleans_upload_dir(client, tmp_path):
    """ParseError → 400，且本次上传目录被清理。"""
    from app.services.data_parser import ParseError

    def boom(*a, **kw):  # parse_* 在 run_in_executor 中同步执行
        raise ParseError("this is not geojson")

    with patch.object(_mod, "parse_vector", boom):
        resp = await client.post(
            "/api/v1/upload",
            files={"files": ("broken.geojson", b"this is not geojson at all", "application/json")},
        )
    assert resp.status_code == 400
    assert _upload_dirs(tmp_path) == [], f"ParseError 后目录残留: {_upload_dirs(tmp_path)}"


@pytest.mark.asyncio
async def test_dbapi_error_cleans_upload_dir(client, tmp_path):
    """DBAPIError（SQLAlchemyError 子类，旧代码漏捕）→ 500，且目录被清理。"""
    def fake_parse(*a, **kw):  # parse_* 在 run_in_executor 中同步执行
        return _fake_meta(tmp_path)

    @asynccontextmanager
    async def boom_db():
        raise DBAPIError("stmt", {}, Exception("connection refused"))
        yield  # pragma: no cover — makes boom_db an async generator (reachable only if raise is removed)

    with patch.object(_mod, "parse_vector", fake_parse), \
         patch.object(_mod, "async_db_session", boom_db):
        resp = await client.post(
            "/api/v1/upload",
            files={"files": ("ok.geojson", b'{"type":"FeatureCollection","features":[]}', "application/json")},
        )
    assert resp.status_code == 500
    assert _upload_dirs(tmp_path) == [], f"DBAPIError 后目录残留: {_upload_dirs(tmp_path)}"


@pytest.mark.asyncio
async def test_save_meta_failure_cleans_upload_dir(client, tmp_path):
    """save_meta OSError（旧代码未捕获 → 500 逃逸）→ 目录仍须清理。"""
    def fake_parse(*a, **kw):  # parse_* 在 run_in_executor 中同步执行
        return _fake_meta(tmp_path)

    def boom_meta(upload_dir, meta):
        raise OSError("permission denied")

    with patch.object(_mod, "parse_vector", fake_parse), \
         patch.object(_mod, "save_meta", boom_meta):
        resp = await client.post(
            "/api/v1/upload",
            files={"files": ("ok.geojson", b'{"type":"FeatureCollection","features":[]}', "application/json")},
        )
    assert resp.status_code == 500
    assert _upload_dirs(tmp_path) == [], f"save_meta 失败后目录残留: {_upload_dirs(tmp_path)}"


@pytest.mark.asyncio
async def test_temp_write_failure_cleans_upload_dir(client, tmp_path):
    """临时文件写入 OSError → 500，且目录被清理。"""
    with patch("builtins.open", side_effect=OSError("disk full")):
        resp = await client.post(
            "/api/v1/upload",
            files={"files": ("ok.geojson", b'{"type":"FeatureCollection","features":[]}', "application/json")},
        )
    assert resp.status_code == 500
    assert _upload_dirs(tmp_path) == [], f"写入失败后目录残留: {_upload_dirs(tmp_path)}"


@pytest.mark.asyncio
async def test_success_keeps_upload_dir(client, tmp_path, monkeypatch):
    """成功路径不受影响：目录保留，DB 行写入（happy path 回归）。"""
    def fake_parse(*a, **kw):  # parse_* 在 run_in_executor 中同步执行
        return _fake_meta(tmp_path)

    with patch.object(_mod, "parse_vector", fake_parse):
        resp = await client.post(
            "/api/v1/upload",
            files={"files": ("ok.geojson", b'{"type":"FeatureCollection","features":[]}', "application/json")},
        )
    assert resp.status_code == 200, resp.text
    dirs = _upload_dirs(tmp_path)
    assert len(dirs) == 1, f"成功上传应保留目录，实际: {dirs}"

    # 文件确实落盘
    upload_dir = tmp_path / "data" / "uploads" / dirs[0]
    assert (upload_dir / "ok.geojson").exists()
