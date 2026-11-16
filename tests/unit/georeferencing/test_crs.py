"""G48 WP10: CRS & Coordinate Transform Verification Tests (R17-R18)."""
from __future__ import annotations

import math

import pytest
from pyproj import Transformer

from app.schemas.georeferencing_schema import GeorefErrorCode
from app.services.georeferencing.crs import (
    convert_wgs84_to_target,
    validate_metric_projected_crs,
)
from app.services.georeferencing.service import GeorefDomainError


def test_r17_crs_metric_projected_accept_and_reject():
    for epsg in ("EPSG:3857", "EPSG:32648", "EPSG:32650", "EPSG:4547"):
        res = validate_metric_projected_crs(epsg)
        assert res.canonical_crs == epsg
        assert res.unit_factor == pytest.approx(1.0, abs=1e-9)

    for bad in ("EPSG:4326", "EPSG:2263", "EPSG:4979", "EPSG:0", "", "NOT_A_CRS"):
        with pytest.raises(GeorefDomainError) as exc_info:
            validate_metric_projected_crs(bad)
        assert exc_info.value.code == GeorefErrorCode.UNSUPPORTED_TARGET_CRS


def test_r18_lonlat_always_xy_and_area_warning():
    lon, lat = 116.3912, 39.9062
    prov = convert_wgs84_to_target(lon, lat, "EPSG:32650")
    ref_x, ref_y = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True).transform(lon, lat)
    assert abs(prov.map_x - ref_x) < 1e-6
    assert abs(prov.map_y - ref_y) < 1e-6
    assert prov.warnings == []

    prov_far = convert_wgs84_to_target(-74.006, 40.7128, "EPSG:32650")
    assert any("area of use" in w.lower() for w in prov_far.warnings)

    for bad_lon, bad_lat in [(185.0, 30.0), (116.0, 95.0), (math.nan, 30.0)]:
        with pytest.raises(GeorefDomainError) as exc_info:
            convert_wgs84_to_target(bad_lon, bad_lat, "EPSG:32650")
        assert exc_info.value.code == GeorefErrorCode.INVALID_GCP_COORDS
