"""Mapbox Vector Tile (MVT) encoder — V2: all geometry types + performance layer.

V1 was a stdlib-only tracer bullet for Point features. V2 keeps that encode
core and adds:

* Geometry support: Point / MultiPoint / LineString / MultiLineString /
  Polygon / MultiPolygon (correct winding, holes, ClosePath). GeometryCollection
  is skipped with a warning (never encoded).
* Spatial index: one STRtree per (session_id, ref_id), bounded LRU (256 refs),
  thread-safe. Tile queries use index.query(tile_bbox) instead of scanning all
  features.
* Tile LRU cache: per-(session_id, ref_id, z, x, y) cache of the final gzip
  bytes, bounded by entry count (4096) and total bytes (256 MB), thread-safe,
  session-isolated via the key.
* Single-flight dedup: concurrent same-tile requests share one computation
  (asyncio.Future), bounded to 512 in-flight keys.
* Zoom-aware simplification: Line/Polygon are simplified at z < 14 with
  shapely.simplify; the tolerance is half a pixel (in degree terms
  360 / (256 * 2^z) / 2). Degenerate results are dropped; self-intersecting
  polygons are never emitted (shapely.is_valid gate).
* Web-Mercator geometry encoding per the MVT 2.1 spec: layer "data",
  extent 4096, zigzag/varint delta commands.

Winding convention: exterior rings are emitted counter-clockwise and holes
clockwise in geographic (lon/lat) terms — i.e. RFC 7946 GeoJSON winding. In
the tile's y-down coordinate space that is exactly the orientation the MVT 2.1
spec requires (4.3.4.4) and what map renderers expect.

shapely (>= 2.1, a hard dependency of the project) is imported behind a guard:
without it the encoder falls back to a pure-python clipper and the spatial
index degrades to a full bbox scan.
"""
import asyncio
import logging
import math
import threading
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LAYER_NAME = "data"
_EXTENT = 4096

# wire types
_VARINT = 0
_BYTES = 2

# value field tags (MVT Value message: only the types we actually emit)
_VAL_STRING = 1
_VAL_DOUBLE = 3
_VAL_INT = 4
_VAL_BOOL = 7

# geometry types (MVT)
_GT_POINT = 1
_GT_LINESTRING = 2
_GT_POLYGON = 3

# command ids
_MOVETO = 1
_LINETO = 2
_CLOSEPATH = 7

# zoom threshold: simplification is skipped at z >= this (preserve detail)
_SIMPLIFY_MAX_ZOOM = 14
# clip buffer beyond the tile extent, in extent units (4/16 = 0.25 px)
_BUFFER_UNITS = 4.0
_BUFFER_PX = _BUFFER_UNITS / (_EXTENT / 256.0)
# minimum ring area (extent units^2) after quantization; smaller is invisible
_AREA_EPS = 1.0

# Web-Mercator is only defined for |lat| <= ~85.051129°. A vertex at the poles
# (lat = ±90) makes tan(±π/2)/1/cos(±π/2) blow up: the pure path raises
# ValueError (math domain), the shapely path emits +inf/NaN. Clamp latitude
# before projecting so pole-spanning geometries (e.g. Antarctica) render at the
# world edge instead of crashing or distorting.
_MAX_WEBMERCATOR_LAT = 85.05112878


def _finite_pair(lon, lat) -> bool:
    """True iff both coordinates are present and finite (drops NaN/Inf inputs)."""
    try:
        return math.isfinite(float(lon)) and math.isfinite(float(lat))
    except (TypeError, ValueError, OverflowError):
        # OverflowError: float(huge_int) overflows (e.g. a pathological coord).
        return False

_SUPPORTED_TYPES = frozenset(
    {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
)
_IS_POINT = frozenset({"Point", "MultiPoint"})
_IS_LINE = frozenset({"LineString", "MultiLineString"})
_IS_POLY = frozenset({"Polygon", "MultiPolygon"})

# ─── optional shapely / numpy ───────────────────────────────────────────────
try:  # pragma: no cover - exercised only in environments without shapely
    import numpy as np
    import shapely
    import shapely.geometry

    _SHAPELY = True
except ImportError:  # pragma: no cover
    np = None
    shapely = None
    _SHAPELY = False


class RefDataUnavailableError(RuntimeError):
    """Raised when a spatial-index build needs ref data that was not supplied."""


# ─── low-level protobuf helpers (unchanged from V1) ─────────────────────────


def _field(num: int, wire: int) -> bytes:
    return _varint((num << 3) | wire)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _zigzag32(n: int) -> int:
    return ((n << 1) ^ (n >> 31)) & 0xFFFFFFFF


def _string_field(num: int, s: str) -> bytes:
    raw = s.encode("utf-8")
    return _field(num, _BYTES) + _varint(len(raw)) + raw


def _project(lon: float, lat: float, z: int) -> Tuple[float, float]:
    """WGS84 → Web-Mercator pixels at zoom z (world = 256 * 2^z).

    Projection is homogeneous in z: _project(lon, lat, z) ==
    _project(lon, lat, 0) * 2^z, so geometries indexed in z0 world pixels
    (world = 256) can be re-used at any zoom by a simple scale.

    Latitude is clamped to the Web-Mercator valid range (|lat| <= 85.0511°);
    without this, a pole vertex (lat = ±90) makes the projection diverge.
    """
    world = 256.0 * (1 << z)
    if lat > _MAX_WEBMERCATOR_LAT:
        lat = _MAX_WEBMERCATOR_LAT
    elif lat < -_MAX_WEBMERCATOR_LAT:
        lat = -_MAX_WEBMERCATOR_LAT
    x = (lon + 180.0) / 360.0 * world
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world
    return x, y


def _transform_z0(coords):
    """Vectorized _project(..., z=0) for shapely.transform (y-down world px).

    Latitude is clamped to the Web-Mercator valid range (see ``_project``); a
    pole vertex otherwise produces inf/NaN and corrupts the indexed geometry.
    """
    x = coords[:, 0]
    y = np.clip(coords[:, 1], -_MAX_WEBMERCATOR_LAT, _MAX_WEBMERCATOR_LAT)
    xs = (x + 180.0) / 360.0 * 256.0
    lat_rad = np.radians(y)
    ys = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / np.pi) / 2.0 * 256.0
    return np.column_stack([xs, ys])


def _encode_value(v: Any) -> Optional[bytes]:
    """Proto Value message for a property value; None for unsupported types."""
    if isinstance(v, bool):
        return _field(_VAL_BOOL, _VARINT) + _varint(1 if v else 0)
    if isinstance(v, int) and not isinstance(v, bool):
        n = v & 0xFFFFFFFF
        if n > 0x7FFFFFFF:
            n -= 0x100000000
        return _field(_VAL_INT, _VARINT) + _varint(n & 0xFFFFFFFF)
    if isinstance(v, float):
        return _field(_VAL_DOUBLE, 1) + _double_bytes(v)
    if isinstance(v, str):
        raw = v.encode("utf-8")
        return _field(_VAL_STRING, _BYTES) + _varint(len(raw)) + raw
    return None


def _double_bytes(v: float) -> bytes:
    import struct
    return struct.pack("<d", v)


def _encode_feature(geometry: bytes, tags: List[int], type_: int = _GT_POINT) -> bytes:
    out = bytearray()
    out += _field(3, _VARINT) + _varint(type_)
    if tags:
        tag_bytes = bytearray()
        for t in tags:
            tag_bytes += _varint(t)
        out += _field(2, _BYTES) + _varint(len(tag_bytes)) + bytes(tag_bytes)
    out += _field(4, _BYTES) + _varint(len(geometry)) + geometry
    return bytes(out)


# ─── geometry helpers ───────────────────────────────────────────────────────


def _geometry_parts(gtype: str, coords) -> List[Tuple[str, Optional[str], List[Tuple[float, float]]]]:
    """Split GeoJSON coordinates into (kind, role, lonlat_points) parts.

    kind: "point" | "line" | "ring"; role: "exterior"/"hole" for rings, else None.
    """
    if gtype == "Point":
        return [("point", None, [tuple(coords)])]
    if gtype == "MultiPoint":
        return [("point", None, [tuple(c) for c in coords])]
    if gtype == "LineString":
        return [("line", None, [tuple(c) for c in coords])]
    if gtype == "MultiLineString":
        return [("line", None, [tuple(c) for c in line]) for line in coords]
    if gtype == "Polygon":
        return [
            ("ring", "exterior" if i == 0 else "hole", [tuple(c) for c in ring])
            for i, ring in enumerate(coords)
        ]
    if gtype == "MultiPolygon":
        parts = []
        for poly in coords:
            for i, ring in enumerate(poly):
                parts.append(("ring", "exterior" if i == 0 else "hole", [tuple(c) for c in ring]))
        return parts
    return []


def _shapely_geom_from_coords(gtype: str, coords) -> Optional[Any]:
    """Build a shapely geometry (lon/lat space) from GeoJSON coordinates."""
    try:
        if gtype == "Point":
            return shapely.geometry.Point(coords)
        if gtype == "MultiPoint":
            return shapely.geometry.MultiPoint(coords)
        if gtype == "LineString":
            return shapely.geometry.LineString(coords)
        if gtype == "MultiLineString":
            return shapely.geometry.MultiLineString(coords)
        if gtype == "Polygon":
            holes = coords[1:] if len(coords) > 1 else None
            return shapely.geometry.Polygon(coords[0], holes)
        if gtype == "MultiPolygon":
            polys = []
            for poly in coords:
                holes = poly[1:] if len(poly) > 1 else None
                polys.append(shapely.geometry.Polygon(poly[0], holes))
            return shapely.geometry.MultiPolygon(polys)
    except Exception:
        return None
    return None


def _tile_rect_z0(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Tile rect in z0 world pixels (world = 256), expanded by the clip buffer.

    buffer = 4 extent units = 0.25 px at zoom z = 0.25 / 2^z px at z0.
    """
    scale = 256.0 / (1 << z)
    buf = _BUFFER_PX / (1 << z)
    return (
        x * scale - buf,
        y * scale - buf,
        (x + 1) * scale + buf,
        (y + 1) * scale + buf,
    )


def _to_extent(z0x: float, z0y: float, factor: int, tile_x: int, tile_y: int) -> Tuple[int, int]:
    """z0 world px → quantized extent units, clamped to [0, _EXTENT]."""
    px = z0x * factor
    py = z0y * factor
    scale = _EXTENT / 256.0
    ex = int(round((px - tile_x * 256.0) * scale))
    ey = int(round((py - tile_y * 256.0) * scale))
    return min(max(ex, 0), _EXTENT), min(max(ey, 0), _EXTENT)


def _shoelace(pts: List[Tuple[float, float]]) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _normalize_ring(coords) -> Optional[List[Tuple[float, float]]]:
    """Ensure the ring is closed and consecutive duplicates are dropped."""
    if len(coords) < 3:
        return None
    ring = [tuple(c) for c in coords]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    deduped = []
    for p in ring:
        if deduped and deduped[-1] == p:
            continue
        deduped.append(p)
    if len(deduped) < 4:  # fewer than 3 distinct points + closing
        return None
    return deduped


def _orient_ring(ring: List[Tuple[float, float]], exterior: bool) -> List[Tuple[float, float]]:
    """Force ring winding: exterior CCW / holes CW in geographic (lon/lat) terms.

    World/tile y points down, so geographic CCW ⇔ shoelace < 0 in y-down
    coordinates. This matches RFC 7946 GeoJSON winding and, after the y flip,
    the MVT 2.1 spec orientation.
    """
    s = _shoelace(ring)
    if exterior and s > 0:
        return list(reversed(ring))
    if not exterior and s < 0:
        return list(reversed(ring))
    return ring


def _quantize_ring(ring, factor: int, tile_x: int, tile_y: int) -> Optional[List[Tuple[int, int]]]:
    """Quantize a closed z0-px ring to extent coords; None if degenerate."""
    pts = []
    for zx, zy in ring[:-1]:  # drop the closing duplicate
        ex, ey = _to_extent(zx, zy, factor, tile_x, tile_y)
        if pts and pts[-1] == (ex, ey):
            continue
        pts.append((ex, ey))
    if len(pts) < 3:
        return None
    if abs(_shoelace(pts)) < _AREA_EPS:
        return None
    return pts


def _quantize_line(coords, factor: int, tile_x: int, tile_y: int) -> List[Tuple[int, int]]:
    pts = []
    for zx, zy in coords:
        ex, ey = _to_extent(zx, zy, factor, tile_x, tile_y)
        if pts and pts[-1] == (ex, ey):
            continue
        pts.append((ex, ey))
    return pts


def _emit_line_commands(out: bytearray, q, cursor_x: int, cursor_y: int) -> Tuple[int, int]:
    """MoveTo(1) + LineTo(n-1) with cursor delta encoding.

    MVT command integer = (count << 3) | command_id.
    """
    out += _varint((1 << 3) | _MOVETO)
    out += _varint(_zigzag32(q[0][0] - cursor_x))
    out += _varint(_zigzag32(q[0][1] - cursor_y))
    cursor_x, cursor_y = q[0]
    out += _varint(((len(q) - 1) << 3) | _LINETO)
    for ex, ey in q[1:]:
        out += _varint(_zigzag32(ex - cursor_x))
        out += _varint(_zigzag32(ey - cursor_y))
        cursor_x, cursor_y = ex, ey
    return cursor_x, cursor_y


def _emit_ring_commands(out: bytearray, q, cursor_x: int, cursor_y: int) -> Tuple[int, int]:
    """MoveTo(1) + LineTo(n-1) + ClosePath; cursor returns to the ring start."""
    cursor_x, cursor_y = _emit_line_commands(out, q, cursor_x, cursor_y)
    out += _varint((1 << 3) | _CLOSEPATH)
    return q[0][0], q[0][1]


# ─── clipping (pure-python fallback when shapely is unavailable) ────────────


def _edge_intersect(a, b, v: float, axis: int):
    da, db = a[axis], b[axis]
    if da == db:
        return b
    t = (v - da) / (db - da)
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _clip_polygon_rect(pts, x0: float, y0: float, x1: float, y1: float):
    """Sutherland–Hodgman clip of a ring against the (convex) rect."""
    if len(pts) < 3:
        return []
    cur = list(pts)
    for v, keep_ge, axis in (
        (x0, True, 0),
        (x1, False, 0),
        (y0, True, 1),
        (y1, False, 1),
    ):
        if len(cur) < 3:
            return []
        nxt = []
        prev = cur[-1]
        prev_in = (prev[axis] >= v) if keep_ge else (prev[axis] <= v)
        for p in cur:
            pin = (p[axis] >= v) if keep_ge else (p[axis] <= v)
            if pin:
                if not prev_in:
                    nxt.append(_edge_intersect(prev, p, v, axis))
                nxt.append(p)
            elif prev_in:
                nxt.append(_edge_intersect(prev, p, v, axis))
            prev, prev_in = p, pin
        cur = nxt
    return cur


def _clip_segment_lb(a, b, x0: float, y0: float, x1: float, y1: float):
    """Liang–Barsky: clip segment a→b to the rect; None if fully outside."""
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return None
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return None
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return None
                if r < t1:
                    t1 = r
    if t1 < t0:
        return None
    return (ax + t0 * dx, ay + t0 * dy), (ax + t1 * dx, ay + t1 * dy)


def _same_point(a, b, eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _clip_polyline_lb(pts, x0: float, y0: float, x1: float, y1: float):
    """Clip a polyline to the rect; returns a list of clipped polylines."""
    if len(pts) < 2:
        return []
    runs = []
    cur = []
    for i in range(len(pts) - 1):
        seg = _clip_segment_lb(pts[i], pts[i + 1], x0, y0, x1, y1)
        if seg is None:
            if cur:
                runs.append(cur)
                cur = []
            continue
        ca, cb = seg
        if not cur:
            cur = [ca, cb]
        elif _same_point(cur[-1], ca):
            cur.append(cb)
        else:
            runs.append(cur)
            cur = [ca, cb]
    if cur:
        runs.append(cur)
    return runs


# ─── geometry encoding ──────────────────────────────────────────────────────


def _encode_points(points: Iterable[Tuple[float, float]], z: int, x: int, y: int) -> Optional[bytes]:
    """Encode Point/MultiPoint coordinates; None if nothing falls inside the tile.

    Behavior matches V1: points are clipped to [tile_x*256, (tile_x+1)*256) ×
    [tile_y*256, (tile_y+1)*256) in zoom-z pixels and clamped to [0, extent-1].
    """
    scale = _EXTENT / 256.0
    out = bytearray()
    cursor_x = 0
    cursor_y = 0
    wrote = False
    for pt in points:
        try:
            lon, lat = pt[0], pt[1]
        except (TypeError, IndexError, ValueError):
            continue
        if lon is None or lat is None:
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        try:
            px_x, px_y = _project(lon, lat, z)
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
        if not (x * 256 <= px_x < (x + 1) * 256 and y * 256 <= px_y < (y + 1) * 256):
            continue
        ex = min(int(round((px_x - x * 256) * scale)), _EXTENT - 1)
        ey = min(int(round((px_y - y * 256) * scale)), _EXTENT - 1)
        out += _varint((1 << 3) | _MOVETO)  # MoveTo, count=1
        out += _varint(_zigzag32(ex - cursor_x))
        out += _varint(_zigzag32(ey - cursor_y))
        cursor_x, cursor_y = ex, ey
        wrote = True
    return bytes(out) if wrote else None


def _encode_parts(parts, is_poly: bool, z: int, x: int, y: int) -> Optional[bytes]:
    """Encode line/ring parts given in z0 world pixels into MVT commands."""
    out = bytearray()
    cursor_x = 0
    cursor_y = 0
    wrote = False
    factor = 1 << z
    for kind, role, coords in parts:
        if kind == "ring":
            if not is_poly:
                continue
            ring = _normalize_ring(coords)
            if ring is None:
                continue
            ring = _orient_ring(ring, exterior=(role == "exterior"))
            q = _quantize_ring(ring, factor, x, y)
            if q is None:
                continue
            cursor_x, cursor_y = _emit_ring_commands(out, q, cursor_x, cursor_y)
            wrote = True
        elif kind == "line":
            if is_poly:
                continue
            q = _quantize_line(coords, factor, x, y)
            if len(q) < 2:
                continue
            cursor_x, cursor_y = _emit_line_commands(out, q, cursor_x, cursor_y)
            wrote = True
    return bytes(out) if wrote else None


def _shapely_parts(geom):
    """Yield (kind, role, coords) from a clipped shapely geometry (z0 px)."""
    gt = geom.geom_type
    if gt == "LineString":
        yield ("line", None, list(geom.coords))
    elif gt == "MultiLineString":
        for line in geom.geoms:
            yield ("line", None, list(line.coords))
    elif gt == "Polygon":
        yield ("ring", "exterior", list(geom.exterior.coords))
        for hole in geom.interiors:
            yield ("ring", "hole", list(hole.coords))
    elif gt == "MultiPolygon":
        for poly in geom.geoms:
            yield ("ring", "exterior", list(poly.exterior.coords))
            for hole in poly.interiors:
                yield ("ring", "hole", list(hole.coords))
    elif gt == "GeometryCollection":
        for g in geom.geoms:
            yield from _shapely_parts(g)


def _simplify_and_project(gtype: str, geom_lonlat, geom_z0, z: int):
    """Return the z0 world-px geometry to clip, reusing cached projections.

    The geometry construction and (for high zoom) the z0 projection are the
    expensive parts of the encode hot path; this function lets an index that
    already built both reuse them instead of redoing the work per tile.

    ``geom_lonlat`` is the lon/lat shapely geometry (always required: the
    simplify tolerance is zoom-dependent and applied in lon/lat space, and the
    is_valid gate runs against it). ``geom_z0`` is the cached z0 projection of
    the *unsimplified* lon/lat geometry, or None when the caller has no index
    (then it is rebuilt on demand at z >= _SIMPLIFY_MAX_ZOOM).

    Output is identical regardless of whether geom_z0 is cached or rebuilt:
    projection is homogeneous in z, so transform(geom_lonlat) is exactly what
    the z >= threshold path emits. Returns None when the feature must be dropped
    (empty / invalid polygon / degenerate simplification).
    """
    is_poly = gtype in _IS_POLY
    if geom_lonlat is None or geom_lonlat.is_empty:
        return None
    if is_poly and not geom_lonlat.is_valid:
        # never emit self-intersecting polygons
        logger.debug("mvt: skipping invalid %s feature", gtype)
        return None
    if z >= _SIMPLIFY_MAX_ZOOM:
        # no simplification: reuse the cached z0 geometry (or rebuild it once).
        # An exception here propagates to ``_encode_geometry``'s guard, which
        # falls back to the pure-python encoder — matching the pre-refactor
        # behavior exactly. (Index callers never reach this line: they pass a
        # cached geom_z0 and return above.)
        if geom_z0 is not None:
            return geom_z0
        return shapely.transform(geom_lonlat, _transform_z0)
    # z < threshold: simplify in lon/lat space, then project the result.
    # half a pixel at zoom z; in degree terms: 360 / (256 * 2^z) / 2,
    # converted to z0 world px (world = 256): 2 ** -(z + 1)
    simplified = geom_lonlat.simplify(2.0 ** -(z + 1), preserve_topology=True)
    if simplified.is_empty:
        return None
    if not is_poly or simplified.is_valid:
        geom_lonlat = simplified
    # else: simplification produced self-intersection → keep the original
    # (which passed the is_valid gate above)
    return shapely.transform(geom_lonlat, _transform_z0)


def _clip_and_encode(geom, is_poly: bool, z: int, x: int, y: int) -> Optional[bytes]:
    """Clip a z0 geometry to the tile rect and encode it; None if nothing inside."""
    if geom is None:
        return None
    clipped = geom.intersection(shapely.geometry.box(*_tile_rect_z0(z, x, y)))
    if clipped.is_empty:
        return None
    return _encode_parts(_shapely_parts(clipped), is_poly, z, x, y)


def _encode_line_polygon_shapely(gtype: str, coords, z: int, x: int, y: int) -> Optional[bytes]:
    """Line/Polygon encode from raw GeoJSON coords (no index to reuse from).

    Constructs the lon/lat shapely geometry and lets ``_simplify_and_project``
    rebuild the z0 projection on demand. This is the path used by the public
    ``encode_tile``; the index-aware path uses ``_encode_line_polygon_from_geoms``.
    """
    geom_lonlat = _shapely_geom_from_coords(gtype, coords)
    geom = _simplify_and_project(gtype, geom_lonlat, None, z)
    return _clip_and_encode(geom, gtype in _IS_POLY, z, x, y)


def _encode_line_polygon_from_geoms(
    gtype: str, geom_lonlat, geom_z0, z: int, x: int, y: int
) -> Optional[bytes]:
    """Line/Polygon encode reusing a feature's pre-built shapely geometries.

    ``geom_lonlat`` / ``geom_z0`` come from the spatial index (built once per
    ref), so no GeoJSON→Shapely construction happens per tile and the z0
    projection is reused at z >= _SIMPLIFY_MAX_ZOOM. Output is byte-identical to
    ``_encode_line_polygon_shapely`` for the same input coordinates.
    """
    geom = _simplify_and_project(gtype, geom_lonlat, geom_z0, z)
    return _clip_and_encode(geom, gtype in _IS_POLY, z, x, y)


def _encode_line_polygon_pure(gtype: str, coords, z: int, x: int, y: int) -> Optional[bytes]:
    """Line/Polygon encode without shapely: pure-python clipping, no simplify."""
    parts = _geometry_parts(gtype, coords)
    rect = _tile_rect_z0(z, x, y)
    out = bytearray()
    cursor_x = 0
    cursor_y = 0
    wrote = False
    factor = 1 << z
    is_poly = gtype in _IS_POLY
    for kind, role, part in parts:
        if kind == "ring":
            if not is_poly:
                continue
            projected = [_project(lon, lat, 0) for lon, lat in part if _finite_pair(lon, lat)]
            clipped = _clip_polygon_rect(projected, *rect)
            ring = _normalize_ring(clipped)
            if ring is None:
                continue
            ring = _orient_ring(ring, exterior=(role == "exterior"))
            q = _quantize_ring(ring, factor, x, y)
            if q is None:
                continue
            cursor_x, cursor_y = _emit_ring_commands(out, q, cursor_x, cursor_y)
            wrote = True
        elif kind == "line":
            if is_poly:
                continue
            projected = [_project(lon, lat, 0) for lon, lat in part if _finite_pair(lon, lat)]
            for run in _clip_polyline_lb(projected, *rect):
                q = _quantize_line(run, factor, x, y)
                if len(q) < 2:
                    continue
                cursor_x, cursor_y = _emit_line_commands(out, q, cursor_x, cursor_y)
                wrote = True
    return bytes(out) if wrote else None


def _encode_geometry(gtype: str, coords, z: int, x: int, y: int) -> Optional[bytes]:
    """Encode one feature geometry; None if nothing lies inside the tile."""
    if gtype in _IS_POINT:
        pts = coords if gtype == "MultiPoint" else [coords]
        return _encode_points(pts, z, x, y)
    if gtype in _IS_LINE or gtype in _IS_POLY:
        if _SHAPELY:
            try:
                return _encode_line_polygon_shapely(gtype, coords, z, x, y)
            except Exception:
                logger.warning("mvt: shapely encode failed, using pure-python fallback", exc_info=True)
        return _encode_line_polygon_pure(gtype, coords, z, x, y)
    return None


# ─── public encode API ──────────────────────────────────────────────────────


def extract_features(data) -> List[dict]:
    """Extract GeoJSON feature dicts from ref-data shapes.

    Accepts a bare FeatureCollection, {"geojson": FC} or
    {"type": "poi_query", "geojson": FC}. Geometry-less entries are dropped.
    """
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return []
    return [
        f
        for f in fc.get("features", [])
        if isinstance(f, dict) and isinstance(f.get("geometry"), dict)
    ]


def _to_feature_dicts(features_data) -> List[dict]:
    """Normalize encode_tile input into a list of GeoJSON feature dicts."""
    if isinstance(features_data, dict):
        return extract_features(features_data)
    if isinstance(features_data, (list, tuple)):
        out = []
        for item in features_data:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                # legacy point format: ((lon, lat), properties)
                coord, props = item
                if (
                    isinstance(coord, (list, tuple))
                    and len(coord) >= 2
                    and coord[0] is not None
                    and coord[1] is not None
                ):
                    out.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [coord[0], coord[1]]},
                            "properties": props or {},
                        }
                    )
        return out
    return []


def _type_id_for(gtype: str) -> Optional[int]:
    if gtype in _IS_POINT:
        return _GT_POINT
    if gtype in _IS_LINE:
        return _GT_LINESTRING
    if gtype in _IS_POLY:
        return _GT_POLYGON
    return None


def _assemble_mvt(records: List[Tuple[int, bytes, Dict[str, Any]]]) -> bytes:
    """Assemble the final MVT tile bytes from encoded feature records.

    Each record is ``(type_id, geometry_bytes, properties)``. Property keys and
    values are deduplicated in first-seen order, exactly as the wire format
    requires. Returns b"" when no feature produced geometry (a valid empty tile
    message with no layer).
    """
    keys: List[str] = []
    key_idx: Dict[str, int] = {}
    values: List[bytes] = []
    value_idx: Dict[bytes, int] = {}

    def _k(k: str) -> int:
        if k not in key_idx:
            key_idx[k] = len(keys)
            keys.append(k)
        return key_idx[k]

    def _v(encoded: bytes) -> int:
        if encoded not in value_idx:
            value_idx[encoded] = len(values)
            values.append(encoded)
        return value_idx[encoded]

    feature_msgs: List[bytes] = []
    for type_id, geom_bytes, props in records:
        tags: List[int] = []
        for k, v in (props or {}).items():
            encoded = _encode_value(v)
            if encoded is None:
                continue
            tags.append(_k(k))
            tags.append(_v(encoded))
        feature_msgs.append(_encode_feature(geom_bytes, tags, type_id))

    if not feature_msgs:
        return b""  # empty tile: valid empty message (no layer)

    layer = bytearray()
    layer += _field(15, _VARINT) + _varint(2)          # version=2
    layer += _string_field(1, _LAYER_NAME)             # name="data"
    for fm in feature_msgs:
        layer += _field(2, _BYTES) + _varint(len(fm)) + fm
    for k in keys:
        layer += _string_field(3, k)
    for v in values:
        layer += _field(4, _BYTES) + _varint(len(v)) + v
    layer += _field(5, _VARINT) + _varint(_EXTENT)     # extent=4096

    tile = _field(3, _BYTES) + _varint(len(layer)) + bytes(layer)
    return bytes(tile)


def encode_tile(features_data, z: int, x: int, y: int) -> bytes:
    """Encode features into a raw (uncompressed) MVT tile for (z, x, y).

    ``features_data`` may be a list of GeoJSON feature dicts, a bare
    FeatureCollection, a wrapped ref-data shape ({"geojson": ...}) or the
    legacy point list [((lon, lat), props), ...]. Returns a valid (possibly
    empty) tile: b"" when nothing falls inside the tile.

    This is the coordinate-based path: it parses GeoJSON coordinates into
    shapely geometries on each call. The index-aware path
    (``encode_tile_from_index``) reuses geometries already built by the spatial
    index and produces byte-identical output.
    """
    features = _to_feature_dicts(features_data)
    records: List[Tuple[int, bytes, Dict[str, Any]]] = []
    for f in features:
        geometry = f.get("geometry")
        if not isinstance(geometry, dict):
            continue
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if gtype == "GeometryCollection":
            logger.warning("mvt: GeometryCollection features are not encoded, skipping")
            continue
        if gtype not in _SUPPORTED_TYPES:
            continue
        geom_bytes = _encode_geometry(gtype, coords, z, x, y)
        if geom_bytes is None:
            continue
        type_id = _type_id_for(gtype)
        if type_id is None:
            continue
        records.append((type_id, geom_bytes, f.get("properties") or {}))
    return _assemble_mvt(records)


def encode_point_tile(
    features: List[Tuple[Tuple[float, float], Dict[str, Any]]],
    z: int,
    x: int,
    y: int,
) -> bytes:
    """Backward-compat wrapper: [(lon, lat), properties] → MVT tile.

    Equivalent to V1's encode_point_tile; delegates to encode_tile with the
    legacy point list format.
    """
    return encode_tile(features, z, x, y)


def encode_tile_from_index(entry: "SpatialIndexEntry", z: int, x: int, y: int) -> bytes:
    """Encode a tile reusing a spatial index's pre-built geometries.

    This is the Data Plane hot path: STRtree candidates come with their already
    projected shapely geometries, so the per-tile GeoJSON→Shapely construction
    and (at z >= _SIMPLIFY_MAX_ZOOM) the z0 projection are eliminated — that
    work happens once at index-build time. Output is byte-identical to
    ``encode_tile(entry.query_tile(z, x, y), z, x, y)``.

    Points still read their coordinates from the feature dict (their projection
    is cheap scalar math, no shapely construction). Falls back to the raw
    ``encode_tile`` path when the index has no prepared shapely geometries
    (shapely unavailable).
    """
    if not _SHAPELY or entry.geoms is None:
        return encode_tile(entry.query_tile(z, x, y), z, x, y)
    records: List[Tuple[int, bytes, Dict[str, Any]]] = []
    for feature, geom_lonlat, geom_z0 in entry.query_candidates(z, x, y):
        geometry = feature.get("geometry")
        gtype = geometry.get("type") if isinstance(geometry, dict) else None
        # GeometryCollections and unsupported types are excluded at index-build
        # time, so this guard is defensive only (no warning, unlike encode_tile
        # which receives arbitrary user input).
        if gtype == "GeometryCollection" or gtype not in _SUPPORTED_TYPES:
            continue
        if gtype in _IS_POINT:
            coords = geometry.get("coordinates")
            pts = coords if gtype == "MultiPoint" else [coords]
            geom_bytes = _encode_points(pts, z, x, y)
        else:
            try:
                geom_bytes = _encode_line_polygon_from_geoms(gtype, geom_lonlat, geom_z0, z, x, y)
            except Exception:
                # Mirror _encode_geometry's resilience: a pathological geometry
                # that survives index build but fails shapely simplify/intersect
                # falls back to the coordinate path (which itself degrades to the
                # pure-python encoder) rather than failing the whole tile.
                logger.warning("mvt: shapely encode failed, using pure-python fallback", exc_info=True)
                geom_bytes = _encode_geometry(gtype, geometry.get("coordinates"), z, x, y)
        if geom_bytes is None:
            continue
        type_id = _type_id_for(gtype)
        if type_id is None:
            continue
        records.append((type_id, geom_bytes, feature.get("properties") or {}))
    return _assemble_mvt(records)


# ─── spatial index ──────────────────────────────────────────────────────────


class SpatialIndexEntry:
    """Immutable per-(session_id, ref_id) index over feature dicts.

    ``geoms`` are shapely geometries in z0 world pixels (world = 256); the
    query box is the tile rect in the same space, so one index serves every
    zoom (projection is homogeneous in z). ``geoms_lonlat`` are the matching
    lon/lat shapely geometries (retained so tile encoding can apply the
    zoom-dependent simplification in lon/lat space without re-parsing the
    GeoJSON coordinates). Retaining both arrays roughly doubles the index's
    per-ref geometry memory vs. the z0-only design; this is the deliberate
    trade-off for eliminating per-tile reconstruction, and stays bounded by the
    per-(session, ref) LRU. ``bounds`` are plain z0-px bboxes used by the
    no-shapely full-scan fallback. ``geoms`` / ``geoms_lonlat`` are None when
    shapely is unavailable.
    """

    __slots__ = ("key", "features", "geoms", "geoms_lonlat", "tree", "bounds")

    def __init__(self, key, features, geoms, geoms_lonlat, tree, bounds):
        self.key = key
        self.features = features
        self.geoms = geoms
        self.geoms_lonlat = geoms_lonlat
        self.tree = tree
        self.bounds = bounds

    def _candidate_indices(self, z: int, x: int, y: int) -> List[int]:
        """Insertion-ordered feature indices whose bbox intersects the tile."""
        x0, y0, x1, y1 = _tile_rect_z0(z, x, y)
        if self.tree is not None:
            return [int(i) for i in np.sort(np.atleast_1d(
                self.tree.query(shapely.geometry.box(x0, y0, x1, y1))))]
        return [
            i for i, b in enumerate(self.bounds)
            if b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0
        ]

    def query_tile(self, z: int, x: int, y: int) -> List[dict]:
        """Features whose bbox intersects the tile (exact filter at encode).

        Results keep the original feature order (STRtree returns spatially
        sorted indices; re-sorting restores insertion order).
        """
        return [self.features[i] for i in self._candidate_indices(z, x, y)]

    def query_candidates(self, z: int, x: int, y: int):
        """Prepared ``(feature, geom_lonlat, geom_z0)`` for each bbox-hit candidate.

        Insertion-ordered (parallel to ``query_tile``). Only valid when
        ``geoms`` is set (shapely available); callers gate on that.
        """
        return [
            (self.features[i], self.geoms_lonlat[i], self.geoms[i])
            for i in self._candidate_indices(z, x, y)
        ]


def _pure_bounds(gtype: str, coords) -> Optional[Tuple[float, float, float, float]]:
    """Bbox in z0 world px computed without shapely; None for bad coords."""
    parts = _geometry_parts(gtype, coords)
    minx = miny = math.inf
    maxx = maxy = -math.inf
    count = 0
    for _kind, _role, part in parts:
        for lon, lat in part:
            if lon is None or lat is None:
                return None
            px, py = _project(lon, lat, 0)
            if px < minx:
                minx = px
            if px > maxx:
                maxx = px
            if py < miny:
                miny = py
            if py > maxy:
                maxy = py
            count += 1
    if count == 0 or not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        return None
    return (minx, miny, maxx, maxy)


def build_spatial_index_entry(key, data) -> SpatialIndexEntry:
    """Build a spatial index entry for (session_id, ref_id) from raw ref data.

    Each kept feature's lon/lat shapely geometry and its z0 projection are
    retained (parallel to ``features``) so that tile encoding can reuse them
    instead of re-parsing coordinates and re-projecting per tile request.
    """
    if data is None:
        raise RefDataUnavailableError(f"ref data unavailable for index build: {key}")
    features = extract_features(data)
    geoms: List[Any] = []
    geoms_lonlat: List[Any] = []
    bounds: List[Tuple[float, float, float, float]] = []
    kept: List[dict] = []
    for f in features:
        g = f.get("geometry")
        gtype = g.get("type") if isinstance(g, dict) else None
        coords = g.get("coordinates") if isinstance(g, dict) else None
        if gtype == "GeometryCollection" or gtype not in _SUPPORTED_TYPES:
            continue
        if _SHAPELY:
            geom = _shapely_geom_from_coords(gtype, coords)
            if geom is None or geom.is_empty:
                continue
            try:
                geom_z0 = shapely.transform(geom, _transform_z0)
                b = geom_z0.bounds
                if b is None or not np.isfinite(b).all():
                    continue
            except Exception:
                continue
            geoms_lonlat.append(geom)
            geoms.append(geom_z0)
            bounds.append(tuple(b))
        else:  # pragma: no cover - no-shapely environments
            b = _pure_bounds(gtype, coords)
            if b is None:
                continue
            bounds.append(b)
        kept.append(f)
    tree = None
    if _SHAPELY and geoms:
        try:
            tree = shapely.STRtree(geoms)
        except Exception:  # pragma: no cover - defensive
            tree = None
    return SpatialIndexEntry(
        key,
        kept,
        geoms if _SHAPELY else None,
        geoms_lonlat if _SHAPELY else None,
        tree,
        bounds,
    )


class SpatialIndexCache:
    """Per-(session_id, ref_id) STRtree cache with bounded LRU eviction.

    Thread-safe (threading.Lock). The heavy build runs outside the lock
    (double-checked), so concurrent misses may build twice — acceptable and
    harmless — while the index itself is only ever queried through the lock.
    """

    def __init__(self, max_refs: int = 256):
        self._max_refs = max_refs
        self._entries: "OrderedDict[tuple, SpatialIndexEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key) -> Optional[SpatialIndexEntry]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry

    def get_or_build(self, key, build_fn) -> SpatialIndexEntry:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry
        entry = build_fn()  # heavy work outside the lock
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                return existing
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_refs:
                self._entries.popitem(last=False)
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# ─── tile LRU cache ─────────────────────────────────────────────────────────


class TileLRUCache:
    """Per-(session_id, ref_id, z, x, y) LRU cache of final gzip bytes.

    Bounded by entry count and total bytes; thread-safe. Session-isolated
    because session_id is part of the key.
    """

    def __init__(self, max_tiles: int = 4096, max_bytes: int = 256 * 1024 * 1024):
        self._max_tiles = max_tiles
        self._max_bytes = max_bytes
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    def get(self, key) -> Optional[bytes]:
        with self._lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return value

    def put(self, key, value: bytes) -> None:
        if value is None or len(value) > self._max_bytes:
            return  # oversized single entry: don't cache
        with self._lock:
            old = self._cache.get(key)
            if old is not None:
                self._total_bytes -= len(old)
            self._cache[key] = value
            self._cache.move_to_end(key)
            self._total_bytes += len(value)
            while len(self._cache) > self._max_tiles or self._total_bytes > self._max_bytes:
                _key, evicted = self._cache.popitem(last=False)
                self._total_bytes -= len(evicted)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._total_bytes = 0


# ─── single-flight dedup ────────────────────────────────────────────────────


class SingleFlightManager:
    """Dedupe concurrent same-key computations: waiters share the leader's result.

    Bounded: when max_inflight keys are already in flight, new callers fall
    through and compute directly instead of waiting. Thread-safe registration
    with an asyncio.Future as the shared result channel.
    """

    def __init__(self, max_inflight: int = 512):
        self._max_inflight = max_inflight
        self._inflight: Dict[Any, "asyncio.Future"] = {}
        self._lock = threading.Lock()

    async def run(self, key, coro_factory) -> Any:
        """Run a coroutine once per key; concurrent same-key callers await it.

        ``coro_factory`` is a zero-arg callable returning a coroutine (or a
        coroutine itself).
        """
        coro = None  # created lazily: only the caller that actually computes
        with self._lock:
            fut = self._inflight.get(key)
            if fut is not None:
                # another request is computing this key: share its result
                return_fut = fut
                direct = False
            elif len(self._inflight) >= self._max_inflight:
                # overloaded: compute directly, don't register / wait
                return_fut = None
                direct = True
            else:
                return_fut = None
                direct = False
                fut = asyncio.get_running_loop().create_future()
                self._inflight[key] = fut
        # NB: never await while holding the lock above — a suspended waiter
        # would block the leader's finally-pop and deadlock.
        if return_fut is not None:
            return await asyncio.shield(return_fut)
        coro = coro_factory() if callable(coro_factory) else coro_factory
        if direct:
            return await coro
        try:
            result = await coro
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        else:
            if not fut.done():
                fut.set_result(result)
            return result
        finally:
            with self._lock:
                self._inflight.pop(key, None)


# module-level singletons (bounded caches, one per process)
spatial_index_cache = SpatialIndexCache()
tile_lru_cache = TileLRUCache()
single_flight = SingleFlightManager()
