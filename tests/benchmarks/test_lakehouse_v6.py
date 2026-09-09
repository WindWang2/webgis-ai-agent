"""Spatial Lakehouse V6 perf harness (nightly-only, ADR-0118).

风格契约（test_data_control_plane_v5.py 同款）：synthetic fixtures、确定性
种子、有界内存；**结构性证据优先**（row-group 剪枝比 / chunk 触达数 /
CAS 复用命中 / tracemalloc 峰值），墙钟只做宽比带（捕捉 O(n²) 级退化，
不做脆阈值断言）；吞吐打印仅 informational。

Nightly-only 理由：依赖可选 geo 栈（zarr / pyarrow / rasterio —— zarr 非
requirements 声明依赖），且含墙钟比带 —— 不进 PR test-perf 显式清单
（见 tests/test_ci_perf_coverage_contract.py::NIGHTLY_ONLY_PERF_FILES）。
"""
from __future__ import annotations

import time
import tracemalloc

import pytest

pytestmark = pytest.mark.perf


def _cluster_features(cx, n, tag):
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [cx + i * 1e-4, i * 1e-4]},
            "properties": {"tag": tag, "i": i},
        }
        for i in range(n)
    ]


@pytest.fixture(scope="module")
def prquet(tmp_path_factory):
    pyarrow = pytest.importorskip("pyarrow")
    from app.services.data_fabric.vector_carrier import (
        features_to_arrow,
        table_to_geoparquet,
    )

    feats = (
        _cluster_features(0.0, 400, "a")
        + _cluster_features(60.0, 400, "b")
        + _cluster_features(120.0, 400, "c")
    )
    table = features_to_arrow(feats, crs="EPSG:4326")
    path = tmp_path_factory.mktemp("lake") / "perf.parquet"
    table_to_geoparquet(table, str(path), row_group_size=400)
    return pyarrow, path


@pytest.fixture(scope="module")
def cube(tmp_path_factory):
    zarr = pytest.importorskip("zarr")
    rasterio = pytest.importorskip("rasterio")
    numpy = pytest.importorskip("numpy")
    from rasterio.transform import from_origin

    from app.lib.geo_analysis.raster_grid import RasterGridProfile
    from app.lib.geo_raster.chunk import build_chunk_descriptor_from_grid
    from app.services.lakehouse.cube_store import write_cube

    grid = RasterGridProfile(
        width=128, height=128, crs="EPSG:4326",
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 128.0),
        dtype="float32", nodata=-9999.0, band_count=1,
    )
    base = tmp_path_factory.mktemp("cube")
    groups = []
    for t in range(6):
        src = base / f"t{t}.tif"
        with rasterio.open(
            str(src), "w", driver="GTiff", width=128, height=128, count=1,
            dtype="float32", crs="EPSG:4326",
            transform=from_origin(0, 128, 1, 1), nodata=-9999.0,
        ) as dst:
            dst.write(numpy.full((128, 128), float(t), dtype="float32"), 1)
        groups.append([
            build_chunk_descriptor_from_grid(
                grid, (x, y, 64, 64), dtype="float32", source_uri=str(src),
                source_fingerprint=f"perf-{t}", identity_extra=f"t{t}:{x}:{y}",
            )
            for y in (0, 64) for x in (0, 64)
        ])
    store = base / "cube.zarr"
    write_cube({"b1": groups}, [f"t{i}" for i in range(6)], store)
    return zarr, store


def test_vector_window_scan_prune_and_ratio(prquet):
    """剪枝是结构断言；墙钟只查 O(n²) 级退化比带（宽 [0.02, 10]，取两次
    测量的 min —— 冷启动/页缓存抖动不进判定，test_data_control_plane_v5
    同款"非脆阈值"纪律）。"""
    _pa, path = prquet
    from app.services.lakehouse.vector_scan import scan_parquet_window

    def _timed(bbox):
        best = float("inf")
        for _ in range(2):
            t0 = time.perf_counter()
            result = scan_parquet_window(path, bbox)
            best = min(best, time.perf_counter() - t0)
        return best, result

    t_window, win = _timed([-0.01, -0.01, 0.01, 0.01])
    t_full, full = _timed([-1.0, -1.0, 180.0, 2.0])

    props = win["properties"]
    assert props["row_groups_total"] == 3
    assert props["row_groups_read"] == 1  # 结构：剪枝真实发生（1/3）
    assert len(win["features"]) == 400
    assert len(full["features"]) == 1200
    ratio = t_window / max(t_full, 1e-9)
    assert 0.02 <= ratio <= 10.0, f"window/full ratio {ratio:.3f} out of band"
    print(f"[lakehouse-perf] vector scan window={t_window:.4f}s "
          f"full={t_full:.4f}s ratio={ratio:.3f} (informational)")


def test_vector_scan_memory_peak_bounded(prquet):
    """tracemalloc 峰值护栏：max_rows 预算下窗口扫描内存有界（结构证据）。"""
    _pa, path = prquet
    from app.services.lakehouse.vector_scan import scan_parquet_window

    tracemalloc.start()
    result = scan_parquet_window(path, [-1.0, -1.0, 180.0, 2.0], max_rows=800)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result["properties"]["truncated"] is True
    assert peak < 64 * 1024 * 1024, f"scan peak {peak / 1e6:.1f}MB over budget"
    print(f"[lakehouse-perf] scan peak={peak / 1e6:.2f}MB (informational)")


def test_cube_time_slice_chunk_granularity(cube):
    """时间片窗口读：结构断言 = 只触窗口相交 chunk（4 chunk 网格 × 单时间
    片 → 恰 4 次 chunk 读）；墙钟 informational。"""
    zarr, store = cube
    from zarr.storage import LocalStore

    class CountingStore(LocalStore):
        def __init__(self, inner):
            self._inner = inner
            self.chunk_reads = 0
            super().__init__(root=inner.root, read_only=True)

        async def get(self, key, *a, **kw):
            if "/c/" in str(key):
                self.chunk_reads += 1
            return await self._inner.get(key, *a, **kw)

    cs = CountingStore(LocalStore(root=str(store), read_only=True))
    root = zarr.open_group(store=cs, mode="r")
    arr = root["b1"]
    t0 = time.perf_counter()
    window = arr[2, 0:64, 0:64]
    elapsed = time.perf_counter() - t0
    assert window.shape == (64, 64)
    assert cs.chunk_reads == 1, cs.chunk_reads
    assert elapsed < 5.0  # 有界宽护栏（非脆阈值）
    print(f"[lakehouse-perf] cube slice read={elapsed:.4f}s (informational)")


def test_chunk_backup_cas_reuse(cube):
    """备份复用是结构断言：第二次备份必须全部 CAS 命中（new_blobs == 0）。"""
    _zarr, store = cube
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.lakehouse.dr import backup_cube_chunks
    from app.services.project_artifact_promotion import reset_content_store_root_cache

    reset_filesystem_blob_store()
    reset_content_store_root_cache()
    first = backup_cube_chunks(store)
    assert first["backed_up"] is True
    second = backup_cube_chunks(store)
    assert second["backed_up"] is True
    assert second["new_blobs"] == 0
    assert second["blobs"] == first["blobs"]
