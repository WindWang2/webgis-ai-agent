"""V3 验收补强：大影像 tile/mosaic 的确定性（memmap 融合路径）。

merge 缓冲的 RAM 预算（MERGE_RAM_BUDGET_BYTES）是模块常量——monkeypatch
为极小值即可在不物化大栅格的前提下强制 ``_SegmentationAccumulator``
与 ``_StackAccumulator`` 走 memmap 兜底路径，验证：

1. memmap 路径与 RAM 路径产出**逐位一致**（同一数学，不同存储）；
2. memmap 路径自身两次运行逐位一致（确定性）；
3. 临时目录在 finalize/close 后清理（无磁盘泄漏）。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.modelops import engine as engine_module


@pytest.fixture()
def mid_raster(tmp_path):
    path = tmp_path / "mid.tif"
    rng = np.random.default_rng(11)
    data = rng.random((3, 140, 120)).astype(np.float32) * 0.5
    data[:, 20:60, 30:80] = 0.85 + rng.random((3, 40, 50)).astype(np.float32) * 0.15
    with rasterio.open(
        path, "w", driver="GTiff", width=120, height=140, count=3,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


def _run(service, raster, session):
    from app.services.modelops.engine import InferenceRequest

    return service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(raster),
            owner_scope={"session_id": session},
        )
    )


def _classes_of(result):
    with rasterio.open(result.outputs["classes"]["path"]) as src:
        return src.read(1)


def _force_memmap(monkeypatch, budget_bytes=1024):
    """把 merge RAM 预算钉到极小值 → 强制 memmap 兜底。"""
    monkeypatch.setattr(engine_module, "MERGE_RAM_BUDGET_BYTES", budget_bytes)


def test_memmap_merge_matches_ram_path(service, mid_raster, monkeypatch):
    """memmap 与 RAM 两条融合路径必须逐位一致（验收：大影像确定性）。"""
    ram_result = _run(service, mid_raster, "s-mm-ram")
    assert ram_result.status in ("completed", "reused")
    ram_classes = _classes_of(ram_result)

    _force_memmap(monkeypatch)
    mm_result = _run(service, mid_raster, "s-mm-disk")
    mm_classes = _classes_of(mm_result)
    np.testing.assert_array_equal(ram_classes, mm_classes)


def test_memmap_merge_deterministic_across_runs(service, mid_raster, monkeypatch):
    _force_memmap(monkeypatch)
    first = _classes_of(_run(service, mid_raster, "s-mm-a"))
    second = _classes_of(_run(service, mid_raster, "s-mm-b"))
    np.testing.assert_array_equal(first, second)


def test_memmap_temp_dir_cleaned(service, mid_raster, monkeypatch):
    """memmap 兜底的临时目录在 run 结束后清理（无磁盘泄漏）。"""
    import tempfile
    from pathlib import Path

    _force_memmap(monkeypatch)
    before = {p.name for p in Path(tempfile.gettempdir()).glob("modelops-merge-*")}
    _run(service, mid_raster, "s-mm-clean")
    after = {p.name for p in Path(tempfile.gettempdir()).glob("modelops-merge-*")}
    assert after <= before
