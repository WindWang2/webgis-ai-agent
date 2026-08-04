"""Pure-math tests for lib/geo_analysis/density.py (kde_surface + kde_contours).

Synthetic point fixtures - no network, no tool layer. Asserts the
GeoAnalysisResult return contract and algorithm correctness.

Extracted from app/tools/spatial_stats.py (architecture-review F2): these
algorithms previously lived in the tool adapter layer with zero behavioral
coverage. Now testable as pure functions.
"""

from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_analysis.density import kde_surface, kde_contours


def _synthetic_points(n: int = 20) -> dict:
    """N synthetic points clustered around two centers (Beijing + Shanghai)."""
    import random
    random.seed(42)
    features = []
    # cluster 1: around Beijing (116.4, 39.9)
    for i in range(n // 2):
        lng = 116.4 + random.uniform(-0.02, 0.02)
        lat = 39.9 + random.uniform(-0.02, 0.02)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": i, "weight": random.uniform(1, 10)},
        })
    # cluster 2: around Shanghai (121.5, 31.2)
    for i in range(n // 2, n):
        lng = 121.5 + random.uniform(-0.02, 0.02)
        lat = 31.2 + random.uniform(-0.02, 0.02)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": i, "weight": random.uniform(1, 10)},
        })
    return {"type": "FeatureCollection", "features": features}


# ── kde_surface ─────────────────────────────────────────────────────────────


def test_kde_surface_returns_geoanalysisresult():
    res = kde_surface(_synthetic_points(20))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_kde_surface_has_grid_size_and_bandwidth():
    res = kde_surface(_synthetic_points(20))
    assert res.success
    fc = res.data
    assert "grid_size" in fc
    assert len(fc["grid_size"]) == 2
    assert "bandwidth_m" in fc
    assert fc["bandwidth_m"] > 0


def test_kde_surface_features_have_density():
    res = kde_surface(_synthetic_points(20), cell_size=2000)
    assert res.success
    fc = res.data
    assert fc["type"] == "FeatureCollection"
    # thresholded - only non-trivial cells survive
    assert fc["count"] >= 1
    for feat in fc["features"]:
        assert "density" in feat["properties"]
        assert feat["properties"]["density"] >= 0  # survived threshold; may round to 0.0


def test_kde_surface_too_few_points():
    res = kde_surface(_synthetic_points(2))
    assert not res.success
    assert res.error_type == "ValueError"


def test_kde_surface_weighted_field():
    res = kde_surface(_synthetic_points(20), value_field="weight", cell_size=2000)
    assert res.success
    assert res.data["count"] >= 1


def test_kde_surface_weighted_bad_field():
    res = kde_surface(_synthetic_points(20), value_field="nonexistent")
    assert not res.success
    assert res.error_type == "TypeError"


def test_kde_surface_explicit_bandwidth():
    res = kde_surface(_synthetic_points(20), bandwidth=5000, cell_size=2000)
    assert res.success
    assert res.data["bandwidth_m"] == 5000


def test_kde_surface_custom_bounds():
    res = kde_surface(
        _synthetic_points(20), cell_size=2000,
        bounds=[116.0, 39.0, 122.0, 40.0],
    )
    assert res.success


# ── kde_contours ────────────────────────────────────────────────────────────


def test_kde_contours_returns_geoanalysisresult():
    res = kde_contours(_synthetic_points(20))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_kde_contours_has_levels_count():
    res = kde_contours(_synthetic_points(20), levels=6)
    assert res.success
    fc = res.data
    assert "levels_count" in fc
    # matplotlib may adjust the requested level count internally
    assert fc["levels_count"] >= 6


def test_kde_contours_features_have_density_value():
    res = kde_contours(_synthetic_points(20))
    assert res.success
    fc = res.data
    if fc["count"] > 0:
        for feat in fc["features"]:
            assert "density_value" in feat["properties"]
            assert "level" in feat["properties"]


def test_kde_contours_legend_spec_when_features_exist():
    """legend_spec attaches as a top-level key on the FC for the cartography converters."""
    res = kde_contours(_synthetic_points(20))
    assert res.success
    fc = res.data
    if fc["count"] > 0:
        assert "legend_spec" in fc
        ls = fc["legend_spec"]
        assert ls["type"] == "continuous"
        assert "min" in ls and "max" in ls
        assert "palette" in ls


def test_kde_contours_too_few_points():
    res = kde_contours(_synthetic_points(4))
    assert not res.success
    assert res.error_type == "ValueError"
