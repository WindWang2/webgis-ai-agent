"""Minimal Mapbox Vector Tile (MVT) encoder — Point features, stdlib only.

Tracer bullet for the Data Plane goal: large POI FeatureCollections are
served to the browser as viewport tiles instead of one huge GeoJSON.
Encoding follows the MVT 2.1 spec (layer "data", extent 4096, Web-Mercator
projection, zigzag/varint delta geometry). Only Point features are encoded;
non-point features are skipped by the caller.

Tile-empty result is a valid empty tile (no layers) — MapLibre renders it
as blank, which is correct for tiles outside the data extent.
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

_LAYER_NAME = "data"
_EXTENT = 4096

# wire types
_VARINT = 0
_BYTES = 2

# value field tags
_VAL_STRING, _VAL_FLOAT, _VAL_DOUBLE, _VAL_INT, _VAL_UINT, _VAL_SINT, _VAL_BOOL = range(1, 8)

# geometry types (MVT)
_GT_POINT = 1

# command ids
_MOVETO = 1


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
    """WGS84 → Web-Mercator pixels at zoom z (world = 256 * 2^z)."""
    world = 256.0 * (1 << z)
    x = (lon + 180.0) / 360.0 * world
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world
    return x, y


def _encode_value(v: Any) -> Optional[bytes]:
    """Proto Value message for a property value; None for unsupported types."""
    if isinstance(v, bool):
        return _field(_VAL_BOOL, _VARINT) + _varint(1 if v else 0)
    if isinstance(v, int) and not isinstance(v, bool):
        # 32-bit signed: keep within int32 (MVT spec)
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


def _encode_geometry(points: Iterable[Tuple[float, float]], tile_x: int, tile_y: int, z: int) -> Optional[bytes]:
    """Encode Point geometry for the given tile; None if no point falls inside.

    Points are projected to pixels, clipped to the tile, then delta-encoded
    in extent units (cursor starts at (0,0); each point is a MoveTo(1)).
    """
    scale = _EXTENT / 256.0
    out = bytearray()
    cursor_x = 0
    cursor_y = 0
    wrote = False
    for lon, lat in points:
        px_x, px_y = _project(lon, lat, z)
        if not (tile_x * 256 <= px_x < (tile_x + 1) * 256 and tile_y * 256 <= px_y < (tile_y + 1) * 256):
            continue
        # extent 量化后夹紧到 [0, extent-1]：像素边界上的点 round 可能溢出
        ex = min(int(round((px_x - tile_x * 256) * scale)), _EXTENT - 1)
        ey = min(int(round((px_y - tile_y * 256) * scale)), _EXTENT - 1)
        # 光标 delta 编码
        dx = ex - cursor_x
        dy = ey - cursor_y
        out += _varint((_MOVETO << 3) | 1)          # MoveTo, count=1
        out += _varint(_zigzag32(dx))
        out += _varint(_zigzag32(dy))
        cursor_x, cursor_y = ex, ey
        wrote = True
    return bytes(out) if wrote else None


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


def encode_point_tile(
    features: List[Tuple[Tuple[float, float], Dict[str, Any]]],
    z: int,
    x: int,
    y: int,
) -> bytes:
    """Encode Point features [(lon, lat), properties] into an MVT tile.

    Returns a valid (possibly empty) tile for (z, x, y). Properties are
    deduped per tile; unsupported value types are dropped from the tag list.
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
    for (lon, lat), props in features:
        if lon is None or lat is None:
            continue
        geometry = _encode_geometry([(lon, lat)], x, y, z)
        if geometry is None:
            continue
        tags: List[int] = []
        for k, v in props.items():
            encoded = _encode_value(v)
            if encoded is None:
                continue
            tags.append(_k(k))
            tags.append(_v(encoded))
        feature_msgs.append(_encode_feature(geometry, tags))

    if not feature_msgs:
        return b""  # 空 tile：合法的空 message（无 layer）

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
