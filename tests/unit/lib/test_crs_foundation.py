"""Reference tests for the CRS-foundation hardening (Slice 1).

Covers: polar stereographic fallback (V-F05), multi-zone span warning
(V-F04), antimeridian robustness (V-F06), and the geographic-CRS centroid
guard (V-F16) in ``app/lib/geo_processor/core.py`` /
``app/lib/geo_analysis/_vector.py``.
"""
import logging

import geopandas as gpd
import pytest

from app.lib.geo_analysis._vector import extract_centroids
from app.lib.geo_processor.core import (
    GeoAnalysisResult,
    clear_utm_cache,
    to_utm_gdf,
)


def _fc(point_lonlat):
    """Build a tiny Point FeatureCollection in EPSG:4326."""
    lon, lat = point_lonlat
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"v": 1.0},
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clean_utm_cache():
    """Each test sees a cold UTM cache (identity-keyed, would otherwise leak)."""
    clear_utm_cache()
    yield
    clear_utm_cache()


def test_polar_north_uses_polar_stereographic():
    """lat > 84N: UTM is undefined → must fall back to EPSG:3413, not a bogus UTM zone."""
    gdf, crs = to_utm_gdf(_fc((10.0, 85.0)))
    assert crs == "EPSG:3413", crs
    assert gdf.crs.to_epsg() == 3413
    # Geometry must be in projected (metric) coordinates, not raw degrees.
    assert abs(gdf.geometry.x.iloc[0]) > 1000 or abs(gdf.geometry.y.iloc[0]) > 1000


def test_polar_south_uses_polar_stereographic():
    gdf, crs = to_utm_gdf(_fc((10.0, -85.0)))
    assert crs == "EPSG:3031", crs
    assert gdf.crs.to_epsg() == 3031


def test_mid_latitude_still_uses_utm():
    """Regression guard: the polar branch must NOT trigger for normal data."""
    gdf, crs = to_utm_gdf(_fc((116.4, 39.9)))  # Beijing
    assert crs.startswith("EPSG:326") or crs.startswith("EPSG:327"), crs


def test_large_span_emits_warning(caplog):
    """A >6° longitudinal span must surface an honesty warning (V-F04)."""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.0, 39.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [140.0, 39.0]}, "properties": {}},
        ],
    }
    with caplog.at_level(logging.WARNING, logger="app.lib.geo_processor.core"):
        to_utm_gdf(fc)
    assert any("exceeds one UTM zone" in r.message for r in caplog.records), [
        r.message for r in caplog.records
    ]


def test_small_span_no_warning(caplog):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.5, 39.0]}, "properties": {}},
        ],
    }
    with caplog.at_level(logging.WARNING, logger="app.lib.geo_processor.core"):
        to_utm_gdf(fc)
    assert not any("exceeds one UTM zone" in r.message for r in caplog.records)


def test_antimeridian_span_does_not_crash():
    """Data straddling ±180° must not raise (V-F06). Zone may be imperfect but
    must be a valid northern-hemisphere UTM code."""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [179.5, 65.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-179.5, 65.0]}, "properties": {}},
        ],
    }
    gdf, crs = to_utm_gdf(fc)
    assert crs.startswith("EPSG:"), crs
    assert gdf.crs.is_projected


def test_extract_centroids_warns_on_geographic_crs(caplog):
    gdf = gpd.GeoDataFrame(
        {"v": [1.0]},
        geometry=gpd.points_from_xy([116.0], [39.0]),
        crs="EPSG:4326",
    )
    with caplog.at_level(logging.WARNING, logger="app.lib.geo_analysis._vector"):
        arr = extract_centroids(gdf)
    assert arr.shape == (1, 2)
    assert any("geographic" in r.message for r in caplog.records)


def test_extract_centroids_silent_on_projected_crs(caplog):
    gdf = gpd.GeoDataFrame(
        {"v": [1.0]},
        geometry=gpd.points_from_xy([500000.0], [4400000.0]),
        crs="EPSG:32650",
    )
    with caplog.at_level(logging.WARNING, logger="app.lib.geo_analysis._vector"):
        extract_centroids(gdf)
    assert not caplog.records


def test_geoanalysis_result_contract_unchanged():
    """Sanity: GeoAnalysisResult dataclass shape is stable for callers."""
    r = GeoAnalysisResult(True, {"x": 1}, "ok")
    assert r.success is True
    assert r.error_message is None
    resp = r.to_llm_response()
    assert resp["success"] is True
    assert resp["data"] == {"x": 1}
