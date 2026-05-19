"""HTTP 客户端 + provider 路由：amap/baidu/tianditu 各家 GET 封装 + fallback 顺序。

M2: 从单体 chinese_maps.py 抽出。register_chinese_map_tools 仍在 __init__.py。
"""
import asyncio
import json
import logging

import aiohttp

from app.core.config import settings
from app.core.network import get_ssl_context, get_shared_client
from app.services.provider_health import health_tracker as ht

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



def _speed_mps(mode: str) -> float:
    """各模式的典型速度（米/秒），用于等时圈半径估算。"""
    return {"driving": 13.9, "walking": 1.4, "riding": 4.2}[mode]


# ── POI around / polygon / input tips / transit / traffic ────────



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


