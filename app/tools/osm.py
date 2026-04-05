"""OSM 数据查询工具 - Overpass API (v2: geocode→bbox 可靠查询)"""
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
    for el in result.get("elements", []):
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
        elif el.get("type") == "relation" and "center" in el:
            geometry = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}

        if geometry:
            features.append({"type": "Feature", "geometry": geometry, "properties": props})

    return {"type": "FeatureCollection", "features": features}


async def _query_overpass(query: str) -> dict:
    """执行 Overpass QL 查询，返回 GeoJSON"""
    full_query = f"[out:json][timeout:30];{query}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            settings.OVERPASS_API_URL,
            data={"data": full_query},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                return {"type": "FeatureCollection", "features": [], "error": f"Overpass error {resp.status}"}
            data = await resp.text()
    return _overpass_to_geojson(data)


async def _geocode_to_bbox(area: str) -> Optional[list]:
    """用 Nominatim 将地名转为 bbox [south, west, north, east]"""
    params = {"q": area, "format": "json", "limit": 1, "accept-language": "zh", "polygon_geojson": "0"}
    url = settings.NOMINATIM_URL
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            results = await resp.json()
    if not results:
        return None
    bb = results[0].get("boundingbox")
    if bb and len(bb) == 4:
        # boundingbox: [south, north, west, east]
        return [float(bb[0]), float(bb[2]), float(bb[1]), float(bb[3])]
    return None


def register_osm_tools(registry: ToolRegistry):
    """注册 OSM 查询工具"""

    @tool(registry, name="query_osm_poi",
           description="查询 OpenStreetMap 中的兴趣点（POI），如餐厅、学校、医院等。会自动将地名转换为坐标范围进行查询。",
           param_descriptions={
               "area": "区域名称，如'北京'、'成都'、'海淀区'",
               "category": "POI 类别，如 restaurant/school/hospital/park/bank/cafe/bar",
               "limit": "返回数量上限，默认50"
           })
    async def query_osm_poi(area: str, category: str = "restaurant", limit: int = 50) -> dict:
        # Step 1: geocode area → bbox
        bbox = await _geocode_to_bbox(area)
        if not bbox:
            return {"type": "poi_query", "area": area, "category": category, "count": 0,
                    "geojson": {"type": "FeatureCollection", "features": []},
                    "error": f"无法定位: {area}"}

        # Step 2: Overpass bbox query
        query = f'node["amenity"="{category}"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});out {limit} body geom;'
        geojson = await _query_overpass(query)
        return {
            "type": "poi_query", "area": area, "category": category,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
        }

    @tool(registry, name="query_osm_roads",
           description="查询 OpenStreetMap 中的道路网络数据",
           param_descriptions={
               "bbox": "边界框 [south, west, north, east]，如 [39.8, 116.3, 39.95, 116.5]",
               "road_type": "道路类型，如 motorway/trunk/primary/secondary/tertiary/residential",
               "limit": "返回数量上限，默认100"
           })
    async def query_osm_roads(bbox: str, road_type: str = "primary", limit: int = 100) -> dict:
        parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
        if len(parts) != 4:
            return {"error": "bbox 格式错误，需要 [south, west, north, east]"}
        query = f'way["highway"="{road_type}"]({parts[0]},{parts[1]},{parts[2]},{parts[3]});out {limit} body geom;'
        geojson = await _query_overpass(query)
        return {"type": "road_query", "bbox": bbox, "road_type": road_type,
                "count": len(geojson.get("features", [])), "geojson": geojson}

    @tool(registry, name="query_osm_buildings",
           description="查询 OpenStreetMap 中的建筑物数据",
           param_descriptions={
               "bbox": "边界框 [south, west, north, east]",
               "limit": "返回数量上限，默认100"
           })
    async def query_osm_buildings(bbox: str, limit: int = 100) -> dict:
        parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
        if len(parts) != 4:
            return {"error": "bbox 格式错误"}
        query = f'way["building"]({parts[0]},{parts[1]},{parts[2]},{parts[3]});out {limit} body geom;'
        geojson = await _query_overpass(query)
        return {"type": "building_query", "bbox": bbox,
                "count": len(geojson.get("features", [])), "geojson": geojson}

    @tool(registry, name="query_osm_boundary",
           description="查询 OpenStreetMap 中的行政区划边界",
           param_descriptions={
               "name": "行政区名称，如'海淀区'、'北京市'",
               "admin_level": "行政级别，默认8（区级），4=省级，6=市级"
           })
    async def query_osm_boundary(name: str, admin_level: int = 6) -> dict:
        query = f'area[name="{name}"]["admin_level"="{admin_level}"]->.searchArea;relation["admin_level"="{admin_level}"](area.searchArea);out body geom;'
        geojson = await _query_overpass(query)
        # 如果 area 匹配失败，尝试 geocode → bbox 查询
        if not geojson.get("features"):
            bbox = await _geocode_to_bbox(name)
            if bbox:
                query2 = f'relation["admin_level"="{admin_level}"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});out body geom;'
                geojson = await _query_overpass(query2)
        return {"type": "boundary_query", "name": name, "admin_level": admin_level,
                "count": len(geojson.get("features", [])), "geojson": geojson}
