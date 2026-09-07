"""Raster Runtime Wave 6 — Zarr foundation (audit 05 §7.5).

Unavailable lane: with the import probe forced False, EVERY entry raises
the typed ZarrUnavailable (code ZARR_UNAVAILABLE, correction_hint
"pip install zarr") — honest degrade, zero fake success.
Available lane (venv has zarr): real store roundtrips under importorskip.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_raster import RasterReader
from app.lib.geo_raster.chunk import (
    build_chunk_descriptor_from_grid,
    raster_runtime_capabilities,
)
from app.lib.geo_analysis.raster_grid import RasterGridProfile
from app.lib.geo_raster.zarr import (
    ZarrGridError,
    ZarrStoreError,
    ZarrUnavailable,
    open_zarr_array,
    write_zarr_cube,
    zarr_available,
    zarr_chunk_descriptors,
)

GRID = RasterGridProfile(
    width=64, height=48, crs="EPSG:4326",
    transform=(1.0, 0.0, 0.0, 0.0, -1.0, 48.0),
    dtype="float32", nodata=-9999.0, band_count=1,
)
TIMES = ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z"]


# ── unavailable lane (probe forced False — every entry refuses) ────────────


@pytest.fixture()
def zarr_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "zarr", None)  # import zarr → ImportError
    return None


class TestZarrUnavailableLane:
    def test_probe_reports_false(self, zarr_blocked):
        assert zarr_available() is False

    def test_typed_error_shape(self):
        err = ZarrUnavailable()
        assert err.code == "ZARR_UNAVAILABLE"
        assert err.correction_hint == "pip install zarr"
        payload = err.to_dict()
        assert payload["code"] == "ZARR_UNAVAILABLE"
        assert payload["success"] is False

    def test_every_entry_raises_typed(self, zarr_blocked, tmp_path):
        with pytest.raises(ZarrUnavailable):
            open_zarr_array(tmp_path / "no.zarr")
        with pytest.raises(ZarrUnavailable):
            zarr_chunk_descriptors(tmp_path / "no.zarr")
        d = build_chunk_descriptor_from_grid(
            GRID, (0, 0, 16, 16), dtype="float32", source_uri="x.tif")
        with pytest.raises(ZarrUnavailable):
            write_zarr_cube([[d]], ["2024-01-01"], tmp_path / "out.zarr")

    def test_capabilities_disclose_false(self, zarr_blocked):
        caps = raster_runtime_capabilities()
        assert caps["zarr"] is False
        assert caps["cog"] is True and caps["chunk_cache"] is True


# ── available lane (real roundtrips) ────────────────────────────────────────

zarr_mod = pytest.importorskip("zarr")


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
        tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


@pytest.fixture()
def cube_sources(tmp_path):
    """Three same-grid GeoTIFF slices; value encodes the time index."""
    return [_write_slice(tmp_path / f"s{t}.tif", float(t + 1)) for t in range(3)]


def _default_descriptors(sources):
    # edge windows are pre-clamped to the 48-row grid (32-row south edge)
    windows = [(0, 0, 32, 32), (32, 0, 32, 32), (0, 32, 32, 16), (32, 32, 32, 16)]
    return [
        [
            build_chunk_descriptor_from_grid(
                GRID, win,
                dtype="float32", band_indexes=(1,),
                source_fingerprint="fp", source_uri=src,
            )
            for win in windows
        ]
        for src in sources
    ]


class TestZarrCubeRoundtrip:
    def test_write_and_read_cube_with_default_loader(self, tmp_path, cube_sources):
        out = write_zarr_cube(
            _default_descriptors(cube_sources), TIMES, tmp_path / "cube.zarr")
        arr = open_zarr_array(out)
        assert arr.shape == (3, 48, 64)
        assert arr.chunks[-2:] == (32, 32)
        assert str(arr.dtype) == "float32"
        assert list(arr.attrs["times"]) == TIMES
        assert arr.attrs["crs"] == "EPSG:4326"
        assert list(arr.attrs["transform"]) == list(GRID.transform)
        # real raster reads went through RasterReader (default loader)
        assert float(arr[0, 0, 0]) == 1.0
        assert float(arr[2, 40, 40]) == 3.0

    def test_chunk_descriptors_roundtrip_store(self, tmp_path, cube_sources):
        out = write_zarr_cube(
            _default_descriptors(cube_sources), TIMES, tmp_path / "cube.zarr")
        descs = zarr_chunk_descriptors(out)
        # 3 times × 2×2 spatial chunks (edge windows clamped to 32×16)
        assert len(descs) == 12
        windows = {d.window for d in descs}
        assert (0, 0, 32, 32) in windows
        assert (32, 32, 32, 16) in windows  # bottom edge clamped
        bands = sorted({d.band_indexes for d in descs})
        assert bands == [(1,), (2,), (3,)]  # time slices exposed as bands
        grid = descs[0].grid
        assert grid.width == 64 and grid.height == 48
        assert grid.crs == "EPSG:4326"
        assert len(descs[0].source_fingerprint) == 16
        assert all(d.chunk_id for d in descs)
        # descriptors are deterministic across calls
        again = zarr_chunk_descriptors(out)
        assert [d.chunk_id for d in again] == [d.chunk_id for d in descs]

    def test_read_chunk_loader_override(self, tmp_path):
        seen = []

        def loader(d, time_label):
            seen.append((d.chunk_id, time_label))
            return np.full((d.window[3], d.window[2]), 7.0, dtype="float32")

        descs = _default_descriptors(["ignored.tif"])
        out = write_zarr_cube(descs, TIMES[:1], tmp_path / "cube.zarr", read_chunk=loader)
        arr = open_zarr_array(out)
        assert float(arr[0, 10, 10]) == 7.0
        assert len(seen) == 4 and seen[0][1] == TIMES[0]

    def test_grid_mismatch_refused(self, tmp_path):
        other_grid = RasterGridProfile(
            width=64, height=48, crs="EPSG:3857",  # different CRS
            transform=(1.0, 0.0, 0.0, 0.0, -1.0, 48.0),
            dtype="float32", band_count=1,
        )
        good = build_chunk_descriptor_from_grid(
            GRID, (0, 0, 32, 32), dtype="float32")
        bad = build_chunk_descriptor_from_grid(
            other_grid, (0, 0, 32, 32), dtype="float32")
        with pytest.raises(ZarrStoreError, match="common grid"):
            write_zarr_cube([[good], [bad]], TIMES[:2], tmp_path / "cube.zarr",
                            read_chunk=lambda d, t: np.zeros((32, 32), "float32"))

    def test_times_length_mismatch_refused(self, tmp_path):
        descs = _default_descriptors(["a.tif"])
        with pytest.raises(ZarrStoreError, match="equal length"):
            write_zarr_cube(descs, TIMES[:2], tmp_path / "cube.zarr",
                            read_chunk=lambda d, t: np.zeros((32, 32), "float32"))

    def test_loader_shape_mismatch_refused(self, tmp_path):
        descs = _default_descriptors(["a.tif"])
        with pytest.raises(ZarrStoreError, match="expected"):
            write_zarr_cube(descs, TIMES[:1], tmp_path / "cube.zarr",
                            read_chunk=lambda d, t: np.zeros((3, 3), "float32"))

    def test_missing_store_and_missing_attrs(self, tmp_path):
        with pytest.raises(ZarrStoreError, match="not found"):
            open_zarr_array(tmp_path / "ghost.zarr")
        # a bare zarr array WITHOUT georeferencing attrs → typed grid refusal
        bare = zarr_mod.create_array(
            store=str(tmp_path / "bare.zarr"), shape=(16, 16),
            chunks=(8, 8), dtype="float32")
        bare.attrs["unrelated"] = 1
        with pytest.raises(ZarrGridError, match="georeferencing"):
            zarr_chunk_descriptors(tmp_path / "bare.zarr")

    def test_capabilities_reflect_true_probe(self):
        assert raster_runtime_capabilities()["zarr"] is True

    def test_raster_reader_window_matches_cube_payload(self, tmp_path, cube_sources):
        """Default loader is the sanctioned bounded window read (spy-clean)."""
        out = write_zarr_cube(
            _default_descriptors(cube_sources), TIMES, tmp_path / "cube.zarr")
        arr = open_zarr_array(out)
        with RasterReader.open(cube_sources[1]) as reader:
            expected = reader.read_window((0, 0, 64, 48), band=1)
        np.testing.assert_array_equal(np.asarray(arr[1]), expected)
