"""地理编码工具 - Nominatim"""
import logging
import ssl
import aiohttp
from typing import Optional
from app.core.config import settings
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

# SSL 证书修复
def _get_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations("/etc/ssl/certs/ca-certificates.crt")
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def register_geocoding_tools(registry: ToolRegistry):
    """注册地理编码工具到 registry"""

    @tool(registry, name="geocode", description="将地名转换为经纬度坐标",
           param_descriptions={"query": "地名，如'北京'、'天安门'", "limit": "返回结果数量，默认5"})
    async def geocode(query: str, limit: int = 5) -> dict:
        """地理编码：地名 → 坐标"""
        params = {
            "q": query,
            "format": "json",
            "limit": limit,
            "accept-language": "zh",
        }
        async with aiohttp.ClientSession(headers={"User-Agent": "WebGIS-AI-Agent/1.0"}) as session:
            async with session.get(settings.NOMINATIM_URL, params=params, ssl=_get_ssl_context()) as resp:
                if resp.status != 200:
                    return {"error": f"Nominatim API error: {resp.status}"}
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

    @tool(registry, name="reverse_geocode", description="将经纬度坐标转换为地名",
           param_descriptions={"lat": "纬度", "lon": "经度"})
    async def reverse_geocode(lat: float, lon: float) -> dict:
        """反向地理编码：坐标 → 地名"""
        url = settings.NOMINATIM_URL.replace("/search", "/reverse")
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "accept-language": "zh",
        }
        async with aiohttp.ClientSession(headers={"User-Agent": "WebGIS-AI-Agent/1.0"}) as session:
            async with session.get(url, params=params, ssl=_get_ssl_context()) as resp:
                if resp.status != 200:
                    return {"error": f"Nominatim API error: {resp.status}"}
                data = await resp.json()

        if "error" in data:
            return {"error": data["error"]}

        return {
            "name": data.get("display_name", ""),
            "lat": float(data.get("lat", 0)),
            "lon": float(data.get("lon", 0)),
            "address": data.get("address", {}),
        }
