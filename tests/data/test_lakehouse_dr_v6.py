"""Lakehouse V6 Wave 11 — DR：损坏模拟 / 修复 / 孤儿与缺失扫描。

覆盖面（ADR-0118 DR + GC 验收）：
- 工作副本损坏检测（逐文件 digest vs manifest）；
- 从 BlobStore 修复（先全量验真后写盘；owner 校验）；
- chunk 备份去重（重备份 CAS 命中）；
- 缺失对象扫描 / 孤儿 manifest 扫描（只读证据）；
- GC 保护：存活/受保护的 cube ref 不被孤儿清扫（与 session GC 闸联动）。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.artifact_registry import probe_ref
from app.services.lakehouse.dr import (
    DRVerifyError,
    backup_cube_chunks,
    repair_cube_store,
    scan_missing_objects,
    scan_orphan_manifests,
    verify_cube_store,
)

pytest.importorskip("zarr")
pytest.importorskip("rasterio")


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


@pytest.mark.asyncio
async def test_corruption_simulation_and_repair(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.lakehouse.cube_service import build_session_cube
    from app.services.lakehouse.cube_store import read_cube_window

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True)
    time_sources = [
        {"time": "2024-01", "source": _write_slice(src / "t1.tif", 1.0)},
        {"time": "2024-02", "source": _write_slice(src / "t2.tif", 2.0)},
    ]
    res = await build_session_cube("sess-dr", time_sources=time_sources, title="T")
    store = tmp_path / "data" / "sess-dr" / "lakehouse-cubes" / res["cube_id"]
    did = res["data_object_id"]

    # 备份（chunk → BlobStore）。
    backup = backup_cube_chunks(store)
    assert backup["backed_up"] is True

    # 健康态：verified。
    assert verify_cube_store(store, data_object_id=did)["state"] == "verified"

    # 损坏模拟：篡改一个 chunk 文件。
    chunk = next(p for p in sorted(store.rglob("*"))
                 if p.is_file() and p.suffix == "" and p.name != "zarr.json")
    chunk.write_bytes(b"corrupted-bytes")
    report = verify_cube_store(store, data_object_id=did)
    assert report["state"] == "corrupt"
    assert report["corrupt"] == [chunk.relative_to(store).as_posix()]

    # 修复（owner 校验通过；从 BlobStore 验真物化）。
    repaired = repair_cube_store(store, data_object_id=did, session_id="sess-dr")
    assert repaired["repaired"] is True
    assert verify_cube_store(store, data_object_id=did)["state"] == "verified"
    out = read_cube_window(store, time=slice(0, 1))
    assert float(out["bands"]["b1"][0, 0, 0]) == 1.0

    # 越权修复拒绝。
    denied = repair_cube_store(store, data_object_id=did, session_id="other")
    assert denied["repaired"] is False


def test_verify_requires_manifest(tmp_path):
    with pytest.raises(DRVerifyError):
        verify_cube_store(tmp_path, data_object_id="nope")
    with pytest.raises(DRVerifyError):
        verify_cube_store(tmp_path)


def test_backup_dedup_hits(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    reset_filesystem_blob_store()
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True)
    store = tmp_path / "cube.zarr"
    from app.services.lakehouse.cube_store import write_cube
    from app.lib.geo_analysis.raster_grid import RasterGridProfile
    from app.lib.geo_raster.chunk import build_chunk_descriptor_from_grid

    grid = RasterGridProfile(
        width=16, height=16, crs="EPSG:4326",
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 16.0),
        dtype="float32", nodata=-9999.0, band_count=1,
    )
    tif = _write_slice(src / "c.tif", 5.0, h=16, w=16)
    d = build_chunk_descriptor_from_grid(
        grid, (0, 0, 16, 16), dtype="float32", source_uri=tif,
        source_fingerprint="fp",
    )
    write_cube({"b1": [[d]]}, ["t1"], store)
    first = backup_cube_chunks(store)
    second = backup_cube_chunks(store)
    assert first["backed_up"] and second["backed_up"]
    # 第二次全命中（new_blobs == 0）。
    assert first["new_blobs"] > 0
    assert second["new_blobs"] == 0


@pytest.mark.asyncio
async def test_scan_missing_and_orphans(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.lakehouse.cube_service import build_session_cube

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True)
    time_sources = [
        {"time": "2024-01", "source": _write_slice(src / "t1.tif", 1.0)},
    ]
    res = await build_session_cube("sess-scan", time_sources=time_sources, title="T")
    did = res["data_object_id"]

    scan = scan_missing_objects([did, "f" * 64])
    assert scan["states"][did] == "verified"
    assert "f" * 64 in scan["missing"]

    # 引用集包含该对象 → 无孤儿；空引用集 → 该 manifest 是孤儿（只读报告）。
    assert scan_orphan_manifests(referenced_ids=[did]) == []
    assert did in scan_orphan_manifests(referenced_ids=[])


@pytest.mark.asyncio
async def test_live_cube_ref_not_collected(tmp_path, monkeypatch):
    """GC 保护验收：存活的 cube ref（valid 态）不被孤儿清扫删除。"""
    from app.core.config import settings
    from app.services.artifact_registry import collect_orphan_refs
    from app.services.lakehouse.cube_service import build_session_cube

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True)
    time_sources = [{"time": "2024-01", "source": _write_slice(src / "t1.tif", 1.0)}]
    res = await build_session_cube("sess-live", time_sources=time_sources, title="T")
    deleted = await collect_orphan_refs("sess-live")
    assert res["ref"] not in deleted
    assert await probe_ref("sess-live", res["ref"]) == {
        "kind": "lakehouse_cube", "exists": True,
    }
