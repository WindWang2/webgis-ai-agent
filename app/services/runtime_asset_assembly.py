"""Runtime fixture asset assembly — MVT tile generation + raster rewrite.

Used by the heavy pytest runner to generate ``tiles/{z}/{x}/{y}.mvt`` from
``points.geojson`` fixtures. Also exposes the pure slippy-map helper
``tiles_for_bbox`` which is unit-tested without a browser.

``encode_tile`` (app.services.mvt) expects raw (uncompressed) MVT bytes;
the validator's static server serves them with
``application/vnd.mapbox-vector-tile`` and no Content-Encoding, so tiles
are written uncompressed.

Live-session raster rewrite (ADR-0011 gap, #696): ``ref:raster/<id>`` cursors
are rewritten to ``__ORIGIN__/raster/<id>.png`` at the compile caller (holds
session_id) so the headless validator's static server can fetch the PNG from
``dist/raster/``. The compiler stays session-agnostic. Copy helper mirrors
the MVT assembly pattern.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


# ── Live-session raster rewrite + asset copy (#696) ──────────────────

_RASTER_REF_PREFIX = "ref:raster/"
_RASTER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _raster_ref_to_origin_url(image_ref: str) -> str | None:
    """Map ``ref:raster/<id>`` → ``__ORIGIN__/raster/<id>.png`` else None."""
    if not isinstance(image_ref, str) or not image_ref.startswith(_RASTER_REF_PREFIX):
        return None
    raster_id = image_ref[len(_RASTER_REF_PREFIX) :]
    if not raster_id or not _RASTER_ID_RE.match(raster_id):
        return None
    return f"__ORIGIN__/raster/{raster_id}.png"


def raster_ids_from_mapspec(mapspec: Dict[str, Any]) -> List[str]:
    """Extract raster ids referenced as ``ref:raster/<id>`` in the MapSpec.

    Handles both ``ref:raster/<id>`` and already-rewritten ``__ORIGIN__/raster/<id>.png``
    forms so the copy helper can be called with either the original or the
    rewritten mapspec.
    """
    ids: List[str] = []
    for src in (mapspec or {}).get("sources", {}).values():
        if not isinstance(src, dict) or src.get("type") != "raster":
            continue
        ref = src.get("imageRef")
        if isinstance(ref, str) and ref.startswith(_RASTER_REF_PREFIX):
            rid = ref[len(_RASTER_REF_PREFIX) :]
            if _RASTER_ID_RE.match(rid) and rid not in ids:
                ids.append(rid)
            continue
        # also recognise already-rewritten __ORIGIN__ form
        if isinstance(ref, str) and "__ORIGIN__/raster/" in ref:
            # extract id between raster/ and .png
            m = re.search(r"__ORIGIN__/raster/([A-Za-z0-9_-]+)\.png", ref)
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
    return ids


def rewrite_mapspec_raster_refs(mapspec: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
    """Return a deep-copied MapSpec with raster ``ref:raster/<id>`` rewritten.

    Rewrites each ``type:"raster"`` source's ``imageRef`` from
    ``ref:raster/<id>`` → ``__ORIGIN__/raster/<id>.png``. Non-raster sources
    and already-rewritten entries are left untouched. Returns
    ``(new_mapspec, rewritten_count)``. The input dict is never mutated.

    Pure function — no IO, suitable for unit testing without a browser.
    """
    if not isinstance(mapspec, dict):
        return mapspec, 0
    sources = mapspec.get("sources")
    if not isinstance(sources, dict):
        return mapspec, 0
    rewritten = 0
    new_sources: Dict[str, Any] = {}
    changed = False
    for sid, src in sources.items():
        if not isinstance(src, dict) or src.get("type") != "raster":
            new_sources[sid] = src
            continue
        ref = src.get("imageRef")
        url = _raster_ref_to_origin_url(ref) if isinstance(ref, str) else None
        if url is not None:
            # copy source dict to avoid mutating input
            new_src = dict(src)
            new_src["imageRef"] = url
            new_sources[sid] = new_src
            rewritten += 1
            changed = True
        else:
            new_sources[sid] = src
    if not changed:
        return mapspec, 0
    new_mapspec = copy.deepcopy(mapspec)
    new_mapspec["sources"] = new_sources
    # Preserve other top-level keys (deepcopy already did).
    return new_mapspec, rewritten


def assemble_raster_assets(session_dir: Path, dist_dir: Path, mapspec: Dict[str, Any] | None = None) -> int:
    """Copy live-session raster PNGs into ``dist/raster/`` for headless fetch.

    ``session_dir`` is ``.webgis-agent/<sid>``; PNGs live at
    ``session_dir/raster/<id>.png``. ``dist_dir`` is the compiler output dir
    (``compiled/``). If ``mapspec`` is given, only raster ids referenced there
    are copied; otherwise all ``*.png`` under ``session_dir/raster/`` are copied.

    Returns number of files copied (0 when no raster dir or no matching PNGs).
    Missing PNGs for referenced ids are silently skipped — the validator will
    surface them as 404/failedRequests, which is the correct signal.
    """
    raster_src_dir = Path(session_dir) / "raster"
    if not raster_src_dir.is_dir():
        return 0
    if mapspec is not None:
        ids = raster_ids_from_mapspec(mapspec)
        # If mapspec has no raster ids, fall through to copy-zero (don't blindly copy all).
        if not ids:
            return 0
        to_copy = []
        for rid in ids:
            src = raster_src_dir / f"{rid}.png"
            if src.is_file():
                to_copy.append((src, rid))
    else:
        to_copy = [(p, p.stem) for p in raster_src_dir.glob("*.png") if p.is_file()]

    if not to_copy:
        return 0
    dest_dir = Path(dist_dir) / "raster"
    dest_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for src, rid in to_copy:
        # Validate id again before filesystem write — defence in depth.
        if not _RASTER_ID_RE.match(rid):
            continue
        dest = dest_dir / f"{rid}.png"
        # Use copy to preserve bytes; atomic replace not needed for test/size.
        shutil.copyfile(src, dest)
        written += 1
    return written


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
