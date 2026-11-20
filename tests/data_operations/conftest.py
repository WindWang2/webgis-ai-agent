"""G23 持久数据操作测试夹具 —— delivery_env 范式（G18 conftest 的对齐复用）。

真实 SQLite（sync+async 双引擎）+ temp DATA_DIR + 内存 session store；
不 mock 被测语义（durable job 生命周期/幂等/取消/恢复全走真实路径）。
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
def ops_env(tmp_path, monkeypatch):
    """隔离环境：temp DB（sync+async 双引擎）+ temp DATA_DIR + 内存 session store。"""
    db_file = tmp_path / "g23.db"
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
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
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

    from app.services.data_import.service import get_data_import_service

    svc = get_data_import_service()
    svc._locks.clear()
    svc._cancel_events.clear()
    svc._commit_tasks.clear()
    svc._committing_ids.clear()

    yield {
        "sync_factory": sync_factory,
        "async_factory": async_factory,
        "data_dir": data_dir,
    }

    try:
        loop = __import__("asyncio").get_event_loop()
        if loop.is_running():
            loop.create_task(async_engine.dispose())
        else:
            loop.run_until_complete(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


GEOJSON_A = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.3, 39.8]}},
        {"type": "Feature",
         "properties": {"n": 2},
         "geometry": {"type": "Point", "coordinates": [116.31, 39.81]}},
    ],
}


def make_csv_bytes(rows, header, *, encoding: str = "utf-8"):
    import io

    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for r in rows:
        buf.write(",".join(str(c) for c in r) + "\n")
    return buf.getvalue().encode(encoding)
