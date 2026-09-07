"""Raster Runtime Wave 6 — chunk descriptors, per-chunk hooks, chunk cache,
COG ingest seam, temporal descriptor fields, and the two whole-read hazard
fixes (audit 05 §7.1-§7.4, §7.7).

Spy discipline (extends tests/unit/lib/test_geo_raster_v5.py:109-160 to the
raw ``rasterio.open`` sites): the terrain full-read path may whole-read ONLY
below the byte budget (guard fires BEFORE any read); the STAC DEM sentinel
path never issues an unbounded native read at all.
"""
from __future__ import annotations

import asyncio
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from app.lib.geo_raster import RasterReader, RasterReaderError, execute_windowed
from app.lib.geo_raster import AlgorithmProfile
from app.lib.geo_raster.chunk import (
    ChunkCacheBackend,
    RasterChunkDescriptor,
    chunk_digest,
    iter_chunk_descriptors,
    raster_runtime_capabilities,
)
from app.lib.geo_analysis.raster_grid import iter_bounded_windows
from app.lib.geo_analysis.raster_windowed import WindowedRasterWriter, build_output_profile
from app.lib.artifact_cache import (
    _chunk_cache_cap,
    _evict_chunks_if_needed,
    clear_chunk_cache,
    get_chunk,
    make_chunk_cache_key,
    publish_chunk,
)


# ── fixtures / helpers ──────────────────────────────────────────────────────

def _write_tiled(path, data, *, dtype="uint8", nodata=None, count=1, block=64):
    h, w = data.shape[-2], data.shape[-1]
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=count,
        dtype=dtype, crs="EPSG:4326", transform=from_origin(0, h, 1, 1),
        nodata=nodata, tiled=True, blockxsize=block, blockysize=block,
    ) as dst:
        if data.ndim == 2:
            dst.write(data, 1)
        else:
            dst.write(data)
    return str(path)


@pytest.fixture()
def tiled_raster(tmp_path):
    rng = np.random.default_rng(11)
    data = rng.integers(1, 200, size=(256, 256), dtype=np.uint8)
    return _write_tiled(tmp_path / "t.tif", data), data


class _ReadSpy:
    """Same discipline as test_geo_raster_v5._ReadSpy: a read is WHOLE iff
    it carries neither ``window=`` nor ``out_shape=``."""

    def __init__(self):
        self.reads: list[dict] = []
        self._real_open = rasterio.open

    def __call__(self, path, mode="r", **kwargs):
        ds = self._real_open(path, mode, **kwargs)
        if mode != "r":
            return ds
        spy = self
        orig_read = ds.read

        def read(*a, **kw):
            window = kw.get("window")
            area = None
            if window is not None:
                area = int(window.width) * int(window.height)
            elif kw.get("out_shape") is not None:
                shape = kw["out_shape"]
                dims = shape[1:] if len(shape) == 3 else shape
                area = int(dims[0]) * int(dims[1])
            spy.reads.append({"windowed": window is not None or kw.get("out_shape") is not None, "area": area})
            return orig_read(*a, **kw)

        ds.read = read  # type: ignore[method-assign]
        return ds

    @property
    def whole_reads(self) -> list[dict]:
        return [r for r in self.reads if not r["windowed"]]


# ── 1. chunk descriptors ────────────────────────────────────────────────────


class TestChunkDescriptors:
    def test_partition_matches_iter_bounded_windows(self, tiled_raster):
        p, _ = tiled_raster
        with RasterReader.open(p) as reader:
            descs = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
            ds = reader._ds()
            meta = reader.metadata()
            bare = [
                (int(w.col_off), int(w.row_off), int(w.width), int(w.height))
                for w in iter_bounded_windows(
                    meta.width, meta.height, window_side=128, src=ds)
            ]
        assert [(d.window[0], d.window[1], d.window[2], d.window[3]) for d in descs] == bare
        # 64×64 native blocks fit the 128-side budget → block-aligned partition
        assert len(descs) == 16
        assert all(d.window[2] == 64 and d.window[3] == 64 for d in descs)
        # exact partition: same total area, all in-bounds
        assert sum(d.window[2] * d.window[3] for d in descs) == 256 * 256
        assert all(d.window[2] > 0 and d.window[3] > 0 for d in descs)

    def test_deterministic_ids_and_byte_math(self, tiled_raster):
        p, _ = tiled_raster
        with RasterReader.open(p) as reader:
            d1 = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
            d2 = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
        assert [d.chunk_id for d in d1] == [d.chunk_id for d in d2]
        assert len({d.chunk_id for d in d1}) == len(d1)  # unique
        for d in d1:
            # uint8 single band: byte_size == pixel count exactly
            assert d.byte_size == d.window[2] * d.window[3] * 1 * 1
            assert len(d.chunk_id) == 16
            assert d.dtype == "uint8"
            assert d.band_indexes == (1,)
            assert d.source_fingerprint  # V5 content fingerprint present

    def test_multiband_byte_math_and_band_indexes(self, tmp_path):
        data = np.stack([np.full((64, 64), 1, "uint8"), np.full((64, 64), 2, "uint8")])
        p = _write_tiled(tmp_path / "mb.tif", data, count=2)
        with RasterReader.open(p) as reader:
            descs = list(iter_chunk_descriptors(reader, bands=(1, 2)))
        (d,) = descs
        assert d.band_indexes == (1, 2)
        assert d.byte_size == 64 * 64 * 1 * 2  # × band count

    def test_projection_roundtrip_and_dict_shape(self, tiled_raster):
        p, _ = tiled_raster
        with RasterReader.open(p) as reader:
            d = list(iter_chunk_descriptors(reader, window_size=(128, 128)))[0]
        proj = d.to_dict()
        assert set(proj) == {
            "chunk_id", "source_fingerprint", "grid", "window", "dtype",
            "byte_size", "band_indexes", "source_uri",
        }
        assert set(proj["window"]) == {"col_off", "row_off", "width", "height"}
        assert isinstance(proj["grid"], dict)
        rt = RasterChunkDescriptor.from_dict(proj)
        assert rt.chunk_id == d.chunk_id
        assert rt.window == d.window
        assert rt.byte_size == d.byte_size
        assert rt.band_indexes == d.band_indexes

    def test_different_windows_get_different_ids(self, tiled_raster):
        p, _ = tiled_raster
        with RasterReader.open(p) as reader:
            a = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
        assert len({d.chunk_id for d in a}) == len(a)
        assert a[0].chunk_id != a[1].chunk_id


# ── 2. per-chunk hooks ──────────────────────────────────────────────────────


class TestExecuteWindowedHooks:
    def test_on_chunk_done_receives_every_chunk_with_digest(self, tiled_raster):
        p, data = tiled_raster
        seen: list[tuple] = []
        with RasterReader.open(p) as reader:
            descs = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
            res = execute_windowed(
                reader, AlgorithmProfile(), lambda a, c, r: a.astype("float64") * 2,
                window_size=(128, 128), dst_dtype="float64",
                on_chunk_done=lambda d, dg, bs: seen.append((d, dg, bs)),
            )
        assert len(seen) == 16
        assert [d.chunk_id for d, _, _ in seen] == [d.chunk_id for d in descs]
        for _, dg, bs in seen:
            assert isinstance(dg, str) and len(dg) == 64  # sha256 hexdigest
            assert bs > 0
        np.testing.assert_array_equal(res.array, data.astype(np.float64) * 2)

    def test_digest_stable_and_payload_sensitive(self, tiled_raster):
        digests = []
        for _ in range(2):
            run: list[str] = []
            with RasterReader.open(tiled_raster[0]) as reader:
                execute_windowed(
                    reader, AlgorithmProfile(), lambda a, c, r: a,
                    window_size=(128, 128),
                    on_chunk_done=lambda d, dg, bs: run.append(dg),
                )
            digests.append(run)
        assert digests[0] == digests[1]  # deterministic for identical compute
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        grid = RasterGridProfile(width=4, height=4, crs=None,
                                 transform=(1, 0, 0, 0, -1, 4), dtype="uint8")
        arr = np.zeros((4, 4), dtype="uint8")
        assert chunk_digest(grid, arr) != chunk_digest(grid, arr + 1)

    def test_windowed_writer_on_chunk_done(self, tmp_path):
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        out = str(tmp_path / "w.tif")
        profile = build_output_profile(width=32, height=32, dtype="float32",
                                       crs="EPSG:3857", transform=from_origin(0, 32, 1, 1))
        grid = RasterGridProfile(width=32, height=32, crs="EPSG:3857",
                                 transform=from_origin(0, 32, 1, 1)[:6],
                                 dtype="float32", band_count=1)
        seen: list[tuple] = []
        with WindowedRasterWriter(out, profile=profile, grid=grid,
                                  on_chunk_done=lambda d, dg, bs: seen.append((d, dg, bs))) as w:
            w.write(Window(0, 0, 32, 16), np.zeros((16, 32), dtype="float32"), band=1)
            w.write(Window(0, 16, 32, 16), np.ones((16, 32), dtype="float32"), band=1)
            w.finalize()
        assert len(seen) == 2
        assert seen[0][0].window == (0, 0, 32, 16)
        assert seen[1][0].window == (0, 16, 32, 16)
        assert seen[0][1] != seen[1][1]  # content-sensitive digest
        assert seen[0][2] == 16 * 32 * 4

    def test_writer_callback_failure_never_breaks_product(self, tmp_path):
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        out = str(tmp_path / "w.tif")
        profile = build_output_profile(width=16, height=16, dtype="float32",
                                       crs="EPSG:3857", transform=from_origin(0, 16, 1, 1))
        grid = RasterGridProfile(width=16, height=16, crs="EPSG:3857",
                                 transform=from_origin(0, 16, 1, 1)[:6],
                                 dtype="float32", band_count=1)

        def boom(*a):
            raise RuntimeError("observer blew up")

        with WindowedRasterWriter(out, profile=profile, grid=grid, on_chunk_done=boom) as w:
            w.write(Window(0, 0, 16, 16), np.zeros((16, 16), dtype="float32"), band=1)
            fin = w.finalize()
        assert fin["content_fingerprint"]
        import rasterio as rio

        with rio.open(out) as src:
            assert src.read(1).shape == (16, 16)


# ── 3. chunk cache ──────────────────────────────────────────────────────────


@pytest.fixture()
def chunk_cache_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # data/artifacts/chunks lands in tmp
    clear_chunk_cache()
    yield tmp_path
    clear_chunk_cache()


class TestChunkCache:
    def test_key_derivation_deterministic(self, chunk_cache_env):
        k1 = make_chunk_cache_key("a.tif", {"window": [0, 0, 8, 8]}, "op")
        k2 = make_chunk_cache_key("a.tif", {"window": [0, 0, 8, 8]}, "op")
        k3 = make_chunk_cache_key("a.tif", {"window": [0, 0, 8, 9]}, "op")
        k4 = make_chunk_cache_key("a.tif", {"window": [0, 0, 8, 8]}, "other")
        assert k1 == k2 and k1 != k3 and k1 != k4
        assert len(k1) == 16

    def test_publish_get_roundtrip_and_invalidation(self, chunk_cache_env):
        (chunk_cache_env / "src.tif").write_bytes(b"SOURCE")
        payload = b"\x93NUMPY" + b"x" * 32
        key = make_chunk_cache_key(str(chunk_cache_env / "src.tif"), {"w": 1}, "op")
        assert get_chunk(key) is None
        src = str(chunk_cache_env / "src.tif")
        out = publish_chunk(key, payload, source_path=src)
        assert out is not None and os.path.exists(out)
        assert open(out, "rb").read() == payload
        assert get_chunk(key) == out

        # source identity change (mtime) → stale → honest miss
        future = os.path.getmtime(src) + 10
        os.utime(src, (future, future))
        assert get_chunk(key) is None

    def test_execute_windowed_hit_avoids_recompute(self, chunk_cache_env, tiled_raster):
        p, data = tiled_raster
        calls = {"n": 0}

        def fn(a, c, r):
            calls["n"] += 1
            return a.astype("float64") * 3

        cache = ChunkCacheBackend(p, "triple_op")
        with RasterReader.open(p) as reader:
            r1 = execute_windowed(reader, AlgorithmProfile(), fn,
                                  window_size=(128, 128), dst_dtype="float64",
                                  chunk_cache=cache)
            first_calls = calls["n"]
            hook_count = {"n": 0}
            r2 = execute_windowed(
                reader, AlgorithmProfile(), fn,
                window_size=(128, 128), dst_dtype="float64", chunk_cache=cache,
                on_chunk_done=lambda d, dg, bs: hook_count.__setitem__(
                    "n", hook_count["n"] + 1),
            )
        assert first_calls == 16  # cold: every chunk computed
        assert calls["n"] == 16  # warm: fn NEVER re-invoked
        assert hook_count["n"] == 16  # progress hook still fires per chunk
        np.testing.assert_array_equal(r1.array, r2.array)
        np.testing.assert_array_equal(r2.array, data.astype(np.float64) * 3)

    def test_cache_refuses_halo_and_global_ops(self, chunk_cache_env, tiled_raster):
        p, _ = tiled_raster
        cache = ChunkCacheBackend(p, "op")
        with RasterReader.open(p) as reader:
            with pytest.raises(RasterReaderError, match="element-wise"):
                execute_windowed(reader, AlgorithmProfile(halo=2),
                                 lambda a, c, r: a, chunk_cache=cache)
            with pytest.raises(RasterReaderError, match="element-wise"):
                execute_windowed(reader, AlgorithmProfile(global_stat_required=True),
                                 lambda a, c, r: a, chunk_cache=cache)

    def test_dedicated_cap_eviction_oldest_first(self, chunk_cache_env, monkeypatch):
        keys = []
        monkeypatch.setenv("WEBGIS_CHUNK_CACHE_BYTES", "4096")  # publish unevicted
        for i in range(4):
            k = make_chunk_cache_key("s.tif", {"i": i}, "op")
            publish_chunk(k, b"y" * 256, source_path="s.tif")
            keys.append(k)
        assert all(get_chunk(k) is not None for k in keys)
        # pin deterministic mtimes (oldest first), then shrink the cap
        import time as _time

        base = _time.time() - 1000
        for i, k in enumerate(keys):
            os.utime(os.path.join("data", "artifacts", "chunks", f"{k}.meta"),
                     (base + i, base + i))
        monkeypatch.setenv("WEBGIS_CHUNK_CACHE_BYTES", str(int(256 * 2.5)))
        assert _chunk_cache_cap() == 640
        _evict_chunks_if_needed()
        assert get_chunk(keys[0]) is None
        assert get_chunk(keys[1]) is None
        assert get_chunk(keys[2]) is not None
        assert get_chunk(keys[3]) is not None

    def test_backend_roundtrip_via_execute_windowed_store(self, chunk_cache_env, tiled_raster):
        p, _ = tiled_raster
        cache = ChunkCacheBackend(p, "op")
        with RasterReader.open(p) as reader:
            descs = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
            execute_windowed(reader, AlgorithmProfile(),
                             lambda a, c, r: a, window_size=(128, 128),
                             chunk_cache=cache)
        # chunk ids from a fresh backend over the same source reproduce keys
        cache2 = ChunkCacheBackend(p, "op")
        key = cache2.key_for(descs[0])
        arr = cache2.load(key, expected_shape=(64, 64), expected_dtype="uint8")
        assert arr is not None
        assert cache2.load(key, expected_shape=(1, 1)) is None  # shape mismatch → miss
        assert cache2.load("deadbeef" * 2) is None  # unknown key → miss


# ── 4. COG ingest seam ──────────────────────────────────────────────────────


class TestCogIngest:
    def _write_strip(self, path, size=1024):
        rng = np.random.default_rng(3)
        data = rng.integers(0, 255, size=(size, size), dtype=np.uint8)
        with rasterio.open(
            path, "w", driver="GTiff", width=size, height=size, count=1,
            dtype="uint8", crs="EPSG:4326", transform=from_origin(0, size, 1, 1),
        ) as dst:  # NOT tiled → validate_cog must report not ok
            dst.write(data, 1)
        return str(path)

    @pytest.mark.parametrize("fn_name", ["ensure_cog", "to_cog"])
    def test_conversion_produces_valid_cog(self, tmp_path, fn_name):
        from app.lib.geo_raster.cog import ensure_cog, to_cog, validate_cog

        src = self._write_strip(tmp_path / "plain.tif")
        fn = ensure_cog if fn_name == "ensure_cog" else to_cog
        out = fn(src, tmp_path / "cog")
        report = validate_cog(str(out))
        assert report["ok"], report["issues"]
        assert out != src  # original never mutated in place
        with rasterio.open(src) as s, rasterio.open(out) as o:
            assert (s.read(1) == o.read(1)).all()
            assert o.profile.get("tiled") or o.is_tiled

    def test_ensure_cog_idempotent_on_valid_cog(self, tmp_path):
        from app.lib.geo_raster.cog import ensure_cog

        src = self._write_strip(tmp_path / "plain.tif")
        cog = ensure_cog(src, tmp_path / "cog")
        again = ensure_cog(cog, tmp_path / "cog")
        assert again == cog  # already a COG → same path, no reconvert

    def test_typed_errors_on_missing_file(self, tmp_path):
        from app.lib.geo_raster.cog import CogWriteError, ensure_cog, to_cog

        missing = str(tmp_path / "nope.tif")
        with pytest.raises(CogWriteError, match="not found"):
            to_cog(missing, tmp_path)
        with pytest.raises(CogWriteError, match="not found"):
            ensure_cog(missing, tmp_path)

    def test_tool_wrapper_registered_and_typed_error(self, tmp_path, monkeypatch):
        from app.tools.registry import ToolRegistry
        from app.tools.raster_tools_cog import register_raster_cog_tools

        registry = ToolRegistry()
        register_raster_cog_tools(registry)
        assert "convert_raster_to_cog" in registry.list_tools()
        func = registry._tools["convert_raster_to_cog"]

        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        res = asyncio.run(func("missing.tif", "data/cog"))
        assert res["success"] is False
        assert res["code"] in ("INVALID_PATH", "COG_CONVERT_FAILED")
        assert res.get("correction_hint")

        self._write_strip(tmp_path / "data" / "plain.tif")
        res = asyncio.run(func("plain.tif", "data/cog"))
        assert res["success"] is True
        assert res["converted"] is True
        assert res["validation"]["ok"] is True
        # pointing the tool AT the COG output: honest already_cog disclosure
        cog_path = os.path.relpath(res["output_path"], tmp_path / "data")
        res2 = asyncio.run(func(cog_path, "data/cog"))
        assert res2["already_cog"] is True
        assert res2["converted"] is False
        assert res2["output_path"] == res["output_path"]


# ── 6. temporal descriptor extension (additive) ─────────────────────────────


class TestDescriptorTimes:
    def _desc(self, **kw):
        from app.schemas.raster_spec import RasterArtifactDescriptor

        return RasterArtifactDescriptor(file_path="x.tif", **kw)

    def test_times_and_stack_ref_accepted(self):
        d = self._desc(times=["2024-01-01T00:00:00Z", "2024-06-01"], stack_ref="data/cubes/a.zarr")
        assert d.times == ["2024-01-01T00:00:00Z", "2024-06-01"]
        assert d.stack_ref == "data/cubes/a.zarr"
        assert d.model_dump()["times"] is not None

    def test_default_absent_backcompat(self):
        d = self._desc()
        assert d.times is None and d.stack_ref is None

    def test_non_iso8601_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="ISO-8601"):
            self._desc(times=["not-a-date"])
        with pytest.raises(ValidationError, match="ISO-8601"):
            self._desc(times=["2024-01-01", ""])

    def test_bounded_at_1024(self):
        from pydantic import ValidationError

        ok = ["2024-01-01T00:00:00Z"] * 1024
        assert self._desc(times=ok).times == ok
        with pytest.raises(ValidationError, match="1024"):
            self._desc(times=["2024-01-01T00:00:00Z"] * 1025)


# ── capabilities honesty ────────────────────────────────────────────────────


def test_raster_runtime_capabilities_honest():
    from app.lib.geo_raster.zarr import zarr_available

    caps = raster_runtime_capabilities()
    assert caps == {"cog": True, "zarr": zarr_available(), "chunk_cache": True}


# ── 7a. terrain full-read accounting (float64 doubling) ─────────────────────


class TestTerrainFullReadBudget:
    @pytest.fixture()
    def terrain_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        return tmp_path

    def test_over_budget_rejected_before_any_read(self, terrain_env, monkeypatch):
        from app.tools.terrain_analysis import _read_terrain_window

        # 15000×15000 = 225M px ≤ 250M pixel cap (check_grid passes) but
        # 225M × 8 B = 1.8 GiB > 1 GiB float64 budget → the new guard fires.
        w = h = 15000
        # sparse GTiff (header + no tiles written) — creation only, no pixels
        with rasterio.open(
            terrain_env / "data" / "big.tif", "w", driver="GTiff", width=w,
            height=h, count=1, dtype="uint8", crs="EPSG:4326",
            transform=from_origin(0, h, 1, 1),
        ):
            pass
        spy = _ReadSpy()
        monkeypatch.setattr(rasterio, "open", spy)
        from app.lib.geo_analysis.raster_guard import RasterResourceExceededError

        with pytest.raises(RasterResourceExceededError) as ei:
            # validate_data_path resolves relative inputs under ./data
            _read_terrain_window("big.tif", None)
        assert ei.value.error_code == "RASTER_FULL_READ_BUDGET_EXCEEDED"
        assert ei.value.estimated_bytes == w * h * 8
        assert spy.reads == [], "guard must fire BEFORE the whole read"

    def test_small_grid_still_passes(self, terrain_env, monkeypatch):
        from app.tools.terrain_analysis import _read_terrain_window

        data = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
        _write_tiled(terrain_env / "data" / "small.tif", data, block=64)
        arr, transform, crs, nodata, bounds = _read_terrain_window("small.tif", None)
        assert arr.dtype == np.float64
        assert arr.shape == (64, 64)
        np.testing.assert_array_equal(arr, data)


# ── 7b. STAC DEM sentinel path: bounded strip reads ─────────────────────────


class _FakeStripDS:
    """Dataset stand-in: records every windowed read, slices from one array."""

    def __init__(self, arr: np.ndarray):
        self.arr = arr
        self.calls: list[tuple[int, int, int, int]] = []

    def read(self, band, window=None):
        assert window is not None, "DEM sentinel path must always read via window="
        c, r = int(window.col_off), int(window.row_off)
        w, h = int(window.width), int(window.height)
        self.calls.append((c, r, w, h))
        return self.arr[r:r + h, c:c + w]


class TestDemSentinelBoundedRead:
    def _native(self, h=211, w=173, seed=9):
        rng = np.random.default_rng(seed)
        arr = rng.uniform(-400, 2000, size=(h, w)).astype("float64")
        arr[::17, ::13] = -9999.0  # scattered sentinels
        return arr

    def test_bitwise_equivalence_with_nan_block_mean(self):
        from app.services.rs.stac_client import (
            _nan_block_mean,
            _read_sentinel_masked_decimated,
        )

        arr = self._native()
        ds = _FakeStripDS(arr)
        win = Window(0, 0, arr.shape[1], arr.shape[0])
        out_h, out_w = 70, 11  # deliberately non-integer ratios
        got = _read_sentinel_masked_decimated(
            ds, win, out_h, out_w, strip_bytes=16 * 1024)  # force many strips
        full = arr.copy()
        full[full <= -9999.0] = np.nan  # caller-side masking (#1002 contract)
        expected = _nan_block_mean(full, out_h, out_w)
        assert got.shape == expected.shape
        assert (np.isnan(got) == np.isnan(expected)).all()
        np.testing.assert_allclose(got, expected, equal_nan=True)
        assert len(ds.calls) > 1, "streaming actually split the read"

    def test_single_strip_matches_full_read_too(self):
        from app.services.rs.stac_client import (
            _nan_block_mean,
            _read_sentinel_masked_decimated,
        )

        arr = self._native(64, 64)
        ds = _FakeStripDS(arr)
        got = _read_sentinel_masked_decimated(
            ds, Window(0, 0, 64, 64), 8, 8, strip_bytes=1 << 30)
        full = arr.copy()
        full[full <= -9999.0] = np.nan  # caller-side masking (#1002 contract)
        expected = _nan_block_mean(full, 8, 8)
        np.testing.assert_allclose(got, expected, equal_nan=True)
        assert len(ds.calls) == 1  # one strip fits the generous budget

    def test_every_read_bounded_and_total_covered(self):
        from app.services.rs.stac_client import (
            DEM_SENTINEL_STRIP_BYTES,
            _read_sentinel_masked_decimated,
        )

        h, w = 1000, 900
        arr = self._native(h, w)
        ds = _FakeStripDS(arr)
        win = Window(0, 0, w, h)
        out_h, out_w = 100, 90
        got = _read_sentinel_masked_decimated(ds, win, out_h, out_w)  # default budget
        assert got.shape == (out_h, out_w)
        # each strip's native window stays under the documented budget
        # (float64 accounting: rows_per_strip = strip_bytes // (W*16))
        max_rows = max(h // out_h, DEM_SENTINEL_STRIP_BYTES // (w * 16))
        for (_, r, sw, sh) in ds.calls:
            assert sw <= w
            assert sh <= max_rows, f"strip read {sh} rows > budget {max_rows}"
        # coverage: strips tile the window exactly once, no skips
        assert sum(sh for (_, _, _, sh) in ds.calls) == h

    def test_sentinel_masking_applied_per_strip(self):
        from app.services.rs.stac_client import _read_sentinel_masked_decimated

        arr = np.full((100, 50), 100.0)
        arr[10, 10] = -9999.0  # sentinel inside the first strip region
        arr[90, 40] = -9999.0  # sentinel in a later strip
        ds = _FakeStripDS(arr)
        got = _read_sentinel_masked_decimated(
            ds, Window(0, 0, 50, 100), 10, 5, strip_bytes=50 * 16 * 12)
        # sentinel pixels are EXCLUDED from block means (no -9999 leakage)
        assert got.min() > 0
        assert got.max() <= 100.0
