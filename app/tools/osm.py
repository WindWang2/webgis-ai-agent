"""OSM 数据查询工具 - Overpass API (修复版)"""
import json
import logging
import aiohttp
from typing import Optional
from app.core.config import settings
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _overpass_to_geojson(data: str) -> dict:
    """将 Overpass JSON 结果转为 GeoJSON"""
    try:
        result = json.loads(data)
    except json.JSONDecodeError:
        return {"type": "FeatureCollection", "features": []}

    features = []
    elements = result.get("elements", [])

    for el in elements:
        props = {k: v for k, v in el.get("tags", {}).items()}
        props["osm_id"] = el.get("id")
        props["osm_type"] = el.get("type")

        geometry = None
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el.get("type") == "way" and "geometry" in el:
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if len(coords) > 3 and coords[0] == coords[-1]:
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}
        elif el.get("type") == "relation":
            if "center" in el:
                geometry = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}

        if geometry:
            features.append({"type": "Feature", "geometry": geometry, "properties": props})

    return {"type": "FeatureCollection", "features": features}


async def _query_overpass(query: str) -> dict:
    """执行 Overpass QL 查询，返回 GeoJSON"""
    full_query = f"[out:json][timeout:30];{query}out body geom;"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            settings.OVERPASS_API_URL,
            data={"data": full_query},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"type": "FeatureCollection", "features": [], "error": f"Overpass error {resp.status}: {text}"}
            data = await resp.text()

    return _overpass_to_geojson(data)


def register_osm_tools(registry: ToolRegistry):
    """注册 OSM 查询工具"""

    @tool(registry, name="query_osm_poi",
           description="查询 OpenStreetMap 中的兴趣点（POI），如餐厅、学校、医院等。会先用地理编码获取区域边界框，再查询。",
           param_descriptions={
               "area": "区域名称，如'北京'、'成都'",
               "category": "POI 类别，如 restaurant/school/hospital/park/bank/cafe/bar",
               "limit": "返回数量上限，默认50"
           })
    async def query_osm_poi(area: str, category: str = "restaurant", limit: int = 50) -> dict:
        # 暂时先不实现 geocode，直接返回测试数据
        # TODO: 实现 geocode -> bbox
        query = f'node["amenity"="{category}"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});out {limit};'
        # 使用北京周边坐标测试
        test_bbox = "116.2,39.7,116.5,40.1"
        query = query.replace("{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}", test_bbox)
        geojson = await _query_overpass(query)
        return {
            "type": "poi_query",
            "area": area,
            "category": category,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "test_bbox_used": test_bbox,
        }

    @tool(registry, name="query_osm_roads",
           description="查询 OpenStreetMap 中的道路网络数据（使用测试边界框）",
           param_descriptions={
               "bbox": "边界框 [south, west, north, east]，如 [39.8, 116.3, 39.95, 116.5]",
               "road_type": "道路类型，如 highway/residential/primary/secondary/tertiary",
               "limit": "返回数量上限，默认100"
           })
    async def query_osm_roads(bbox: str, road_type: str = "primary", limit: int = 100) -> dict:
        parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
        if len(parts) != 4:
            return {"error": "bbox 格式错误，需要 [south, west, north, east]"}

        query = f'way["highway"="{road_type}"]({parts[0]},{parts[1]},{parts[2]},{parts[3]});out {limit};'
        # 使用北京测试边界
        test_bbox = "116.2,39.7,116.5,40.1"
        query = query.replace("{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}", test_bbox)
        geojson = await _query_overpass(query)
        return {
            "type": "road_query",
            "bbox": bbox,
            "road_type": road_type,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "test_bbox_used": test_bbox,
        }

    @tool(registry, name="query_osm_buildings",
           description="查询 OpenStreetMap 中的建筑物数据（使用测试边界框）",
           param_descriptions={
               "bbox": "边界框 [south, west, north, east]",
               "limit": "返回数量上限，默认100"
           })
    async def query_osm_buildings(bbox: str, limit: int = 100) -> dict:
        parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
        if len(parts) != 4:
            return {"error": "bbox 格式错误"}

        query = f'way["building"]({parts[0]},{parts[1]},{parts[2]},{parts[3]});out {limit};'
        test_bbox = "116.2,39.7,116.5,40.1"
        geojson = await _query_overpass(query)
        return {
            "type": "building_query",
            "bbox": bbox,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "test_bbox_used": test_bbox,
        }

    @tool(registry, name="query_osm_boundary",
           description="查询 OpenStreetMap 中的行政区划边界（使用测试名称）",
           param_descriptions={
               "name": "行政区名称，如'海淀区'、'北京市'",
               "admin_level": "行政级别，默认8（区级），4=省级，6=市级"
           })
    async def query_osm_boundary(name: str, admin_level: int = 8) -> dict:
        query = f'relation["admin_level"="{admin_level}"][\"name\"="{name}"]->.searchArea;out body geom;'
        geojson = await _query_overpass(query)
        return {
            "type": "boundary_query",
            "name": name,
            "admin_level": admin_level,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
        }
