"""survey_sampling 测试夹具 —— ops_env 范式（rs_env 同款）。

真实 SQLite + temp DATA_DIR + 内存 session store + 合成等面积分类栅格
（EPSG:6933，已知答案类别 + 独立真值图）。不 mock 被测语义。
"""
from __future__ import annotations

import contextlib
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.getcwd())

#: 类别字典（值 → 名称）；与真值字典一致（参考类 = 地图类同字典）
CLASS_DICT = {1: "forest", 2: "crop", 3: "water"}


def write_survey_raster(
    path,
    *,
    width: int = 80, height: int = 60, pixel: float = 100.0,
    error_rate: float = 0.25, seed: int = 11,
    nodata_value: int | None = 0,
    mutate: bool = False,
):
    """合成"分类图 + 真值图"对（EPSG:6933 等面积）。

    分类图与真值图按 ``error_rate`` 混淆（真值独立于预测——参考标签
    语义）；``mutate=True`` 时重写分类图（内容漂移 → stale 测试用）。
    返回 (class_tif, truth_tif)。
    """
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    cls = rng.choice([1, 2, 3], size=(height, width),
                     p=[0.5, 0.3, 0.2]).astype(np.uint8)
    cls[:, width // 2:] = 1                       # 大 forest 层
    cls[height - 12:, : width // 4] = 3           # 窄 water 带（稀有层）
    truth = cls.copy()
    flip = rng.random((height, width)) < error_rate
    other = rng.choice([1, 2, 3], size=(height, width))
    truth[flip] = other[flip].astype(np.uint8)    # 独立真值（≠预测）
    if nodata_value is not None:
        cls[0:4, 0:4] = nodata_value              # 4×4 nodata 补丁
        truth[0:4, 0:4] = nodata_value
    transform = from_origin(500000.0, 5200000.0, pixel, pixel)
    for p, data in ((path, cls), (str(path).replace(".tif", "_truth.tif"),
                                  truth)):
        kwargs = dict(driver="GTiff", width=width, height=height, count=1,
                      dtype="uint8", crs="EPSG:6933", transform=transform)
        if nodata_value is not None and p == path:
            kwargs["nodata"] = nodata_value
        with rasterio.open(p, "w", **kwargs) as dst:
            dst.write(data, 1)
    if mutate:
        # 重写分类图（版本漂移；真值不变）。指纹采样角窗 ⇒ 漂移必须触及
        # 角部区域（左上角 nodata 补丁之外的角带）
        cls2 = cls.copy()
        cls2[4:24, 4:44] = 2
        with rasterio.open(path, "w", driver="GTiff", width=width,
                           height=height, count=1, dtype="uint8",
                           crs="EPSG:6933", transform=transform,
                           nodata=nodata_value) as dst:
            dst.write(cls2, 1)
    return path, str(path).replace(".tif", "_truth.tif")


def truth_labels_for(path_truth, coords_lonlat):
    """真值图逐点取参考标签（模拟独立判读；坐标 WGS84 → 栅格行列）。"""
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import rowcol

    tr = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    with rasterio.open(path_truth) as src:
        out = []
        for lon, lat in coords_lonlat:
            x, y = tr.transform(lon, lat)
            r, c = rowcol(src.transform, x, y)
            if 0 <= r < src.height and 0 <= c < src.width:
                v = int(src.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])
                out.append({1: "forest", 2: "crop", 3: "water"}.get(v))
            else:
                out.append(None)
    return out


@pytest.fixture()
def survey_env(tmp_path, monkeypatch):
    """隔离环境：temp DB + temp DATA_DIR + 内存 session store。"""
    db_file = tmp_path / "survey.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("USE_REDIS", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "cache+memory://")

    raster_root = tmp_path / "raster_owner"
    raster_root.mkdir()
    monkeypatch.setattr(
        "app.services.scoped_raster_paths.scoped_raster_roots",
        lambda **kwargs: [raster_root])
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none")
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

    from app.services.raster_authoring.source import (
        _SOURCE_CACHE,
        _REVISION_CACHE,
    )

    _SOURCE_CACHE.clear()
    _REVISION_CACHE.clear()

    yield {
        "tmp_path": tmp_path,
        "raster_root": raster_root,
        "store_root": store_root,
        "data_dir": data_dir,
    }

    try:
        import asyncio as _asyncio

        _asyncio.run(async_engine.dispose())
    except Exception:  # noqa: BLE001
        pass


async def _register_and_build_frame(env, *, mutate: bool = False,
                                    seed: int = 11):
    """注册分类栅格 ref → service.build_survey_frame → (frame, cls_path,
    truth_path, survey_id-free helpers)。"""
    from app.services.session_data import session_data_manager
    from app.services.survey_sampling import service

    cls_path, truth_path = write_survey_raster(
        env["raster_root"] / "cls.tif", mutate=mutate, seed=seed)
    ref_id = await session_data_manager.store(
        "sess-survey-journey",
        {"kind": "numeric_raster", "file_path": str(cls_path)},
        prefix="raster")
    scope = {"session_id": "sess-survey-journey"}
    frame = await service.build_survey_frame(
        "sess-survey-journey", None, scope,
        source_ref=str(ref_id), band=1, class_dictionary=dict(CLASS_DICT),
        trusted_dispatch=True)
    return frame, str(cls_path), truth_path, str(ref_id), scope
