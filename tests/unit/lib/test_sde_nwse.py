"""Issue #693 item 6: SDE degenerate branch delta==0, sum_xy<0 => -45° (NW-SE).

Real WGS84 polygons rarely hit exact delta==0 after UTM projection due to
ellipsoidal distortion; the branch is tested via controlled UTM coords by
mocking to_utm_gdf/extract_centroids.
"""

from unittest.mock import patch
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

from app.lib.geo_analysis.statistics import calculate_sde

# Controlled metric coords with exact delta==0, sum_xy<0: anti-diagonal
_MOCK_COORDS = np.array([[0, 0], [1, -1], [-1, 1], [0.5, -0.5], [-0.5, 0.5]], float)


def _fake_to_utm(*_a, **_kw):
    gdf = gpd.GeoDataFrame(geometry=[Point(float(x), float(y)) for x, y in _MOCK_COORDS], crs="EPSG:32633")
    return gdf, "EPSG:32633"


def test_sde_nwse_degenerate_branch():
    """delta==0, sum_xy<0 must be -45° (135° mod 180) -> NW-SE, not 0°/East-West."""
    dummy_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}} for _ in _MOCK_COORDS
    ]}
    with patch("app.lib.geo_analysis.statistics.to_utm_gdf", side_effect=_fake_to_utm):
        with patch("app.lib.geo_analysis.statistics.extract_centroids", return_value=_MOCK_COORDS):
            res = calculate_sde(dummy_fc)
    assert res.success, res.summary
    assert res.data["properties"]["angle_deg"] == float(135.0)
    assert res.data["properties"]["direction"] == "North-West to South-East"


def test_sde_ne_positive_diagonal_still_45():
    coords = np.array([[0, 0], [1, 1], [-1, -1], [0.5, 0.5], [-0.5, -0.5]], float)

    def _fake2(*_a, **_kw):
        return gpd.GeoDataFrame(geometry=[Point(float(x), float(y)) for x, y in coords], crs="EPSG:32633"), "EPSG:32633"

    dummy_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}} for _ in coords
    ]}
    with patch("app.lib.geo_analysis.statistics.to_utm_gdf", side_effect=_fake2):
        with patch("app.lib.geo_analysis.statistics.extract_centroids", return_value=coords):
            res = calculate_sde(dummy_fc)
    assert res.success
    assert 30 < res.data["properties"]["angle_deg"] < 60
    assert res.data["properties"]["direction"] == "North-East to South-West"


def test_sde_branch_sign_pure():
    """Pure branch check: old code returned 0 for sum_xy<0, new returns -45°."""
    def old_theta(sx2, sy2, sxy):
        d = sx2 - sy2
        if d == 0:
            return np.pi / 4 if sxy > 0 else 0
        return 0.5 * np.arctan2(2 * sxy, d)

    def new_theta(sx2, sy2, sxy):
        d = sx2 - sy2
        if d == 0:
            if sxy > 0:
                return np.pi / 4
            elif sxy < 0:
                return -np.pi / 4
            else:
                return 0
        return 0.5 * np.arctan2(2 * sxy, d)

    assert np.degrees(old_theta(2.5, 2.5, -2.5)) % 180 == 0
    assert np.degrees(new_theta(2.5, 2.5, -2.5)) % 180 == 135.0
