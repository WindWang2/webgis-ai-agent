"""Metadata truthfulness tests (Section 27/28/29): CRS, geometry type,
feature-count — unknown must stay unknown, never fabricated."""
import pytest

from app.services.data_fabric.metadata import (
    classify_feature_type,
    normalize_crs,
    normalize_feature_count,
    normalize_geometry_type,
)


# ── CRS ──────────────────────────────────────────────────────────────────────


def test_crs_passthrough_epsg():
    assert normalize_crs("EPSG:4326") == "EPSG:4326"
    assert normalize_crs("epsg:3857") == "epsg:3857"


def test_crs_ogc_uri_to_epsg():
    uri = "http://www.opengis.net/def/crs/EPSG/0/3857"
    assert normalize_crs(uri) == "EPSG:3857"


def test_crs_crs84_marker_preserved():
    uri = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
    assert normalize_crs(uri) == "CRS84"


def test_crs_bare_code_assumes_epsg():
    assert normalize_crs("4326") == "EPSG:4326"


def test_crs_unknown_is_none_not_fabricated():
    """The P0: undeclared CRS must NOT become EPSG:4326."""
    assert normalize_crs(None) is None
    assert normalize_crs("") is None
    assert normalize_crs("unknown") is None
    assert normalize_crs("UNKNOWN") is None


def test_crs_unrecognized_kept_verbatim():
    assert normalize_crs("ESRI:102100") == "ESRI:102100"


# ── geometry type ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("Point", "Point"),
    ("MULTIPOLYGON", "MultiPolygon"),
    ("esriGeometryPolygon", "Polygon"),
    ("esriGeometryPolyline", "MultiLineString"),
    ("esriGeometryPoint", "Point"),
    ("Feature", "Geometry"),
    ("raster", "Raster"),
    ("TilePyramid", "TilePyramid"),
    ("WMS", "TilePyramid"),
    ("", "unknown"),
    (None, "unknown"),
    ("nonsense", "unknown"),
])
def test_normalize_geometry_type(raw, expected):
    assert normalize_geometry_type(raw) == expected


# ── feature type classification ─────────────────────────────────────────────


def test_tile_pyramid_not_mislabeled_vector():
    """The bug: 'TilePyramid' contained no 'raster' → was classed vector."""
    assert classify_feature_type("TilePyramid") == "tile"
    assert classify_feature_type("PMTiles") == "tile"


def test_raster_classified():
    assert classify_feature_type("Raster") == "raster"


def test_vector_classified():
    assert classify_feature_type("Polygon") == "vector"
    assert classify_feature_type("esriGeometryPoint") == "vector"


def test_unknown_classified():
    assert classify_feature_type(None) == "unknown"


# ── feature count ────────────────────────────────────────────────────────────


def test_feature_count_known():
    assert normalize_feature_count(0) == 0          # genuine zero
    assert normalize_feature_count(42) == 42
    assert normalize_feature_count("7") == 7


def test_feature_count_unknown_is_none():
    """0-from-unknown fabrication replaced by truthful None."""
    assert normalize_feature_count(None) is None
    assert normalize_feature_count("n/a") is None
    assert normalize_feature_count(-1) is None
