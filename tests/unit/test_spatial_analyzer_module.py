"""Unit tests for the deepened SpatialAnalyzer module (app/services/spatial_analyzer.py)."""
import json
import pytest
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
