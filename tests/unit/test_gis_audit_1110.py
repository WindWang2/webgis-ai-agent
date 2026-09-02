"""#1110: attribute_filter / central_feature keep declared FeatureCollection CRS.

Mirrors tests/unit/test_advanced_spatial_audit_764_765.py (#765). Tool adapters
must forward the full FeatureCollection so EPSG:3857 `crs` reaches
SpatialAnalyzer / to_utm_gdf.
"""
import pytest
from pyproj import Transformer

from app.lib.geo_processor.core import (
    GeoAnalysisResult,
    extract_declared_crs,
    to_utm_gdf,
)
from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


_TR_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _beijing_3857_points_fc():
    pts_wgs = [(116.0 + 0.001 * i, 39.9) for i in range(5)]
    features = []
    for i, (lon, lat) in enumerate(pts_wgs):
        x, y = _TR_3857.transform(lon, lat)
        features.append({
            "type": "Feature",
            "properties": {"pop": 50 + 20 * i, "name": f"p{i}"},
            "geometry": {"type": "Point", "coordinates": [x, y]},
        })
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": features,
    }


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_preserves_declared_crs_1110(registry):
    fc = _beijing_3857_points_fc()
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "pop > 60"}
    )
    assert result.get("success") is True, result
    out = result["data"]
    assert isinstance(out, dict) and out.get("type") == "FeatureCollection"
    assert extract_declared_crs(out) == "EPSG:3857", (
        f"declared crs stripped at attribute_filter tool boundary: {out.get('crs')}"
    )
    assert len(out["features"]) == 4

    direct = SpatialAnalyzer.attribute_filter(fc, "pop > 60")
    assert direct.success
    gdf_tool, _ = to_utm_gdf(out)
    gdf_direct, _ = to_utm_gdf(direct.data)
    assert gdf_tool is not None and gdf_direct is not None
    assert len(gdf_tool) == len(gdf_direct)
    assert list(gdf_tool.total_bounds) == pytest.approx(
        list(gdf_direct.total_bounds), rel=1e-6, abs=1e-3
    )


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_forwards_declared_crs_1110(registry, monkeypatch):
    fc = _beijing_3857_points_fc()
    captured = {}

    def fake(features, method="mean_center", callback=None):
        captured["features"] = features
        return GeoAnalysisResult(
            True, {"type": "FeatureCollection", "features": []}, "captured"
        )

    monkeypatch.setattr(SpatialAnalyzer, "central_feature", fake)
    result = await registry.dispatch(
        "central_feature", {"geojson": fc, "method": "mean_center"}
    )
    assert result.get("success") is True, result
    arg = captured["features"]
    assert isinstance(arg, dict), "adapter passed a bare features list"
    assert arg.get("type") == "FeatureCollection"
    assert extract_declared_crs(arg) == "EPSG:3857"


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_adapter_forwards_fc_not_list_1110(registry, monkeypatch):
    fc = _beijing_3857_points_fc()
    captured = {}

    def fake(features, query, callback=None):
        captured["features"] = features
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
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "pop > 0"}
    )
    assert result.get("success") is True, result
    arg = captured["features"]
    assert isinstance(arg, dict)
    assert extract_declared_crs(arg) == "EPSG:3857"
