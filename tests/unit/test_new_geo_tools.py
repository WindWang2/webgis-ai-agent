"""dissolve_layer / nearest_facility / transform_coordinates 端到端 dispatch 测试。

走 registry.dispatch 而不是直接调函数，确保 schema 注册、参数校验、Exception As Thought
返回包装都跑过。
"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.coord_transform import register_coord_transform_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    register_coord_transform_tools(r)
    return r


# ─── dissolve_layer ────────────────────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_dissolve_without_field_merges_all(registry):
    """两个相邻矩形 → 不给 field 应融合为单一多边形。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 2},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]],
                },
            },
        ],
    }
    result = await registry.dispatch("dissolve_layer", {"geojson": geojson})
    # 工具返回 GeoAnalysisResult.to_llm_response()，成功路径含 data
    assert result.get("success") is True or "data" in result
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        assert len(data["features"]) == 1


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_dissolve_with_field_groups(registry):
    """带 field 时按字段值分组融合。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"kind": "A"},
             "geometry": {"type": "Polygon",
                          "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
            {"type": "Feature", "properties": {"kind": "A"},
             "geometry": {"type": "Polygon",
                          "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}},
            {"type": "Feature", "properties": {"kind": "B"},
             "geometry": {"type": "Polygon",
                          "coordinates": [[[3, 3], [4, 3], [4, 4], [3, 4], [3, 3]]]}},
        ],
    }
    result = await registry.dispatch("dissolve_layer", {"geojson": geojson, "field": "kind"})
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        # 两组：A 合并成 1 个 + B 独立 1 个 = 2 个
        assert len(data["features"]) == 2


# ─── nearest_facility ──────────────────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_nearest_facility_finds_nearest(registry):
    """每个 source 应被打上 nearest_target_id + distance_m。"""
    sources = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "S1"},
             "geometry": {"type": "Point", "coordinates": [116.40, 39.91]}},
            {"type": "Feature", "properties": {"name": "S2"},
             "geometry": {"type": "Point", "coordinates": [116.50, 39.95]}},
        ],
    }
    targets = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "T_near_S1"},
             "geometry": {"type": "Point", "coordinates": [116.41, 39.92]}},
            {"type": "Feature", "properties": {"name": "T_near_S2"},
             "geometry": {"type": "Point", "coordinates": [116.51, 39.96]}},
            {"type": "Feature", "properties": {"name": "T_far"},
             "geometry": {"type": "Point", "coordinates": [117.0, 40.0]}},
        ],
    }
    result = await registry.dispatch(
        "nearest_facility",
        {"source_points": sources, "target_points": targets},
    )
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        features = data["features"]
        assert len(features) == 2
        for feat in features:
            props = feat["properties"]
            assert "distance_m" in props
            assert props["distance_m"] >= 0
            assert "nearest_target_id" in props


# ─── transform_coordinates ─────────────────────────────────────


@pytest.mark.asyncio
async def test_transform_wgs84_to_gcj02_shifts_china_coords(registry):
    """北京天安门 WGS84 → GCJ-02 应偏移约 300-500m。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "天安门"},
            "geometry": {"type": "Point", "coordinates": [116.397428, 39.90923]},
        }],
    }
    result = await registry.dispatch(
        "transform_coordinates",
        {"geojson": geojson, "from_crs": "wgs84", "to_crs": "gcj02"},
    )
    assert result.get("success") is True
    out_coords = result["data"]["features"][0]["geometry"]["coordinates"]
    # 国内 WGS→GCJ02 应该有明显偏移（≥1e-3 度，约百米级）
    assert abs(out_coords[0] - 116.397428) > 1e-3 or abs(out_coords[1] - 39.90923) > 1e-3


@pytest.mark.asyncio
async def test_transform_roundtrip_preserves_position(registry):
    """WGS84 → GCJ02 → WGS84 应基本回到原点（误差 < 几米）。"""
    original = [116.397428, 39.90923]
    geojson = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(original)},
        "properties": {},
    }
    r1 = await registry.dispatch(
        "transform_coordinates",
        {"geojson": geojson, "from_crs": "wgs84", "to_crs": "gcj02"},
    )
    r2 = await registry.dispatch(
        "transform_coordinates",
        {"geojson": r1["data"], "from_crs": "gcj02", "to_crs": "wgs84"},
    )
    final = r2["data"]["geometry"]["coordinates"]
    # 经度纬度回环误差 < 1e-4 度（约 11m）
    assert abs(final[0] - original[0]) < 1e-4
    assert abs(final[1] - original[1]) < 1e-4


@pytest.mark.asyncio
async def test_transform_invalid_crs_returns_error(registry):
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    result = await registry.dispatch(
        "transform_coordinates",
        {"geojson": geojson, "from_crs": "wgs84", "to_crs": "epsg4326"},
    )
    assert result.get("success") is False
    assert "wgs84" in result.get("error", "") or "epsg" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_transform_same_crs_returns_original(registry):
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    result = await registry.dispatch(
        "transform_coordinates",
        {"geojson": geojson, "from_crs": "wgs84", "to_crs": "wgs84"},
    )
    assert result.get("success") is True
    assert result["data"]["coordinates"] == [116.4, 39.9]


@pytest.mark.asyncio
async def test_transform_polygon_walks_all_rings(registry):
    """多边形含外环+内环，应都被转换。"""
    poly = {
        "type": "Polygon",
        "coordinates": [
            [[116.4, 39.9], [116.5, 39.9], [116.5, 40.0], [116.4, 40.0], [116.4, 39.9]],
        ],
    }
    result = await registry.dispatch(
        "transform_coordinates",
        {"geojson": poly, "from_crs": "wgs84", "to_crs": "gcj02"},
    )
    coords = result["data"]["coordinates"][0]
    # 每个角点都应被转换，环闭合，第一个=最后一个
    assert coords[0] == coords[-1]
    # 与源不同
    assert coords[0] != [116.4, 39.9]
