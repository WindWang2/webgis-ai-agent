"""Unit tests for the deepened SpatialAnalyzer module (app/services/spatial_analyzer.py)."""
import json
from app.services.spatial_analyzer import (
    SpatialAnalyzer,
    _to_feature_collection,
    AnalysisResult,
)
from app.lib.geo_processor.core import GeoAnalysisResult


def test_analysis_result_alias():
    # ADR-0009 / Candidate #3: AnalysisResult is a *type alias* for
    # GeoAnalysisResult, not a wrapper subclass. They are the same class.
    assert AnalysisResult is GeoAnalysisResult
    res = GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "OK")
    assert isinstance(res, AnalysisResult)


def test_to_feature_collection_normalization():
    # FeatureCollection dict
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}}]}
    assert _to_feature_collection(fc) == fc

    # Single Feature dict
    feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}
    fc_from_feat = _to_feature_collection(feat)
    assert fc_from_feat["type"] == "FeatureCollection"
    assert len(fc_from_feat["features"]) == 1

    # Features list
    feat_list = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}}]
    fc_from_list = _to_feature_collection(feat_list)
    assert fc_from_list["type"] == "FeatureCollection"
    assert fc_from_list["features"] == feat_list

    # String GeoJSON
    str_fc = json.dumps(fc)
    assert _to_feature_collection(str_fc) == fc

    # Invalid dict fallback
    invalid_dict = {"invalid": "shape"}
    assert _to_feature_collection(invalid_dict) == {"type": "FeatureCollection", "features": []}


def test_spatial_analyzer_buffer_accepts_full_geojson():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"id": 1}
            }
        ]
    }
    res = SpatialAnalyzer.buffer(fc, distance=500, unit="m")
    assert res.success is True
    assert res.data["type"] == "FeatureCollection"
    assert len(res.data["features"]) > 0


def test_spatial_analyzer_buffer_concrete_method():
    # ADR-0013: the dynamic name-dispatch seam (execute / execute_analysis) was
    # deleted — concrete methods (.buffer, .overlay, ...) are the interface.
    # This test migrated from the seam to the concrete method it always reached.
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"val": 10}
            }
        ]
    }

    res = SpatialAnalyzer.buffer(fc, distance=200, unit="m")
    assert res.success is True


# ── F2: hotspot + lisa operators route through SpatialAnalyzer ──────────────
# (architecture-review F2, step 3: bypass fix)

import random as _random


def _synthetic_weighted_points(n: int = 20) -> dict:
    """N points with a numeric 'value' field for hotspot/lisa testing."""
    _random.seed(42)
    feats = []
    for i in range(n):
        lng = 116.3 + _random.uniform(0, 0.2)
        lat = 39.8 + _random.uniform(0, 0.2)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"value": _random.uniform(1, 100)},
        })
    return {"type": "FeatureCollection", "features": feats}


def test_spatial_analyzer_hotspot_operator():
    """SpatialAnalyzer.hotspot returns GeoAnalysisResult (routes through the seam)."""
    res = SpatialAnalyzer.hotspot(_synthetic_weighted_points(20), "value", distance_band=5000)
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_spatial_analyzer_lisa_operator():
    """SpatialAnalyzer.lisa returns GeoAnalysisResult (contract holds even if h3 missing)."""
    res = SpatialAnalyzer.lisa(_synthetic_weighted_points(20), "value")
    assert isinstance(res, GeoAnalysisResult)
    # h3 lib may be absent (ImportError) - the contract is what matters here


def test_path_analysis_operator_deleted():
    """path_analysis was deleted (vaporware - live ImportError crash, no shortest_path impl)."""
    assert not hasattr(SpatialAnalyzer, "path_analysis")


def test_path_analysis_tool_not_registered():
    """The path_analysis tool must not be registered after deletion."""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    assert "path_analysis" not in r.list_tools()


# ── F2: operator parity - all new operators return GeoAnalysisResult ───────
# (architecture-review F2, step 4: the invariant is machine-checkable)

def test_all_f2_operators_return_geoanalysisresult():
    """All 7 operators added/moved in F2 return GeoAnalysisResult with synthetic input."""
    pts = _synthetic_weighted_points(20)
    operators = [
        ("kde_surface", lambda: SpatialAnalyzer.kde_surface(pts, cell_size=2000)),
        ("kde_contours", lambda: SpatialAnalyzer.kde_contours(pts)),
        ("voronoi_polygons", lambda: SpatialAnalyzer.voronoi_polygons(pts)),
        ("convex_hull", lambda: SpatialAnalyzer.convex_hull(pts)),
        ("multi_ring_buffer", lambda: SpatialAnalyzer.multi_ring_buffer(pts, distances=[500, 1000])),
        ("hotspot", lambda: SpatialAnalyzer.hotspot(pts, "value", distance_band=5000)),
        ("lisa", lambda: SpatialAnalyzer.lisa(pts, "value")),
    ]
    for name, call in operators:
        res = call()
        assert isinstance(res, GeoAnalysisResult), f"{name} did not return GeoAnalysisResult"
        # success or failure is fine (e.g. lisa may fail on h3 lib), but the
        # contract is the return type


def test_no_direct_lib_bypass_in_spatial_stats_tools():
    """spatial_stats.py tool wrappers must not import lib.geo_analysis directly.

    The 'all geo math through SpatialAnalyzer' invariant means the tool layer
    routes through the seam, not around it. This catches any re-introduction of
    the hotspot/h3_lisa bypass pattern.
    """
    import app.tools.spatial_stats as mod
    import inspect
    src = inspect.getsource(mod)
    # The tool layer should not import from lib.geo_analysis directly;
    # it should go through SpatialAnalyzer.
    assert "from app.lib.geo_analysis" not in src, (
        "spatial_stats.py bypasses SpatialAnalyzer by importing lib.geo_analysis directly"
    )


def test_spatial_analyzer_has_no_path_analysis():
    """path_analysis was deleted - confirm it stays gone (vaporware guard)."""
    assert not hasattr(SpatialAnalyzer, "path_analysis")
    assert not hasattr(SpatialAnalyzer, "execute")
    assert not hasattr(SpatialAnalyzer, "OPERATOR_MAP")
