"""G45 坐标操作域测试夹具 —— G39 rs_env 范式 + 合成矢量 FC ref 播种。

真实 SQLite + temp DATA_DIR；合成源用 session FC ref（带权威 CRS 证据
schema_info.crs）与 fabric-parquet 两种载体。不 mock 被测语义（候选/冻结/
变换内核/发布/作业全走真实路径）。
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

sys.path.insert(0, os.getcwd())


@pytest.fixture()
def co_env(tmp_path, monkeypatch):
    """隔离环境：temp DB + temp DATA_DIR。"""
    db_file = tmp_path / "g45.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("USE_REDIS", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "cache+memory://")

    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none")

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.database as db_mod
    from app.core.config import settings
    from app.models.db_model import Base

    sync_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    # 本域模型（create_all 环境 = 迁移共存面；正式面走 alembic）
    from app.models.coordinate_operations import (
        CoordinateOperationPlan,
        CoordinateOperationRun,
    )  # noqa: F401
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

    yield {
        "sync_factory": sync_factory,
        "async_factory": async_factory,
        "data_dir": data_dir,
        "tmp_path": tmp_path,
        "db_file": db_file,
    }

    try:
        import asyncio as _asyncio

        _asyncio.run(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def seed_fc_ref(co_env):
    """合成矢量 FC 播种工厂：session FC ref（权威 CRS 证据在 schema_info）。"""

    def _seed(
        session_id: str,
        features: list,
        crs: str = "EPSG:4326",
        alias: str = "合成源",
        truncated: bool = False,
        with_crs_evidence: bool = True,
    ) -> dict:
        import asyncio

        from app.services.session_data import session_data_manager

        fc = {
            "type": "FeatureCollection",
            "features": features,
            "properties": {},
        }
        if with_crs_evidence:
            fc["properties"]["schema_info"] = {"crs": crs}
        if truncated:
            fc["properties"]["truncated"] = True

        async def _store():
            return await session_data_manager.store(
                session_id, fc, prefix="seed-fc")

        ref = asyncio.run(_store())
        return {"kind": "session_ref", "session_id": session_id, "ref": ref,
                "fc": fc}

    return _seed


def make_point_features(n: int, *, start_lon: float = 116.0,
                        start_lat: float = 39.9, crs_z: bool = False,
                        _wfid_prefix: str = "f") -> list:
    """确定性合成点要素（含中文/null/大整数属性 —— C36 保真面）。"""
    feats = []
    for i in range(n):
        props = {
            "_wfid": f"{_wfid_prefix}-{i:05d}",
            "name": f"要素{i}·中文",
            "big_int": 98765432101234 + i,
            "maybe_null": None if i % 3 == 0 else float(i) / 7.0,
        }
        coord = [start_lon + (i % 37) * 0.013, start_lat + (i % 23) * 0.007]
        if crs_z:
            coord.append(100.0 + i)
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": coord},
        })
    return feats
