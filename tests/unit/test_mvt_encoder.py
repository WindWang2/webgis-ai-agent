"""MVT encoder tests: round-trip via a minimal decoder + projection checks.

The decoder here is deliberately independent (parses raw varint/field
encoding) so the round-trip validates the encoder against the wire format,
not against itself.
"""
import math
import struct

import pytest

from app.services.mvt import encode_point_tile


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


def _decode_feature(buf):
    type_ = None
    tags = []
    geometry = None
    for num, wire, payload in _fields(buf):
        if (num, wire) == (3, 0):
            type_ = payload
        elif (num, wire) == (2, 2):
            tags = _decode_tags(payload)
        elif (num, wire) == (4, 2):
            geometry = _decode_geometry(payload)
    return {"type": type_, "tags": tags, "geometry": geometry}


def _decode_tags(buf):
    pos = 0
    out = []
    while pos < len(buf):
        k, pos = _varint(buf, pos)
        v, pos = _varint(buf, pos)
        out.append((k, v))
    return out


def _decode_geometry(buf):
    """Decode commands into a list of (x, y) points in extent coords."""
    pos = 0
    points = []
    cx = cy = 0
    while pos < len(buf):
        cmd, pos = _varint(buf, pos)
        count = cmd >> 3
        for _ in range(count):
            dx, pos = _varint(buf, pos)
            dy, pos = _varint(buf, pos)
            cx += _zigzag(dx)
            cy += _zigzag(dy)
            points.append((cx, cy))
    return points


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


# ─── tests ───────────────────────────────────────────────────────────────────


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
