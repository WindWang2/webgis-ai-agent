"""Unit tests for raster tile streaming service (app/services/raster_tile_service.py)."""
import os
import tempfile
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image

from app.services.raster_tile_service import (
    tile_bounds_3857,
    _transparent_tile_png,
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
