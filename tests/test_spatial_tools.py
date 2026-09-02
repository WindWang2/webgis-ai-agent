"""Spatial tools tests — _safe_parse_geojson + #1110 CRS tool-boundary regressions"""
import pytest
from pyproj import Transformer

from app.tools.spatial import safe_parse_geojson as _safe_parse_geojson
from app.lib.geo_processor.core import (
    GeoAnalysisResult,
    extract_declared_crs,
    to_utm_gdf,
)
from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


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


# ─── #1110: attribute_filter / central_feature keep declared CRS ─────────────
# Mirrors tests/unit/test_advanced_spatial_audit_764_765.py (#765) — tool
# adapters must forward the full FeatureCollection so a declared EPSG:3857
# `crs` member reaches SpatialAnalyzer / to_utm_gdf.


@pytest.fixture
def advanced_registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


_TR_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _beijing_3857_points_fc():
    """Point cloud around Beijing in EPSG:3857 metres with declared crs."""
    pts_wgs = [(116.0 + 0.001 * i, 39.9) for i in range(5)]
    features = []
    for i, (lon, lat) in enumerate(pts_wgs):
        x, y = _TR_3857.transform(lon, lat)
        features.append(
            {
                "type": "Feature",
                "properties": {"pop": 50 + 20 * i, "name": f"p{i}"},
                "geometry": {"type": "Point", "coordinates": [x, y]},
            }
        )
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": features,
    }


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_preserves_declared_crs_1110(advanced_registry):
    """EPSG:3857 input through attribute_filter must keep crs and roundtrip
    to_utm_gdf identically to processing the original FC directly."""
    fc = _beijing_3857_points_fc()
    result = await advanced_registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "pop > 60"}
    )
    assert result.get("success") is True, result
    out = result["data"]
    assert isinstance(out, dict) and out.get("type") == "FeatureCollection"
    assert extract_declared_crs(out) == "EPSG:3857", (
        f"declared crs stripped at attribute_filter tool boundary: {out.get('crs')}"
    )
    # pop values 50,70,90,110,130 → pop > 60 keeps four features
    assert len(out["features"]) == 4

    # Roundtrip: to_utm_gdf on tool output must match direct processing of
    # the filtered original FC (same projected metres, not misread as degrees).
    direct = SpatialAnalyzer.attribute_filter(fc, "pop > 60")
    assert direct.success
    gdf_tool, _crs_tool = to_utm_gdf(out)
    gdf_direct, _crs_direct = to_utm_gdf(direct.data)
    assert gdf_tool is not None and gdf_direct is not None
    assert len(gdf_tool) == len(gdf_direct)
    assert list(gdf_tool.total_bounds) == pytest.approx(
        list(gdf_direct.total_bounds), rel=1e-6, abs=1e-3
    )


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_forwards_declared_crs_1110(advanced_registry, monkeypatch):
    """Adapter must hand SpatialAnalyzer the full FC (crs intact), not a bare list."""
    fc = _beijing_3857_points_fc()
    captured = {}

    def fake(features, method="mean_center", callback=None):
        captured["features"] = features
        captured["method"] = method
        return GeoAnalysisResult(
            True, {"type": "FeatureCollection", "features": []}, "captured"
        )

    monkeypatch.setattr(SpatialAnalyzer, "central_feature", fake)
    result = await advanced_registry.dispatch(
        "central_feature", {"geojson": fc, "method": "mean_center"}
    )
    assert result.get("success") is True, result
    arg = captured["features"]
    assert isinstance(arg, dict), "adapter passed a bare features list"
    assert arg.get("type") == "FeatureCollection"
    assert extract_declared_crs(arg) == "EPSG:3857", (
        "declared crs member stripped from central_feature at adapter boundary"
    )


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_adapter_forwards_fc_not_list_1110(
    advanced_registry, monkeypatch
):
    fc = _beijing_3857_points_fc()
    captured = {}

    def fake(features, query, callback=None):
        captured["features"] = features
        captured["query"] = query
        return GeoAnalysisResult(
            True,
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
                "features": [],
            },
            "captured",
        )

    monkeypatch.setattr(SpatialAnalyzer, "attribute_filter", fake)
    result = await advanced_registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "pop > 0"}
    )
    assert result.get("success") is True, result
    arg = captured["features"]
    assert isinstance(arg, dict)
    assert arg.get("type") == "FeatureCollection"
    assert extract_declared_crs(arg) == "EPSG:3857"
