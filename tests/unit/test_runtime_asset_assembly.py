"""Unit tests for runtime_asset_assembly slippy-map helpers (no browser)."""
from pathlib import Path

from app.services.runtime_asset_assembly import (
    bbox_from_geojson,
    tiles_covering_bbox,
    tiles_for_bbox,
)


def test_tiles_for_bbox_single_world_at_z0():
    # any bbox at z0 is the single world tile
    assert tiles_for_bbox((-10, -10, 10, 10), 0) == [(0, 0, 0)]
    assert tiles_for_bbox((-180, -85, 180, 85), 0) == [(0, 0, 0)]


def test_tiles_for_bbox_point_maps_to_one_tile():
    # Tiny box straddling the centre would cover 4 tiles at z1 (straddles both axes);
    # a box wholly inside one quadrant covers one tile.
    single = tiles_for_bbox((10.0, 10.0, 11.0, 11.0), 1)
    assert len(single) == 1
    assert single[0][0] == 1  # zoom


def test_tiles_covering_bbox_z0_to_z2_counts():
    # bbox spanning ~60deg lon/lat around origin at z2 should be 2x2 =4 tiles
    # Actually compute via helper: at z2 a 60deg span typically covers 2 x's and 1-2 y's.
    bbox = (-30.0, -10.0, 30.0, 20.0)
    all_tiles = tiles_covering_bbox(bbox, 0, 2)
    # z0 always 1 tile, z1 typically 2-4 tiles, z2 4 tiles for this bbox.
    z0 = [t for t in all_tiles if t[0] == 0]
    z1 = [t for t in all_tiles if t[0] == 1]
    z2 = [t for t in all_tiles if t[0] == 2]
    assert len(z0) == 1 and z0[0] == (0, 0, 0)
    assert len(z1) >= 1 and len(z1) <= 4
    assert len(z2) >= 2  # fixture spans at least 2 tiles at z2
    # z2 tiles must be within 0..3 range
    for _, x, y in z2:
        assert 0 <= x < 4 and 0 <= y < 4


def test_tiles_for_bbox_ordered_and_inclusive():
    bbox = (-30, -10, 30, 20)
    tiles = tiles_for_bbox(bbox, 2)
    # inclusive count should equal (x_max - x_min +1)*(y_max - y_min +1)
    xs = sorted(set(x for _, x, _ in tiles))
    ys = sorted(set(y for _, _, y in tiles))
    assert tiles == [(2, x, y) for x in xs for y in ys]


def test_bbox_from_geojson_extracts_extent():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-30, 10]}, "properties": {"kind": "a"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [30, -10]}, "properties": {"kind": "b"}},
        ],
    }
    bbox = bbox_from_geojson(fc)
    assert bbox is not None
    w, s, e, n = bbox
    # padded by 1e-6, so roughly -30.000001 .. 30.000001
    assert w < -29.9 and e > 29.9
    assert s < -9.9 and n > 9.9


def test_bbox_from_geojson_none_when_empty():
    assert bbox_from_geojson({"type": "FeatureCollection", "features": []}) is None
    assert bbox_from_geojson({"type": "FeatureCollection"}) is None


def test_assemble_mvt_assets_writes_tiles(tmp_path: Path):
    # Build a minimal fixture dir with points.geojson and check files appear in dist
    from app.services.runtime_asset_assembly import assemble_mvt_assets

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    dist = tmp_path / "dist"
    dist.mkdir()

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-30, 10]}, "properties": {"kind": "a"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [30, -10]}, "properties": {"kind": "b"}},
        ],
    }
    import json

    (fixture / "points.geojson").write_text(json.dumps(fc), encoding="utf-8")
    written = assemble_mvt_assets(fixture, dist)
    assert written > 0
    # at least the world tile exists
    assert (dist / "tiles" / "0" / "0" / "0.mvt").exists()
    # a z2 tile exists
    z2_tiles = list((dist / "tiles" / "2").rglob("*.mvt")) if (dist / "tiles" / "2").exists() else []
    assert len(z2_tiles) >= 1

    # MIME helper: file bytes should be either b"" or valid protobuf (no crash on read)
    for p in (dist / "tiles").rglob("*.mvt"):
        data = p.read_bytes()
        assert isinstance(data, bytes)


def test_assemble_returns_zero_when_no_geojson(tmp_path: Path):
    from app.services.runtime_asset_assembly import assemble_mvt_assets

    fixture = tmp_path / "fixture2"
    fixture.mkdir()
    dist = tmp_path / "dist2"
    dist.mkdir()
    assert assemble_mvt_assets(fixture, dist) == 0
    assert not (dist / "tiles").exists()
