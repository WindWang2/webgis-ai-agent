"""OSM 数据查询工具 - Overpass API (修复版)"""
import json
import logging
import aiohttp
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.network import get_ssl_context, get_base_headers
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)




def _sanitize_overpass_value(value: str) -> str:
    """Escape characters that have special meaning in Overpass QL strings."""
    # Remove characters that could break out of a quoted Overpass QL value
    return str(value).replace("\\", "").replace('"', "").replace("]", "").replace(";", "").replace("\n", "").replace("\r", "")


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
    full_query = f"[out:json][timeout:30];{query.rstrip(';')};out body geom;"
    logger.info(f"[OSM] Querying Overpass API...")
    
    try:
        async with aiohttp.ClientSession(headers=get_base_headers()) as session:
            async with session.post(
                settings.OVERPASS_API_URL,
                data={"data": full_query},
                timeout=aiohttp.ClientTimeout(total=60),
                ssl=get_ssl_context(),
                proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY
                        ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[OSM] Overpass error {resp.status}: {text}")
                    return {"type": "FeatureCollection", "features": [], "error": f"Overpass error {resp.status}: {text}"}
                data = await resp.text()
                logger.info(f"[OSM] Overpass query successful, data size: {len(data)} bytes")
    except aiohttp.ClientError as e:
        logger.error(f"[OSM] Overpass network/timeout error: {e}")
        return {"type": "FeatureCollection", "features": [], "error": str(e)}

    return _overpass_to_geojson(data)


async def _geocode_bbox(query: str, expand_km: float = 0) -> Optional[str]:
    """通过 Nominatim 地理编码获取边界框，返回 'south,west,north,east' 格式"""
    params = {
        "q": query,
        "format": "json",
        "limit": 5,
        "accept-language": "zh",
    }
    async with aiohttp.ClientSession(headers=get_base_headers()) as session:
        async with session.get(
            settings.NOMINATIM_URL, 
            params=params, 
            ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY
            ) as resp:
            if resp.status != 200:
                logger.error(f"Nominatim error: {resp.status}")
                return None
            results = await resp.json()

    if not results:
        return None

    # 按 importance 降序排序，选择最相关的结果
    results.sort(key=lambda r: float(r.get("importance", 0)), reverse=True)
    best = results[0]

    bb = best.get("boundingbox")
    lat = float(best.get("lat", 0))
    lon = float(best.get("lon", 0))

    if bb and len(bb) == 4:
        south, north, west, east = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
    else:
        south, north = lat - 0.05, lat + 0.05
        west, east = lon - 0.05, lon + 0.05

    if expand_km > 0:
        delta = expand_km / 111.0
        south -= delta
        north += delta
        west -= delta
        east += delta

    return f"{south},{west},{north},{east}"


async def _nominatim_search_poi(category: str, bbox: str, limit: int) -> dict:
    """通过 Nominatim Search API 查询 POI（Overpass 备选方案）"""
    parts = bbox.split(",")
    if len(parts) != 4:
        return {"type": "FeatureCollection", "features": []}

    south, west, north, east = [float(p) for p in parts]
    # Nominatim viewbox 参数
    params = {
        "q": category,
        "format": "json",
        "limit": limit,
        "accept-language": "zh",
        "viewbox": f"{west},{south},{east},{north}",
        "bounded": "1",
    }
    features = []
    async with aiohttp.ClientSession(headers={"User-Agent": "WebGIS-AI-Agent/1.0"}) as session:
        async with session.get(settings.NOMINATIM_URL, params=params, ssl=_get_ssl_context(),
            ) as resp:
            if resp.status != 200:
                return {"type": "FeatureCollection", "features": []}
            results = await resp.json()

    for r in results:
        lat = float(r.get("lat", 0))
        lon = float(r.get("lon", 0))
        props = {
            "name": r.get("name", r.get("display_name", "").split(",")[0]),
            "type": r.get("type", ""),
            "class": r.get("class", ""),
            "display_name": r.get("display_name", ""),
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    return {"type": "FeatureCollection", "features": features}


class QueryOsmPoiArgs(BaseModel):
    area: str = Field(..., description="区域名称或地名，如'北京'、'成都天府广场5公里内'")
    category: str = Field("restaurant", description="POI 类别，如 restaurant/school/hospital/park/bank/cafe/bar")
    limit: int = Field(50, ge=1, le=500, description="返回数量上限，范围 1-500")

def register_osm_tools(registry: ToolRegistry):
    """注册 OSM 查询工具"""

    @tool(registry, name="query_osm_poi",
           description=(
               "在 OpenStreetMap (Overpass API) 内查询区域内的兴趣点 (POI)，返回 GeoJSON 点要素集。"
               "\n何时用：用户给的是区域+POI类型 (如『成都的学校』『海淀区的医院』)；"
               "需要全量 / 大批 POI 而不是周边几条结果；预算允许等待 (Overpass 可能 3-10s)。"
               "\n何时不用：(1) 用户问『附近 500 米的便利店』 — 用 search_poi_around (按半径搜)；"
               "(2) 已知一个闭合多边形要查内部 POI — 用 search_poi_polygon (Amap 数据质量更好)；"
               "(3) 区域明显跨多个国家 — Overpass 会超时，分批或换 chinese_maps 工具。"
               "\n关键约束：area 必须能被地理编码 (Nominatim)；POI 类别基于 OSM amenity/shop/leisure tag。"
           ),
           args_model=QueryOsmPoiArgs)
    async def query_osm_poi(area: str, category: str = "restaurant", limit: int = 50) -> dict:
        # 从 area 中提取距离信息（如 "5公里内"、"3km"）并扩大搜索范围
        import re
        dist_match = re.search(r'(\d+)\s*(公里|千米|km|公里内)', area, re.IGNORECASE)
        # 默认不外扩，或者仅外扩 1km 以防点不在正中心
        expand_km = float(dist_match.group(1)) if dist_match else 1.0 
        
        # 如果关键词包含“区”、“县”、“街道”，通常不需要大幅外扩
        if not dist_match and any(x in area for x in ["区", "县", "街道", "镇", "市"]):
            expand_km = 0

        # 提取纯地名
        clean_area = re.sub(r'\d+\s*(公里|千米|km|公里内|内).*$', '', area, flags=re.IGNORECASE).strip()
        if not clean_area:
            clean_area = area

        # 先地理编码获取 bbox，并扩大范围
        bbox = await _geocode_bbox(clean_area, expand_km=expand_km)
        if not bbox:
            raise ValueError(f"无法地理编码: {clean_area}")

        # 中文 category 转英文
        category_map = {
            "大学": "university", "高校": "university", "高等学校": "university",
            "学校": "school", "中小学": "school",
            "医院": "hospital", "诊所": "clinic",
            "餐厅": "restaurant", "餐馆": "restaurant", "饭店": "restaurant",
            "银行": "bank",
            "咖啡": "cafe", "咖啡厅": "cafe", "咖啡店": "cafe",
            "酒吧": "bar",
            "公园": "park", "花园": "garden",
            "酒店": "hotel", "宾馆": "hotel", "旅馆": "hotel",
            "博物馆": "museum",
            "图书馆": "library",
            "药店": "pharmacy", "药房": "pharmacy",
            "加油站": "fuel",
            "停车场": "parking",
            "公交站": "bus_station", "汽车站": "bus_station",
            "派出所": "police", "警察局": "police",
            "消防站": "fire_station", "消防局": "fire_station",
            "邮局": "post_office",
            "剧院": "theatre", "剧场": "theatre",
            "电影院": "cinema",
            "体育馆": "sports_centre", "体育场": "stadium",
            "游泳池": "swimming_pool",
            "幼儿园": "kindergarten", "托儿所": "kindergarten",
            "学院": "college",
        }
        mapped_category = category_map.get(category, category)

        # 构造 Overpass 查询 - 查询 bbox 内的 POI
        # amenity 类型
        amenity_types = {"restaurant", "school", "hospital", "bank", "cafe", "bar", "pharmacy",
                         "police", "fire_station", "post_office", "library", "cinema", "theatre",
                         "parking", "fuel", "bus_station", "university", "college", "kindergarten"}
        # leisure 类型
        leisure_types = {"park", "garden", "playground", "sports_centre", "swimming_pool", "stadium"}
        # tourism 类型
        tourism_types = {"hotel", "museum", "attraction", "viewpoint", "hostel"}

        safe_category = _sanitize_overpass_value(mapped_category)
        if mapped_category in leisure_types:
            tag_filter = f'"leisure"="{safe_category}"'
        elif mapped_category in tourism_types:
            tag_filter = f'"tourism"="{safe_category}"'
        else:
            tag_filter = f'"amenity"="{safe_category}"'

        query = f'node[{tag_filter}]({bbox});way[{tag_filter}]({bbox});relation[{tag_filter}]({bbox});'
        geojson = await _query_overpass(query)

        # Overpass 失败时，fallback 到 Nominatim 搜索
        if geojson.get("error") or len(geojson.get("features", [])) == 0:
            # Overpass 明确报错，抛出异常以触发标准化错误响应
            if geojson.get("error"):
                 raise RuntimeError(geojson["error"])

            # 用中英文关键词搜索（优先使用英文 tag，增加成功率）
            category_names = {
                "park": ["park", "公园"], "garden": ["garden", "花园"],
                "school": ["school", "学校"], "hospital": ["hospital", "医院"],
                "restaurant": ["restaurant", "餐厅"], "bank": ["bank", "银行"],
                "hotel": ["hotel", "酒店"], "museum": ["museum", "博物馆"],
                "cafe": ["cafe", "咖啡店"], "pharmacy": ["pharmacy", "药店"],
                "library": ["library", "图书馆"],
                "university": ["university", "大学"], "college": ["college", "学院"],
                "kindergarten": ["kindergarten", "幼儿园"],
                "police": ["police", "警察局"], "fire_station": ["fire station", "消防站"],
                "post_office": ["post office", "邮局"],
                "bus_station": ["bus station", "公交站"], "parking": ["parking", "停车场"],
                "fuel": ["fuel", "加油站"],
            }
            search_terms = category_names.get(mapped_category, [mapped_category])
            nom_geojson = {"type": "FeatureCollection", "features": []}
            for term in search_terms:
                nom_geojson = await _nominatim_search_poi(term, bbox, limit // len(search_terms))
                if len(nom_geojson.get("features", [])) > 0:
                    break

            # 使用 Nominatim 结果作为 fallback
            if len(nom_geojson.get("features", [])) > 0:
                geojson = nom_geojson
            else:
                # 依然没找到数据，抛出异常引导 AI 自愈或向用户解释
                raise ValueError(f"在区域 '{clean_area}' 内找不到类别为 '{category}' 的兴趣点。")

        return {
            "type": "poi_query",
            "area": area,
            "category": category,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
        }

    @tool(registry, name="query_osm_roads",
           description=(
               "OSM 道路网络查询：按区域+道路等级拉取 LineString 路网 GeoJSON。"
               "\n何时用：路径规划/可达性分析需要路网底图；按等级筛选 (highway/primary 主干道) 做密度统计。"
               "\n何时不用：仅需路径规划终端结果 — 用 search_route_cn (高德路径) 或 isochrone_analysis (等时圈)；"
               "需要实时路况 — 用 get_traffic_status。"
               "\n关键约束：road_type 是 OSM highway tag 值，常见: motorway/primary/secondary/tertiary/residential/footway。"
           ),
           param_descriptions={
               "area": "区域名称，如 '成都' '海淀区'。会先地理编码取 bbox",
               "road_type": "OSM highway tag 值。常用 primary(主干) / secondary(次干) / residential(支路)",
               "limit": "返回上限，默认 100。大区域 + 低等级路（如 residential）极易超量",
           })
    async def query_osm_roads(area: str, road_type: str = "primary", limit: int = 100) -> dict:
        bbox = await _geocode_bbox(area)
        if not bbox:
            raise ValueError(f"无法地理编码: {area}")

        query = f'way["highway"="{_sanitize_overpass_value(road_type)}"]({bbox});'
        geojson = await _query_overpass(query)
        if geojson.get("error"):
            raise RuntimeError(geojson["error"])
        if len(geojson.get("features", [])) == 0:
            raise ValueError(f"在区域 '{area}' 内找不到类型为 '{road_type}' 的道路数据。")

        return {
            "type": "road_query",
            "area": area,
            "road_type": road_type,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
        }

    @tool(registry, name="query_osm_buildings",
           description=(
               "OSM 建筑物轮廓查询：在指定区域拉取所有带 building=* tag 的多边形 GeoJSON。"
               "\n何时用：用户要看建筑物轮廓底图；做建筑密度/容积率/建筑年代统计；"
               "城市肌理可视化（结合 buffer_analysis）。"
               "\n何时不用：用户问『XX 建筑的信息』(单体查询) — 用 search_poi_around；"
               "需要建筑高度 — OSM 仅部分城市有 building:levels，可能空。"
               "\n关键约束：大城市中心 (如北京三环内) 一次拉可能 10k+ 要素，建议先缩小 area。"
           ),
           param_descriptions={
               "area": "区域名称（街道/小区/POI 级精度更好），如 '成都春熙路'。会被地理编码为 bbox",
               "limit": "返回上限，默认 100。Overpass 服务器对超量请求会拒绝",
           })
    async def query_osm_buildings(area: str, limit: int = 100) -> dict:
        bbox = await _geocode_bbox(area)
        if not bbox:
            raise ValueError(f"无法地理编码: {area}")

        query = f'way["building"]({bbox});'
        geojson = await _query_overpass(query)
        if geojson.get("error"):
            raise RuntimeError(geojson["error"])
        if len(geojson.get("features", [])) == 0:
            raise ValueError(f"在区域 '{area}' 内找不到建筑物数据。")

        return {
            "type": "building_query",
            "area": area,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
        }

    @tool(registry, name="query_osm_boundary",
           description=(
               "OSM 行政边界轮廓查询：拉取一个行政区的多边形 GeoJSON。Overpass 失败时自动 fallback 到 Nominatim。"
               "\n何时用：需要国际通用的行政边界（OSM 数据全球覆盖）；用作 clip_layer 的遮罩；做空间统计的母图层。"
               "\n何时不用：中国境内 — 优先 get_local_admin_boundary (本地 SHP，更稳更快)，"
               "或 get_admin_division (天地图官方界线)；要下级单元列表 — 用 get_child_districts。"
               "\n关键约束：admin_level 是 OSM 体系（4=省级/state, 6=市级/prefecture, 8=区/county, 10=街道）；不同国家约定不同。"
           ),
           param_descriptions={
               "name": "行政区名称，需与 OSM 数据一致。如 '海淀区' '成都市' 'California'",
               "admin_level": "OSM admin_level，中国常用 4(省) / 6(市) / 8(区县)。默认 8",
           })
    async def query_osm_boundary(name: str, admin_level: int = 8) -> dict:
        # 先尝试 Overpass
        query = f'relation["admin_level"="{int(admin_level)}"]["name"="{_sanitize_overpass_value(name)}"]->.searchArea;.searchArea out body geom;'
        geojson = await _query_overpass(query)

        # Overpass 失败时，用 Nominatim 搜索行政边界
        if len(geojson.get("features", [])) == 0:
            params = {
                "q": name,
                "format": "json",
                "limit": 1,
                "accept-language": "zh",
                "polygon_geojson": "1",
            }
            async with aiohttp.ClientSession(headers=get_base_headers()) as session:
                async with session.get(
                    settings.NOMINATIM_URL, 
                    params=params, 
                    ssl=get_ssl_context(),
                    proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY
                ) as resp:
                    if resp.status == 200:
                        results = await resp.json()
                        if results:
                            r = results[0]
                            geojson_poly = r.get("geojson")
                            if geojson_poly:
                                geojson = {
                                    "type": "FeatureCollection",
                                    "features": [{
                                        "type": "Feature",
                                        "geometry": geojson_poly,
                                        "properties": {
                                            "name": r.get("name", name),
                                            "display_name": r.get("display_name", ""),
                                        },
                                    }],
                                }

        return {
            "type": "boundary_query",
            "name": name,
            "admin_level": admin_level,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
        }
