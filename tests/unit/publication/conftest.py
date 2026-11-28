"""共享 fixture —— 发布生命周期集成测试。

真实面：真 sqlite（async+sync 双引擎指向同一文件，executor 的 sync
db_session 与路由的 AsyncSession 同库）、真服务层、真物化落盘（tmp
DATA_DIR）；隔离面：mapspec store 与会话数据面打桩（合成区县学校数据，
不依赖 Redis/上传）。
"""
from __future__ import annotations

import os
from typing import AsyncIterator, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-pub-32-chars-okxxxxx")
os.environ.setdefault("ENV", "development")
os.environ.setdefault("PI_AUTOSTART", "false")

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402

#: 合成区县学校 FC（作者会话 ref 数据）。
SCHOOLS_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"school-{i}",
            "properties": {
                "school_name": f"第{i}小学",
                "level": "primary",
                "students": 400 + i * 13,
                "internal_budget": f"SECRET-{i}",
            },
            "geometry": {"type": "Point", "coordinates": [116.30 + i * 0.01, 39.85 + i * 0.008]},
        }
        for i in range(1, 13)
    ],
}

DISTRICTS_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"district-{d}",
            "properties": {"district_name": f"示范区{d}", "population": 10000 * d},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [116.2 + d * 0.05, 39.7], [116.35 + d * 0.05, 39.7],
                    [116.35 + d * 0.05, 39.95], [116.2 + d * 0.05, 39.95],
                    [116.2 + d * 0.05, 39.7],
                ]],
            },
        }
        for d in range(1, 4)
    ],
}

#: 作者会话 MapSpec（2.2 词表；geojson ref 源 ×2）。
SCHOOL_MAPSPEC = {
    "version": "2.2",
    "view": {"center": [116.45, 39.9], "zoom": 10, "projection": "EPSG:3857"},
    "sources": {
        "schools": {
            "type": "geojson",
            "name": "学校点",
            "attribution": "合成区县数据（simulated）",
            "ref_id": "ref:schools-1",
            "url": "ref:schools-1",
        },
        "districts": {
            "type": "geojson",
            "name": "区县面",
            "attribution": "合成区县数据（simulated）",
            "ref_id": "ref:districts-1",
            "url": "ref:districts-1",
        },
    },
    "layers": [
        {
            "id": "lyr-districts",
            "source": "districts",
            "type": "fill",
            "paint": {"fill-color": "#3b82f6", "fill-opacity": 0.35},
            "legend": {"title": "区县"},
        },
        {
            "id": "lyr-schools",
            "source": "schools",
            "type": "circle",
            "paint": {"circle-color": "#ef4444", "circle-radius": 6},
            "legend": {"title": "学校"},
        },
    ],
    "layout": {"title": "区县学校分布专题图"},
}

SESSION_A = "sess-author-a-1234"
SESSION_B = "sess-other-b-5678"
OWNER_TOKEN_A = "owner-token-a-secret-value"
OWNER_TOKEN_B = "owner-token-b-secret-value"


def _install_session_world(monkeypatch, *, spec: dict, refs: dict) -> None:
    """打桩会话世界：mapspec store + session ref 数据。"""
    from app.services.mapspec import store as mapspec_store_module
    from app.services import session_data as session_data_module

    class _StubStore:
        async def get_mapspec(self, session_id, state_hint=None):
            if session_id == SESSION_A:
                return spec
            return None

    class _StubManager:
        async def get(self, session_id, ref):
            if session_id != SESSION_A:
                return None
            return refs.get(ref)

    monkeypatch.setattr(mapspec_store_module, "mapspec_store_instance", _StubStore())
    monkeypatch.setattr(session_data_module, "session_data_manager", _StubManager())


@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch) -> AsyncIterator[Callable]:
    """真 sqlite（async+sync 同文件）会话工厂 + 发布产物目录隔离。"""
    db_file = tmp_path / "publication.db"
    aengine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    afactory = async_sessionmaker(bind=aengine, expire_on_commit=False)
    async with aengine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # executor 的 sync db_session 与路由 AsyncSession 指向同一文件。
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    sfactory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    import app.core.database as database_module

    monkeypatch.setattr(database_module, "SessionLocal", sfactory)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    yield afactory
    await aengine.dispose()
    sync_engine.dispose()


@pytest_asyncio.fixture
async def world(db_factory, monkeypatch) -> dict:
    """标准世界：作者 A 的学校专题图 + 两个 ref 源。"""
    _install_session_world(monkeypatch, spec=SCHOOL_MAPSPEC, refs={
        "ref:schools-1": SCHOOLS_FC,
        "ref:districts-1": DISTRICTS_FC,
    })
    return {"db_factory": db_factory}


def make_app(db_factory):
    """构建只挂 publications 路由的 FastAPI 应用（依赖覆写 DB 会话）。"""
    from fastapi import FastAPI

    from app.api.routes import publications as pub_routes
    from app.core.database import get_async_db

    app = FastAPI()
    app.include_router(pub_routes.router, prefix="/api/v1")

    async def _override_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_db] = _override_db
    return app


@pytest_asyncio.fixture
async def author_client(world):
    """作者 A 的客户端（匿名作者 + owner_token 链；会话归属走打桩 store）。"""
    from app.core.auth import get_current_user_optional, get_owner_token

    app = make_app(world["db_factory"])

    async def _user():
        return {"user_id": None, "org_id": None}

    async def _token():
        return OWNER_TOKEN_A

    app.dependency_overrides[get_current_user_optional] = _user
    app.dependency_overrides[get_owner_token] = _token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def create_published_publication(db_factory, monkeypatch=None) -> dict:
    """直达发布态（service 层：draft → executor 物化 CAS published → link）。"""
    import asyncio

    from app.services.publication import executor
    from app.services.publication.service import create_draft, mint_link

    async with db_factory() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={
                "title": "区县学校分布专题图",
                "purpose": "合成数据演示",
                "attribution": "合成数据（simulated）",
                "license": "CC0-SIMULATED",
            },
            selected_layer_ids=["lyr-schools", "lyr-districts"],
            field_whitelists={
                "lyr-schools": ["school_name", "level", "students"],
                "lyr-districts": ["district_name", "population"],
            },
        )
        # executor 在线程内跑（生产是 Celery worker 线程；run_sync 禁止
        # 在 running loop 内调用 —— 同构约束）。
        result = await asyncio.to_thread(executor.materialize_publication, row.id)
        # executor 经独立 sync 会话 CAS 了 published；本 async 会话的身份映射
        # 是旧快照 —— 先取 PK，过期后重取再签发链接。
        row_id = row.id
        db.expire_all()
        refreshed = await db.get(type(row), row_id)
        code = await mint_link(db, refreshed, ttl_days=7)
    return {"row_id": row_id, "share_code": code, "executor_result": result}
