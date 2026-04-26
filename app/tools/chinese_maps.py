"""高德地图/百度地图 API 工具 — POI搜索、地理编码、路径规划、行政区划查询"""
import logging
import aiohttp
from typing import Optional
from app.core.config import settings
from app.core.network import get_ssl_context, get_base_headers
from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
)

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = ("amap", "baidu")
_AMAP_BASE = "https://restapi.amap.com/v3"
_BAIDU_BASE = "https://api.map.baidu.com"


def _has_provider(provider: str) -> bool:
    if provider == "amap":
        return bool(settings.AMAP_API_KEY)
    if provider == "baidu":
        return bool(settings.BAIDU_MAP_AK)
    return False


def _provider_key(provider: str) -> str:
    if provider == "amap":
        return settings.AMAP_API_KEY
    return settings.BAIDU_MAP_AK


async def _amap_get(endpoint: str, params: dict) -> dict:
    params["key"] = settings.AMAP_API_KEY
    params["output"] = "json"
    url = f"{_AMAP_BASE}{endpoint}"
    async with aiohttp.ClientSession(headers=get_base_headers()) as session:
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {"error": f"Amap API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != "1" and data.get("infocode") != "10000":
                return {"error": f"Amap: {data.get('info', 'unknown error')}"}
            return data


async def _baidu_get(endpoint: str, params: dict) -> dict:
    params["ak"] = settings.BAIDU_MAP_AK
    params["output"] = "json"
    url = f"{_BAIDU_BASE}{endpoint}"
    async with aiohttp.ClientSession(headers=get_base_headers()) as session:
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {"error": f"Baidu API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != 0:
                return {"error": f"Baidu: {data.get('message', 'unknown error')}"}
            return data


def register_chinese_map_tools(registry: ToolRegistry):

    @tool(registry, name="search_poi",
           description="使用高德或百度地图搜索 POI（餐厅、学校、医院等），支持中文关键词和城市限定",
           param_descriptions={
               "keyword": "搜索关键词，如'火锅店'、'三甲医院'",
               "city": "城市名称，如'北京'、'上海'",
               "provider": "服务商: 'amap'(高德, 默认) 或 'baidu'(百度)",
               "limit": "返回结果数量，默认20",
           })
    async def search_poi(keyword: str, city: str = "", provider: str = "amap", limit: int = 20) -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'，收到: {provider}"}

        if _has_provider(provider):
            try:
                if provider == "amap":
                    return await _search_poi_amap(keyword, city, limit)
                else:
                    return await _search_poi_baidu(keyword, city, limit)
            except Exception as e:
                logger.warning(f"search_poi {provider} failed: {e}, trying fallback")

        # Try other provider as fallback
        other = "baidu" if provider == "amap" else "amap"
        if _has_provider(other):
            try:
                if other == "amap":
                    return await _search_poi_amap(keyword, city, limit)
                else:
                    return await _search_poi_baidu(keyword, city, limit)
            except Exception as e:
                return {"error": f"两个服务商均失败: {e}"}

        return {"error": "未配置高德或百度 API Key，请在 .env 中设置 AMAP_API_KEY 或 BAIDU_MAP_AK"}

    @tool(registry, name="geocode_cn",
           description="中文地址转坐标（高德/百度），比 Nominatim 中文地址准确率更高",
           param_descriptions={
               "address": "中文地址，如'北京市海淀区中关村'",
               "city": "限定城市，如'北京'",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def geocode_cn(address: str, city: str = "", provider: str = "amap") -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _geocode_amap(address, city)
                else:
                    return await _geocode_baidu(address, city)
            except Exception as e:
                logger.warning(f"geocode_cn {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, name="reverse_geocode_cn",
           description="坐标转中文地址（高德/百度），返回详细地址和附近 POI",
           param_descriptions={
               "location": "WGS84 坐标 [经度, 纬度]",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def reverse_geocode_cn(location: list, provider: str = "amap") -> dict:
        if len(location) != 2:
            return {"error": "location 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _reverse_geocode_amap(location[0], location[1])
                else:
                    return await _reverse_geocode_baidu(location[0], location[1])
            except Exception as e:
                logger.warning(f"reverse_geocode_cn {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, name="plan_route",
           description="路径规划（驾车/步行/骑行/公交），返回距离、时间和路线坐标",
           param_descriptions={
               "origin": "起点 WGS84 坐标 [经度, 纬度]",
               "destination": "终点 WGS84 坐标 [经度, 纬度]",
               "mode": "出行方式: 'driving'(默认), 'walking', 'cycling', 'transit'",
               "city": "城市名（公交模式必填）",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def plan_route(origin: list, destination: list, mode: str = "driving", city: str = "", provider: str = "amap") -> dict:
        if len(origin) != 2 or len(destination) != 2:
            return {"error": "origin/destination 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _route_amap(origin, destination, mode, city)
                else:
                    return await _route_baidu(origin, destination, mode, city)
            except Exception as e:
                logger.warning(f"plan_route {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key，路径规划需要 API Key"}

    @tool(registry, name="get_district",
           description="查询行政区划边界（高德/百度），返回 GeoJSON 格式",
           param_descriptions={
               "keywords": "行政区划名称，如'海淀区'、'成都市'",
               "level": "级别: 'province', 'city', 'district'",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def get_district(keywords: str, level: str = "district", provider: str = "amap") -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _district_amap(keywords, level)
                else:
                    return await _district_baidu(keywords, level)
            except Exception as e:
                logger.warning(f"get_district {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}


# ── Provider-specific helper functions ──────────────────────────

async def _search_poi_amap(keyword: str, city: str, limit: int) -> dict:
    params = {"keywords": keyword, "city": city, "citylimit": "true" if city else "false", "offset": str(limit)}
    data = await _amap_get("/place/text", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
                "city": p.get("cityname", ""),
                "district": p.get("adname", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "amap",
    }


async def _search_poi_baidu(keyword: str, city: str, limit: int) -> dict:
    params = {"query": keyword, "region": city or "全国", "page_size": str(min(limit, 20))}
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
                "city": p.get("city", ""),
                "district": p.get("area", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "baidu",
    }


async def _geocode_amap(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _amap_get("/geocode/geo", params)
    if "error" in data:
        return data
    geocodes = data.get("geocodes", [])
    if not geocodes:
        return {"results": [], "count": 0}
    results = []
    for g in geocodes:
        loc = g.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        results.append({
            "location": [lng, lat],
            "formatted_address": g.get("formatted_address", ""),
            "province": g.get("province", ""),
            "city": g.get("city", ""),
            "district": g.get("district", ""),
            "adcode": g.get("adcode", ""),
        })
    return {"results": results, "count": len(results), "provider": "amap"}


async def _geocode_baidu(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _baidu_get("/geocoding/v3/", params)
    if "error" in data:
        return data
    loc = data.get("result", {}).get("location", {})
    bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
    lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": data.get("result", {}).get("level", ""),
            "province": "",
            "city": city,
            "district": "",
            "adcode": str(data.get("result", {}).get("cityCode", "")),
        }],
        "count": 1,
        "provider": "baidu",
    }


async def _reverse_geocode_amap(lng: float, lat: float) -> dict:
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    params = {"location": f"{gcj_lng},{gcj_lat}", "extensions": "all"}
    data = await _amap_get("/geocode/regeo", params)
    if "error" in data:
        return data
    r = data.get("regeocode", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("streetNumber", {}).get("street", ""),
        "street_number": addr.get("streetNumber", {}).get("number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "amap",
    }


async def _reverse_geocode_baidu(lng: float, lat: float) -> dict:
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    params = {"location": f"{bd_lat},{bd_lng}", "extensions_poi": 1}
    data = await _baidu_get("/reverse_geocoding/v3/", params)
    if "error" in data:
        return data
    r = data.get("result", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("street", ""),
        "street_number": addr.get("street_number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "baidu",
    }


async def _route_amap(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "bicycling", "transit": "transit/integrated"}
    endpoint = mode_map.get(mode, "driving")
    o_gcj = wgs84_to_gcj02(origin[0], origin[1])
    d_gcj = wgs84_to_gcj02(dest[0], dest[1])
    params = {"origin": f"{o_gcj[0]},{o_gcj[1]}", "destination": f"{d_gcj[0]},{d_gcj[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _amap_get(f"/direction/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"error": "未找到路线"}
    path = paths[0]
    steps_out = []
    polyline = []
    for step in path.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("polyline", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                gcj_lng, gcj_lat = float(parts[0]), float(parts[1])
                lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(path.get("distance", 0)),
        "duration_s": int(path.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "amap",
    }


async def _route_baidu(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "riding", "transit": "transit"}
    endpoint = mode_map.get(mode, "driving")
    o_bd = wgs84_to_bd09(origin[0], origin[1])
    d_bd = wgs84_to_bd09(dest[0], dest[1])
    params = {"origin": f"{o_bd[0]},{o_bd[1]}", "destination": f"{d_bd[0]},{d_bd[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _baidu_get(f"/directionlite/v1/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("result", {}).get("routes", [])
    if not route:
        return {"error": "未找到路线"}
    r = route[0]
    steps_out = []
    polyline = []
    for step in r.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("path", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                bd_lng, bd_lat = float(parts[0]), float(parts[1])
                lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(r.get("distance", 0)),
        "duration_s": int(r.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "baidu",
    }


async def _district_amap(keywords: str, level: str) -> dict:
    params = {"keywords": keywords, "subdistrict": "1", "extensions": "base"}
    level_map = {"country": "0", "province": "1", "city": "2", "district": "3"}
    if level in level_map:
        params["subdistrict"] = level_map[level]
    data = await _amap_get("/config/district", params)
    if "error" in data:
        return data
    districts = data.get("districts", [])
    features = []
    for d in districts:
        center = d.get("center", "").split(",")
        lng, lat = (gcj02_to_wgs84(float(center[0]), float(center[1])) if len(center) == 2 else (0, 0))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "adcode": d.get("adcode", ""),
                "citycode": d.get("citycode", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "amap"}


async def _district_baidu(keywords: str, level: str) -> dict:
    params = {"q": keywords}
    data = await _baidu_get("/api/v2/administrative", params)
    if "error" in data:
        return data
    districts = data.get("results", [])
    features = []
    for d in districts:
        loc = d.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "code": str(d.get("code", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "baidu"}
