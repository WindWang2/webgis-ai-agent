"""地理编码工具 - Nominatim"""
import logging
import aiohttp
from app.core.config import settings
from app.core.network import get_ssl_context, get_base_headers
from app.tools.registry import ToolRegistry, tool


def register_geocoding_tools(registry: ToolRegistry):
    """注册地理编码工具到 registry"""

    @tool(registry, name="geocode",
           description=(
               "地名 → WGS84 经纬度坐标 (使用 OpenStreetMap Nominatim，国际通用、无 key 限制)。"
               "\n何时用：用户给的是英文地名、海外地址、或不确定 provider 时的兜底；"
               "需要 importance 排序选择最权威匹配项。"
               "\n何时不用：中文行政区/POI/精确街道 — 改用 geocode_cn（高德/百度数据更全更准），"
               "或 input_tips（处理拼错/不完整）。需要边界轮廓（非点位）则用 get_district。"
               "\n返回：results=[{name,lat,lon,type,importance}]，按 importance 降序。"
           ),
           param_descriptions={
               "query": "完整地名或地址，如 'Beijing'、'Tiananmen Square'、'1600 Pennsylvania Ave'",
               "limit": "返回候选数，默认 5。结果按权威度排序，第 1 个通常最准",
           })
    async def geocode(query: str, limit: int = 5) -> dict:
        """地理编码：地名 → 坐标"""
        params = {
            "q": query,
            "format": "json",
            "limit": limit,
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
                    raise RuntimeError(f"Nominatim API error: {resp.status}")
                results = await resp.json()

        if not results:
            return {"results": [], "count": 0}

        # 按 importance 降序排序
        results.sort(key=lambda r: float(r.get("importance", 0)), reverse=True)

        geocoded = []
        for r in results:
            geocoded.append({
                "name": r.get("display_name", ""),
                "lat": float(r.get("lat", 0)),
                "lon": float(r.get("lon", 0)),
                "type": r.get("type", ""),
                "importance": r.get("importance", 0),
            })

        return {"results": geocoded, "count": len(geocoded)}

    @tool(registry, name="reverse_geocode",
           description=(
               "WGS84 经纬度 → 地名 / 行政归属 (Nominatim 反查)。"
               "\n何时用：用户点击地图后想知道『这是哪里』；分析结果点位需要地名标注；"
               "国际坐标的反查。"
               "\n何时不用：地图上已有图层要素的属性查询 — 改用 query_map_features；"
               "中文区域的精细反查 — 改用 reverse_geocode_cn (Amap/Baidu)。"
               "\n返回：{name, address={country, state, city, road, ...}}"
           ),
           param_descriptions={
               "lat": "WGS84 纬度（-90..90）",
               "lon": "WGS84 经度（-180..180）",
           })
    async def reverse_geocode(lat: float, lon: float) -> dict:
        """反向地理编码：坐标 → 地名"""
        url = settings.NOMINATIM_URL.replace("/search", "/reverse")
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "accept-language": "zh",
        }
        async with aiohttp.ClientSession(headers=get_base_headers()) as session:
            async with session.get(
                url, 
                params=params, 
                ssl=get_ssl_context(),
                proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Nominatim API error: {resp.status}")
                data = await resp.json()

        if "error" in data:
            raise ValueError(data["error"])

        return {
            "name": data.get("display_name", ""),
            "lat": float(data.get("lat", 0)),
            "lon": float(data.get("lon", 0)),
            "address": data.get("address", {}),
        }
