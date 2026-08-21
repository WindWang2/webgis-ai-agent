"""Issue #693 item 5: partial tile without declared nodata must be transparent outside footprint."""

import io
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image

from app.services.raster_tile_service import render_raster_tile, _STATS_CACHE


def _write_small_raster(path):
    # 100x100 raster covering 116.0-116.1 lon (~11km) — small, so many tiles are partial
    w = h = 100
    data = np.zeros((1, h, w), dtype=np.float32)
    data[0, :, w // 2:] = 1000.0
    tr = from_bounds(116.0, 39.0, 116.1, 39.1, w, h)
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32", crs="EPSG:4326", transform=tr) as dst:
        dst.write(data)


def test_partial_tile_transparent_outside_footprint(tmp_path):
    _STATS_CACHE.clear()
    path = str(tmp_path / "small.tif")
    _write_small_raster(path)
    # z11 tile 1684,782 is ~34% covered (found by brute force above)
    png = render_raster_tile(path, 11, 1684, 782, tile_size=256)
    px = np.asarray(Image.open(io.BytesIO(png)))
    alpha = px[:, :, 3]
    assert np.sum(alpha == 0) > 0, "uncovered margin must be transparent"
    assert np.sum(alpha == 255) > 0, "covered area must be opaque"
    # The uncovered margin with the old dst_nodata=None-but-valid==all path rendered as opaque stretched 0 (hard block).


def test_fully_inside_tile_stays_opaque(tmp_path):
    _STATS_CACHE.clear()
    # 200x200 raster covering 116-117 (~111km) — tile 842,389 at z10 is fully inside
    w = h = 200
    data = np.zeros((1, h, w), dtype=np.float32)
    data[0, :, w // 2:] = 1000.0
    data[0, 20, 20] = np.nan
    tr = from_bounds(116.0, 39.0, 117.0, 40.0, w, h)
    path = str(tmp_path / "big.tif")
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32", crs="EPSG:4326", transform=tr) as dst:
        dst.write(data)
    png = render_raster_tile(path, 10, 842, 389, tile_size=256)
    px = np.asarray(Image.open(io.BytesIO(png)))
    assert (px[:, :, 3] > 0).mean() > 0.99, "wholly-inside tile must stay opaque"
