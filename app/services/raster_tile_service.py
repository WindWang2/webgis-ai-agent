"""Windowed Raster Tile Service (Data Plane - COG/XYZ Raster Tile Streaming).

Renders 256x256 Web Mercator (EPSG:3857) PNG tiles from GeoTIFF rasters
using windowed reads (rasterio.windows.from_bounds) and reprojection on the fly.
Only reads the requested spatial window from disk.
"""
import io
import collections
import threading
from typing import Tuple, Dict, Optional
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

# Paths warned about lacking a CRS (bounded by the distinct-file working set;
# not a leak — rasters are a small, short-lived set per process).
# PERF-F6: bounded (was unbounded per-path growth over process lifetime).
_CRS_LESS_WARNED: "set[str]" = set()
_CRS_LESS_WARNED_MAX = 4096


def _channel_valid_mask(arr: np.ndarray, nodata_val) -> np.ndarray:
    """Boolean mask of *renderable* pixels for one band of the reprojected tile.

    A declared nodata sentinel excludes matching pixels; float arrays also
    exclude NaN/Inf (``NaN != NaN`` would otherwise make every pixel "valid"
    and feed NaN into the stretch, GIS-P3-5).

    #596: with NO declared nodata the sentinel is NOT 0. Treating 0 as nodata
    made legitimate zero-valued pixels (0°C, 0m sea level, flat 0° slope,
    NDVI 0 at the water boundary) render transparent and fall out of the
    stretch. So every pixel counts as valid except non-finite floats.
    """
    if nodata_val is None:
        valid = np.ones(arr.shape, dtype=bool)
    else:
        valid = arr != nodata_val
    if np.issubdtype(arr.dtype, np.floating):
        valid = valid & np.isfinite(arr)
    return valid


def _normalize_channel(
    arr: np.ndarray,
    valid_mask: np.ndarray,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> np.ndarray:
    """Normalize numeric array to 0-255 uint8 channel.

    ``vmin``/``vmax`` are the dataset-global stretch bounds (see
    ``_get_band_stats``); every tile of a raster shares them so adjacent
    tiles render with one consistent stretch (no color seams). When omitted
    (fallback), the per-tile min/max are used.
    """
    if not valid_mask.any():
        return np.zeros_like(arr, dtype=np.uint8)
    if vmin is None or vmax is None:
        vmin = float(arr[valid_mask].min())
        vmax = float(arr[valid_mask].max())
    norm = np.zeros_like(arr, dtype=float)
    if vmax > vmin:
        # Compute only over valid pixels: NaN/Inf in invalid positions must
        # not propagate into the cast (callers replace invalid positions with
        # 0 via np.where anyway).
        norm[valid_mask] = np.clip((arr[valid_mask] - vmin) / (vmax - vmin), 0.0, 1.0)
    return (norm * 255).astype(np.uint8)


# ─── per-raster global normalization stats (Issue #410) ─────────────────────
# Per-tile min/max stretching gave adjacent tiles of the same raster
# different stretches -> visible color seams at tile boundaries. Compute the
# dataset-global (vmin, vmax) per band once per raster and reuse it for every
# tile. Bounded LRU-ish dict; double-checked under a lock (worst case two
# threads compute the same raster's stats concurrently, one wins — harmless).
_STATS_CACHE: "Dict[str, Tuple[Tuple[float, float], ...]]" = {}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_MAX_ENTRIES = 512
# Longest-side cap for the decimated stats read: bounds the one-time cost even
# for huge rasters. The approximation is fine — tiles need one CONSISTENT
# stretch, not pixel-exact global extremes.
_STATS_MAX_SIDE = 2048


def _compute_band_stats(src, count: int) -> Tuple[Tuple[float, float], ...]:
    """Dataset-wide (vmin, vmax) per band, skipping nodata/NaN pixels.

    Reads the whole raster decimated to at most ``_STATS_MAX_SIDE`` pixels on
    the longest side (masked=True so nodata never enters the min/max; NaN and
    Inf values are additionally filtered — the NaN-nodata fix from #372 stays
    intact because declared-nodata NaN is masked AND stray NaNs are skipped).
    """
    scale = min(1.0, _STATS_MAX_SIDE / float(max(src.width, src.height)))
    out_shape = (
        count,
        max(1, int(round(src.height * scale))),
        max(1, int(round(src.width * scale))),
    )
    data = src.read(indexes=tuple(range(1, count + 1)), out_shape=out_shape, masked=True)
    arr = np.ma.getdata(data)
    mask = np.ma.getmaskarray(data)
    stats: "list[Tuple[float, float]]" = []
    for b in range(count):
        valid = np.isfinite(arr[b]) & ~mask[b]
        if not valid.any():
            stats.append((0.0, 0.0))
            continue
        stats.append((float(arr[b][valid].min()), float(arr[b][valid].max())))
    return tuple(stats)


def _get_band_stats(raster_path: str, src, count: int) -> Tuple[Tuple[float, float], ...]:
    """Cached per-raster (vmin, vmax) per band (double-checked, lock-protected)."""
    with _STATS_CACHE_LOCK:
        stats = _STATS_CACHE.get(raster_path)
    if stats is not None:
        return stats
    stats = _compute_band_stats(src, count)
    with _STATS_CACHE_LOCK:
        existing = _STATS_CACHE.get(raster_path)
        if existing is not None:
            return existing
        if len(_STATS_CACHE) >= _STATS_MAX_ENTRIES:
            _STATS_CACHE.clear()  # bounded working set; staleness is best-effort
        _STATS_CACHE[raster_path] = stats
    return stats


_RASTER_TILE_CACHE: Dict[Tuple[str, int, int, int, int, str], bytes] = collections.OrderedDict()
_MAX_RASTER_CACHE_ENTRIES = 2048
_RASTER_CACHE_LOCK = threading.Lock()


def _get_cached_tile(key: Tuple[str, int, int, int, int, str]) -> Optional[bytes]:
    with _RASTER_CACHE_LOCK:
        if key in _RASTER_TILE_CACHE:
            _RASTER_TILE_CACHE.move_to_end(key)
            return _RASTER_TILE_CACHE[key]
    return None


def _set_cached_tile(key: Tuple[str, int, int, int, int, str], tile_bytes: bytes) -> None:
    with _RASTER_CACHE_LOCK:
        _RASTER_TILE_CACHE[key] = tile_bytes
        if len(_RASTER_TILE_CACHE) > _MAX_RASTER_CACHE_ENTRIES:
            _RASTER_TILE_CACHE.popitem(last=False)


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

    key = (raster_path, z, x, y, tile_size, cmap_name)
    cached = _get_cached_tile(key)
    if cached is not None:
        return cached

    bounds_3857 = tile_bounds_3857(z, x, y)
    dst_transform = rasterio.transform.from_bounds(*bounds_3857, tile_size, tile_size)

    try:
        with rasterio.open(raster_path) as src:
            # A GeoTIFF does NOT guarantee a CRS (unlike GeoJSON RFC 7946).
            # Previously a missing CRS was silently treated as EPSG:4326, which
            # produced wildly wrong tile windows for projected (UTM/3857)
            # rasters. Treat a CRS-less raster as already in the rendering CRS
            # (EPSG:3857 -> identity transform) and warn loudly so it is not
            # silently misprojected.
            if src.crs is None:
                # Warn at most once per file: a pan/zoom touches many tiles and
                # would otherwise emit thousands of identical warnings.
                if raster_path not in _CRS_LESS_WARNED:
                    if len(_CRS_LESS_WARNED) >= _CRS_LESS_WARNED_MAX:
                        _CRS_LESS_WARNED.clear()  # warn-once is best-effort
                    _CRS_LESS_WARNED.add(raster_path)
                    logger.warning(
                        "raster_tile_service: %s has no CRS tag; assuming source "
                        "is already in EPSG:3857 (no reprojection). Assign a CRS "
                        "for correct tiling.",
                        raster_path,
                    )
                src_crs = "EPSG:3857"
            else:
                src_crs = src.crs
            src_bounds = transform_bounds("EPSG:3857", src_crs, *bounds_3857)

            # Read windowed slice from source
            win_raw = from_bounds(*src_bounds, transform=src.transform)
            win = win_raw.round_offsets().round_shape()
            win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))

            if win.width <= 0 or win.height <= 0:
                res = _transparent_tile_png(tile_size)
                _set_cached_tile(key, res)
                return res

            # True partial: the ideal (floating) window extends beyond the raster
            # bounds, so this tile is only partially filled. Rounding alone does
            # not count (wholly-inside tiles must stay fully opaque).
            is_partial_tile = (
                win_raw.col_off < 0
                or win_raw.row_off < 0
                or win_raw.col_off + win_raw.width > src.width
                or win_raw.row_off + win_raw.height > src.height
            )

            # Perform windowed read from source file (O(win_size) memory).
            # Issue #410: read ONLY the bands we render (max 3) — the previous
            # full-band read decoded every band of a >3-band raster (~2x waste).
            count = min(src.count, 3)
            indexes = tuple(range(1, count + 1))
            # #595: decimate the read to ~the destination resolution. A low-zoom
            # tile's window can cover the WHOLE raster (10000×10000 uint16 × 3
            # bands ≈ 600MB decoded per request) just to reproject it down to
            # 256×256 — GDAL only consults overviews when the buffer is smaller
            # than the window, so a native-resolution read decoded everything.
            # out_shape scales each axis by min(1, tile_size / window) — GDAL
            # then picks the appropriate overview level. High-zoom windows
            # (≤ tile_size px) keep scale 1 → native read, unchanged behavior.
            win_bounds = rasterio.windows.bounds(win, src.transform)
            scale = min(1.0, tile_size / float(win.width), tile_size / float(win.height))
            out_h = max(1, int(round(win.height * scale)))
            out_w = max(1, int(round(win.width * scale)))
            out_shape = (count, out_h, out_w)
            win_data = src.read(indexes=indexes, window=win, out_shape=out_shape)
            # The decimated read covers exactly the window's geographic extent
            # at out_shape resolution — its transform is from_bounds of those
            # bounds, not the native-grid window transform.
            win_transform = rasterio.transform.from_bounds(*win_bounds, out_w, out_h)

            # Dataset-global stretch bounds (per band) so adjacent tiles share
            # one normalization instead of per-tile min/max (seam fix, #410).
            try:
                stats = _get_band_stats(raster_path, src, count)
            except Exception:
                logger.debug(
                    "[raster_tile_service] global stats unavailable for %s, "
                    "falling back to per-tile normalization",
                    raster_path,
                    exc_info=True,
                )
                stats = None

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
                # #596: with no declared nodata, `or 0` conflated "0 is
                # nodata" with "0 is data". dst_nodata=None leaves the
                # destination buffer's zero init for genuinely uncovered
                # cells instead of marking every 0 as fill.
                dst_nodata=src.nodata,
            )

            # Footprint mask: 1 where the source window actually covers the
            # destination pixel, 0 where no source data was resampled to.
            # With src_nodata=None, a legitimate data value of 0 is valid but
            # an UNCOVERED pixel also reads as 0 — they must not be conflated.
            # Only true partial tiles (ideal window beyond raster bounds) need
            # this; wholly-inside tiles keep a full-true mask (no rounding
            # artifacts at the tile edge).
            if is_partial_tile:
                try:
                    win_mask_src = np.ones((win_data.shape[1], win_data.shape[2]), dtype=np.uint8) * 255
                    dst_coverage = np.zeros((tile_size, tile_size), dtype=np.uint8)
                    reproject(
                        source=win_mask_src,
                        destination=dst_coverage,
                        src_transform=win_transform,
                        src_crs=src_crs,
                        dst_transform=dst_transform,
                        dst_crs="EPSG:3857",
                        resampling=Resampling.nearest,
                        src_nodata=0,
                        dst_nodata=0,
                    )
                    coverage_mask = dst_coverage > 0
                except Exception:
                    coverage_mask = np.ones((tile_size, tile_size), dtype=bool)
            else:
                coverage_mask = np.ones((tile_size, tile_size), dtype=bool)

            # Format to RGBA image
            if count >= 3:
                rgb = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
                # #596: no declared nodata → no 0-sentinel; real 0-value
                # pixels (pure black shadows, 0°C, water at 0) must render
                # and take part in the stretch.
                nodata_val = src.nodata  # may be None
                for c in range(3):
                    arr = dst_data[c]
                    valid_mask = _channel_valid_mask(arr, nodata_val) & coverage_mask
                    stretch = stats[c] if stats is not None else ()
                    rgb[:, :, c] = np.where(valid_mask, _normalize_channel(arr, valid_mask, *stretch), 0)

                band_valid = _channel_valid_mask(dst_data, nodata_val).any(axis=0) & coverage_mask
                alpha = np.where(band_valid, 255, 0).astype(np.uint8)
                rgba = np.dstack([rgb, alpha])
                img = Image.fromarray(rgba, "RGBA")
            else:
                arr = dst_data[0]
                # #596: no 0-sentinel when the raster declares no nodata —
                # a true 0-valued flat region must stay visible.
                nodata_val = src.nodata  # may be None
                valid_mask = _channel_valid_mask(arr, nodata_val) & coverage_mask

                if not valid_mask.any():
                    res = _transparent_tile_png(tile_size)
                    _set_cached_tile(key, res)
                    return res

                stretch = stats[0] if stats is not None else ()
                gray = _normalize_channel(arr, valid_mask, *stretch)
                alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
                rgba = np.dstack([gray, gray, gray, alpha])
                img = Image.fromarray(rgba, "RGBA")

            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=1)
            png_bytes = buf.getvalue()

            _set_cached_tile(key, png_bytes)
            return png_bytes

    except Exception as err:
        logger.warning(f"[raster_tile_service] Failed to render tile z={z} x={x} y={y} for {raster_path}: {err}")
        return _transparent_tile_png(tile_size)
