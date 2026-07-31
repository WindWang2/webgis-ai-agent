"""Unit tests for the deepened SpatialAnalyzer module (app/services/spatial_analyzer.py)."""
import json
import pytest
from app.services.spatial_analyzer import (
    SpatialAnalyzer,
    execute_analysis,
    _to_feature_collection,
    AnalysisResult,
)
from app.lib.geo_processor.core import GeoAnalysisResult


def test_analysis_result_alias():
    res = GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "OK")
    assert AnalysisResult.from_geo(res) is res


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


def test_spatial_analyzer_execute_dynamic_dispatch():
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
    
    # Dynamic execute for buffer
    res_buf = SpatialAnalyzer.execute("buffer", fc, {"distance": 200})
    assert res_buf.success is True

    # Dynamic execute via top-level function
    res_top = execute_analysis("buffer", {"distance": 200}, fc)
    assert res_top.success is True


def test_spatial_analyzer_unknown_operation():
    res = SpatialAnalyzer.execute("unknown_op", {})
    assert res.success is False
    assert "Unknown analysis type" in res.summary
