"""MVT encoder tests: round-trip via a minimal decoder + projection checks.

The decoder here is deliberately independent (parses raw varint/field
encoding) so the round-trip validates the encoder against the wire format,
not against itself. Covers Points (V1 behavior), LineString / Polygon /
Multi* geometries, winding, the spatial index, the tile LRU cache and the
single-flight dedup.
"""
import asyncio
import math
import struct

import pytest

from app.services.mvt import (
    SingleFlightManager,
    SpatialIndexCache,
    TileLRUCache,
    build_spatial_index_entry,
    encode_point_tile,
    encode_tile,
    spatial_index_cache,
    tile_lru_cache,
)


# ─── minimal MVT/protobuf decoder ────────────────────────────────────────────


def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf):
    """Yield (field_num, wire_type, payload_bytes) at top level."""
    pos = 0
    while pos < len(buf):
        tag, pos = _varint(buf, pos)
        num, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, pos = _varint(buf, pos)
            yield num, wire, val
        elif wire == 1:
            yield num, wire, buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            ln, pos = _varint(buf, pos)
            yield num, wire, buf[pos:pos + ln]
            pos += ln
        else:
            raise AssertionError(f"unexpected wire type {wire}")


def _zigzag(n):
    return (n >> 1) ^ -(n & 1)


def _decode_tile(data):
    layers = []
    for num, wire, payload in _fields(data):
        assert (num, wire) == (3, 2), "tile must contain only layer field"
        layers.append(_decode_layer(payload))
    return layers


def _decode_layer(buf):
    name = None
    version = None
    extent = None
    features = []
    keys = []
    values = []
    for num, wire, payload in _fields(buf):
        if (num, wire) == (15, 0):
            version = payload
        elif (num, wire) == (1, 2):
            name = payload.decode("utf-8")
        elif (num, wire) == (2, 2):
            features.append(_decode_feature(payload))
        elif (num, wire) == (3, 2):
            keys.append(payload.decode("utf-8"))
        elif (num, wire) == (4, 2):
            values.append(_decode_value(payload))
        elif (num, wire) == (5, 0):
            extent = payload
    return {"name": name, "version": version, "extent": extent,
            "features": features, "keys": keys, "values": values}


def _decode_value(buf):
    for num, wire, payload in _fields(buf):
        if num == 1:  # string
            return payload.decode("utf-8")
        if num in (2, 3):  # float / double
            return struct.unpack("<f" if num == 2 else "<d", payload)[0]
        if num in (4, 5, 6):  # int / uint / sint
            return _zigzag(payload) if num == 6 else payload
        if num == 7:  # bool
            return bool(payload)
    return None


def _decode_geometry_parts(buf):
    """Decode commands into parts: (kind, points).

    kind is "MoveTo" | "LineTo" (points list) or "ClosePath" (points=None).
    Cursor tracking mirrors the MVT spec (ClosePath returns the cursor to the
    part start)."""
    pos = 0
    parts = []
    cx = cy = 0
    part_start = None
    while pos < len(buf):
        cmd, pos = _varint(buf, pos)
        cid = cmd & 0x7
        count = cmd >> 3
        if cid == 7:  # ClosePath — no parameters
            parts.append(("ClosePath", None))
            cx, cy = part_start
            continue
        pts = []
        for _ in range(count):
            dx, pos = _varint(buf, pos)
            dy, pos = _varint(buf, pos)
            cx += _zigzag(dx)
            cy += _zigzag(dy)
            pts.append((cx, cy))
        if cid == 1:  # MoveTo
            part_start = pts[-1]
            parts.append(("MoveTo", pts))
        elif cid == 2:  # LineTo
            parts.append(("LineTo", pts))
        else:
            raise AssertionError(f"unexpected command id {cid}")
    return parts


def _decode_feature(buf):
    type_ = None
    tags = []
    geometry = None
    geometry_parts = None
    for num, wire, payload in _fields(buf):
        if (num, wire) == (3, 0):
            type_ = payload
        elif (num, wire) == (2, 2):
            tags = _decode_tags(payload)
        elif (num, wire) == (4, 2):
            geometry_parts = _decode_geometry_parts(payload)
            geometry = []
            for kind, pts in geometry_parts:
                if kind in ("MoveTo", "LineTo"):
                    geometry.extend(pts)
    return {"type": type_, "tags": tags, "geometry": geometry,
            "geometry_parts": geometry_parts}


def _decode_tags(buf):
    pos = 0
    out = []
    while pos < len(buf):
        k, pos = _varint(buf, pos)
        v, pos = _varint(buf, pos)
        out.append((k, v))
    return out


def _project(lon, lat, z):
    world = 256.0 * (1 << z)
    x = (lon + 180.0) / 360.0 * world
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world
    return x, y


def _inverse_project(px_x, px_y, z):
    world = 256.0 * (1 << z)
    lon = px_x / world * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * px_y / world
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def _shoelace(pts):
    """Signed area (surveyor's formula); > 0 = CCW in a y-up coordinate system."""
    n = len(pts)
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _parts_to_rings(parts):
    """Assemble decoded parts into closed rings (list of point lists)."""
    rings = []
    cur = None
    for kind, pts in parts:
        if kind == "MoveTo":
            cur = list(pts)
        elif kind == "LineTo":
            cur.extend(pts)
        elif kind == "ClosePath":
            rings.append(cur)
            cur = None
    return rings


def _extent_to_lonlat(ring, z, x, y):
    out = []
    for ex, ey in ring:
        px = ex / 4096.0 * 256.0 + x * 256.0
        py = ey / 4096.0 * 256.0 + y * 256.0
        out.append(_inverse_project(px, py, z))
    return out


def _tile_of(z, lon, lat):
    """Tile x/y that contains a lon/lat point (for test fixtures)."""
    px, py = _project(lon, lat, z)
    return int(px // 256), int(py // 256)


def _feature(gtype, coords, props=None):
    return {
        "type": "Feature",
        "geometry": {"type": gtype, "coordinates": coords},
        "properties": props or {},
    }


# ─── point tests (V1 behavior preserved) ────────────────────────────────────


def test_round_trip_point_tile():
    """Points encode and decode back to ~the same lon/lat (extent quantization)."""
    points = [
        ((116.4, 39.9), {"name": "北京站", "score": 7, "is_top": True, "price": 12.5}),
        ((116.5, 40.0), {"name": "point2", "score": 3, "is_top": False, "price": 0.0}),
        ((-0.1, 51.5), {"name": "london", "score": 1, "is_top": False, "price": 9.99}),
    ]
    # encode each point in its own tile for a clean assertion
    for (lon, lat), props in points:
        tile_z1_x = int((lon + 180.0) / 360.0 * 2)
        tile_z1_y = 0 if lat >= 0 else 1
        tile = encode_point_tile([((lon, lat), props)], 1, tile_z1_x, tile_z1_y)
        layers = _decode_tile(tile)
        assert len(layers) == 1
        layer = layers[0]
        assert layer["name"] == "data"
        assert layer["version"] == 2
        assert layer["extent"] == 4096
        assert len(layer["features"]) == 1
        feat = layer["features"][0]
        assert feat["type"] == 1  # Point
        assert len(feat["geometry"]) == 1
        # decode extent coords → px → lon/lat
        ex, ey = feat["geometry"][0]
        px = ex / 4096.0 * 256.0 + tile_z1_x * 256.0
        py = ey / 4096.0 * 256.0 + tile_z1_y * 256.0
        r_lon, r_lat = _inverse_project(px, py, 1)
        assert abs(r_lon - lon) < 0.05
        assert abs(r_lat - lat) < 0.05
        # properties round-trip with correct types
        assert layer["keys"] == list(props.keys())
        decoded_props = dict(zip(
            (layer["keys"][k] for k, _ in feat["tags"]),
            (layer["values"][v] for _, v in feat["tags"]),
        ))
        for k, v in props.items():
            assert decoded_props[k] == v


def test_tile_filters_out_of_bounds_points():
    """Points outside the requested tile must be dropped; empty tile is valid."""
    tile = encode_point_tile(
        [((116.4, 39.9), {"name": "east"})],
        1, 0, 0,  # NW quadrant — the point is in the east quadrant
    )
    layers = _decode_tile(tile)
    assert layers == []  # valid empty tile (no layers)


def test_point_on_tile_edge_included():
    """Boundary points inside [tile_x*256, (tile_x+1)*256) are included."""
    lon, lat = -0.1, 0.5  # clearly inside the NW quadrant, near the prime meridian
    tile = encode_point_tile([((lon, lat), {"n": 1})], 1, 0, 0)
    layers = _decode_tile(tile)
    assert len(layers) == 1
    assert len(layers[0]["features"]) == 1


def test_property_types_round_trip():
    tile = encode_point_tile(
        [((0.0, 0.0), {"str": "abc", "int": 42, "float": 3.5, "bool": True, "none": None})],
        0, 0, 0,
    )
    layer = _decode_tile(tile)[0]
    feat = layer["features"][0]
    props = dict(zip((layer["keys"][k] for k, _ in feat["tags"]),
                     (layer["values"][v] for _, v in feat["tags"])))
    assert props["str"] == "abc"
    assert props["int"] == 42
    assert props["float"] == 3.5
    assert props["bool"] is True
    assert "none" not in props  # unsupported (None) dropped


def test_delta_encoding_multiple_points_same_feature():
    """One feature carrying multiple points decodes to all of them."""
    pts = [((0.1, 0.1), {"i": 1}), ((0.2, 0.1), {"i": 2}), ((0.3, 0.2), {"i": 3})]
    tile = encode_point_tile(pts, 1, 1, 0)  # east-north quadrant covers lon 0..180
    layer = _decode_tile(tile)[0]
    assert len(layer["features"]) == 3  # one feature per point (MoveTo count=1)
    lons = []
    for feat in layer["features"]:
        ex, ey = feat["geometry"][0]
        px = ex / 4096.0 * 256.0 + 1 * 256.0
        py = ey / 4096.0 * 256.0 + 0 * 256.0
        r_lon, _ = _inverse_project(px, py, 1)
        lons.append(r_lon)
    assert sorted(lons) == pytest.approx([0.1, 0.2, 0.3], abs=0.05)


# ─── LineString ─────────────────────────────────────────────────────────────


def test_linestring_encoding():
    """LineString → MVT geometry type 2 with MoveTo(1) + LineTo(n-1)."""
    # z=13 (just below the z>=14 no-simplify threshold): the tile spans ~0.044°
    # so the whole line fits in one tile and simplification tolerance
    # (half a pixel ≈ 8.6e-5°) keeps every vertex.
    coords = [(116.390, 39.895), (116.400, 39.902), (116.410, 39.905)]
    z = 13
    x, y = _tile_of(z, 116.400, 39.900)
    tile = encode_tile([_feature("LineString", coords, {"name": "road", "lanes": 2})], z, x, y)
    layer = _decode_tile(tile)[0]
    assert len(layer["features"]) == 1
    feat = layer["features"][0]
    assert feat["type"] == 2  # LINESTRING
    parts = feat["geometry_parts"]
    assert parts[0][0] == "MoveTo" and len(parts[0][1]) == 1
    assert parts[1][0] == "LineTo" and len(parts[1][1]) == 2
    # round-trip every vertex
    for (ex, ey), (rlon, rlat) in zip(feat["geometry"], coords):
        px = ex / 4096.0 * 256.0 + x * 256.0
        py = ey / 4096.0 * 256.0 + y * 256.0
        lon, lat = _inverse_project(px, py, z)
        assert abs(lon - rlon) < 1e-4
        assert abs(lat - rlat) < 1e-4
    # properties round-trip
    props = dict(zip((layer["keys"][k] for k, _ in feat["tags"]),
                     (layer["values"][v] for _, v in feat["tags"])))
    assert props == {"name": "road", "lanes": 2}


def test_linestring_out_of_bounds_empty_tile():
    tile = encode_tile(
        [_feature("LineString", [[116.3, 39.8], [116.5, 40.0]])],
        1, 0, 0,  # NW quadrant — line is in the east
    )
    assert _decode_tile(tile) == []


# ─── Polygon ────────────────────────────────────────────────────────────────


def test_polygon_with_hole_winding():
    """Polygon with hole: exterior ring CCW, hole CW (geographic winding)."""
    exterior = [(116.0, 39.5), (116.5, 39.5), (116.5, 40.0), (116.0, 40.0), (116.0, 39.5)]
    hole = [(116.15, 39.6), (116.15, 39.7), (116.25, 39.7), (116.25, 39.6), (116.15, 39.6)]
    # fixture sanity: RFC 7946 input winding (y-up shoelace)
    assert _shoelace(exterior) > 0
    assert _shoelace(hole) < 0

    z = 3
    x, y = _tile_of(z, 116.3, 39.75)
    tile = encode_tile([_feature("Polygon", [exterior, hole], {"kind": "zone"})], z, x, y)
    layer = _decode_tile(tile)[0]
    assert len(layer["features"]) == 1
    feat = layer["features"][0]
    assert feat["type"] == 3  # POLYGON

    parts = feat["geometry_parts"]
    # each ring: MoveTo(1) + LineTo(len-2) + ClosePath(1)
    assert parts[0][0] == "MoveTo" and len(parts[0][1]) == 1
    assert parts[1][0] == "LineTo" and len(parts[1][1]) == 3  # 4 distinct pts per ring
    assert parts[2][0] == "ClosePath"
    assert parts[3][0] == "MoveTo"
    assert parts[4][0] == "LineTo" and len(parts[4][1]) == 3
    assert parts[5][0] == "ClosePath"

    rings = _parts_to_rings(parts)
    assert len(rings) == 2
    ext_ll = _extent_to_lonlat(rings[0], z, x, y)
    hole_ll = _extent_to_lonlat(rings[1], z, x, y)
    # exterior CCW / hole CW in geographic (y-up) terms
    assert _shoelace(ext_ll) > 0
    assert _shoelace(hole_ll) < 0
    # exterior is the bigger ring
    assert abs(_shoelace(ext_ll)) > abs(_shoelace(hole_ll))


def test_polygon_out_of_bounds_empty_tile():
    tile = encode_tile(
        [_feature("Polygon", [[[116.0, 39.5], [116.5, 39.5], [116.5, 40.0], [116.0, 39.5]]])],
        1, 0, 0,  # NW quadrant — polygon is in the east
    )
    assert _decode_tile(tile) == []


# ─── Multi* geometries ──────────────────────────────────────────────────────


def test_multipoint_encoding():
    coords = [[116.40, 39.90], [116.50, 39.95]]
    z = 3
    x, y = _tile_of(z, coords[0][0], coords[0][1])
    tile = encode_tile([_feature("MultiPoint", coords)], z, x, y)
    layer = _decode_tile(tile)[0]
    feat = layer["features"][0]
    assert feat["type"] == 1
    assert len(feat["geometry"]) == 2
    kinds = [p[0] for p in feat["geometry_parts"]]
    assert kinds == ["MoveTo", "MoveTo"]  # one MoveTo per point
    for (ex, ey), (rlon, rlat) in zip(feat["geometry"], coords):
        px = ex / 4096.0 * 256.0 + x * 256.0
        py = ey / 4096.0 * 256.0 + y * 256.0
        lon, lat = _inverse_project(px, py, z)
        assert abs(lon - rlon) < 0.01
        assert abs(lat - rlat) < 0.01


def test_multilinestring_encoding():
    # parallel, non-crossing lines (crossing lines get noded by the
    # intersection op, which would split them into extra parts)
    coords = [
        [[116.30, 39.80], [116.40, 39.90]],
        [[116.30, 39.60], [116.40, 39.70]],
    ]
    z = 3
    x, y = _tile_of(z, 116.35, 39.75)
    tile = encode_tile([_feature("MultiLineString", coords)], z, x, y)
    layer = _decode_tile(tile)[0]
    feat = layer["features"][0]
    assert feat["type"] == 2
    assert len(feat["geometry"]) == 4  # two 2-point lines
    parts = feat["geometry_parts"]
    # two parts, each MoveTo(1) + LineTo(1)
    assert parts[0][0] == "MoveTo" and parts[1][0] == "LineTo"
    assert parts[2][0] == "MoveTo" and parts[3][0] == "LineTo"


def test_multipolygon_encoding():
    # disjoint members (overlapping members would make the MultiPolygon invalid
    # and the encoder's is_valid gate rejects it)
    poly1 = [[[116.0, 39.5], [116.5, 39.5], [116.5, 40.0], [116.0, 40.0], [116.0, 39.5]]]
    poly2 = [[[116.6, 39.6], [117.1, 39.6], [117.1, 40.1], [116.6, 40.1], [116.6, 39.6]]]
    z = 3
    x, y = _tile_of(z, 116.3, 39.75)
    tile = encode_tile([_feature("MultiPolygon", [poly1, poly2])], z, x, y)
    layer = _decode_tile(tile)[0]
    feat = layer["features"][0]
    assert feat["type"] == 3
    rings = _parts_to_rings(feat["geometry_parts"])
    assert len(rings) == 2
    for ring in rings:
        ll = _extent_to_lonlat(ring, z, x, y)
        assert _shoelace(ll) > 0  # every polygon exterior CCW (geographic)


# ─── skipped geometries ─────────────────────────────────────────────────────


def test_geometry_collection_skipped():
    features = [_feature("GeometryCollection", None, {"name": "skip"})]
    tile = encode_tile(features, 3, 2, 1)
    assert _decode_tile(tile) == []


# ─── spatial index ──────────────────────────────────────────────────────────


def test_spatial_index_returns_correct_features_for_tile():
    fc = {
        "type": "FeatureCollection",
        "features": [
            _feature("Point", [-10.0, 30.0], {"id": "point-west"}),
            _feature("Point", [120.0, 30.0], {"id": "point-east"}),
            _feature("LineString", [[-10.0, 30.0], [-9.0, 31.0]], {"id": "line-west"}),
            _feature("Polygon", [[[110.0, 29.0], [130.0, 29.0], [130.0, 31.0], [110.0, 29.0]]], {"id": "poly-east"}),
            _feature("GeometryCollection", None, {"id": "gc"}),  # skipped
        ],
    }
    entry = build_spatial_index_entry(("sess_test", "ref_a"), fc)
    # z=1, x=0 → lon -180..0 ; x=1 → lon 0..180 ; y=0 → lat 0..85
    west = {f["properties"]["id"] for f in entry.query_tile(1, 0, 0)}
    assert west == {"point-west", "line-west"}
    east = {f["properties"]["id"] for f in entry.query_tile(1, 1, 0)}
    assert east == {"point-east", "poly-east"}
    # high-zoom sub-tile keeps only what intersects it
    z3_x, z3_y = _tile_of(3, 120.0, 30.0)
    sub = {f["properties"]["id"] for f in entry.query_tile(3, z3_x, z3_y)}
    assert "point-east" in sub and "poly-east" in sub and "point-west" not in sub


def test_spatial_index_build_requires_data():
    with pytest.raises(Exception):
        build_spatial_index_entry(("sess", "ref"), None)


def test_spatial_index_cache_lru_and_build_once():
    cache = SpatialIndexCache(max_refs=2)
    builds = []

    def build_a():
        builds.append("a")
        return "entry-a"

    def build_b():
        builds.append("b")
        return "entry-b"

    def build_c():
        builds.append("c")
        return "entry-c"

    assert cache.get_or_build(("s", "a"), build_a) == "entry-a"
    assert cache.get_or_build(("s", "a"), build_a) == "entry-a"  # cached
    assert builds == ["a"]  # built once
    assert cache.get_or_build(("s", "b"), build_b) == "entry-b"
    cache.get_or_build(("s", "c"), build_c)  # evicts "a" (LRU)
    assert cache.get(("s", "a")) is None
    assert cache.get(("s", "b")) == "entry-b"
    cache.clear()
    assert cache.get(("s", "b")) is None


# ─── tile LRU cache ─────────────────────────────────────────────────────────


def test_tile_lru_cache_hit_miss_and_eviction():
    cache = TileLRUCache(max_tiles=2, max_bytes=1 << 20)
    assert cache.get(("s1", "r1", 1, 1, 1)) is None  # miss
    cache.put(("s1", "r1", 1, 1, 1), b"AAA")
    assert cache.get(("s1", "r1", 1, 1, 1)) == b"AAA"  # hit
    cache.put(("s1", "r1", 1, 1, 2), b"BBB")
    cache.put(("s1", "r1", 1, 1, 3), b"CCC")
    assert cache.get(("s1", "r1", 1, 1, 1)) is None  # evicted (LRU)
    assert cache.get(("s1", "r1", 1, 1, 3)) == b"CCC"
    cache.clear()
    assert cache.get(("s1", "r1", 1, 1, 3)) is None


def test_tile_lru_cache_session_isolation():
    cache = TileLRUCache(max_tiles=8, max_bytes=1 << 20)
    cache.put(("s1", "ref", 0, 0, 0), b"session-one")
    assert cache.get(("s2", "ref", 0, 0, 0)) is None
    assert cache.get(("s1", "ref", 0, 0, 0)) == b"session-one"


def test_tile_lru_cache_byte_limit():
    cache = TileLRUCache(max_tiles=100, max_bytes=10)
    cache.put(("s", "r", 0, 0, 0), b"0123456789")  # 10 bytes
    cache.put(("s", "r", 0, 0, 1), b"0123456789")
    assert cache.get(("s", "r", 0, 0, 0)) is None  # byte limit evicted oldest
    assert cache.get(("s", "r", 0, 0, 1)) == b"0123456789"
    cache.put(("s", "r", 0, 0, 2), b"0123456789ABCDEF")  # oversized → not stored
    assert cache.get(("s", "r", 0, 0, 2)) is None


def test_module_singleton_caches_usable():
    """Module-level singletons accept the real (session, ref, z, x, y) keys."""
    tile_lru_cache.clear()
    spatial_index_cache.clear()
    key = ("sess_singleton", "ref_singleton", 2, 1, 1)
    tile_lru_cache.put(key, b"gzip-bytes")
    assert tile_lru_cache.get(key) == b"gzip-bytes"
    tile_lru_cache.clear()


# ─── single-flight ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_singleflight_dedupes_concurrent_requests():
    mgr = SingleFlightManager(max_inflight=8)
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return b"shared-result"

    key = ("sess", "ref", 3, 2, 1)
    results = await asyncio.gather(*[mgr.run(key, compute) for _ in range(8)])
    assert all(r == b"shared-result" for r in results)
    assert calls == 1  # computed once, shared by all waiters


@pytest.mark.asyncio
async def test_singleflight_overflow_falls_through():
    mgr = SingleFlightManager(max_inflight=1)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return b"done"

    t1 = asyncio.create_task(mgr.run("a", compute))
    await started.wait()
    # key "b" while "a" is in flight → falls through and computes directly
    t2 = asyncio.create_task(mgr.run("b", compute))
    await asyncio.sleep(0.01)
    assert not t2.done()
    release.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == r2 == b"done"
    assert calls == 2


# ─── pure-python fallback (no shapely) ──────────────────────────────────────


def test_pure_python_fallback_encodes_line_and_polygon(monkeypatch):
    import app.services.mvt as mvt_mod

    monkeypatch.setattr(mvt_mod, "_SHAPELY", False)
    exterior = [(116.0, 39.5), (116.5, 39.5), (116.5, 40.0), (116.0, 40.0), (116.0, 39.5)]
    hole = [(116.15, 39.6), (116.15, 39.7), (116.25, 39.7), (116.25, 39.6), (116.15, 39.6)]
    z = 3
    x, y = _tile_of(z, 116.3, 39.75)
    tile = encode_tile(
        [
            _feature("Polygon", [exterior, hole], {"kind": "zone"}),
            _feature("LineString", [[116.30, 39.80], [116.40, 39.90], [116.50, 40.00]], {"name": "road"}),
            _feature("MultiPoint", [[116.42, 39.85], [116.48, 39.92]], {"n": 2}),
        ],
        z, x, y,
    )
    layer = _decode_tile(tile)[0]
    types = sorted(f["type"] for f in layer["features"])
    assert types == [1, 2, 3]
    poly = next(f for f in layer["features"] if f["type"] == 3)
    rings = _parts_to_rings(poly["geometry_parts"])
    assert len(rings) == 2
    ext_ll = _extent_to_lonlat(rings[0], z, x, y)
    hole_ll = _extent_to_lonlat(rings[1], z, x, y)
    assert _shoelace(ext_ll) > 0  # exterior CCW (geographic)
    assert _shoelace(hole_ll) < 0  # hole CW


def test_pure_python_fallback_index_query(monkeypatch):
    import app.services.mvt as mvt_mod

    monkeypatch.setattr(mvt_mod, "_SHAPELY", False)
    fc = {
        "type": "FeatureCollection",
        "features": [
            _feature("Point", [-10.0, 30.0], {"id": "west"}),
            _feature("Point", [120.0, 30.0], {"id": "east"}),
        ],
    }
    entry = build_spatial_index_entry(("s", "r"), fc)
    assert {f["properties"]["id"] for f in entry.query_tile(1, 0, 0)} == {"west"}
    assert {f["properties"]["id"] for f in entry.query_tile(1, 1, 0)} == {"east"}
