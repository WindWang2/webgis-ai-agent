"""Spatial tools tests — _safe_parse_geojson"""
import pytest
from app.tools.spatial import _safe_parse_geojson


class TestSafeParseGeojson:
    def test_dict_passthrough(self):
        data = {"type": "FeatureCollection", "features": []}
        assert _safe_parse_geojson(data) == data

    def test_string_parse(self):
        s = '{"type": "FeatureCollection", "features": []}'
        result = _safe_parse_geojson(s)
        assert result is not None
        assert result["type"] == "FeatureCollection"

    def test_invalid_string(self):
        result = _safe_parse_geojson("not json at all")
        assert result is None

    def test_empty_string(self):
        assert _safe_parse_geojson("") is None
        assert _safe_parse_geojson("   ") is None

    def test_none_input(self):
        assert _safe_parse_geojson(None) is None

    def test_number_input(self):
        assert _safe_parse_geojson(42) is None

    def test_list_input(self):
        assert _safe_parse_geojson([1, 2, 3]) is None

    def test_truncated_geojson_repair(self):
        truncated = '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}'
        result = _safe_parse_geojson(truncated)
        assert result is not None
        assert "features" in result
