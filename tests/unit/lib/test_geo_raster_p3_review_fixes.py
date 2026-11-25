"""P3 逐行审查修复回归 —— app/lib/geo_raster（任务 P3-app_lib_geo_raster-00）。

覆盖：非方形 window_size 两轴都生效（chunk.py / windowed.py）、read_band /
read_overview 的 band 范围校验、RemoteReadPolicy.max_attempts 校验、
/vsi* 源的 kind 分类、write_zarr_cube 按源复用 RasterReader（假 zarr，
不需要安装 zarr）。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.windows import Window

from app.lib.geo_raster import (
    AlgorithmProfile,
    RasterReader,
    RasterReaderError,
    execute_windowed,
    zarr as zarr_mod,
)
from app.lib.geo_raster.chunk import iter_chunk_descriptors
from app.lib.geo_raster.remote import (
    RemoteReadPolicy,
    RemoteReadSession,
    remote_read_window,
)
from app.lib.geo_raster.source import RasterSource


def _write_tiled(path, data, *, count=1, block=64):
    h, w = data.shape[-2], data.shape[-1]
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=count,
        dtype=data.dtype.name, crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1),
        tiled=True, blockxsize=block, blockysize=block,
    ) as dst:
        if data.ndim == 2:
            dst.write(data, 1)
        else:
            dst.write(data)
    return str(path)


@pytest.fixture()
def tiled_2band(tmp_path):
    """256×192 two-band uint8 GeoTIFF (64×64 blocks) with overviews."""
    h, w = 192, 256
    data = np.stack([
        np.arange(h * w, dtype="uint8").reshape(h, w),
        np.full((h, w), 7, dtype="uint8"),
    ])
    path = _write_tiled(tmp_path / "two_band.tif", data, count=2)
    with rasterio.open(path, "r+") as ds:
        ds.build_overviews([2], Resampling.nearest)
    return path


# ── non-square window_size (chunk.py / windowed.py) ─────────────────────────

def test_iter_chunk_descriptors_honours_non_square_window_size(tiled_2band):
    with RasterReader.open(tiled_2band) as reader:
        descs = list(iter_chunk_descriptors(reader, window_size=(32, 16)))
    assert len(descs) == (256 // 32) * (192 // 16)
    assert all(d.window[2] == 32 and d.window[3] == 16 for d in descs)
    assert sum(d.window[2] * d.window[3] for d in descs) == 256 * 192


def test_execute_windowed_honours_non_square_window_size(tiled_2band):
    with RasterReader.open(tiled_2band) as reader:
        expected = reader.read_band(2)
        cores: list[tuple[int, int, int, int]] = []

        def fn(data, core, read_win):
            cores.append(core)
            return data

        res = execute_windowed(
            reader, AlgorithmProfile(), fn, band=2, window_size=(32, 16)
        )
    assert len(cores) == (256 // 32) * (192 // 16)
    assert all(c[2] == 32 and c[3] == 16 for c in cores)
    np.testing.assert_array_equal(res.array, expected)


# ── band range validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("band", [0, -1, 3])
def test_read_band_rejects_out_of_range_band(tiled_2band, band):
    with RasterReader.open(tiled_2band) as reader:
        with pytest.raises(RasterReaderError, match="band .* out of range"):
            reader.read_band(band)


@pytest.mark.parametrize("band", [0, -1, 3])
def test_read_overview_rejects_out_of_range_band(tiled_2band, band):
    with RasterReader.open(tiled_2band) as reader:
        with pytest.raises(RasterReaderError, match="band .* out of range"):
            reader.read_overview(band=band)


# ── remote retry policy validation ──────────────────────────────────────────

def test_remote_read_window_rejects_non_positive_max_attempts():
    session = RemoteReadSession(policy=RemoteReadPolicy(max_attempts=0))
    with pytest.raises(ValueError, match="max_attempts"):
        remote_read_window(
            session, "https://example.com/a.tif", object(), 1,
            Window(0, 0, 1, 1),
        )


# ── RasterSource.kind for bare /vsi prefixes ────────────────────────────────

@pytest.mark.parametrize(
    "uri",
    [
        "/vsi/foo.tif",
        "/vsizip/archive.zip/inner.tif",
        "/vsitar/archive.tar/inner.tif",
        "/vsicurl/https://example.com/a.tif",
    ],
)
def test_kind_reports_remote_for_every_vsi_prefix(uri):
    assert RasterSource.auto(uri).kind == "remote"


# ── write_zarr_cube reader reuse (fake zarr, no dependency) ─────────────────

class _FakeCubeArray:
    def __init__(self, shape, dtype):
        self.shape = shape
        self.dtype = np.dtype(dtype)
        self.attrs = {}
        self.writes = []

    def __setitem__(self, index, value):
        self.writes.append((index, np.array(value, copy=True)))


class _FakeZarr:
    def __init__(self):
        self.array = None

    def create_array(self, **kwargs):
        self.array = _FakeCubeArray(kwargs["shape"], kwargs["dtype"])
        return self.array


def test_write_zarr_cube_reuses_one_reader_per_source(
    tiled_2band, tmp_path, monkeypatch
):
    with RasterReader.open(tiled_2band) as reader:
        descs = list(iter_chunk_descriptors(reader, window_size=(128, 128)))
    assert len(descs) == 12  # 64×64 native blocks

    fake = _FakeZarr()
    monkeypatch.setattr(zarr_mod, "_require_zarr", lambda: fake)
    opened: list[str] = []
    real_open = RasterReader.open

    def counting_open(cls, uri):
        opened.append(uri)
        return real_open(uri)

    monkeypatch.setattr(RasterReader, "open", classmethod(counting_open))

    zarr_mod.write_zarr_cube([descs], ["2024-01-01T00:00:00Z"], tmp_path / "cube.zarr")

    assert opened == [tiled_2band]  # one open, not one per chunk
    assert len(fake.array.writes) == len(descs)
    for (_t, row_slice, col_slice), data in fake.array.writes:
        assert data.shape == (
            row_slice.stop - row_slice.start,
            col_slice.stop - col_slice.start,
        )
