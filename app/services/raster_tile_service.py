"""Windowed Raster Tile Service (Data Plane - COG/XYZ Raster Tile Streaming).

Renders 256x256 Web Mercator (EPSG:3857) PNG tiles from GeoTIFF rasters
using windowed reads (rasterio.windows.from_bounds) and reprojection on the fly.
Only reads the requested spatial window from disk.
"""
import io
from typing import Tuple
import numpy as np
from PIL import Image
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.windows import from_bounds

_MERCATOR_BOUND = 20037508.342789244
_INITIAL_RESOLUTION = 2 * _MERCATOR_BOUND / 256.0


def tile_bounds_3857(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Calculate EPSG:3857 bounding box [minx, miny, maxx, maxy] for XYZ tile."""
    num_tiles = 1 << z
    tile_size = 2 * _MERCATOR_BOUND / num_tiles
    minx = -_MERCATOR_BOUND + x * tile_size
    maxx = -_MERCATOR_BOUND + (x + 1) * tile_size
    maxy = _MERCATOR_BOUND - y * tile_size
    miny = _MERCATOR_BOUND - (y + 1) * tile_size
    return (minx, miny, maxx, maxy)


def _transparent_tile_png(tile_size: int = 256) -> bytes:
    img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


import logging

logger = logging.getLogger(__name__)


def _normalize_channel(arr: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """Normalize numeric array to 0-255 uint8 channel."""
    if not valid_mask.any():
        return np.zeros_like(arr, dtype=np.uint8)
    vmin, vmax = float(arr[valid_mask].min()), float(arr[valid_mask].max())
    if vmax > vmin:
        norm = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
    else:
        norm = np.zeros_like(arr, dtype=float)
    return (norm * 255).astype(np.uint8)


def render_raster_tile(
    raster_path: str,
    z: int,
    x: int,
    y: int,
    tile_size: int = 256,
    cmap_name: str = "viridis",
) -> bytes:
    """Render a 256x256 PNG tile for the given XYZ coordinates from a GeoTIFF."""
    if z < 0 or z > 22 or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        return _transparent_tile_png(tile_size)

    bounds_3857 = tile_bounds_3857(z, x, y)
    dst_transform = rasterio.transform.from_bounds(*bounds_3857, tile_size, tile_size)

    try:
        with rasterio.open(raster_path) as src:
            src_crs = src.crs or "EPSG:4326"
            src_bounds = transform_bounds("EPSG:3857", src_crs, *bounds_3857)

            # Read windowed slice from source
            win = from_bounds(*src_bounds, transform=src.transform)
            win = win.round_offsets().round_shape()
            win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))

            if win.width <= 0 or win.height <= 0:
                return _transparent_tile_png(tile_size)

            # Perform windowed read from source file (O(win_size) memory)
            count = min(src.count, 3)
            win_transform = rasterio.windows.transform(win, src.transform)
            win_data = src.read(window=win)

            # Destination array for reproject
            dst_data = np.zeros((count, tile_size, tile_size), dtype=src.dtypes[0])

            reproject(
                source=win_data[:count],
                destination=dst_data,
                src_transform=win_transform,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs="EPSG:3857",
                resampling=Resampling.bilinear,
                src_nodata=src.nodata,
                dst_nodata=src.nodata or 0,
            )

            # Format to RGBA image
            if count >= 3:
                rgb = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
                for c in range(3):
                    arr = dst_data[c]
                    valid_mask = arr != (src.nodata or 0)
                    rgb[:, :, c] = np.where(valid_mask, _normalize_channel(arr, valid_mask), 0)

                alpha = np.where((dst_data != (src.nodata or 0)).any(axis=0), 255, 0).astype(np.uint8)
                rgba = np.dstack([rgb, alpha])
                img = Image.fromarray(rgba, "RGBA")
            else:
                arr = dst_data[0]
                nodata_val = src.nodata if src.nodata is not None else 0
                valid_mask = np.isfinite(arr) & (arr != nodata_val)

                if not valid_mask.any():
                    return _transparent_tile_png(tile_size)

                gray = _normalize_channel(arr, valid_mask)
                alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
                rgba = np.dstack([gray, gray, gray, alpha])
                img = Image.fromarray(rgba, "RGBA")

            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=1)
            return buf.getvalue()

    except Exception as err:
        logger.warning(f"[raster_tile_service] Failed to render tile z={z} x={x} y={y} for {raster_path}: {err}")
        return _transparent_tile_png(tile_size)
