"""Lakehouse V6 Wave 5 — Raster lakehouse（原子写 + COG DataObject）。

覆盖面（ADR-0118）：
- save_png 原子发布：失败不留半截 PNG（全仓持久写统一纪律的最后一例）；
- COG → DataObject：内容寻址身份、grid identity（header-only）、有界
  chunk checksums、materialize 回原字节；
- 同文件重发布 = CAS 命中（deduped）；owner scope 缺席 = 诚实跳过；
- lazy 读证明：COG 窗口读**不触发全量像素 IO**（counting dataset 代理的
  结构性证据 —— 窗口读面积 << 全图面积）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import numpy as np

rasterio = pytest.importorskip("rasterio")

from app.services.lakehouse.data_object import (
    materialize_data_object,
    resolve_data_object,
)
from app.services.lakehouse.raster_object import (
    grid_identity_from_path,
    publish_cog_data_object,
)


@pytest.fixture(autouse=True)
def _sandbox_data_dir(tmp_path, monkeypatch):
    """publish_cog_data_object 走 BlobStore 单例 —— DATA_DIR 沙箱化，
    绝不写仓库 ./data。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))


def _write_raster(path: Path, width=64, height=48, blocksize=0, seed=7):
    import rasterio
    from rasterio.transform import from_origin

    data = ((np.arange(width * height, dtype=np.uint16).reshape(height, width)
             + seed) % 1000).astype("uint16")
    kwargs = {}
    if blocksize:
        kwargs = dict(BLOCKXSIZE=blocksize, BLOCKYSIZE=blocksize, TILED=True)
    with rasterio.open(
        str(path), "w", driver="GTiff", width=width, height=height, count=1,
        dtype="uint16", crs="EPSG:4326",
        transform=from_origin(104.0, 30.6, 0.01, 0.01),
        **kwargs,
    ) as ds:
        ds.write(data, 1)
    return data


@pytest.fixture()
def cog_path(tmp_path):
    from app.lib.geo_raster.cog import write_cog

    src = tmp_path / "plain.tif"
    _write_raster(src)
    return Path(write_cog(str(src), str(tmp_path / "c.tif")))


# ── save_png 原子性 ───────────────────────────────────────────────────────


def test_save_png_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    from app.services import raster_store

    sid = tmp_path / "sess"
    png = b"\x89PNG\r\n\x1a\nfake-bytes"
    ok_ref = raster_store.save_png(sid, "a" * 8, png)
    assert ok_ref.startswith("ref:raster/")
    assert (sid / "raster" / ("a" * 8 + ".png")).read_bytes() == png

    # 发布失败（os.replace 注入失败）→ 目标位不留半截文件、无 tmp 残留。
    import os

    def boom(src, dst, *a, **kw):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        raster_store.save_png(sid, "b" * 8, b"partial")
    monkeypatch.undo()
    assert not (sid / "raster" / ("b" * 8 + ".png")).exists()
    leftovers = [p for p in (sid / "raster").iterdir() if ".tmp-" in p.name]
    assert leftovers == []


# ── COG → DataObject ─────────────────────────────────────────────────────


def test_grid_identity_header_only(cog_path):
    grid = grid_identity_from_path(cog_path)
    assert grid["width"] == 64 and grid["height"] == 48
    assert grid["crs"] == "EPSG:4326"
    assert grid["band_count"] == 1
    assert len(grid["transform"]) == 6


def test_cog_publish_roundtrip_and_dedup(cog_path, tmp_path):
    owner_sid = "sess-cog"
    first = publish_cog_data_object(cog_path, session_id=owner_sid)
    assert first["published"] is True
    assert first["chunk_checksums"] is True
    assert first["grid"]["width"] == 64

    manifest = resolve_data_object(first["data_object_id"])
    assert manifest is not None
    assert manifest["kind"] == "cog_raster"
    assert manifest["payload"]["grid"]["crs"] == "EPSG:4326"

    # 物化回原字节（digest 校验）。
    target = tmp_path / "restore"
    written = materialize_data_object(
        first["data_object_id"], target, owner_session_id=owner_sid,
    )
    assert written == ["data.tif"]
    assert (target / "data.tif").read_bytes() == cog_path.read_bytes()

    # 同文件重发布 = CAS 命中。
    second = publish_cog_data_object(cog_path, session_id=owner_sid)
    assert second["data_object_id"] == first["data_object_id"]
    assert second["deduped"] is True

    # 跨 owner = 不同逻辑对象。
    other = publish_cog_data_object(cog_path, session_id="sess-other")
    assert other["data_object_id"] != first["data_object_id"]


def test_cog_publish_honest_skips(cog_path):
    assert publish_cog_data_object(cog_path) == {
        "published": False, "reason": "owner_missing",
    }
    res = publish_cog_data_object(cog_path, session_id="s", max_total_bytes=1)
    assert res == {"published": False, "reason": "oversized"}


# ── lazy 读证明：窗口读 ≠ 全量读 ─────────────────────────────────────────


def test_cog_window_read_does_not_full_load(tmp_path):
    """2048² 栅格上读 512² 窗口：dataset.read 收到的请求面积受窗口+块对齐
    约束，远小于全图（结构性证据，非 wall-clock）。"""
    from app.lib.geo_raster.cog import write_cog
    from app.lib.geo_raster.reader import RasterReader

    src = tmp_path / "big.tif"
    _write_raster(src, width=2048, height=2048, blocksize=256, seed=1)
    cog = str(write_cog(str(src), str(tmp_path / "big_cog.tif"), blocksize=256))

    reader = RasterReader.open(cog)
    ds = reader.dataset
    orig_read = ds.read
    requested = {"pixels": 0}

    def counting_read(*a, **kw):
        window = kw.get("window")
        if window is not None:
            requested["pixels"] += int(window.width * window.height)
        else:
            requested["pixels"] += int(ds.width * ds.height)
        return orig_read(*a, **kw)

    ds.read = counting_read
    try:
        data = reader.read_window((768, 768, 512, 512), band=1)
    finally:
        reader.close()
    assert data.shape == (512, 512)
    full = 2048 * 2048
    # 块对齐放 read 至多放大一圈（±256/边）；全量读则恰为 full。
    assert requested["pixels"] < full, (
        f"window read requested {requested['pixels']} px >= full {full} px "
        "— implicit full load detected"
    )
    assert requested["pixels"] <= (512 + 512) ** 2  # 窗口 + 两圈块对齐上界


# ── 工具接线（additive result）───────────────────────────────────────────


@pytest.mark.asyncio
async def test_cog_tool_publishes_data_object(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # validate_data_path 以 CWD ./data 为基
    base = tmp_path / "data"
    base.mkdir(parents=True, exist_ok=True)
    src = base / "in.tif"
    _write_raster(src)

    from app.tools.raster_tools_cog import register_raster_cog_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_raster_cog_tools(registry)
    result = await registry.dispatch(
        "convert_raster_to_cog",
        {"raster_path": "in.tif", "out_dir": "cog-out"},
        session_id="sess-tool",
    )
    assert result["success"] is True, result
    assert result["data_object"]["published"] is True
    assert len(result["data_object"]["data_object_id"]) == 64

    # 无 session 上下文 → 诚实跳过（owner scope 不可虚构）。
    result2 = await registry.dispatch(
        "convert_raster_to_cog",
        {"raster_path": "in.tif", "out_dir": "cog-out2"},
    )
    assert result2["data_object"]["published"] is False
    assert result2["data_object"]["reason"] == "owner_missing"
