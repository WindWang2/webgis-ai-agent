"""Runtime fixture asset assembly — MVT tiles + raster PNG generation.

Used by the heavy pytest runner to generate ``tiles/{z}/{x}/{y}.mvt`` from
``points.geojson`` and ``raster/<name>.png`` from ``raster.json`` fixtures.
Also exposes the pure slippy-map helper ``tiles_for_bbox`` which is
unit-tested without a browser.

``encode_tile`` (app.services.mvt) expects raw (uncompressed) MVT bytes;
the validator's static server serves them with
``application/vnd.mapbox-vector-tile`` and no Content-Encoding, so tiles
are written uncompressed. ``render_array_to_png`` (app.services.
raster_cartography_converter) renders a 2D numeric array with a named
palette to a colormap-baked PNG (1 px per cell).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Tuple

_MAX_LAT = 85.05112878


def _clamp_lat(lat: float) -> float:
    return max(min(lat, _MAX_LAT), -_MAX_LAT)


def _lon_to_tile_x(lon: float, z: int) -> int:
    n = 1 << z
    x = (lon + 180.0) / 360.0 * n
    # clamp to valid tile range (longitude exactly 180 maps to n, wrap to n-1)
    xi = int(math.floor(x))
    if xi < 0:
        return 0
    if xi >= n:
        return n - 1
    return xi


def _lat_to_tile_y(lat: float, z: int) -> int:
    lat = _clamp_lat(lat)
    n = 1 << z
    lat_rad = math.radians(lat)
    # slippy-map y: (1 - ln(tan+sec)/pi)/2 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    yi = int(math.floor(y))
    if yi < 0:
        return 0
    if yi >= n:
        return n - 1
    return yi


def tiles_for_bbox(
    bbox: Tuple[float, float, float, float],
    zoom: int,
) -> List[Tuple[int, int, int]]:
    """Return all (z,x,y) tiles intersecting the WGS84 bbox at the given zoom.

    bbox is (west, south, east, north) in degrees. The result is inclusive
    on both axes and ordered by (x, y). Handles antimeridian crossing by
    splitting the longitude range into two spans; otherwise the simple
    min/max range is used.
    """
    w, s, e, n = bbox
    # latitude indices: north has smaller y
    y_n = _lat_to_tile_y(n, zoom)
    y_s = _lat_to_tile_y(s, zoom)
    y_min = min(y_n, y_s)
    y_max = max(y_n, y_s)

    # longitude handling
    tiles: List[Tuple[int, int, int]] = []
    n_tiles = 1 << zoom
    if w <= e:
        x_min = _lon_to_tile_x(w, zoom)
        x_max = _lon_to_tile_x(e, zoom)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tiles.append((zoom, x, y))
    else:
        # antimeridian crossing: [w, 180] + [-180, e]
        x_min_a = _lon_to_tile_x(w, zoom)
        x_max_a = n_tiles - 1
        x_min_b = 0
        x_max_b = _lon_to_tile_x(e, zoom)
        for x in list(range(x_min_a, x_max_a + 1)) + list(range(x_min_b, x_max_b + 1)):
            for y in range(y_min, y_max + 1):
                tiles.append((zoom, x, y))
    return tiles


def tiles_covering_bbox(
    bbox: Tuple[float, float, float, float],
    min_zoom: int = 0,
    max_zoom: int = 2,
) -> List[Tuple[int, int, int]]:
    """All tiles covering bbox for zoom levels min_zoom..max_zoom inclusive."""
    out: List[Tuple[int, int, int]] = []
    for z in range(min_zoom, max_zoom + 1):
        out.extend(tiles_for_bbox(bbox, z))
    return out


def bbox_from_geojson(geojson: dict) -> Tuple[float, float, float, float] | None:
    """Compute (w,s,e,n) bbox of Point features in a FeatureCollection.

    Returns None when no valid point is found.
    """
    if not isinstance(geojson, dict):
        return None
    features = geojson.get("features", []) if geojson.get("type") == "FeatureCollection" else []
    w = s = math.inf
    e = n = -math.inf
    found = False
    for f in features:
        try:
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                # also support generic: take first coordinate pair for bbox estimate
                coords = geom.get("coordinates")
                if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                    continue
                lon, lat = float(coords[0]), float(coords[1])
            else:
                lon, lat = float(geom["coordinates"][0]), float(geom["coordinates"][1])
        except Exception:
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        found = True
        if lon < w:
            w = lon
        if lon > e:
            e = lon
        if lat < s:
            s = lat
        if lat > n:
            n = lat
    if not found:
        return None
    # tiny padding so a point exactly on a tile edge is not culled by strict
    # half-open range in encode_points (px < (x+1)*256). 1e-6 deg is negligible.
    w -= 1e-6
    s -= 1e-6
    e += 1e-6
    n += 1e-6
    return (w, s, e, n)


def assemble_raster_assets(fixture_dir: Path, dist_dir: Path) -> int:
    """If fixture_dir contains raster.json, render raster/<name>.png into dist_dir.

    ``raster.json`` is a text declaration with ``array`` (2D numeric list),
    ``palette`` (e.g. ``"Viridis"``) and ``bounds`` ([w,s,e,n]). An optional
    ``name`` field controls the output basename (default ``"raster"``). The
    array is rendered via :func:`app.services.raster_cartography_converter.
    render_array_to_png` to ``dist/raster/<name>.png``.

    Returns the number of PNG files written (0 when no asset source present,
    1 otherwise). Corrupt array/palette/bounds raise a real exception
    (fail-loud) — returning 0 would hide the regression behind a misleading
    image 404 in the headless run.
    """
    raster_path = fixture_dir / "raster.json"
    if not raster_path.is_file():
        return 0
    # Corrupt JSON must raise — returning 0 would silently produce a missing
    # image and fail the scenario with a 404 instead of the real parse error.
    data = json.loads(raster_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"raster.json must be an object, got {type(data).__name__}")
    if "array" not in data or "palette" not in data or "bounds" not in data:
        raise ValueError("raster.json must contain 'array', 'palette' and 'bounds'")
    array_data = data["array"]
    palette = data["palette"]
    bounds = data["bounds"]

    # palette validation — unknown palette must raise, not silently fallback
    if not isinstance(palette, str) or not palette:
        raise ValueError(f"raster palette must be a non-empty string, got {palette!r}")
    from app.lib.cartography.palettes import COLOR_PALETTES

    if palette not in COLOR_PALETTES:
        raise ValueError(f"unknown palette {palette!r}; known: {sorted(COLOR_PALETTES)}")

    # bounds validation — must be 4 numeric values
    if (
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
        or not all(isinstance(v, (int, float)) for v in bounds)
    ):
        raise ValueError(f"raster bounds must be [w,s,e,n] numeric 4-tuple, got {bounds!r}")
    for v in bounds:
        if not math.isfinite(float(v)):
            raise ValueError(f"raster bounds values must be finite, got {bounds!r}")

    # array validation — non-empty 2D rectangular numeric list
    if not isinstance(array_data, list) or len(array_data) == 0:
        raise ValueError("raster array must be a non-empty 2D list")
    row_len: int | None = None
    for r_idx, row in enumerate(array_data):
        if not isinstance(row, list):
            raise ValueError(f"raster array row {r_idx} must be a list")
        if row_len is None:
            row_len = len(row)
            if row_len == 0:
                raise ValueError("raster array rows must be non-empty")
        elif len(row) != row_len:
            raise ValueError("raster array rows must have equal length")
        for c_idx, v in enumerate(row):
            if not isinstance(v, (int, float)):
                raise ValueError(f"raster array[{r_idx}][{c_idx}] must be numeric, got {v!r}")
            if not math.isfinite(float(v)) and not (isinstance(v, float) and math.isnan(v)):
                # NaN is allowed as nodata (renders transparent), but infinities are not
                if math.isinf(float(v)):
                    raise ValueError(f"raster array[{r_idx}][{c_idx}] must be finite or NaN, got {v!r}")

    import numpy as np

    arr = np.array(array_data, dtype=float)
    if arr.ndim != 2 or arr.size == 0:
        raise ValueError("raster array must be a 2D non-empty array")

    # output name — explicit 'name' field or default 'raster'; sanitize
    raw_name = data.get("name") if isinstance(data.get("name"), str) and data.get("name") else "raster"
    safe_name = Path(raw_name).name
    if not safe_name:
        safe_name = "raster"
    if safe_name.lower().endswith(".png"):
        safe_name = safe_name[: -len(".png")]
    png_filename = f"{safe_name}.png"

    from app.services.raster_cartography_converter import render_array_to_png

    png_bytes = render_array_to_png(arr, palette=palette)

    out_path = dist_dir / "raster" / png_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)
    return 1


def assemble_mvt_assets(fixture_dir: Path, dist_dir: Path) -> int:
    """If fixture_dir contains points.geojson, encode z0..z2 MVT tiles into dist_dir/tiles.

    Returns the number of tile files written (0 when no asset source present).
    Uses app.services.mvt.encode_tile with the FeatureCollection directly and
    writes raw (uncompressed) MVT bytes — the validator static server serves
    them with the correct MIME and no Content-Encoding.
    """
    geojson_path = fixture_dir / "points.geojson"
    if not geojson_path.is_file():
        return 0
    # Corrupt GeoJSON must raise here — returning 0 would silently produce
    # missing tiles and fail the scenario with a misleading 404 instead of
    # the real parse error.
    geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
    # normalize to FeatureCollection dict for encode_tile
    features_data = geojson
    bbox = bbox_from_geojson(geojson)
    if bbox is None:
        # fallback: cover the world to keep the scenario from silently empty
        bbox = (-180.0, -_MAX_LAT, 180.0, _MAX_LAT)
    tiles = tiles_covering_bbox(bbox, 0, 2)

    # Import here to avoid circulars at module load time; mvt is pure python.
    from app.services.mvt import encode_tile

    written = 0
    for z, x, y in tiles:
        raw = encode_tile(features_data, z, x, y)
        # encode_tile returns b"" for tiles no feature falls into. Write those
        # too — an empty tile is a valid response, whereas a 404 would surface
        # as a failedRequests entry and fail the scenario for the wrong reason.
        tile_path = dist_dir / "tiles" / str(z) / str(x) / f"{y}.mvt"
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        # Write raw MVT bytes — no gzip, no header. The static server MIME map
        # serves .mvt as application/vnd.mapbox-vector-tile.
        tile_path.write_bytes(raw)
        written += 1
    return written
