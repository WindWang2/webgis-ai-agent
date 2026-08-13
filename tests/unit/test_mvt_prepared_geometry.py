"""Regression + performance tests for MVT prepared-geometry reuse.

Guards the optimization that makes the spatial index → tile candidate → geometry
encode hot path reuse geometries already built at index time, instead of
re-parsing GeoJSON coordinates and re-projecting to z0 on every tile request.

The tests are deterministic (work-counters, not wall-clock) and assert:

  A. After an index build, encoding adjacent tiles does NOT repeat the
     GeoJSON→Shapely construction / z0 projection per candidate.
  B. The expensive projection work converges to O(features) at build time plus
     O(candidate clipping/encoding) at tile time.
  C. Output is byte-identical to the coordinate-based ``encode_tile`` for every
     supported geometry type (Point / MultiPoint / LineString / MultiLineString
     / Polygon + holes / MultiPolygon), at z < 14 and z >= 14.
  D. A warm tile LRU cache hit performs no geometry work.
  E. Index eviction + rebuild still encodes correctly.
  F. The index path is deterministic (the basis for single-flight sharing).
  G. The index geometry arrays stay bounded and parallel to the feature list.
"""
import math

import pytest

from app.services.mvt import (
    SpatialIndexCache,
    TileLRUCache,
    _transform_z0,
    build_spatial_index_entry,
    encode_tile,
    encode_tile_from_index,
)
import app.services.mvt as mvt_mod


# ─── helpers ─────────────────────────────────────────────────────────────────


def _feature(gtype, coords, props=None):
    return {
        "type": "Feature",
        "geometry": {"type": gtype, "coordinates": coords},
        "properties": props or {},
    }


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _tile_of(z, lon, lat):
    """Tile (z,x,y) containing lon/lat — mirrors mvt._project."""
    world = 256.0 * (1 << z)
    px = (lon + 180.0) / 360.0 * world
    lat_rad = math.radians(lat)
    py = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world
    return (z, int(px // 256), int(py // 256))


def _first_lonlat(coords):
    cur = coords
    while isinstance(cur, list) and len(cur) and isinstance(cur[0], list):
        cur = cur[0]
    return cur[0], cur[1]


class _WorkCounter:
    """Count _shapely_geom_from_coords and z0-transform calls on the mvt module."""

    def __init__(self, monkeypatch):
        self.construct = 0
        self.transform_z0 = 0
        orig_construct = mvt_mod._shapely_geom_from_coords
        orig_transform = mvt_mod.shapely.transform

        def c_construct(gtype, coords):
            self.construct += 1
            return orig_construct(gtype, coords)

        def c_transform(geom, func):
            if func is _transform_z0:
                self.transform_z0 += 1
            return orig_transform(geom, func)

        monkeypatch.setattr(mvt_mod, "_shapely_geom_from_coords", c_construct)
        monkeypatch.setattr(mvt_mod.shapely, "transform", c_transform)

    def snapshot(self):
        return self.construct, self.transform_z0


# ─── fixtures per geometry type (criterion C) ────────────────────────────────

BEIJING = (116.40, 39.92)


def _line(n=24, span=0.05):
    bx, by = BEIJING
    return [[bx + span * j / n, by + span * j / (n * 2)] for j in range(n)]


GEOMETRY_FIXTURES = {
    "Point": _feature("Point", [BEIJING[0], BEIJING[1]], {"id": "pt"}),
    "MultiPoint": _feature("MultiPoint", [[BEIJING[0], BEIJING[1]],
                                          [BEIJING[0] + 0.01, BEIJING[1] + 0.01]], {"id": "mpt"}),
    "LineString": _feature("LineString", _line(), {"id": "ln", "cat": 1}),
    "MultiLineString": _feature("MultiLineString", [_line(), _line(span=0.03)], {"id": "mln"}),
    "Polygon": _feature(
        "Polygon",
        [
            # exterior ring (CCW in geographic terms)
            [[116.30, 39.85], [116.50, 39.85], [116.50, 40.00], [116.30, 40.00], [116.30, 39.85]],
            # hole
            [[116.35, 39.90], [116.40, 39.90], [116.40, 39.95], [116.35, 39.95], [116.35, 39.90]],
        ],
        {"id": "poly"},
    ),
    "MultiPolygon": _feature(
        "MultiPolygon",
        [
            [[[116.30, 39.85], [116.40, 39.85], [116.40, 39.95], [116.30, 39.95], [116.30, 39.85]]],
            [[[116.45, 39.85], [116.55, 39.85], [116.55, 39.95], [116.45, 39.95], [116.45, 39.85]]],
        ],
        {"id": "mpoly"},
    ),
}


# ─── C. byte-identical output per geometry type ─────────────────────────────


@pytest.mark.parametrize("gtype", list(GEOMETRY_FIXTURES))
@pytest.mark.parametrize("z", [3, 12, 14, 16])  # spans simplify (z<14) and no-simplify (z>=14)
def test_index_path_byte_identical_to_coordinate_path(gtype, z):
    """encode_tile_from_index must equal encode_tile(query_tile) byte-for-byte."""
    feat = GEOMETRY_FIXTURES[gtype]
    fc = _fc(feat)
    entry = build_spatial_index_entry(("s", "r"), fc)
    assert len(entry.features) == 1
    tile = _tile_of(z, *_first_lonlat(feat["geometry"]["coordinates"]))
    via_index = encode_tile_from_index(entry, *tile)
    via_coords = encode_tile(entry.query_tile(*tile), *tile)
    assert via_index == via_coords, f"{gtype} z={z} diverged"
    assert via_index != b"", f"{gtype} z={z} produced an empty tile"


def test_mixed_collection_byte_identical_across_adjacent_tiles():
    """A mixed collection encodes identically on both paths for many tiles."""
    fc = _fc(*GEOMETRY_FIXTURES.values())
    entry = build_spatial_index_entry(("s", "r2"), fc)
    for z in (4, 11, 13, 15):
        seen = set()
        for feat in fc["features"]:
            t = _tile_of(z, *_first_lonlat(feat["geometry"]["coordinates"]))
            if t in seen:
                continue
            seen.add(t)
            assert encode_tile_from_index(entry, *t) == encode_tile(entry.query_tile(*t), *t)


# ─── C-extras: tricky correctness invariants ─────────────────────────────────


@pytest.mark.parametrize("z", [12, 15])  # simplify and no-simplify regimes
def test_invalid_polygon_dropped_identically_on_both_paths(z):
    """A self-intersecting (bowtie) polygon is dropped by the is_valid gate on
    both the coordinate path and the index path — never emitted."""
    bx, by = BEIJING
    # bowtie: edges cross at the center → invalid
    bowtie = _feature(
        "Polygon",
        [[[bx, by], [bx + 0.02, by + 0.02], [bx + 0.02, by],
          [bx, by + 0.02], [bx, by]]],
        {"id": "bowtie"},
    )
    fc = _fc(bowtie)
    entry = build_spatial_index_entry(("s", "rbow"), fc)
    assert len(entry.features) == 1  # kept in the index (non-empty)...
    tile = _tile_of(z, bx, by)
    via_index = encode_tile_from_index(entry, *tile)
    via_coords = encode_tile(entry.query_tile(*tile), *tile)
    assert via_index == via_coords == b""  # ...but dropped at encode on both paths


def test_multipolygon_with_hole_byte_identical():
    """A MultiPolygon whose member carries an interior ring encodes identically."""
    bx, by = BEIJING
    mpoly_hole = _feature(
        "MultiPolygon",
        [
            # member 0: square with a hole
            [
                [[bx, by], [bx + 0.04, by], [bx + 0.04, by + 0.04], [bx, by + 0.04], [bx, by]],
                [[bx + 0.01, by + 0.01], [bx + 0.02, by + 0.01], [bx + 0.02, by + 0.02],
                 [bx + 0.01, by + 0.02], [bx + 0.01, by + 0.01]],
            ],
            # member 1: simple square (disjoint)
            [[[bx + 0.06, by], [bx + 0.08, by], [bx + 0.08, by + 0.02], [bx + 0.06, by + 0.02], [bx + 0.06, by]]],
        ],
        {"id": "mpoly_hole"},
    )
    fc = _fc(mpoly_hole)
    entry = build_spatial_index_entry(("s", "rmh"), fc)
    for z in (11, 14, 16):
        t = _tile_of(z, bx, by)
        assert encode_tile_from_index(entry, *t) == encode_tile(entry.query_tile(*t), *t)
        assert encode_tile_from_index(entry, *t) != b""


def test_polygon_straddling_tile_boundary_byte_identical():
    """A polygon clipped to a tile that catches only a slice must match exactly."""
    bx, by = BEIJING
    # a large polygon spanning several z=15 tiles
    big = _feature(
        "Polygon",
        [[[bx, by], [bx + 0.5, by], [bx + 0.5, by + 0.5], [bx, by + 0.5], [bx, by]]],
        {"id": "big"},
    )
    fc = _fc(big)
    entry = build_spatial_index_entry(("s", "rbig"), fc)
    origin_tile = _tile_of(15, bx, by)
    # encode the origin tile and three neighbors — each intersects a different slice
    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        t = (15, origin_tile[1] + dx, origin_tile[2] + dy)
        via_index = encode_tile_from_index(entry, *t)
        via_coords = encode_tile(entry.query_tile(*t), *t)
        assert via_index == via_coords


# ─── A + B. no per-candidate rebuild; projection converges to build time ─────


def test_index_path_eliminates_geometry_construction_per_tile(monkeypatch):
    """After build, encoding adjacent tiles does zero GeoJSON→Shapely builds."""
    counter = _WorkCounter(monkeypatch)
    # many multi-vertex line/poly features so construction cost is real
    feats = [_feature("LineString", _line(n=30, span=0.02 * (i + 1)), {"id": i}) for i in range(50)]
    feats += [_feature("Polygon", [
        [[116.0 + i * 0.01, 39.9], [116.05 + i * 0.01, 39.9],
         [116.05 + i * 0.01, 40.0], [116.0 + i * 0.01, 40.0], [116.0 + i * 0.01, 39.9]]]
    ) for i in range(50)]
    fc = _fc(*feats)
    entry = build_spatial_index_entry(("s", "r3"), fc)
    build_construct, build_transform = counter.snapshot()
    assert build_construct == len(feats)          # built once per feature
    assert build_transform == len(feats)          # projected once per feature

    # encode a cluster of adjacent tiles at z<14 (simplify) and z>=14 (no simplify)
    tiles = [_tile_of(12, 116.1 + d * 0.02, 39.95) for d in range(4)]
    for t in tiles:
        encode_tile_from_index(entry, *t)
    tile_construct, _ = counter.snapshot()
    # construction must NOT grow with the number of tiles (criterion A/B)
    assert tile_construct == build_construct, "construction repeated per tile candidate"

    # z>=14 must also avoid the z0 projection per tile
    build_transform2 = counter.transform_z0
    hi_tiles = [_tile_of(15, 116.1 + d * 0.02, 39.95) for d in range(3)]
    for t in hi_tiles:
        encode_tile_from_index(entry, *t)
    _, hi_transform = counter.snapshot()
    assert hi_transform == build_transform2, "z>=14 z0 projection repeated per tile"


def test_projection_work_is_o_features_at_build_plus_o_candidates_at_tiles(monkeypatch):
    """The expensive projection is paid at build; tile work is clipping/encoding only."""
    counter = _WorkCounter(monkeypatch)
    fc = _fc(*[_feature("LineString", _line(n=20, span=0.01 * i), {"id": i}) for i in range(30)])
    entry = build_spatial_index_entry(("s", "r4"), fc)
    n_features = len(entry.features)
    base_construct, base_transform = counter.snapshot()
    assert base_construct == n_features
    assert base_transform == n_features

    # repeated tiles: total construction stays flat regardless of request count
    tile = _tile_of(13, *_first_lonlat(fc["features"][0]["geometry"]["coordinates"]))
    for _ in range(5):
        encode_tile_from_index(entry, *tile)
    final_construct, _ = counter.snapshot()
    assert final_construct == n_features  # 5 requests → still 0 extra constructions


# ─── D. warm tile LRU hit performs no geometry work ──────────────────────────


def test_warm_tile_cache_hit_does_no_geometry_work(monkeypatch):
    """A warm tile LRU hit must not invoke the encode path at all."""
    fc = _fc(*GEOMETRY_FIXTURES.values())
    entry = build_spatial_index_entry(("s", "r5"), fc)
    tile = _tile_of(12, *_first_lonlat(GEOMETRY_FIXTURES["Point"]["geometry"]["coordinates"]))
    cache = TileLRUCache(max_tiles=8, max_bytes=1 << 20)

    calls = {"encode": 0}

    def encode_spy(ent, z, x, y):
        calls["encode"] += 1
        return encode_tile_from_index(ent, z, x, y)

    # mirrors the route: cache hit short-circuits before encode
    def serve(t):
        cached = cache.get(("s", "r5", *t))
        if cached is not None:
            return cached
        import gzip
        body = gzip.compress(encode_spy(entry, *t))
        cache.put(("s", "r5", *t), body)
        return body

    serve(tile)              # cold: encodes once
    assert calls["encode"] == 1
    serve(tile)              # warm: cache hit, no encode
    serve(tile)              # warm again
    assert calls["encode"] == 1, "warm cache hit performed geometry work"


# ─── E. eviction / rebuild ───────────────────────────────────────────────────


def test_index_eviction_and_rebuild_still_encodes():
    fc = _fc(*GEOMETRY_FIXTURES.values())
    cache = SpatialIndexCache(max_refs=1)
    key = ("s", "r6")
    e1 = cache.get_or_build(key, lambda: build_spatial_index_entry(key, fc))
    tile = _tile_of(13, *_first_lonlat(GEOMETRY_FIXTURES["LineString"]["geometry"]["coordinates"]))
    first = encode_tile_from_index(e1, *tile)
    # evict by inserting another ref (max_refs=1)
    cache.get_or_build(("s", "other"), lambda: build_spatial_index_entry(("s", "other"), _fc(GEOMETRY_FIXTURES["Point"])))
    assert cache.get(key) is None
    # rebuild from scratch
    e2 = cache.get_or_build(key, lambda: build_spatial_index_entry(key, fc))
    assert e2 is not e1
    rebuilt = encode_tile_from_index(e2, *tile)
    assert rebuilt == first  # deterministic after rebuild


# ─── F. determinism (basis for single-flight sharing) ────────────────────────


def test_index_path_is_deterministic():
    """Same index + tile always yields identical bytes (single-flight correctness)."""
    fc = _fc(*GEOMETRY_FIXTURES.values())
    entry = build_spatial_index_entry(("s", "r7"), fc)
    tile = _tile_of(10, *_first_lonlat(GEOMETRY_FIXTURES["Polygon"]["geometry"]["coordinates"]))
    a = encode_tile_from_index(entry, *tile)
    b = encode_tile_from_index(entry, *tile)
    c = encode_tile(entry.query_tile(*tile), *tile)
    assert a == b == c


# ─── G. bounded, parallel geometry arrays ────────────────────────────────────


def test_index_geometry_arrays_are_bounded_and_parallel():
    fc = _fc(*GEOMETRY_FIXTURES.values())
    entry = build_spatial_index_entry(("s", "r8"), fc)
    n = len(entry.features)
    assert n == len(GEOMETRY_FIXTURES)
    # z0 and lon/lat arrays are plain lists, parallel to features, not unbounded
    assert isinstance(entry.geoms, list) and len(entry.geoms) == n
    assert isinstance(entry.geoms_lonlat, list) and len(entry.geoms_lonlat) == n
    assert isinstance(entry.bounds, list) and len(entry.bounds) == n
    # each entry carries a non-None prepared geometry
    assert all(g is not None for g in entry.geoms)
    assert all(g is not None for g in entry.geoms_lonlat)


def test_geometry_collection_not_in_index_but_skipped_in_encode():
    """GeometryCollections are excluded from the index and never encoded."""
    gc = _feature("GeometryCollection", None, {"id": "gc"})
    fc = _fc(GEOMETRY_FIXTURES["Point"], gc)
    entry = build_spatial_index_entry(("s", "r9"), fc)
    assert len(entry.features) == 1  # GC dropped at index time
    tile = _tile_of(13, *_first_lonlat(GEOMETRY_FIXTURES["Point"]["geometry"]["coordinates"]))
    assert encode_tile_from_index(entry, *tile) != b""


# ─── no-shapely fallback still works through the index path ──────────────────


def test_index_path_falls_back_when_shapely_unavailable(monkeypatch):
    """encode_tile_from_index delegates to encode_tile(query_tile) without shapely,
    and still produces a non-empty tile for an in-bounds feature."""
    monkeypatch.setattr(mvt_mod, "_SHAPELY", False)
    fc = _fc(GEOMETRY_FIXTURES["Point"], GEOMETRY_FIXTURES["LineString"])
    entry = build_spatial_index_entry(("s", "r10"), fc)
    assert entry.geoms is None and entry.geoms_lonlat is None
    tile = _tile_of(13, *_first_lonlat(GEOMETRY_FIXTURES["Point"]["geometry"]["coordinates"]))
    out = encode_tile_from_index(entry, *tile)
    # fallback runs without error AND actually encodes the in-bounds point
    assert out == encode_tile(entry.query_tile(*tile), *tile)
    assert out != b""


def test_index_path_line_poly_degrades_to_pure_python_on_shapely_failure(monkeypatch, caplog):
    """A GEOS failure on the index line/poly path falls back to the coordinate
    path (→ pure-python) instead of failing the whole tile. Preserves the
    resilience invariant the scope requires."""
    import logging

    fc = _fc(GEOMETRY_FIXTURES["LineString"])
    entry = build_spatial_index_entry(("s", "r11"), fc)
    tile = _tile_of(13, *_first_lonlat(fc["features"][0]["geometry"]["coordinates"]))

    def _raising(gtype, geom_lonlat, geom_z0, z, x, y):
        raise RuntimeError("simulated GEOS failure")

    # the fallback in encode_tile_from_index routes through _encode_geometry,
    # which retries shapely (raising again) then uses the pure-python encoder.
    # Make the *coordinate* shapely path also raise so the pure fallback is hit.
    def _raising_shapely(gtype, coords, z, x, y):
        raise RuntimeError("simulated GEOS failure")

    monkeypatch.setattr(mvt_mod, "_encode_line_polygon_from_geoms", _raising)
    monkeypatch.setattr(mvt_mod, "_encode_line_polygon_shapely", _raising_shapely)
    with caplog.at_level(logging.WARNING, logger="app.services.mvt"):
        out = encode_tile_from_index(entry, *tile)
    # did not raise; pure-python encoder produced the line geometry
    assert out != b""
    assert any("pure-python fallback" in r.message for r in caplog.records)
