"""高德/百度/天地图工具包（M2 拆分自单文件 chinese_maps.py）。

公共入口：
- register_chinese_map_tools(registry) — 注册所有 LLM 工具
- geocode_cn / batch_geocode_cn — 跨 provider 地址→坐标 dispatcher

子模块：
- http.py     — _amap_get / _baidu_get / _tianditu_get + _has_provider + _fallback_order + _speed_mps
- amap.py     — Amap provider 实现
- baidu.py    — Baidu provider 实现
- tianditu.py — Tianditu provider 实现

为了让 register_chinese_map_tools 里现有的 `_dispatch = {"amap": _xxx_amap, ...}`
写法尽量少改，本 __init__.py 一次性把三个 provider 模块所有 _xxx 函数 import 进来。
"""
import asyncio
import json
import logging
from typing import Any, List, Optional

import aiohttp

from app.core.config import settings
from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
)

# HTTP + provider 路由
from app.tools.chinese_maps.http import (
    _has_provider, _fallback_order,
    _VALID_PROVIDERS,
    _amap_get, _baidu_get, _tianditu_get,
    _speed_mps,
)

# 三个 provider 的全部 _*_* 实现，按原名 import
from app.tools.chinese_maps.amap import (
    _search_poi_amap, _geocode_amap, _reverse_geocode_amap, _route_amap,
    _district_amap, _distance_matrix_amap,
    _isochrone_analysis, _get_route_distance_amap,
    _search_poi_around_amap, _search_poi_polygon_amap,
    _input_tips_amap, _transit_amap, _traffic_amap,
)
from app.tools.chinese_maps.baidu import (
    _search_poi_baidu, _geocode_baidu, _reverse_geocode_baidu, _route_baidu,
    _district_baidu, _distance_matrix_baidu,
    _search_poi_around_baidu, _search_poi_polygon_baidu,
    _input_tips_baidu,
)
from app.tools.chinese_maps.tianditu import (
    _search_poi_tianditu, _geocode_tianditu, _reverse_geocode_tianditu,
    _district_tianditu_v2, _district_tianditu,
    _search_poi_around_tianditu,
)

logger = logging.getLogger(__name__)


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

