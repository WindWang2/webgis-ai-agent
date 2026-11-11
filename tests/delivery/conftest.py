"""G18 数据交付测试夹具 —— 真实 SQLite（sync+async）+ 内存 session store。

不 mock 被测语义：源解析/授权/job 生命周期/写盘/反读全部走真实路径；
mock 仅限权限边界（无凭据调用方）与故障注入（由各测试自管）。
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.getcwd())

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def delivery_env(tmp_path, monkeypatch):
    """隔离环境：temp DB（sync+async 双引擎）+ temp DATA_DIR + 内存 session store。"""
    db_file = tmp_path / "g18.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("USE_REDIS", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "cache+memory://")

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.database as db_mod
    from app.core.config import settings
    from app.models.db_model import Base

    sync_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    sync_factory = sessionmaker(bind=sync_engine)

    @contextlib.asynccontextmanager
    async def fake_async_db():
        async with async_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(db_mod, "async_db_session", fake_async_db)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    monkeypatch.setattr(db_mod, "SessionLocal", sync_factory)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir))

    # 会话种子：user-1 拥有 sess-1（所有源锚测试共用；额外 ref 由各测试自存）
    from app.models.db_model import Conversation

    with sync_factory() as db:
        db.add(Conversation(id="sess-1", user_id="user-1", title="G18"))
        db.commit()

    # 重置进程内状态（semaphore/runner 表 / 驱动探测缓存不受影响）
    from app.services.data_delivery import service as dd_service

    dd_service._ACTIVE_RUNNERS.clear()

    yield {
        "sync_factory": sync_factory,
        "async_factory": async_factory,
        "data_dir": data_dir,
    }

    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(async_engine.dispose())
        else:
            loop.run_until_complete(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def seeded_session(delivery_env):
    """user-1 拥有的会话 + 一个 3 要素 FC ref（含 null 几何/中文/嵌套值）。"""
    import asyncio

    from app.services.session_data import session_data_manager

    feats = [
        {"type": "Feature", "id": "f1",
         "geometry": {"type": "Point", "coordinates": [116.397, 39.916]},
         "properties": {"name": "北京", "pop": 2154, "flag": True,
                         "nested": {"k": 1}}},
        {"type": "Feature", "id": "f2",
         "geometry": {"type": "Point", "coordinates": [116.407, 39.926]},
         "properties": {"name": "东城", "pop": 709, "flag": False,
                         "nested": {"k": 2}}},
        {"type": "Feature", "id": "f3", "geometry": None,
         "properties": {"name": "无几何", "pop": None, "flag": None,
                         "nested": None}},
    ]
    fc = {
        "type": "FeatureCollection",
        "features": feats,
        "properties": {"total_count": 3, "truncated": False,
                       "schema_info": {"crs": "EPSG:4326"}},
    }
    ref = asyncio.run(
        session_data_manager.store("sess-1", fc, prefix="data-fabric"))
    return {"ref": ref, "features": feats, "session_id": "sess-1"}


@pytest.fixture()
def actor():
    from app.services.data_delivery.source_resolver import DeliveryActor

    return DeliveryActor(user_id="user-1")


async def run_job_to_completion(job_id: str) -> None:
    """等待进程内 runner 完成（测试直接 await，不经 HTTP）。"""
    from app.services.data_delivery.service import _ACTIVE_RUNNERS

    runner = _ACTIVE_RUNNERS.get(job_id)
    if runner is not None:
        await runner
