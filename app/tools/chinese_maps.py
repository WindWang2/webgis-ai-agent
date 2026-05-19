"""高德/百度/天地图 API 工具 — POI搜索、地理编码、路径规划、行政区划查询"""
import asyncio
import json
import logging
import aiohttp
from typing import Optional, Any, List
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
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
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
            # Baidu 的 success 响应有时返回 Content-Type: text/javascript
            # （/geocoding/v3 就是这样），aiohttp .json() 默认会因此 ContentTypeError；
            # 用 content_type=None 跳过校验，让它纯按 JSON 解析。
            data = await resp.json(content_type=None)
            if data.get("status") != 0:
                await ht.record_error("baidu")
                return {"error": f"Baidu: {data.get('message', 'unknown error')}"}
            await ht.record_success("baidu")
            return data
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        await ht.record_error("baidu", e)
        raise


async def _tianditu_get(endpoint: str, params: dict) -> dict:
    if not await ht.record_attempt("tianditu"):
        return {"error": "天地图暂时不可用（频率限制或服务故障），请稍后重试"}
    
    if "tk" not in params:
        params["tk"] = settings.TIANDITU_TOKEN
        
    url = f"{_TIANDITU_BASE}{endpoint}"
    
    # 模拟浏览器 Header 以绕过 WAF 418 拦截
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.tianditu.gov.cn/",
        "Connection": "keep-alive"
    }
    
    try:
        session = await get_shared_client()
        async with session.get(
            url, params=params, headers=headers, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
        ) as resp:
            if resp.status != 200:
                await ht.record_error("tianditu")
                return {"error": f"Tianditu API HTTP {resp.status}"}
            
            # 天地图有些接口返回 text/plain 但内容是 JSON
            data = await resp.json(content_type=None)
            
            returncode = str(data.get("returncode", data.get("status", "")))
            if returncode not in ("100", "0"):
                await ht.record_error("tianditu")
                msg = data.get("msg") or data.get("message") or "unknown error"
                return {"error": f"Tianditu: {msg}"}
            await ht.record_success("tianditu")
            return data
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        # 网络/解析异常：上抛，使 geocode_cn fallback 链可尝试下一个 provider
        await ht.record_error("tianditu", e)
        raise
    except Exception as e:
        # 其他异常：记录后转为 error dict 返回（避免阻断调用方）
        await ht.record_error("tianditu", e)
        return {"error": f"Tianditu Error: {str(e)}"}


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
        except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"geocode_cn {p} failed: {e}")
    return {"error": "未配置任何地图 API Key"}


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
        # 不在这里 record_attempt/success/error —— 内层 _amap_get/_baidu_get/_tianditu_get
        # 已经做过熔断计数，重复打点会让 5 错熔断和速率窗口提前误触发。
        async with semaphore:
            try:
                result = await geocode_cn(addr, provider=provider)
                if "error" in result:
                    return {"index": idx, "status": "error", "address": addr, "error": str(result["error"])}
                return {"index": idx, "status": "ok", "address": addr, **result}
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                return {"index": idx, "status": "error", "address": addr, "error": str(e)}

    results = await asyncio.gather(*[_one(i, a) for i, a in enumerate(addresses)])

    ok = [r for r in results if r["status"] == "ok"]
    errs = [r for r in results if r["status"] != "ok"]
    return {"total": len(addresses), "success_count": len(ok), "error_count": len(errs),
            "results": ok, "errors": errs, "provider": provider}


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
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"search_poi {p} failed: {e}")
                errors.append(f"{p}: {e}")

        return {"error": f"所有服务商均失败: {'; '.join(errors)}" if errors else "未配置任何地图 API Key"}

    tool(registry, name="geocode_cn",
         description="中文地址转坐标，比 Nominatim 中文地址准确率更高，可选高德/百度/天地图",
         param_descriptions={
             "address": "中文地址，如'北京市海淀区中关村'",
             "city": "限定城市，如'北京'",
             "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
         })(geocode_cn)

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
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"reverse_geocode_cn {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    @tool(registry, tier=2, domains=["network"], name="plan_route",
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
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"plan_route {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key，路径规划需要 API Key"}

    @tool(registry, name="get_district",
           description="查询行政区划边界及下级单元列表。适合获取省、市、区/县的范围轮廓或下级列表。支持高德/百度/天地图。",
           param_descriptions={
               "keywords": "行政区划名称，如'海淀区'、'成都市'",
               "level": "级别: 'province', 'city', 'district'",
               "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
               "return_geometry": "返回几何类型: 'point'(默认,中心点), 'polygon'(返回完整的行政边界轮廓)",
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
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"get_district {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    tool(registry, name="batch_geocode_cn",
         description="批量中文地址转坐标，支持高德/百度/天地图。一次处理多条地址，带并发控制。返回每个地址的 WGS84 坐标、成功/失败状态和标准化地址。",
         param_descriptions={
             "addresses": "地址列表，最多100条，例如 ['北京市朝阳区','上海市浦东新区']",
             "provider": "服务商: 'amap'(默认，高德国内准确率最高)，'baidu'，'tianditu'",
             "max_concurrency": "最大并发调用数（默认3，防止触发限流）",
         })(batch_geocode_cn)

    @tool(registry, tier=2, domains=["network"], name="distance_matrix_cn",
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

    @tool(registry, tier=2, domains=["network"], name="isochrone_analysis",
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

    @tool(registry, name="search_poi_around",
           description="在指定坐标周围按半径搜索 POI。适合『附近 500 米的便利店』『地铁站周边餐厅』等近邻问题。返回 GeoJSON 点集。",
           param_descriptions={
               "center": "中心点 WGS84 坐标 [经度, 纬度]",
               "radius_m": "搜索半径（米），1~50000，默认 1000",
               "keyword": "搜索关键词（可选）；为空则按 types 检索",
               "types": "POI 分类编码或中文分类（可选），如 '050000'(餐饮) 或 '餐饮'",
               "provider": "服务商: 'amap'(默认), 'baidu', 'tianditu'",
               "limit": "返回结果数量，默认 20",
           })
    async def search_poi_around(
        center: list,
        radius_m: int = 1000,
        keyword: str = "",
        types: str = "",
        provider: str = "amap",
        limit: int = 20,
    ) -> dict:
        if not center or len(center) != 2:
            return {"error": "center 必须是 [经度, 纬度]"}
        if radius_m <= 0 or radius_m > 50000:
            return {"error": "radius_m 必须在 1~50000 之间"}
        if not keyword and not types:
            return {"error": "keyword 与 types 至少提供一个"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap', 'baidu' 或 'tianditu'"}

        _dispatch = {
            "amap": _search_poi_around_amap,
            "baidu": _search_poi_around_baidu,
            "tianditu": _search_poi_around_tianditu,
        }
        for p in _fallback_order(provider):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](center, radius_m, keyword, types, limit)
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"search_poi_around {p} failed: {e}")
        return {"error": "未配置任何地图 API Key"}

    @tool(registry, name="search_poi_polygon",
           description="多边形区域内搜索：在指定的闭合多边形区域内搜索 POI。适合『查询锦江区内的咖啡馆』等精准场景。注意：如果是行政区，请先拿边界再搜。",
           param_descriptions={
               "polygon": "闭合多边形坐标列表 [[lng,lat],...]，或 4 元素 bbox [w,s,e,n]，或 GeoJSON 要素引用(ref:xxx)",
               "keyword": "搜索关键词，如'咖啡'",
               "types": "POI 类型，如'餐饮服务'",
               "provider": "服务商: 'amap'(默认), 'baidu'",
               "limit": "返回数量限制，默认 50",
           })
    async def search_poi_polygon(
        polygon: Any,
        keyword: str = "",
        types: str = "",
        provider: str = "amap",
        limit: int = 50,
    ) -> dict:
        if not polygon:
            return {"error": "polygon 参数不能为空"}

        target_poly = []

        # 处理 GeoJSON 或引用解析后的 dict
        if isinstance(polygon, dict):
            # 尝试从 FeatureCollection 或 Feature 中提取多边形
            features = polygon.get("features", []) if polygon.get("type") == "FeatureCollection" else [polygon]
            for f in features:
                geom = f.get("geometry") or {}
                if geom.get("type") in ("Polygon", "MultiPolygon"):
                    # 取第一个多边形的外环作为搜索范围（API 限制）
                    coords = geom["coordinates"]
                    ring = coords[0] if geom["type"] == "Polygon" else coords[0][0]
                    target_poly = ring
                    break
            if not target_poly:
                return {"error": "无法从输入数据中提取有效的多边形边界"}
        elif isinstance(polygon, list):
            # bbox 形式 → 转 4 点多边形
            if len(polygon) == 4 and all(isinstance(v, (int, float)) for v in polygon):
                w, s, e, n = polygon
                target_poly = [[w, s], [e, s], [e, n], [w, n]]
            else:
                target_poly = polygon
        else:
            return {"error": f"不支持的多边形格式: {type(polygon)}"}

        if len(target_poly) < 3:
            return {"error": "多边形至少需要 3 个坐标点"}

        if not keyword and not types:
            return {"error": "keyword 与 types 至少提供一个"}

        _dispatch = {"amap": _search_poi_polygon_amap, "baidu": _search_poi_polygon_baidu}
        for p in _fallback_order(provider, exclude={"tianditu"}):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](target_poly, keyword, types, limit)
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"search_poi_polygon {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, name="input_tips",
           description="地点输入联想/纠错。给一个不完整或可能拼错的地名（如『中关创业大街』），返回候选地名+坐标，帮助消除歧义。比直接 geocode 更鲁棒，适合用户口语化输入。",
           param_descriptions={
               "keyword": "用户输入的地名片段，如『中关创』",
               "city": "限定城市（可选）",
               "location": "附近优先排序的坐标 [lng,lat]（可选）",
               "provider": "服务商: 'amap'(默认), 'baidu'",
           })
    async def input_tips(
        keyword: str,
        city: str = "",
        location: Optional[list] = None,
        provider: str = "amap",
    ) -> dict:
        if not keyword:
            return {"error": "keyword 不能为空"}
        if provider not in ("amap", "baidu"):
            return {"error": "provider 必须是 'amap' 或 'baidu'"}

        _dispatch = {"amap": _input_tips_amap, "baidu": _input_tips_baidu}
        for p in _fallback_order(provider, exclude={"tianditu"}):
            if not _has_provider(p):
                continue
            try:
                return await _dispatch[p](keyword, city, location)
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"input_tips {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, tier=2, domains=["network"], name="search_transit_route",
           description="公交路径规划：起终点之间的公交/地铁换乘方案，返回多个备选路线（步行段+乘车段），含换乘次数、总耗时、票价。仅支持 Amap。",
           param_descriptions={
               "origin": "起点 WGS84 [lng,lat]",
               "destination": "终点 WGS84 [lng,lat]",
               "city": "起点城市名（必填），如'北京'",
               "city_d": "终点城市名（跨城公交才需要）",
               "strategy": "策略: 0=最快捷, 1=最经济, 2=最少换乘, 3=最少步行, 5=不乘地铁。默认 0",
           })
    async def search_transit_route(
        origin: list,
        destination: list,
        city: str,
        city_d: str = "",
        strategy: int = 0,
    ) -> dict:
        if not city:
            return {"error": "公交查询必须传 city（起点城市）"}
        if len(origin) != 2 or len(destination) != 2:
            return {"error": "origin/destination 必须是 [lng,lat]"}
        if not _has_provider("amap"):
            return {"error": "公交查询当前仅支持 amap，请配置 AMAP_API_KEY"}
        return await _transit_amap(origin, destination, city, city_d, strategy)

    @tool(registry, tier=2, domains=["network"], name="get_traffic_status",
           description="查询实时路况：指定矩形或圆形范围内的道路拥堵情况。返回道路名+拥堵等级+长度。适合『现在三环堵不堵』『机场高速路况』等问题。仅 Amap。",
           param_descriptions={
               "mode": "查询模式: 'rectangle'(矩形) 或 'circle'(圆形)",
               "rectangle": "矩形左下右上 [west,south,east,north]（mode=rectangle 时）",
               "center": "圆心 [lng,lat]（mode=circle 时）",
               "radius_m": "圆半径米数（mode=circle，默认 1000）",
               "level": "拥堵等级过滤: 0=全部 1=畅通 2=缓行 3=拥堵 4=严重拥堵。默认 0",
           })
    async def get_traffic_status(
        mode: str = "rectangle",
        rectangle: Optional[list] = None,
        center: Optional[list] = None,
        radius_m: int = 1000,
        level: int = 0,
    ) -> dict:
        if mode not in ("rectangle", "circle"):
            return {"error": "mode 必须是 'rectangle' 或 'circle'"}
        if mode == "rectangle" and (not rectangle or len(rectangle) != 4):
            return {"error": "rectangle 模式需要 [west,south,east,north]"}
        if mode == "circle" and (not center or len(center) != 2):
            return {"error": "circle 模式需要 center=[lng,lat]"}
        if not _has_provider("amap"):
            return {"error": "实时路况当前仅支持 amap"}
        return await _traffic_amap(mode, rectangle, center, radius_m, level)

    @tool(registry, name="get_admin_division",
           description="查询行政区划：获取省、市、区/县的行政边界（GeoJSON）及下级行政单元列表。适合『成都市的轮廓』『查询锦江区下属街道』等场景。支持 Tianditu。",
           param_descriptions={
               "keywords": "行政区名称，如'成都市'、'锦江区'",
               "child_level": "是否查询下一级行政单元: 0=不查询(默认), 1=查询一级, 2=查询二级",
               "extensions": "是否返回行政边界轮廓（GeoJSON）: 'base'=不返回, 'all'=返回(默认)",
               "provider": "服务商: 'tianditu'(默认)",
           })
    async def get_admin_division(
        keywords: str,
        child_level: int = 0,
        extensions: str = "all",
        provider: str = "tianditu",
    ) -> dict:
        if not keywords:
            return {"error": "keywords 不能为空"}
        
        # 目前仅 Tianditu 支持较好的边界输出
        if provider != "tianditu":
            # 自动 fallback 到 tianditu 如果可用
            provider = "tianditu"

        if not _has_provider("tianditu"):
            return {"error": "行政区划查询需要配置 TIANDITU_TOKEN"}

        return await _district_tianditu_v2(keywords, child_level, extensions == "all")

    @tool(registry, name="get_child_districts",
           description="获取下级行政区列表及轮廓。例如『获取成都市的所有区县边界』或『获取锦江区的所有街道边界』。比多次调用 get_district 更高效。",
           param_descriptions={
               "keywords": "父级行政区名称，如'成都市'、'锦江区'",
               "return_geometry": "返回几何类型: 'point'(默认), 'polygon'(返回下级单位的完整轮廓)",
               "provider": "服务商: 'amap'(默认), 'tianditu'",
           })
    async def get_child_districts(keywords: str, return_geometry: str = "point", provider: str = "amap") -> dict:
        """获取下级行政区的列表及几何边界"""
        if not keywords:
            return {"error": "keywords 不能为空"}
            
        if provider == "tianditu" or not _has_provider("amap"):
            # 天地图 V2 本身就支持返回下级，且支持 polygon
            return await _district_tianditu_v2(keywords, child_level=1, return_polygon=(return_geometry == "polygon"))
        
        # 高德方案：先获取下级名称列表，然后（如果是 polygon 模式）并发获取每个下级的边界
        params = {"keywords": keywords, "subdistrict": "1", "extensions": "base"}
        data = await _amap_get("/config/district", params)
        if "error" in data: return data
        
        districts = data.get("districts", [])
        if not districts: return {"error": f"未找到 '{keywords}' 的下级行政区"}
        
        sub_units = districts[0].get("districts", [])
        if not sub_units: return {"error": f"'{keywords}' 没有更细分的下级单位"}
        
        if return_geometry == "point":
            features = []
            for s in sub_units:
                loc = s.get("center", "").split(",")
                if len(loc) == 2:
                    lng, lat = gcj02_to_wgs84(float(loc[0]), float(loc[1]))
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lng, lat]},
                        "properties": {"name": s.get("name"), "adcode": s.get("adcode"), "level": s.get("level")}
                    })
            return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "amap"}
        
        # Polygon 模式：并发请求每个子级的边界
        import asyncio
        tasks = []
        for s in sub_units:
            name = s.get("name")
            if name:
                tasks.append(_district_amap(name, level=s.get("level", "district"), return_geometry="polygon"))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_features = []
        for r in results:
            if isinstance(r, dict) and "features" in r:
                all_features.extend(r["features"])
        
        return {
            "type": "FeatureCollection", 
            "features": all_features, 
            "count": len(all_features), 
            "provider": "amap",
            "parent": keywords
        }


    @tool(registry, name="get_sub_districts_polygons",
           description="获取指定区域下属所有子单位的边界轮廓。例如『获取锦江区下属所有街道的边界』。适合做街道级空间统计。",
           param_descriptions={
               "keywords": "行政区名称，如'锦江区'、'成都市'",
               "provider": "服务商: 'amap'(默认), 'tianditu'",
           })
    async def get_sub_districts_polygons(keywords: str, provider: str = "amap") -> dict:
        """获取下级行政区的多边形边界"""
        # 封装 get_child_districts 的 polygon 模式，更方便 AI 发现和调用
        return await get_child_districts(keywords, return_geometry="polygon", provider=provider)

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
    result = data.get("result", {})
    loc = result.get("location", {})
    bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
    lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
    # Baidu geocoding v3 不返回 canonical 地址，回显用户输入并把精度等级单独暴露
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": address,
            "precision_level": result.get("level", ""),
            "confidence": result.get("confidence"),
            "comprehension": result.get("comprehension"),
            "province": "",
            "city": city,
            "district": "",
            "adcode": str(result.get("cityCode", "")),
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
    # subdistrict 参数含义：0:不返回下级, 1:返回下级, 2:返回下级及其下级, 3:返回下级及其下级及其下级
    # 我们默认设为 1 以便用户能看到下级行政区列表
    data = await _amap_get("/config/district", params)
    if "error" in data:
        return data
    districts = data.get("districts", [])
    features = []
    for d in districts:
        center = d.get("center", "").split(",")
        lng, lat = (gcj02_to_wgs84(float(center[0]), float(center[1])) if len(center) == 2 else (0, 0))

        if return_geometry == "polygon":
            # 高德行政区划边界字段名为 'polyline'
            polyline_str = d.get("polyline", "")
            if polyline_str:
                from shapely.geometry import Polygon, MultiPolygon
                from shapely import simplify
                
                polygons = []
                # 高德 polyline 可能包含多个部分，以 | 分隔
                for part in polyline_str.split("|"):
                    coords = [
                        [float(v) for v in pt.split(",")]
                        for pt in part.split(";") if pt
                    ]
                    if len(coords) >= 3:
                        wgs84_coords = [gcj02_to_wgs84(lon, lat) for lon, lat in coords]
                        # 闭合环
                        if wgs84_coords[0] != wgs84_coords[-1]:
                            wgs84_coords.append(wgs84_coords[0])
                        polygons.append(Polygon(wgs84_coords))
                
                if not polygons:
                    geometry = {"type": "Point", "coordinates": [lng, lat]}
                else:
                    if len(polygons) == 1:
                        geom_obj = polygons[0]
                    else:
                        geom_obj = MultiPolygon(polygons)
                    
                    # 简化几何以提高传输效率
                    simplified = simplify(geom_obj, tolerance=0.0005, preserve_topology=True)
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


async def _search_poi_tianditu(keyword: str, city: str, limit: int) -> dict:
    # specifyAdminCode 必须是数字行政区代码（如 "110000"），中文名传进去等于无过滤
    payload: dict = {
        "keyWord": keyword,
        "level": "12",
        "mapBound": "-180,-90,180,90",
        "queryType": "1",
        "start": "0",
        "count": str(min(limit, 50)),
    }
    if city and city.isdigit():
        payload["specifyAdminCode"] = city
    post_str = json.dumps(payload, ensure_ascii=False)
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
    ds = json.dumps({"keyWord": address}, ensure_ascii=False)
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
    post_str = json.dumps({"lon": lng, "lat": lat, "ver": 1})
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


async def _district_tianditu_v2(keywords: str, child_level: int, return_polygon: bool) -> dict:
    """天地图行政区划查询 V2 (支持边界轮廓)"""
    post_str = json.dumps({
        "searchWord": keywords,
        "searchType": "1",
        "needSubInfo": "true" if child_level > 0 else "false",
        "needAll": "false",
        "needPolygon": "true" if return_polygon else "false",
        "needPre": "true",
    }, ensure_ascii=False)
    
    data = await _tianditu_get("/administrative", {"postStr": post_str})
    if "error" in data:
        return data
    
    # 状态码 100 表示成功
    if str(data.get("status")) != "100":
        return {"error": f"Tianditu: {data.get('msg', '查询失败')}"}
        
    districts = data.get("data", [])
    if isinstance(districts, dict):
        districts = [districts]
        
    features = []
    
    def _parse_points(points_str):
        if not points_str: return None
        try:
            polygons = []
            for poly_str in points_str.split("|"):
                coords = []
                for pair in poly_str.split(";"):
                    parts = pair.split(",")
                    if len(parts) >= 2:
                        coords.append([float(parts[0]), float(parts[1])])
                if len(coords) >= 3:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    polygons.append([coords])
            if not polygons: return None
            if len(polygons) == 1:
                return {"type": "Polygon", "coordinates": polygons[0]}
            return {"type": "MultiPolygon", "coordinates": polygons}
        except Exception:
            return None

    for d in districts:
        # 主项
        main_geom = _parse_points(d.get("points", ""))
        if not main_geom:
            lng, lat = float(d.get("lnt", 0)), float(d.get("lat", 0))
            main_geom = {"type": "Point", "coordinates": [lng, lat]}
            
        features.append({
            "type": "Feature",
            "geometry": main_geom,
            "properties": {
                "name": d.get("name", ""),
                "cityCode": d.get("cityCode", ""),
                "level": d.get("adminType", ""),
                "is_parent": True
            },
        })
        
        # 下级项 (如果存在且 child_level > 0)
        if child_level > 0:
            child_data = d.get("child", [])
            for c in child_data:
                # 注意：天地图 child 节点通常不带 points，除非 searchType 设为特定值
                # 这里我们先尝试解析，如果没有则存为点
                c_geom = _parse_points(c.get("points", ""))
                if not c_geom:
                    c_lng, c_lat = float(c.get("lnt", 0)), float(c.get("lat", 0))
                    c_geom = {"type": "Point", "coordinates": [c_lng, c_lat]}
                
                features.append({
                    "type": "Feature",
                    "geometry": c_geom,
                    "properties": {
                        "name": c.get("name", ""),
                        "cityCode": c.get("cityCode", ""),
                        "level": c.get("adminType", ""),
                        "is_child": True,
                        "parent_name": d.get("name")
                    }
                })

    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "tianditu",
    }


async def _district_tianditu(keywords: str, level: str, return_geometry: str = "point") -> dict:
    post_str = json.dumps({
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
    """Amap 距离矩阵。

    - driving / walking 走 `/v3/distance`（一次请求完成全量 OD 计算）
        - type=1 驾车，type=3 步行
    - riding 该批量接口不支持，回退到 N×M 并发调用 /direction/bicycling

    所有坐标在请求前 WGS84 → GCJ-02。
    """
    # ── driving / walking：批量接口
    if mode in ("driving", "walking"):
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
            "type": "1" if mode == "driving" else "3",
        }
        data = await _amap_get("/distance", params)
        if "error" in data:
            return data

        results = data.get("results", [])
        matrix: list[list[dict | None]] = [
            [None] * len(destinations) for _ in range(len(origins))
        ]
        for item in results:
            # Amap origin_id/dest_id 从 1 开始计数
            oi = int(item.get("origin_id", 0)) - 1
            di = int(item.get("dest_id", 0)) - 1
            if 0 <= oi < len(origins) and 0 <= di < len(destinations):
                matrix[oi][di] = {
                    "origin_index": oi,
                    "dest_index": di,
                    "distance_km": float(item.get("distance", 0)) / 1000.0,
                    "duration_sec": int(item.get("duration", 0)),
                }
        return {
            "matrix": matrix,
            "origins_count": len(origins),
            "dests_count": len(destinations),
            "mode": mode,
            "provider": "amap",
        }

    # ── riding：批量接口不支持 → N×M 并发兜底
    semaphore = asyncio.Semaphore(6)

    async def _one(oi: int, di: int) -> dict | None:
        async with semaphore:
            dist_m = await _get_route_distance_amap(origins[oi], destinations[di], "riding")
            if dist_m <= 0:
                return None
            # 骑行速度估算 4.2 m/s 给个粗略 duration
            return {
                "origin_index": oi,
                "dest_index": di,
                "distance_km": dist_m / 1000.0,
                "duration_sec": int(dist_m / 4.2),
            }

    pairs = [(oi, di) for oi in range(len(origins)) for di in range(len(destinations))]
    flat = await asyncio.gather(*[_one(oi, di) for oi, di in pairs])
    matrix = [[None] * len(destinations) for _ in range(len(origins))]
    for (oi, di), cell in zip(pairs, flat):
        matrix[oi][di] = cell
    return {
        "matrix": matrix,
        "origins_count": len(origins),
        "dests_count": len(destinations),
        "mode": mode,
        "provider": "amap",
        "note": "Amap 批量距离接口不支持骑行，已通过 N×M 并发路径规划兜底",
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
        except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
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
            params = {"origin": o_str, "destination": d_str}
            data = await _amap_get("/direction/walking", params)
        elif mode == "riding":
            params = {"origin": o_str, "destination": d_str}
            data = await _amap_get("/direction/bicycling", params)
        else:  # driving
            params = {"origin": o_str, "destination": d_str, "strategy": "10"}
            data = await _amap_get("/direction/driving", params)

        if "error" in data:
            return 0.0
        route = data.get("route", {})
        paths = route.get("paths", [])
        if not paths:
            return 0.0
        return float(paths[0].get("distance", 0))
    except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 0.0


# ── Math / angle utilities ──────────────────────────────────────


def _speed_mps(mode: str) -> float:
    """各模式的典型速度（米/秒），用于等时圈半径估算。"""
    return {"driving": 13.9, "walking": 1.4, "riding": 4.2}[mode]


# ── POI around / polygon / input tips / transit / traffic ────────


async def _search_poi_around_amap(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    gcj_lng, gcj_lat = wgs84_to_gcj02(center[0], center[1])
    params = {
        "location": f"{gcj_lng},{gcj_lat}",
        "radius": str(radius_m),
        "offset": str(min(limit, 25)),
        "sortrule": "distance",
    }
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    data = await _amap_get("/place/around", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        lng, lat = gcj02_to_wgs84(float(loc[0]), float(loc[1]))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "distance_m": int(p.get("distance", 0) or 0),
                "tel": p.get("tel", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "center": center,
        "radius_m": radius_m,
        "provider": "amap",
    }


async def _search_poi_around_baidu(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    bd_lng, bd_lat = wgs84_to_bd09(center[0], center[1])
    params = {
        "query": keyword or types,
        "location": f"{bd_lat},{bd_lng}",
        "radius": str(radius_m),
        "page_size": str(min(limit, 20)),
        "scope": "2",
    }
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        b_lng, b_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(b_lng, b_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "distance_m": int(p.get("detail_info", {}).get("distance", 0) or 0),
                "tel": p.get("telephone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "center": center,
        "radius_m": radius_m,
        "provider": "baidu",
    }


async def _search_poi_around_tianditu(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    payload = {
        "keyWord": keyword or types,
        "queryRadius": str(radius_m),
        "pointLonlat": f"{center[0]},{center[1]}",
        "queryType": "3",  # 周边搜索
        "start": "0",
        "count": str(min(limit, 50)),
    }
    post_str = json.dumps(payload, ensure_ascii=False)
    data = await _tianditu_get("/search", {"postStr": post_str, "type": "query"})
    if "error" in data:
        return data
    pois = data.get("pois", [])
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
        "center": center,
        "radius_m": radius_m,
        "provider": "tianditu",
    }


async def _search_poi_polygon_amap(
    polygon: list, keyword: str, types: str, limit: int
) -> dict:
    # Amap polygon 参数：lng,lat|lng,lat|...
    gcj_pts = [wgs84_to_gcj02(p[0], p[1]) for p in polygon]
    poly_str = "|".join(f"{lng},{lat}" for lng, lat in gcj_pts)
    params = {"polygon": poly_str, "offset": str(min(limit, 25))}
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    data = await _amap_get("/place/polygon", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        lng, lat = gcj02_to_wgs84(float(loc[0]), float(loc[1]))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "polygon": polygon,
        "provider": "amap",
    }


async def _search_poi_polygon_baidu(
    polygon: list, keyword: str, types: str, limit: int
) -> dict:
    # Baidu place v2 不接收 polygon 直接参数；用 polygon 外接 bbox 近似
    lngs = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    w, e = min(lngs), max(lngs)
    s, n = min(lats), max(lats)
    sw_bd = wgs84_to_bd09(w, s)
    ne_bd = wgs84_to_bd09(e, n)
    params = {
        "query": keyword or types,
        "bounds": f"{sw_bd[1]},{sw_bd[0]},{ne_bd[1]},{ne_bd[0]}",
        "page_size": str(min(limit, 20)),
    }
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        b_lng, b_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(b_lng, b_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "polygon": polygon,
        "provider": "baidu",
        "note": "Baidu 用 polygon 外接矩形 (bbox) 近似查询",
    }


async def _input_tips_amap(
    keyword: str, city: str, location: Optional[list]
) -> dict:
    params = {"keywords": keyword}
    if city:
        params["city"] = city
        params["citylimit"] = "true"
    if location and len(location) == 2:
        lng, lat = wgs84_to_gcj02(location[0], location[1])
        params["location"] = f"{lng},{lat}"
    data = await _amap_get("/assistant/inputtips", params)
    if "error" in data:
        return data
    tips = data.get("tips", [])
    out = []
    for t in tips:
        loc_str = t.get("location", "")
        coords = None
        if isinstance(loc_str, str) and "," in loc_str:
            parts = loc_str.split(",")
            if len(parts) == 2:
                try:
                    lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                    coords = [lng, lat]
                except ValueError:
                    coords = None
        out.append({
            "name": t.get("name", ""),
            "district": t.get("district", ""),
            "address": t.get("address", ""),
            "location": coords,
            "adcode": t.get("adcode", ""),
        })
    return {"tips": out, "count": len(out), "provider": "amap"}


async def _input_tips_baidu(
    keyword: str, city: str, location: Optional[list]
) -> dict:
    params = {"query": keyword, "region": city or "全国"}
    if location and len(location) == 2:
        bd_lng, bd_lat = wgs84_to_bd09(location[0], location[1])
        params["location"] = f"{bd_lat},{bd_lng}"
    data = await _baidu_get("/place/v2/suggestion", params)
    if "error" in data:
        return data
    suggestions = data.get("result", [])
    out = []
    for s in suggestions:
        loc = s.get("location") or {}
        coords = None
        if loc.get("lng") and loc.get("lat"):
            lng, lat = bd09_to_wgs84(loc["lng"], loc["lat"])
            coords = [lng, lat]
        out.append({
            "name": s.get("name", ""),
            "district": s.get("district", ""),
            "address": s.get("address", ""),
            "location": coords,
            "adcode": str(s.get("city_id", "")),
        })
    return {"tips": out, "count": len(out), "provider": "baidu"}


async def _transit_amap(
    origin: list, destination: list, city: str, city_d: str, strategy: int
) -> dict:
    o_gcj = wgs84_to_gcj02(origin[0], origin[1])
    d_gcj = wgs84_to_gcj02(destination[0], destination[1])
    params: dict = {
        "origin": f"{o_gcj[0]},{o_gcj[1]}",
        "destination": f"{d_gcj[0]},{d_gcj[1]}",
        "city": city,
        "strategy": str(strategy),
    }
    if city_d:
        params["cityd"] = city_d
    data = await _amap_get("/direction/transit/integrated", params)
    if "error" in data:
        return data
    route = data.get("route", {})
    transits = route.get("transits", []) or []
    plans = []
    for t in transits[:5]:
        segments = []
        polyline = []
        for seg in t.get("segments", []):
            walking = seg.get("walking", {})
            bus = seg.get("bus", {})
            for step in walking.get("steps", []) or []:
                for loc in (step.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        try:
                            lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                            polyline.append([lng, lat])
                        except ValueError:
                            pass
            for bl in bus.get("buslines", []) or []:
                segments.append({
                    "type": "bus",
                    "name": bl.get("name", ""),
                    "departure_stop": bl.get("departure_stop", {}).get("name", ""),
                    "arrival_stop": bl.get("arrival_stop", {}).get("name", ""),
                    "via_num": int(bl.get("via_num", 0) or 0),
                })
                for loc in (bl.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        try:
                            lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                            polyline.append([lng, lat])
                        except ValueError:
                            pass
        plans.append({
            "duration_s": int(t.get("duration", 0) or 0),
            "walking_distance_m": int(t.get("walking_distance", 0) or 0),
            "cost_yuan": float(t.get("cost", 0) or 0),
            "transit_count": len([s for s in t.get("segments", []) if s.get("bus", {}).get("buslines")]),
            "segments": segments,
            "polyline": polyline,
        })
    return {
        "plans": plans,
        "count": len(plans),
        "provider": "amap",
    }


async def _traffic_amap(
    mode: str,
    rectangle: Optional[list],
    center: Optional[list],
    radius_m: int,
    level: int,
) -> dict:
    if mode == "rectangle":
        w, s, e, n = rectangle  # type: ignore[misc]
        # WGS84 → GCJ02 双角
        sw = wgs84_to_gcj02(w, s)
        ne = wgs84_to_gcj02(e, n)
        params = {"rectangle": f"{sw[0]},{sw[1]};{ne[0]},{ne[1]}"}
        endpoint = "/traffic/status/rectangle"
    else:
        gcj_lng, gcj_lat = wgs84_to_gcj02(center[0], center[1])  # type: ignore[index]
        params = {"location": f"{gcj_lng},{gcj_lat}", "radius": str(radius_m)}
        endpoint = "/traffic/status/circle"
    if level:
        params["level"] = str(level)
    data = await _amap_get(endpoint, params)
    if "error" in data:
        return data
    ts = data.get("trafficinfo", {})
    eval_block = ts.get("evaluation", {})
    roads = ts.get("roads", [])
    out_roads = []
    for r in roads:
        out_roads.append({
            "name": r.get("name", ""),
            "status": r.get("status", ""),
            "speed_kmh": float(r.get("speed", 0) or 0),
            "direction": r.get("direction", ""),
            "lcodes": r.get("lcodes", ""),
        })
    return {
        "description": ts.get("description", ""),
        "evaluation": {
            "status": eval_block.get("status", ""),
            "expedite": eval_block.get("expedite", ""),
            "congested": eval_block.get("congested", ""),
            "blocked": eval_block.get("blocked", ""),
            "unknown": eval_block.get("unknown", ""),
        },
        "roads": out_roads,
        "road_count": len(out_roads),
        "provider": "amap",
    }


