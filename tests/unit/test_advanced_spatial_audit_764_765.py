"""GIS audit round-2 regressions for the advanced_spatial tool adapters.

- #764: spatial_aggregate's documented ``count_field`` parameter must name the
  OUTPUT count column (default ``point_count``). It was forwarded to the deep
  function as ``value_field`` (a point-attribute selector), so the output
  column was always ``count`` and consumers reading
  ``properties.point_count`` got undefined.
- #765: the adapters handed down ``data.get("features", [])``, stripping the
  declared GeoJSON ``crs`` member at the tool boundary (GIS-599/#682
  half-stack). The parsed FeatureCollection must be forwarded so the deep
  operators (gdf_from_features / to_utm_gdf) can honor a declared projected
  CRS.

Walks the TOOL ROUTE (ToolRegistry.dispatch) so schema registration and the
adapter boundary are both exercised.
"""
import pytest
from pyproj import Transformer

from app.lib.geo_processor.core import GeoAnalysisResult
from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


_TR_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _wgs84_points_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": f"p{i}"},
                "geometry": {"type": "Point", "coordinates": [116.0 + 0.001 * i, 39.9]},
            }
            for i in range(3)
        ],
    }


def _wgs84_polygon_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"dist": "A"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[115.99, 39.89], [116.01, 39.89], [116.01, 39.91], [115.99, 39.91], [115.99, 39.89]]
                    ],
                },
            }
        ],
    }


# ─── #764: count_field names the output column ──────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_aggregate_count_field_names_output_column_764(registry):
    pts = _wgs84_points_fc()
    polys = _wgs84_polygon_fc()

    result = await registry.dispatch(
        "spatial_aggregate", {"points": pts, "polygons": polys, "count_field": "n_pois"}
    )
    assert result.get("success") is True, result
    props = result["data"]["features"][0]["properties"]
    assert props.get("n_pois") == 3, f"count_field column missing/wrong: {props}"
    assert "count" not in props, "renamed column must not leave a stale 'count' behind"
    assert props.get("has_data") is True


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_aggregate_count_field_default_is_point_count_764(registry):
    """The documented default ('point_count') must hold without opt-out."""
    result = await registry.dispatch(
        "spatial_aggregate", {"points": _wgs84_points_fc(), "polygons": _wgs84_polygon_fc()}
    )
    assert result.get("success") is True, result
    props = result["data"]["features"][0]["properties"]
    assert props.get("point_count") == 3, f"default count_field broken: {props}"
    assert "count" not in props


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_aggregate_count_field_count_keeps_count_764(registry):
    result = await registry.dispatch(
        "spatial_aggregate",
        {"points": _wgs84_points_fc(), "polygons": _wgs84_polygon_fc(), "count_field": "count"},
    )
    assert result.get("success") is True, result
    props = result["data"]["features"][0]["properties"]
    assert props.get("count") == 3


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_aggregate_count_field_not_a_point_attribute_764(registry):
    """A point attribute that happens to share the count_field name must not
    be picked up as the aggregated metric (the old code forwarded count_field
    as value_field — a silent contract trap)."""
    pts = _wgs84_points_fc()
    for f in pts["features"]:
        f["properties"]["n_pois"] = 100  # same name as the output column
    result = await registry.dispatch(
        "spatial_aggregate", {"points": pts, "polygons": _wgs84_polygon_fc(), "count_field": "n_pois"}
    )
    assert result.get("success") is True, result
    props = result["data"]["features"][0]["properties"]
    assert props.get("n_pois") == 3, (
        f"output n_pois must be the point COUNT (3), not the point attribute: {props}"
    )


# ─── #765: declared `crs` member survives the adapter boundary ─────────────


def _beijing_3857_polygon_fc():
    """Small square around Beijing, coordinates in EPSG:3857 metres, with the
    declared legacy ``crs`` member (as written by reproject_coordinates)."""
    ring_wgs = [
        (116.0, 39.95), (116.02, 39.95), (116.02, 39.97), (116.0, 39.97), (116.0, 39.95)
    ]
    ring = [[list(_TR_3857.transform(x, y)) for x, y in ring_wgs]]
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [
            {"type": "Feature", "properties": {"id": 1},
             "geometry": {"type": "Polygon", "coordinates": ring}}
        ],
    }


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_clip_layer_forwards_declared_crs_765(registry):
    """End-to-end: a declared-EPSG:3857 target clipped by a WGS84 mask whose
    extent covers the reprojected coordinates must KEEP its feature — with
    the crs stripped at the boundary the 12.9M-metre 'degrees' fall far
    outside the mask and the clip silently returns nothing."""
    target = _beijing_3857_polygon_fc()
    mask = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"admin": "x"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[115.9, 39.9], [116.1, 39.9], [116.1, 40.0], [115.9, 40.0], [115.9, 39.9]]
                    ],
                },
            }
        ],
    }
    result = await registry.dispatch("clip_layer", {"target_layer": target, "mask_layer": mask})
    assert result.get("success") is True, result
    feats = result["data"]["features"]
    assert len(feats) == 1, (
        f"declared-crs target misread as WGS84 at the tool boundary: {len(feats)} features survived"
    )


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_overlay_analysis_forwards_declared_crs_765(registry):
    """End-to-end: 3857-declared square ∩ WGS84 square must intersect."""
    layer_a = _beijing_3857_polygon_fc()
    ring_wgs = [(116.01, 39.955), (116.03, 39.955), (116.03, 39.975), (116.01, 39.975), (116.01, 39.955)]
    layer_b = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": 2},
             "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring_wgs]]}}
        ],
    }
    result = await registry.dispatch(
        "overlay_analysis", {"layer_a": layer_a, "layer_b": layer_b, "how": "intersection"}
    )
    assert result.get("success") is True, result
    feats = result["data"]["features"]
    assert len(feats) >= 1, "declared-crs layer misread as WGS84: intersection came back empty"


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_adapters_pass_featurecollection_not_features_list_765(registry, monkeypatch):
    """Unit-level: every adapter must hand the deep analyzer the parsed FC
    dict (crs member intact), not a bare features list."""
    crs_fc = _beijing_3857_polygon_fc()
    wgs_fc = _wgs84_polygon_fc()
    pts_fc = _wgs84_points_fc()
    network_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "LineString", "coordinates": [[116.0, 39.9], [116.01, 39.91]]}}
        ],
    }
    for fc in (crs_fc, wgs_fc, pts_fc, network_fc):
        fc["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}

    captured = {}

    def _capture(key):
        def fake(*args, **kwargs):
            captured[key] = {"args": args, "kwargs": kwargs}
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "captured")
        return fake

    monkeypatch.setattr(SpatialAnalyzer, "clip", _capture("clip"))
    monkeypatch.setattr(SpatialAnalyzer, "overlay", _capture("overlay"))
    monkeypatch.setattr(SpatialAnalyzer, "spatial_join", _capture("spatial_join"))
    monkeypatch.setattr(SpatialAnalyzer, "aggregate", _capture("aggregate"))
    monkeypatch.setattr(SpatialAnalyzer, "isochrone_network", _capture("isochrone_network"))
    monkeypatch.setattr(SpatialAnalyzer, "buffer", _capture("buffer"))

    await registry.dispatch("clip_layer", {"target_layer": crs_fc, "mask_layer": wgs_fc})
    await registry.dispatch("overlay_analysis", {"layer_a": crs_fc, "layer_b": wgs_fc})
    await registry.dispatch("spatial_join", {"left_layer": pts_fc, "right_layer": wgs_fc})
    await registry.dispatch("spatial_aggregate", {"points": pts_fc, "polygons": wgs_fc})
    await registry.dispatch("isochrone_network", {"network_layer": network_fc, "facilities": pts_fc})
    await registry.dispatch("service_area_simple", {"geojson": pts_fc})

    def _assert_fc(key, arg, name):
        assert isinstance(arg, dict), f"{key}: adapter passed a bare list for {name}"
        assert arg.get("type") == "FeatureCollection", f"{key}: {name} is not a FeatureCollection"
        assert extract_declared_crs(arg) == "EPSG:3857", (
            f"{key}: declared crs member stripped from {name} at the adapter boundary"
        )

    from app.lib.geo_processor.core import extract_declared_crs

    _assert_fc("clip", captured["clip"]["args"][0], "target")          # both layers symmetric
    _assert_fc("clip", captured["clip"]["args"][1], "mask")
    _assert_fc("overlay", captured["overlay"]["args"][0], "layer_a")
    _assert_fc("overlay", captured["overlay"]["args"][1], "layer_b")
    _assert_fc("spatial_join", captured["spatial_join"]["args"][0], "left_layer")
    _assert_fc("spatial_join", captured["spatial_join"]["args"][1], "right_layer")
    _assert_fc("aggregate", captured["aggregate"]["args"][0], "points")
    _assert_fc("aggregate", captured["aggregate"]["args"][1], "polygons")
    _assert_fc("isochrone_network", captured["isochrone_network"]["args"][0], "network_layer")
    _assert_fc("isochrone_network", captured["isochrone_network"]["args"][1], "facilities")
    _assert_fc("buffer(service_area_simple)", captured["buffer"]["args"][0], "geojson")
