"""Runtime fixture asset assembly — MVT tile generation.

Used by the heavy pytest runner to generate ``tiles/{z}/{x}/{y}.mvt`` from
``points.geojson`` fixtures. Also exposes the pure slippy-map helper
``tiles_for_bbox`` which is unit-tested without a browser.

``encode_tile`` (app.services.mvt) expects raw (uncompressed) MVT bytes;
the validator's static server serves them with
``application/vnd.mapbox-vector-tile`` and no Content-Encoding, so tiles
are written uncompressed.
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
