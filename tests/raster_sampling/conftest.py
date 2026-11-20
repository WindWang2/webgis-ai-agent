"""G39 采样域测试夹具 —— ops_env 范式 + 合成栅格/站位 ref 播种。

真实 SQLite + temp DATA_DIR + 内存 session store；合成 GeoTIFF 用
``tests/unit/raster_authoring/helpers.write_tif``（已知答案栅格工厂）。
不 mock 被测语义（采样内核/调度/发布/作业全走真实路径）。
"""
from __future__ import annotations

import contextlib
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.getcwd())


@pytest.fixture()
def rs_env(tmp_path, monkeypatch):
    """隔离环境：temp DB + temp DATA_DIR + 内存 session store。"""
    db_file = tmp_path / "g39.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("USE_REDIS", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "cache+memory://")

    # raster owner scope：允许 tmp 栅格（G20 conftest 同款 monkeypatch）
    raster_root = tmp_path / "raster_owner"
    raster_root.mkdir()
    monkeypatch.setattr(
        "app.services.scoped_raster_paths.scoped_raster_roots",
        lambda **kwargs: [raster_root])
    # 单元级 store 没有 Conversation 行；HTTP 层归属另测
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none")
    # 计划/收据存储根 → tmp（避免写入共享 DATA_DIR）
    import app.services.mapspec.store as store_module
    store_root = tmp_path / "mapspec_store"
    store_root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", store_root,
                        raising=False)

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
    # store.py 在 import 时绑定 BASE_STORAGE_DIR 路径派生 —— 域根每次
    # 实时取（_domain_root 内 import），monkeypatch 生效即可。

    # 源描述缓存跨测试隔离（G20 TTL 缓存按 path+mtime 键 —— tmp 路径天然隔离，
    # 这里仍显式清理避免同进程串扰）
    from app.services.raster_authoring.source import _SOURCE_CACHE, _REVISION_CACHE
    _SOURCE_CACHE.clear()
    _REVISION_CACHE.clear()

    yield {
        "sync_factory": sync_factory,
        "async_factory": async_factory,
        "data_dir": data_dir,
        "tmp_path": tmp_path,
        "raster_root": raster_root,
        "store_root": store_root,
    }

    try:
        import asyncio as _asyncio

        _asyncio.run(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def raster_factory(rs_env):
    """合成数值栅格工厂（默认 0.01° EPSG:4326，左上 (10, 20)）。"""
    from tests.unit.raster_authoring.helpers import register_raster_ref

    created: list = []

    async def _make(
        session_id: str,
        *,
        name: str = "raster",
        bands: int = 1,
        width: int = 8,
        height: int = 8,
        dtype: str = "float32",
        nodata=None,
        crs="EPSG:4326",
        transform=None,
        tags=None,
        band_tags=None,
        values=None,
        tiled=False,
        blockxsize=256,
        blockysize=256,
    ):
        import rasterio
        from rasterio.transform import from_origin

        path = rs_env["raster_root"] / f"{name}.tif"
        if transform is None:
            transform = from_origin(10.0, 20.0, 0.01, 0.01)
        if values is None:
            rows, cols = np.mgrid[0:height, 0:width]
            values = np.stack(
                [(b + 1) * 100 + rows * 10 + cols for b in range(bands)]
            ).astype(dtype)
        kwargs = dict(
            driver="GTiff", width=width, height=height, count=bands,
            dtype=dtype, crs=crs, transform=transform,
        )
        if nodata is not None:
            kwargs["nodata"] = nodata
        if tiled:
            kwargs.update(tiled=True, blockxsize=blockxsize,
                          blockysize=blockysize)
        with rasterio.open(path, "w", **kwargs) as dst:
            dst.write(values)
            for b in range(1, bands + 1):
                dst.set_band_description(b, f"src band {b}")
                dst.update_tags(b, **(band_tags or {}).get(b, {}))
            if tags:
                dst.update_tags(**tags)
        ref = await register_raster_ref(session_id, path)
        created.append((session_id, ref, path))
        return ref, path

    yield _make


@pytest.fixture()
def seed_points_ref():
    """点 FeatureCollection ref 播种（稳定 feature id）。"""
    from app.services.session_data import session_data_manager

    async def _seed(session_id: str, points, *, prefix="points"):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": p.get("id", f"pt{i}"),
                 "properties": p.get("properties", {}),
                 "geometry": {"type": "Point",
                              "coordinates": [p["x"], p["y"]]}}
                for i, p in enumerate(points)
            ],
        }
        ref = await session_data_manager.store(session_id, fc, prefix=prefix)
        return str(ref)

    return _seed


@pytest.fixture()
def seed_lines_ref():
    """线路 FeatureCollection ref 播种。"""
    from app.services.session_data import session_data_manager

    async def _seed(session_id: str, coordinates, *, feature_id="line-a",
                    gtype="LineString", prefix="lines"):
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": feature_id, "properties": {},
                "geometry": {"type": gtype, "coordinates": coordinates},
            }],
        }
        ref = await session_data_manager.store(session_id, fc, prefix=prefix)
        return str(ref)

    return _seed
