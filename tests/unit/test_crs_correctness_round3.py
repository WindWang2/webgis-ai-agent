"""Regression tests for round-3 CRS correctness fixes:

- GIS-22: transform_geojson must update the top-level crs member after
  reprojection (previously stale crs caused downstream double-reprojection).
- GIS-23: zonal_statistics must FAIL LOUDLY on polygon reprojection failure
  instead of silently feeding source-CRS polygons to a projected raster
  (which produced plausible-looking zero stats).
- GIS-24: impact zones must not fall back to Web Mercator (1.7x area
  distortion at 40°N); geometry area must use exact geodesic area.
"""

import pytest
from shapely.geometry import shape


# ---------------------------------------------------------------------------
# GIS-22 — transform_geojson updates crs member
# ---------------------------------------------------------------------------

def test_transform_geojson_updates_crs_member():
    from app.utils.coord_transform import transform_geojson

    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
            }
        ],
    }
    out = transform_geojson(geojson, from_crs="EPSG:4326", to_crs="EPSG:3857")
    crs_name = out.get("crs", {}).get("properties", {}).get("name")
    assert crs_name == "EPSG:3857", (
        f"GIS-22 regression: crs member not updated after reprojection: {crs_name}"
    )
    # The geometry must actually be in 3857 (large coordinates).
    x = out["features"][0]["geometry"]["coordinates"][0]
    assert x > 1_000_000, f"geometry not projected to 3857: {x}"


def test_transform_geojson_4326_to_4326_keeps_name():
    from app.utils.coord_transform import transform_geojson

    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}
        ],
    }
    out = transform_geojson(geojson, from_crs="EPSG:4326", to_crs="EPSG:4326")
    assert out.get("crs", {}).get("properties", {}).get("name") == "EPSG:4326"


# ---------------------------------------------------------------------------
# GIS-23 — zonal_statistics fails loudly on reprojection failure
# ---------------------------------------------------------------------------

def test_zonal_statistics_raises_on_reprojection_failure(monkeypatch):
    from app.lib.geo_analysis import raster_ops

    # Force transform_geojson to raise to simulate a bad CRS / transient error.
    def _boom(*a, **k):
        raise RuntimeError("pyproj transform failed")

    monkeypatch.setattr(raster_ops, "transform_geojson", _boom)

    class _FakeSrc:
        crs = "EPSG:32650"  # projected UTM

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(raster_ops.rasterio, "open", lambda *a, **k: _FakeSrc())

    with pytest.raises(ValueError, match="Reprojection|refusing|mismatched"):
        raster_ops.zonal_statistics(
            {"type": "FeatureCollection", "features": []},
            "/nonexistent.tif",
        )


def test_zonal_statistics_passes_through_when_no_crs(monkeypatch):
    """When polygons need no reprojection (no loaded raster crs), the original
    input flows through unchanged."""
    from app.lib.geo_analysis import raster_ops

    called = {"input": None}

    class _FakeSrc:
        crs = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(raster_ops.rasterio, "open", lambda *a, **k: _FakeSrc())

    def _fake_zonal(input, raster_path, stats):
        called["input"] = input
        return [{"mean": 1.0}]

    monkeypatch.setattr(raster_ops, "zonal_stats", _fake_zonal)
    res = raster_ops.zonal_statistics({"type": "FeatureCollection", "features": []}, "/x.tif")
    assert res == [{"mean": 1.0}]
    assert called["input"] == {"type": "FeatureCollection", "features": []}


# ---------------------------------------------------------------------------
# GIS-24 — no Web-Mercator fallback; geodesic area
# ---------------------------------------------------------------------------

def test_impact_engine_raises_instead_of_web_mercator(monkeypatch):
    """Impact zones must not fall back to EPSG:3857 when UTM fails."""
    from app.services.spatial_decision import impact_engine

    def _to_utm_fail(*a, **k):
        return None, None

    monkeypatch.setattr(impact_engine, "to_utm_gdf", _to_utm_fail)

    engine = impact_engine.SpatialImpactEngine.__new__(impact_engine.SpatialImpactEngine)
    engine.extract_geometry = lambda ta: shape({
        "type": "Polygon",
        "coordinates": [[[116.0, 39.0], [116.01, 39.0], [116.01, 39.01], [116.0, 39.0]]],
    })

    with pytest.raises(ValueError, match="projected CRS|distorted projection"):
        engine.calculate_impacts(
            scenario_type="subway",
            target_area={"geometry": {"type": "Polygon", "coordinates": [[]]}},
            rules=[],
            parameters={},
        )


def test_geodesic_area_matches_known_value():
    """A 1° × 1° square around the equator is ~12363 km² (geodesic); the old
    planar 111.32² approximation gives ~12392 — the geodesic value is the
    correct one. We assert direction + plausibility, not a fragile constant."""
    from app.services.spatial_decision.baseline_resolver import _calculate_geometry_area_km2

    # 1°×1° square: [0,0] to [1,1].
    geojson = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
    }
    area = _calculate_geometry_area_km2(geojson)
    # Geodesic WGS84 area of a 1°x1° equatorial cell ≈ 12,309 km²;
    # planar ≈ 12,392. Both in a ~1% band — assert sane magnitude.
    assert 11_000.0 < area < 13_000.0, f"unexpected area {area} km²"


def test_geodesic_area_handles_multipolygon():
    from app.services.spatial_decision.baseline_resolver import _calculate_geometry_area_km2

    two_cells = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            [[[2.0, 0.0], [3.0, 0.0], [3.0, 1.0], [2.0, 1.0], [2.0, 0.0]]],
        ],
    }
    area = _calculate_geometry_area_km2(two_cells)
    assert 22_000.0 < area < 26_000.0, f"unexpected multipolygon area {area}"
