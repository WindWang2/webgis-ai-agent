"""高德/百度/天地图 API 工具 — POI搜索、地理编码、路径规划、行政区划查询"""
import asyncio
import logging
import aiohttp
from typing import Optional
from app.core.config import settings
from app.core.network import get_ssl_context, get_base_headers, get_shared_client
from app.services.provider_health import health_tracker as ht
from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
)

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = ("amap", "baidu", "tianditu")
_AMAP_BASE = "https://restapi.amap.com/v3"
_BAIDU_BASE = "https://api.map.baidu.com"
_TIANDITU_BASE = "https://api.tianditu.gov.cn"


def _has_provider(provider: str) -> bool:
    if provider == "amap":
        return bool(settings.AMAP_API_KEY)
    if provider == "baidu":
        return bool(settings.BAIDU_MAP_AK)
    if provider == "tianditu":
        return bool(settings.TIANDITU_TOKEN)
    return False


def _fallback_order(preferred: str, exclude: set[str] | None = None) -> list[str]:
    order = ["amap", "baidu", "tianditu"]
    if exclude:
        order = [p for p in order if p not in exclude]
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


async def _amap_get(endpoint: str, params: dict) -> dict:
    if not await ht.record_attempt("amap"):
        return {"error": "Amap 暂时不可用（频率限制或服务故障），请稍后重试"}
    params["key"] = settings.AMAP_API_KEY
    params["output"] = "json"
    url = f"{_AMAP_BASE}{endpoint}"
    try:
        session = await get_shared_client()
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
        ) as resp:
            if resp.status != 200:
                await ht.record_error("amap")
                return {"error": f"Amap API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != "1" and data.get("infocode") != "10000":
                await ht.record_error("amap")
                return {"error": f"Amap: {data.get('info', 'unknown error')}"}
            await ht.record_success("amap")
            return data
    except Exception as e:
        await ht.record_error("amap", e)
        raise


async def _baidu_get(endpoint: str, params: dict) -> dict:
    if not await ht.record_attempt("baidu"):
        return {"error": "百度地图暂时不可用（频率限制或服务故障），请稍后重试"}
    params["ak"] = settings.BAIDU_MAP_AK
    params["output"] = "json"
    url = f"{_BAIDU_BASE}{endpoint}"
    try:
        session = await get_shared_client()
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
        ) as resp:
            if resp.status != 200:
                await ht.record_error("baidu")
                return {"error": f"Baidu API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != 0:
                await ht.record_error("baidu")
                return {"error": f"Baidu: {data.get('message', 'unknown error')}"}
            await ht.record_success("baidu")
            return data
    except Exception as e:
        await ht.record_error("baidu", e)
        raise


async def _tianditu_get(endpoint: str, params: dict) -> dict:
    if not await ht.record_attempt("tianditu"):
        return {"error": "天地图暂时不可用（频率限制或服务故障），请稍后重试"}
    params["tk"] = settings.TIANDITU_TOKEN
    url = f"{_TIANDITU_BASE}{endpoint}"
    try:
        session = await get_shared_client()
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
        ) as resp:
            if resp.status != 200:
                await ht.record_error("tianditu")
                return {"error": f"Tianditu API HTTP {resp.status}"}
            data = await resp.json()
            returncode = str(data.get("returncode", data.get("status", "")))
            if returncode not in ("100", "0"):
                await ht.record_error("tianditu")
                msg = data.get("msg") or data.get("message") or "unknown error"
                return {"error": f"Tianditu: {msg}"}
            await ht.record_success("tianditu")
            return data
    except Exception as e:
        await ht.record_error("tianditu", e)
        raise


def register_chinese_map_tools(registry: ToolRegistry):

    @tool(registry, name="search_poi",
           description="搜索 POI（餐厅、学校、医院等），支持中文关键词和城市限定，可选高德/百度/天地图",
           param_descriptions={
               "keyword": "搜索关键词，如'火锅店'、'三甲医院'",
               "city": "城市名称，如'北京'、'上海'",
               "provider": "服务商: 'amap'(高德, 默认), 'baidu'(百度), 'tianditu'(天地图)",
               "limit": "返回结果数量，默认20",
           })
    async def search_poi(keyword: str, city: str = "", provider: str = "amap", limit: int = 20) -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'，收到: {provider}"}

        _dispatch = {
            "amap": _search_poi_amap, "baidu": _search_poi_baidu, "tianditu": _search_poi_tianditu,
        }
        errors = []
        for p in _fallback_order(provider):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](keyword, city, limit)
            except Exception as e:
                logger.warning(f"search_poi {p} failed: {e}")
                errors.append(f"{p}: {e}")

        return {"error": f"所有服务商均失败: {'; '.join(errors)}" if errors else "未配置任何地图 API Key"}

    @tool(registry, name="geocode_cn",
           description="中文地址转坐标，比 Nominatim 中文地址准确率更高，可选高德/百度/天地图",
           param_descriptions={
               "address": "中文地址，如'北京市海淀区中关村'",
               "city": "限定城市，如'北京'",
               "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
           })
    async def geocode_cn(address: str, city: str = "", provider: str = "amap") -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'"}

        _dispatch = {
            "amap": _geocode_amap, "baidu": _geocode_baidu, "tianditu": _geocode_tianditu,
        }
        for p in _fallback_order(provider):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](address, city)
            except Exception as e:
                logger.warning(f"geocode_cn {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    @tool(registry, name="reverse_geocode_cn",
           description="坐标转中文地址，返回详细地址和附近 POI，可选高德/百度/天地图",
           param_descriptions={
               "location": "WGS84 坐标 [经度, 纬度]",
               "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
           })
    async def reverse_geocode_cn(location: list, provider: str = "amap") -> dict:
        if len(location) != 2:
            return {"error": "location 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'"}

        _dispatch = {
            "amap": _reverse_geocode_amap, "baidu": _reverse_geocode_baidu,
            "tianditu": _reverse_geocode_tianditu,
        }
        for p in _fallback_order(provider):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](location[0], location[1])
            except Exception as e:
                logger.warning(f"reverse_geocode_cn {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    @tool(registry, name="plan_route",
           description="路径规划（驾车/步行/骑行/公交），返回距离、时间和路线坐标",
           param_descriptions={
               "origin": "起点 WGS84 坐标 [经度, 纬度]",
               "destination": "终点 WGS84 坐标 [经度, 纬度]",
               "mode": "出行方式: 'driving'(默认), 'walking', 'cycling', 'transit'",
               "city": "城市名（公交模式必填）",
               "provider": "服务商: 'amap'(默认) 或 'baidu'（天地图不支持路径规划）",
           })
    async def plan_route(origin: list, destination: list, mode: str = "driving", city: str = "", provider: str = "amap") -> dict:
        if len(origin) != 2 or len(destination) != 2:
            return {"error": "origin/destination 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        _dispatch = {"amap": _route_amap, "baidu": _route_baidu}
        for p in _fallback_order(provider, exclude={"tianditu"}):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](origin, destination, mode, city)
            except Exception as e:
                logger.warning(f"plan_route {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key，路径规划需要 API Key"}

    @tool(registry, name="get_district",
           description="查询行政区划边界，返回 GeoJSON 格式，可选高德/百度/天地图",
           param_descriptions={
               "keywords": "行政区划名称，如'海淀区'、'成都市'",
               "level": "级别: 'province', 'city', 'district'",
               "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
           })
    async def get_district(keywords: str, level: str = "district", provider: str = "amap",
                           return_geometry: str = "point") -> dict:
        if return_geometry not in ("point", "polygon"):
            return {"error": "return_geometry 必须是 'point' 或 'polygon'"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'"}

        _dispatch = {
            "amap": _district_amap, "baidu": _district_baidu, "tianditu": _district_tianditu,
        }
        for p in _fallback_order(provider):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](keywords, level, return_geometry)
            except Exception as e:
                logger.warning(f"get_district {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    @tool(registry, name="batch_geocode_cn",
           description="批量中文地址转坐标，支持高德/百度/天地图。一次处理多条地址，带并发控制。返回每个地址的 WGS84 坐标、成功/失败状态和标准化地址。",
           param_descriptions={
               "addresses": "地址列表，最多100条，例如 ['北京市朝阳区','上海市浦东新区']",
               "provider": "服务商: 'amap'(默认，高德国内准确率最高)，'baidu'，'tianditu'",
               "max_concurrency": "最大并发调用数（默认3，防止触发限流）",
           })
    async def batch_geocode_cn(
        addresses: list[str],
        provider: str = "amap",
        max_concurrency: int = 3,
    ) -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'"}
        if not addresses or len(addresses) > 100:
            return {"error": "地址列表长度必须在 1~100 之间"}
        if not _has_provider(provider):
            return {"error": f"未配置 {provider} API Key"}

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _one(idx: int, addr: str) -> dict:
            async with semaphore:
                if not await ht.record_attempt(provider):
                    return {"index": idx, "status": "skipped", "address": addr,
                            "error": f"{provider} 暂时不可用（频率限制或服务故障）"}
                try:
                    result = await geocode_cn(addr, provider=provider)
                    await ht.record_success(provider)
                    if "error" in result:
                        return {"index": idx, "status": "error", "address": addr, "error": str(result["error"])}
                    return {"index": idx, "status": "ok", "address": addr, **result}
                except Exception as e:
                    await ht.record_error(provider, e)
                    return {"index": idx, "status": "error", "address": addr, "error": str(e)}

        results = await asyncio.gather(*[_one(i, a) for i, a in enumerate(addresses)])

        ok = [r for r in results if r["status"] == "ok"]
        errs = [r for r in results if r["status"] != "ok"]
        return {"total": len(addresses), "success_count": len(ok), "error_count": len(errs),
                "results": ok, "errors": errs, "provider": provider}

    @tool(registry, name="distance_matrix_cn",
           description="OD距离矩阵：计算多个起点到多个终点之间的驾驶/步行/骑行距离和时间，结果为二维矩阵。适合物流选址、通勤可达性分析。",
           param_descriptions={
               "origins": "起点坐标列表 [[lng, lat], ...]，最多10个（WGS84）",
               "destinations": "终点坐标列表 [[lng, lat], ...]，最多10个（WGS84）",
               "mode": "出行方式: 'driving'(默认)，'walking'，'riding'",
               "provider": "服务商: 'amap'(默认)，'baidu'",
           })
    async def distance_matrix_cn(
        origins: list[list],
        destinations: list[list],
        mode: str = "driving",
        provider: str = "amap",
    ) -> dict:
        if provider not in ("amap", "baidu"):
            return {"error": "provider 必须是 'amap' 或 'baidu'"}
        if not origins or not destinations:
            return {"error": "origins 和 destinations 不能为空"}
        if len(origins) > 10 or len(destinations) > 10:
            return {"error": "origins 和 destinations 最多支持 10 个"}
        if mode not in ("driving", "walking", "riding"):
            return {"error": "mode 必须是 'driving', 'walking' 或 'riding'"}
        if not _has_provider(provider):
            return {"error": f"未配置 {provider} API Key"}

        if provider == "amap":
            return await _distance_matrix_amap(origins, destinations, mode)
        else:
            return await _distance_matrix_baidu(origins, destinations, mode)

    @tool(registry, name="isochrone_analysis",
           description="等时圈分析：从一个中心点出发，计算并可视化指定时间内可达的范围。支持驾驶/步行/骑行方向。返回 GeoJSON 面数据和半径米数。当前仅支持高德路径规划。",
           param_descriptions={
               "center": "中心点坐标 [lng, lat]（WGS84）",
               "minutes": "时间（分钟），如 5, 10, 15",
               "mode": "出行方式: 'driving'(默认)，'walking'，'riding'",
               "provider": "服务商: 'amap'(默认，当前仅支持高德)",
           })
    async def isochrone_analysis(
        center: list,
        minutes: int = 10,
        mode: str = "driving",
        provider: str = "amap",
    ) -> dict:
        if not center or len(center) != 2:
            return {"error": "center 必须是 [经度, 纬度] 格式"}
        if not isinstance(minutes, int) or minutes <= 0 or minutes > 60:
            return {"error": "minutes 必须是 1~60 的整数（分钟）"}
        if mode not in ("driving", "walking", "riding"):
            return {"error": "mode 必须是 'driving', 'walking' 或 'riding'"}
        if provider != "amap":
            return {"error": "等时圈分析当前仅支持 amap（高德）"}
        if not _has_provider(provider):
            return {"error": f"未配置 {provider} API Key"}

        return await _isochrone_analysis(provider, center, minutes, mode)


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


async def _district_amap(keywords: str, level: str, return_geometry: str = "point") -> dict:
    params = {"keywords": keywords, "subdistrict": "1", "extensions": "all" if return_geometry == "polygon" else "base"}
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

        if return_geometry == "polygon":
            polyline_str = d.get("boundary", "")
            if polyline_str:
                coords = [
                    [float(v) for v in pt.split(",")]
                    for pt in polyline_str.split(";") if pt
                ]
                wgs84_coords = [gcj02_to_wgs84(lon, lat) for lon, lat in coords]
                from shapely.geometry import LineString
                from shapely import simplify
                line = LineString(wgs84_coords)
                simplified = simplify(line, tolerance=0.001, preserve_topology=True)
                geometry = simplified.__geo_interface__
            else:
                geometry = {"type": "Point", "coordinates": [lng, lat]}
        else:
            geometry = {"type": "Point", "coordinates": [lng, lat]}

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "adcode": d.get("adcode", ""),
                "citycode": d.get("citycode", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features),
            "provider": "amap", "geometry_type": features[0]["geometry"]["type"] if features else "Point"}


async def _district_baidu(keywords: str, level: str, return_geometry: str = "point") -> dict:
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


# ── Tianditu (天地图) provider functions ───────────────────────
# CGCS2000 ≈ WGS84, no coordinate transformation needed

import json as _json


async def _search_poi_tianditu(keyword: str, city: str, limit: int) -> dict:
    post_str = _json.dumps({
        "keyWord": keyword,
        "level": "12",
        "mapBound": "-180,-90,180,90",
        "queryType": "1",
        "start": "0",
        "count": str(min(limit, 50)),
        "specifyAdminCode": city,
    }, ensure_ascii=False)
    data = await _tianditu_get("/search", {"postStr": post_str, "type": "query"})
    if "error" in data:
        return data
    pois = data.get("pois", [])
    if not pois and isinstance(data.get("resultType"), int):
        return {"type": "FeatureCollection", "features": [], "count": 0, "provider": "tianditu"}
    features = []
    for p in pois[:limit]:
        lonlat = p.get("lonlat", "").split(" ")
        if len(lonlat) != 2:
            continue
        lng, lat = float(lonlat[0]), float(lonlat[1])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "tel": p.get("phone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "tianditu",
    }


async def _geocode_tianditu(address: str, city: str) -> dict:
    ds = _json.dumps({"keyWord": address}, ensure_ascii=False)
    data = await _tianditu_get("/geocoder", {"ds": ds})
    if "error" in data:
        return data
    result = data.get("result", {})
    loc = result.get("location", {})
    lng, lat = loc.get("lon", 0), loc.get("lat", 0)
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": address,
            "level": result.get("level", ""),
        }],
        "count": 1,
        "provider": "tianditu",
    }


async def _reverse_geocode_tianditu(lng: float, lat: float) -> dict:
    post_str = _json.dumps({"lon": lng, "lat": lat, "ver": 1})
    data = await _tianditu_get("/geocoder", {"postStr": post_str, "type": "geocode"})
    if "error" in data:
        return data
    result = data.get("result", {})
    addr = result.get("addressComponent", {})
    return {
        "formatted_address": result.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("county", ""),
        "street": addr.get("street", ""),
        "street_number": addr.get("streetNumber", ""),
        "provider": "tianditu",
    }


async def _district_tianditu(keywords: str, level: str, return_geometry: str = "point") -> dict:
    post_str = _json.dumps({
        "searchWord": keywords,
        "searchType": "1",
        "needSubInfo": "true",
        "needAll": "false",
        "needPolygon": "false",
        "needPre": "true",
    }, ensure_ascii=False)
    data = await _tianditu_get("/administrative", {"postStr": post_str})
    if "error" in data:
        return data
    districts = data.get("data", [])
    if isinstance(districts, dict):
        districts = [districts]
    features = []
    for d in districts:
        lng = d.get("lnt", 0)
        lat = d.get("lat", 0)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("adminType", d.get("level", "")),
                "code": str(d.get("cityCode", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "tianditu"}


# ── Phase 2 helpers: distance matrix, isochrone ─────────────────


async def _distance_matrix_amap(
    origins: list[list],
    destinations: list[list],
    mode: str,
) -> dict:
    """Amap v3 驾车距离矩阵 API（一次请求完成全量 OD 计算）。"""
    # 将 WGS84 → GCJ-02 再送入 API
    origin_str = ";".join(
        f"{wgs84_to_gcj02(lng, lat)[0]},{wgs84_to_gcj02(lng, lat)[1]}"
        for lng, lat in origins
    )
    dest_str = ";".join(
        f"{wgs84_to_gcj02(lng, lat)[0]},{wgs84_to_gcj02(lng, lat)[1]}"
        for lng, lat in destinations
    )
    params = {
        "origins": origin_str,
        "destination": dest_str,
        "strategy": {"driving": 10, "walking": 4}[mode],
        "output": "json",
    }
    data = await _amap_get("/distance", params)
    if "error" in data:
        return data

    results = data.get("results", [])
    # Amap /v3/distance 返回扁平列表，每项含 origin_id/dest_id/distance/duration
    matrix: list[list[dict | None]] = [[None] * len(destinations) for _ in range(len(origins))]
    for item in results:
        oi = int(item.get("origin_id", 0))
        di = int(item.get("dest_id", 0))
        if 0 <= oi < len(origins) and 0 <= di < len(destinations):
            matrix[oi][di] = {
                "origin_index": oi,
                "dest_index": di,
                "distance_km": item.get("distance", 0) / 1000.0,  # 米→公里
                "duration_sec": item.get("duration", 0),
            }

    return {
        "matrix": matrix,
        "origins_count": len(origins),
        "dests_count": len(destinations),
        "mode": mode,
        "provider": "amap",
    }


async def _distance_matrix_baidu(
    origins: list[list],
    destinations: list[list],
    mode: str,
) -> dict:
    """Baidu v2 direction Matrix API — 一次请求完成全量 OD 计算。"""

    def _bd_fmt(lng: float, lat: float) -> str:
        # WGS84 → BD09，然后按"纬度,经度"格式提交给百度
        bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
        return f"{bd_lat},{bd_lng}"

    origin_str = "|".join(_bd_fmt(lo, la) for lo, la in origins)
    dest_str = "|".join(_bd_fmt(ld, la) for ld, la in destinations)
    mode_map = {"driving": "car", "walking": "foot", "riding": "bike"}
    params = {
        "origin": origin_str,
        "destination": dest_str,
        "mode": mode_map.get(mode, "car"),
    }
    data = await _baidu_get("/direction/v2/matrix", params)
    if "error" in data:
        return data

    result = data.get("result", {})
    rows = result.get("rows", [])
    matrix = []
    for ri, row in enumerate(rows):
        row_dist = []
        for ci, elem in enumerate(row.get("elements", [])):
            row_dist.append({
                "origin_index": ri,
                "dest_index": ci,
                "distance_km": elem.get("distance", {}).get("value", 0) / 1000.0,
                "duration_sec": elem.get("duration", {}).get("value", 0),
            })
        matrix.append(row_dist)
    return {
        "matrix": matrix,
        "origins_count": len(origins),
        "dests_count": len(destinations),
        "mode": mode,
        "provider": "baidu",
    }


async def _isochrone_analysis(
    provider: str,
    center: list,
    minutes: int,
    mode: str,
) -> dict:
    """沿 N 个方向调用路径规划 API，收集各方向在 `minutes` 时间内的最远到达点，用 Convex Hull 近似等时圈。"""
    import math

    num_radials = 12  # 每30°一条射线
    km_scale = {"driving": 1.0, "walking": 0.06, "riding": 0.35}[mode]
    angle_step = 2 * math.pi / num_radials

    # 生成候选径向目的地（在中心点周围大致方向）
    async def _radial_point(angle: float) -> tuple[float, float]:
        # 用单位向量 × 固定半径来确定方向，再做线性缩放
        probe_lng = center[0] + 0.01 * math.cos(angle) * km_scale
        probe_lat = center[1] + 0.01 * math.sin(angle) * km_scale

        try:
            dist_m = await _get_route_distance_amap(center, [probe_lng, probe_lat], mode)
            ratio = (minutes * 60 * _speed_mps(mode)) / max(dist_m, 1)
            capped_ratio = min(ratio, 1.0)  # 不超过探测器本身的位置
            return (
                center[0] + (probe_lng - center[0]) * capped_ratio,
                center[1] + (probe_lat - center[1]) * capped_ratio,
            )
        except Exception:
            # 回退：用均匀半径圆上的点
            fallback_radius_m = minutes * 60 * _speed_mps(mode)
            return (
                center[0] + fallback_radius_m * math.cos(angle) / 111000,
                center[1] + fallback_radius_m * math.sin(angle) / 111000,
            )

    semaphore = asyncio.Semaphore(6)
    angles = [angle_step * i for i in range(num_radials)]

    async def _guarded_radial(angle: float) -> tuple[float, float]:
        async with semaphore:
            return await _radial_point(angle)

    pts = await asyncio.gather(*[_guarded_radial(a) for a in angles])

    all_points = list(pts)

    if len(all_points) >= 3:
        from shapely.geometry import MultiPoint
        hull = MultiPoint(all_points).convex_hull
        geometry = hull.__geo_interface__
    else:
        geometry = {"type": "Point", "coordinates": center}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "center": center,
            "minutes": minutes,
            "mode": mode,
            "provider": provider,
            "radius_m": minutes * 60 * _speed_mps(mode),
        },
    }


async def _get_route_distance_amap(
    origin: list,
    destination: list,
    mode: str,
) -> float:
    """调用 Amap 路径规划 API，返回两点间单程距离（米）。用于等时圈半径探测。"""
    try:
        og_lng, og_lat = origin
        dg_lng, dg_lat = destination
        og_gcj = wgs84_to_gcj02(og_lng, og_lat)
        dg_gcj = wgs84_to_gcj02(dg_lng, dg_lat)
        o_str = f"{og_gcj[0]},{og_gcj[1]}"
        d_str = f"{dg_gcj[0]},{dg_gcj[1]}"

        if mode == "walking":
            params = {"origin": o_str, "destination": d_str, "strategy": "4"}
            data = await _amap_get("/direction/walking", params)
        elif mode == "riding":
            params = {"origin": o_str, "destination": d_str}
            data = await _amap_get("/direction/bicycling", params)
        else:  # driving
            params = {"origin": o_str, "destination": d_str, "strategy": "10"}
            data = await _amap_get("/direction/drive", params)

        if "error" in data:
            return 0.0
        route = data.get("route", {})
        paths = route.get("paths", [])
        if not paths:
            return 0.0
        return float(paths[0].get("distance", 0))
    except Exception:
        return 0.0


# ── Math / angle utilities ──────────────────────────────────────


def _speed_mps(mode: str) -> float:
    """各模式的典型速度（米/秒），用于等时圈半径估算。"""
    return {"driving": 13.9, "walking": 1.4, "riding": 4.2}[mode]


