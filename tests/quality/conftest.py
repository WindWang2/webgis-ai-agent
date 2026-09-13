"""tests/quality 共享 fixture：ADR-0159 制图质量事实库（tmp sqlite 双引擎）。

同步引擎建表（供脚本侧/内省）+ 异步引擎（aiosqlite, NullPool）替身
``app.core.database.AsyncSessionLocal``（供 store/ratchet 的 async 面）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

import app.core.database as database
from app.core.config import settings
from app.core.database import Base

TABLES = (
    "cartography_quality_runs",
    "cartography_quality_metrics",
    "cartography_quality_baselines",
    "cartography_quality_waivers",
)


def _register_models():
    from app.models.cartography_quality import (  # noqa: F401 — 注册四张表
        CartographyQualityBaseline,
        CartographyQualityMetric,
        CartographyQualityRun,
        CartographyQualityWaiver,
    )


def _create_tables(conn):
    Base.metadata.create_all(
        conn,
        tables=[Base.metadata.tables[t] for t in TABLES],
    )


def make_facts_db(db_path: Path):
    """建库 + 建表；返回 (同步引擎, async_sessionmaker)。供 fixture 与测试复用。"""
    _register_models()
    engine = sa.create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    _create_tables(engine)
    engine.dispose()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
    )
    return async_engine, async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture()
async def facts_db(tmp_path, monkeypatch):
    """事实库替身：AsyncSessionLocal 指向 tmp sqlite，开关复位为开。

    建表由 make_facts_db 的同步引擎一次完成（create_all checkfirst 幂等，
    异步面连同一文件无需重复建）。
    """
    db_path = Path(tmp_path) / "facts.db"
    async_engine, session_maker = make_facts_db(db_path)
    monkeypatch.setattr(database, "AsyncSessionLocal", session_maker)
    monkeypatch.setattr(settings, "CARTO_METRICS_STORE_ENABLED", True)
    yield db_path
    await async_engine.dispose()
