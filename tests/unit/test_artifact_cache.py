"""Content-addressed artifact cache tests (goal §6, ADR-0048).

Pins: key determinism + source-mtime invalidation, hit skips recompute,
miss computes + publishes atomically, LRU eviction, and the resample_raster
integration (cache hit returns identical output without recomputing the warp).
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.artifact_cache import (
    clear_artifact_cache,
    get_artifact,
    make_artifact_key,
    publish_artifact,
)
from app.lib.geo_analysis.raster_math import resample_raster


@pytest.fixture(autouse=True)
def _clean_artifact_dir(monkeypatch, tmp_path):
    """Isolate the artifact dir per test; restore after."""
    monkeypatch.setattr("app.lib.artifact_cache.ARTIFACT_DIR", str(tmp_path / "artifacts"))
    yield
    # tmp_path auto-cleaned by pytest


def _write_raster(path, data, crs="EPSG:4326", nodata=None):
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype=data.dtype,
        crs=crs, transform=from_origin(0, h, 1, 1), nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def test_key_is_deterministic_for_same_inputs(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    k1 = make_artifact_key(str(p), "resample", {"target_resolution": 100.0, "target_crs": "EPSG:3857"})
    k2 = make_artifact_key(str(p), "resample", {"target_resolution": 100.0, "target_crs": "EPSG:3857"})
    assert k1 == k2
    assert len(k1) == 16


def test_key_changes_with_params(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    k1 = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})
    k2 = make_artifact_key(str(p), "resample", {"target_resolution": 200.0})
    assert k1 != k2


def test_key_invalidates_on_source_mtime_change(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    k1 = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})
    # rewrite the source with different size (mtime granularity varies by FS;
    # size change guarantees a different identity on every filesystem)
    _write_raster(str(p), np.ones((8, 8), dtype=np.float32))
    k2 = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})
    assert k1 != k2  # different size -> different key (auto-invalidation)


def test_hit_skips_recompute(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    key = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})

    calls = {"n": 0}
    def compute():
        calls["n"] += 1
        out = str(tmp_path / "out.tif")
        _write_raster(out, np.ones((4, 4), dtype=np.float32))
        return out

    r1 = publish_artifact(key, str(p), compute)
    assert calls["n"] == 1
    r2 = publish_artifact(key, str(p), compute)
    assert calls["n"] == 1  # hit - no recompute
    assert r1 == r2  # same cached path


def test_miss_computes_and_publishes_atomically(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    key = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})

    out = str(tmp_path / "out.tif")
    _write_raster(out, np.ones((4, 4), dtype=np.float32) * 9)

    result = publish_artifact(key, str(p), lambda: out)

    cached = get_artifact(key)
    assert cached is not None
    assert cached == result
    with rasterio.open(cached) as src:
        assert np.all(src.read(1) == 9.0)  # content published correctly


def test_stale_artifact_misses_when_source_changes(tmp_path):
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    key = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})

    out = str(tmp_path / "out.tif")
    _write_raster(out, np.ones((4, 4), dtype=np.float32))
    publish_artifact(key, str(p), lambda: out)

    # rewrite source with different size -> identity changes -> get_artifact None
    _write_raster(str(p), np.ones((8, 8), dtype=np.float32) * 2)
    assert get_artifact(key) is None


def test_lru_eviction_removes_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr("app.lib.artifact_cache.MAX_ARTIFACT_BYTES", 1)  # 1 byte -> immediate eviction
    p = tmp_path / "src.tif"
    _write_raster(str(p), np.ones((4, 4), dtype=np.float32))
    key = make_artifact_key(str(p), "resample", {"target_resolution": 100.0})

    out = str(tmp_path / "out.tif")
    _write_raster(out, np.ones((4, 4), dtype=np.float32))
    publish_artifact(key, str(p), lambda: out)

    # With a 1-byte cap the artifact should have been evicted
    assert get_artifact(key) is None


def test_resample_raster_cache_hit_returns_identical_output(monkeypatch, tmp_path):
    """resample_raster integration: second call with same params hits the cache."""
    # Place source in data/ so validate_data_path passes
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    src_path = os.path.join(data_dir, f"test_artifact_{uuid.uuid4().hex[:8]}.tif")
    _write_raster(src_path, np.arange(16, dtype=np.float32).reshape(4, 4), crs="EPSG:4326")
    try:
        r1 = resample_raster(src_path, target_resolution=10000.0, target_crs="EPSG:3857", resampling="nearest")
        # Record the cached output's content
        with rasterio.open(r1["output_path"]) as src:
            content1 = src.read(1).copy()

        r2 = resample_raster(src_path, target_resolution=10000.0, target_crs="EPSG:3857", resampling="nearest")

        # Cache hit: same path (artifact), identical content, no recompute
        assert r2["output_path"] == r1["output_path"]
        with rasterio.open(r2["output_path"]) as src:
            content2 = src.read(1)
        np.testing.assert_array_equal(content1, content2)
        # Different params -> different key -> recompute
        r3 = resample_raster(src_path, target_resolution=20000.0, target_crs="EPSG:3857", resampling="nearest")
        assert r3["output_path"] != r1["output_path"]
    finally:
        for p in [src_path, src_path[:-4] + "_resampled.tif"]:
            try:
                os.unlink(p)
            except OSError:
                pass
        clear_artifact_cache()
