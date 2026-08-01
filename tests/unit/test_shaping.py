"""Behavioral tests for ``app.tools.chinese_maps._shaping.shape_poi_collection``.

The ``_shaping.py`` module docstring references a ``test_shaping`` that did not
exist (verified: ``grep shape_poi_collection tests/`` returned nothing). These
tests lock the five behaviors the docstring promises, *before* the F1 provider
refactor touches the module's callers:

1. normalizes source-CRS coordinates to WGS84 in one pass (gcj02 path);
2. leaves coordinates untouched when ``src_crs=None`` (tianditu path);
3. skips records whose ``extract_coord`` returns ``None`` (mirrors the prior
   ``if len(loc) != 2: continue`` guard);
4. honors ``limit``;
5. merges ``extra_envelope`` keys into the FeatureCollection envelope.

All synthetic — no network, no provider.
"""
from app.tools.chinese_maps._shaping import shape_poi_collection


# ── fixtures: synthetic POI records in each provider's source shape ──────────

_AMAP_POI = {"name": "火锅店", "location": "116.40,39.90"}        # "lng,lat"
_BAIDU_POI = {"name": "便利店", "location": {"lng": 116.40, "lat": 39.90}}
_TIANDITU_POI = {"name": "学校", "lonlat": "116.40 39.90"}         # "lng lat", WGS84


def _amap_coord(p):
    parts = p.get("location", "").split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _baidu_coord(p):
    loc = p.get("location") or {}
    lng, lat = loc.get("lng"), loc.get("lat")
    if lng is None or lat is None:
        return None
    try:
        return float(lng), float(lat)
    except (TypeError, ValueError):
        return None


def _tianditu_coord(p):
    parts = p.get("lonlat", "").split(" ")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


# ── 1. CRS normalization: gcj02 → wgs84 shifts coordinates ──────────────────


def test_normalizes_gcj02_to_wgs84():
    """gcj02 coords must be transformed; output coords differ from input."""
    fc = shape_poi_collection(
        [_AMAP_POI],
        extract_coord=_amap_coord,
        properties_fn=lambda p: {"name": p.get("name")},
        provider="amap",
        src_crs="gcj02",
    )
    assert fc["type"] == "FeatureCollection"
    assert fc["provider"] == "amap"
    out_lng, out_lat = fc["features"][0]["geometry"]["coordinates"]
    # gcj02 → wgs84 is a small offset (~tens of arc-seconds in mainland China),
    # so the coords must shift away from the raw 116.40 / 39.90.
    assert abs(out_lng - 116.40) > 1e-6 or abs(out_lat - 39.90) > 1e-6
    assert fc["features"][0]["properties"]["name"] == "火锅店"


def test_normalizes_bd09_to_wgs84():
    fc = shape_poi_collection(
        [_BAIDU_POI],
        extract_coord=_baidu_coord,
        properties_fn=lambda p: {"name": p.get("name")},
        provider="baidu",
        src_crs="bd09",
    )
    out_lng, out_lat = fc["features"][0]["geometry"]["coordinates"]
    # bd09 → wgs84 is an even larger offset than gcj02 → wgs84.
    assert abs(out_lng - 116.40) > 1e-3 or abs(out_lat - 39.90) > 1e-3


# ── 2. src_crs=None (tianditu) leaves coords untouched ───────────────────────


def test_src_crs_none_is_identity():
    """tianditu (CGCS2000 ≈ WGS84) passes through unchanged."""
    fc = shape_poi_collection(
        [_TIANDITU_POI],
        extract_coord=_tianditu_coord,
        properties_fn=lambda p: {"name": p.get("name")},
        provider="tianditu",
        src_crs=None,
    )
    out_lng, out_lat = fc["features"][0]["geometry"]["coordinates"]
    assert out_lng == 116.40
    assert out_lat == 39.90


# ── 3. records with no usable coordinate are skipped ─────────────────────────


def test_skips_records_with_none_coord():
    """A record whose extract_coord returns None is dropped, not included."""
    records = [
        _AMAP_POI,
        {"name": "broken", "location": "garbage"},   # _amap_coord → None
        {"name": "empty"},                             # no location key
    ]
    fc = shape_poi_collection(
        records,
        extract_coord=_amap_coord,
        properties_fn=lambda p: {"name": p.get("name")},
        provider="amap",
        src_crs="gcj02",
    )
    assert fc["count"] == 1
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["name"] == "火锅店"


def test_all_invalid_coords_yields_empty_collection():
    fc = shape_poi_collection(
        [{"location": "?"}, {"location": ""}],
        extract_coord=_amap_coord,
        properties_fn=lambda p: {},
        provider="amap",
        src_crs="gcj02",
    )
    assert fc["count"] == 0
    assert fc["features"] == []


# ── 4. limit caps the number of shaped records ───────────────────────────────


def test_limit_caps_record_count():
    # lng fixed at 116.0, lat varies so each record is a distinct valid coord.
    records = [{"location": f"116.0,39.{i}"} for i in range(10)]
    fc = shape_poi_collection(
        records,
        extract_coord=_amap_coord,
        properties_fn=lambda p: {},
        provider="amap",
        src_crs="gcj02",
        limit=3,
    )
    assert fc["count"] == 3
    assert len(fc["features"]) == 3


def test_limit_none_shapes_all():
    records = [{"location": f"116.0,39.{i}"} for i in range(5)]
    fc = shape_poi_collection(
        records,
        extract_coord=_amap_coord,
        properties_fn=lambda p: {},
        provider="amap",
        src_crs="gcj02",
        limit=None,
    )
    assert fc["count"] == 5


# ── 5. extra_envelope merges into the FeatureCollection envelope ─────────────


def test_extra_envelope_is_merged():
    fc = shape_poi_collection(
        [_AMAP_POI],
        extract_coord=_amap_coord,
        properties_fn=lambda p: {},
        provider="amap",
        src_crs="gcj02",
        extra_envelope={"center": [116.40, 39.90], "radius_m": 1000},
    )
    assert fc["center"] == [116.40, 39.90]
    assert fc["radius_m"] == 1000
    # standard envelope keys still present alongside the extras
    assert fc["type"] == "FeatureCollection"
    assert fc["provider"] == "amap"


def test_no_extra_envelope_leaves_envelope_clean():
    fc = shape_poi_collection(
        [_AMAP_POI],
        extract_coord=_amap_coord,
        properties_fn=lambda p: {},
        provider="amap",
        src_crs="gcj02",
    )
    assert "center" not in fc
    assert "radius_m" not in fc
    assert set(fc.keys()) == {"type", "features", "count", "provider"}
