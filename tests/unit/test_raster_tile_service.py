"""Unit tests for raster tile streaming service (app/services/raster_tile_service.py)."""
import io
import os
import tempfile
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image

from app.services.raster_tile_service import (
    tile_bounds_3857,
    _transparent_tile_png,
    _normalize_channel,
    render_raster_tile,
)


def test_tile_bounds_3857_zoom0():
    bounds = tile_bounds_3857(0, 0, 0)
    assert len(bounds) == 4
    # Full world bounding box approx [-20037508, -20037508, 20037508, 20037508]
    assert abs(bounds[0] - (-20037508.342789244)) < 1.0
    assert abs(bounds[2] - 20037508.342789244) < 1.0


def test_transparent_tile_png():
    png_bytes = _transparent_tile_png(256)
    assert png_bytes.startswith(b"\x89PNG")
    img = Image.open(rasterio.io.MemoryFile(png_bytes) if hasattr(rasterio.io, "MemoryFile") else open_image_bytes(png_bytes))
    assert img.size == (256, 256)
    assert img.mode == "RGBA"


def open_image_bytes(b: bytes) -> Image.Image:
    import io
    return Image.open(io.BytesIO(b))


def test_render_raster_tile_synthetic_geotiff():
    # Create small synthetic GeoTIFF (EPSG:4326, Beijing area)
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        path = f.name

    try:
        width, height = 100, 100
        data = np.ones((1, height, width), dtype=np.float32) * 50.0
        transform = from_bounds(116.0, 39.0, 117.0, 40.0, width, height)

        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0,
        ) as dst:
            dst.write(data)

        # Render tile covering Beijing zoom 8 (z=8, x=210, y=100)
        png_bytes = render_raster_tile(path, z=8, x=210, y=100, tile_size=256)
        assert png_bytes.startswith(b"\x89PNG")

        img = open_image_bytes(png_bytes)
        assert img.size == (256, 256)
        assert img.mode == "RGBA"
    finally:
        os.unlink(path)


def _write_dual_region_tiff(path: str) -> None:
    """200x200 float32 GeoTIFF: left half 0, right half 1000, nodata top rows,
    one stray NaN pixel inside the left half."""
    width = height = 200
    data = np.zeros((1, height, width), dtype=np.float32)
    data[0, :, width // 2:] = 1000.0
    data[0, :5, :] = -9999.0  # declared nodata
    data[0, 20, 20] = np.nan  # NaN that is NOT the declared nodata
    transform = from_bounds(116.0, 39.0, 117.0, 40.0, width, height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data)


def test_render_uses_dataset_global_stretch_across_tiles(monkeypatch, tmp_path):
    """Issue #410: all tiles of one raster normalize with the SAME
    dataset-global min/max (no per-tile stretch -> no color seams), and
    nodata/NaN pixels never enter the global stats."""
    from app.services import raster_tile_service as svc

    path = str(tmp_path / "dual_region.tif")
    _write_dual_region_tiff(path)

    seen = []
    real_normalize = svc._normalize_channel

    def spy(arr, valid_mask, *stretch):
        seen.append(stretch)
        return real_normalize(arr, valid_mask, *stretch)

    monkeypatch.setattr(svc, "_normalize_channel", spy)

    rendered = []
    # Grid of tiles overlapping the raster footprint (z=8, Beijing area).
    for x in range(209, 213):
        for y in range(95, 100):
            png = svc.render_raster_tile(path, z=8, x=x, y=y, tile_size=256)
            img = Image.open(io.BytesIO(png))
            if np.asarray(img)[:, :, 3].any():
                rendered.append((x, y))

    assert len(rendered) >= 2, "expected at least two non-transparent tiles"

    # Every normalization across every tile used one identical stretch.
    assert seen, "no normalization happened"
    assert len(set(seen)) == 1
    vmin, vmax = seen[0]
    # Dataset-global stats over VALID data only: nodata row (-9999) and the
    # stray NaN pixel are excluded from the min/max.
    assert vmin == 0.0
    assert vmax == 1000.0

    # Stats are cached per raster path after the first render.
    assert path in svc._STATS_CACHE


def test_multiband_read_uses_indexes_not_all_bands(monkeypatch, tmp_path):
    """Issue #410: src.read is called with indexes=(1,2,3) — a >3-band raster
    is no longer decoded band-by-band (previously read ALL bands then sliced
    [:3], ~2x decode waste for 4+ band rasters)."""
    from app.services import raster_tile_service as svc

    path = str(tmp_path / "four_band.tif")
    width = height = 100
    base = np.linspace(0.0, 1.0, width * height, dtype=np.float32).reshape(1, height, width)
    data = np.repeat(base, 4, axis=0) * np.array([1.0, 100.0, 10000.0, 1e6]).reshape(4, 1, 1)
    transform = from_bounds(116.0, 39.0, 117.0, 40.0, width, height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=4,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data)

    read_calls = []

    class RecordingReader:
        """Proxy around a real rasterio dataset that records read() calls."""

        def __init__(self, src):
            self._src = src

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._src.close()

        def __getattr__(self, name):
            return getattr(self._src, name)

        def read(self, *args, **kwargs):
            read_calls.append((args, kwargs))
            return self._src.read(*args, **kwargs)

    real_open = rasterio.open
    monkeypatch.setattr(
        rasterio, "open",
        lambda p, *a, **k: RecordingReader(real_open(p, *a, **k)),
    )

    png_bytes = svc.render_raster_tile(path, z=8, x=210, y=97, tile_size=256)
    assert png_bytes.startswith(b"\x89PNG")
    img = open_image_bytes(png_bytes)
    assert img.size == (256, 256)
    assert np.asarray(img)[:, :, 3].any()  # window actually overlaps the raster

    assert read_calls, "no read() calls recorded"
    for args, kwargs in read_calls:
        assert "indexes" in kwargs, f"read() called without indexes: {args=} {kwargs=}"
        assert kwargs["indexes"] == (1, 2, 3)

    # Stats cache holds exactly the 3 rendered bands.
    assert path in svc._STATS_CACHE
    assert len(svc._STATS_CACHE[path]) == 3


def test_normalize_channel_explicit_stretch():
    """Issue #410: explicit dataset-global bounds override the per-tile min/max."""
    arr = np.array([[0.0, 50.0], [100.0, 100.0]])
    valid = np.ones_like(arr, dtype=bool)

    local = _normalize_channel(arr, valid)  # per-tile stretch 0..100
    assert local[0, 0] == 0
    assert local[0, 1] == int(50 / 100 * 255)

    global_ = _normalize_channel(arr, valid, 0.0, 200.0)  # global stretch 0..200
    assert global_[0, 1] == int(50 / 200 * 255)
    assert global_[1, 1] == int(100 / 200 * 255)  # not clipped to 255 by local max

    # vmax == vmin -> zeros (constant band renders as 0).
    flat = _normalize_channel(arr, valid, 5.0, 5.0)
    assert (flat == 0).all()
