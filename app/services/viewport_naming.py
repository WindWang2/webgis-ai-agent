"""视口反查地名 (用于 [环境感知] 注入)。

设计要点：
- **零阻塞**：env summary 是同步路径，每轮都跑，绝不能引入 100ms+ 的网络往返。
  所以这里只暴露同步 `lookup()`，命中缓存就返回，未命中返回 None。
- **预热**：`schedule_populate()` 在 map_state push 时异步触发 Nominatim 反查，
  写入进程内 LRU。下一轮 build summary 时就有结果了。
- **量化键**：lng/lat 量化到 0.05° (~5km)，让小幅度平移也能命中缓存。
- **失败不洗脏数据**：上游 Nominatim 抖动/超时，跳过即可，不缓存错误。
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

# 量化精度：度数四舍五入到 1/20，约等于 5km
_QUANT = 20.0
_CACHE_MAX = 1024
_LOOKUP_TIMEOUT_SEC = 3.0

# 进程级 LRU。在 worker 间不共享是有意为之 —— Nominatim 响应稳定，
# 多 worker 各自跑一次完全够用，不值得引入 Redis 复杂度。
_cache: "OrderedDict[tuple[float, float], str]" = OrderedDict()
_in_flight: set[tuple[float, float]] = set()


def _quantize(lng: float, lat: float) -> tuple[float, float]:
    return (round(lng * _QUANT) / _QUANT, round(lat * _QUANT) / _QUANT)


def lookup(lng: float, lat: float) -> Optional[str]:
    """读缓存。命中返回简短地名字符串；未命中返回 None。"""
    key = _quantize(lng, lat)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _put(key: tuple[float, float], name: str) -> None:
    _cache[key] = name
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _format_address(address: dict) -> str:
    """从 Nominatim address 字典里挑出最有信息量的 1-3 段。

    Nominatim 中文反查通常返回 country/state/city/county/suburb/road 等。
    我们按粗→细顺序找前 3 个非空字段，逗号分隔。
    """
    keys_priority = [
        "country",
        "state", "province",
        "city", "town", "village", "municipality",
        "county", "district", "suburb",
        "road", "neighbourhood",
    ]
    seen: list[str] = []
    for k in keys_priority:
        v = address.get(k)
        if v and v not in seen:
            seen.append(str(v))
        if len(seen) >= 3:
            break
    return " ".join(seen) if seen else ""


async def _fetch_nominatim(lng: float, lat: float) -> Optional[str]:
    """实际调用 Nominatim 反查；失败返回 None。"""
    import aiohttp
    from app.core.config import settings
    from app.core.network import get_ssl_context, get_base_headers

    url = settings.NOMINATIM_URL.replace("/search", "/reverse")
    params = {
        "lat": lat,
        "lon": lng,
        "format": "json",
        "accept-language": "zh",
        "zoom": 10,  # 城市/区县级精度足矣，不要返回门牌号
    }
    timeout = aiohttp.ClientTimeout(total=_LOOKUP_TIMEOUT_SEC)
    try:
        async with aiohttp.ClientSession(headers=get_base_headers(), timeout=timeout) as session:
            async with session.get(
                url,
                params=params,
                ssl=get_ssl_context(),
                proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        if "error" in data:
            return None
        address = data.get("address") or {}
        if isinstance(address, dict):
            label = _format_address(address)
            if label:
                return label
        # fallback: display_name 取前 60 个字符
        name = data.get("display_name") or ""
        return name[:60] if name else None
    except Exception as e:
        logger.debug(f"viewport_naming: nominatim error ({lng:.4f},{lat:.4f}): {e}")
        return None


async def _populate(lng: float, lat: float) -> None:
    key = _quantize(lng, lat)
    if key in _cache or key in _in_flight:
        return
    _in_flight.add(key)
    try:
        name = await _fetch_nominatim(lng, lat)
        if name:
            _put(key, name)
    finally:
        _in_flight.discard(key)


def schedule_populate(lng: float, lat: float) -> None:
    """触发后台预热。安全在同步代码中调用 —— 没有 running loop 时就静默跳过。"""
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return
    key = _quantize(lng, lat)
    if key in _cache or key in _in_flight:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的 loop（例如纯同步路径），跳过；下次有 loop 时会再触发
        return
    loop.create_task(_populate(lng, lat))


def clear_cache() -> None:
    """测试钩子。"""
    _cache.clear()
    _in_flight.clear()


def schedule_populate_from_map_state(map_state: dict | None) -> None:
    """从 map_state 字典里抽出 viewport.center 并触发预热。

    map_state push 路径调用这个就够了，不用关心结构细节。
    """
    if not isinstance(map_state, dict):
        return
    viewport = map_state.get("viewport")
    if not isinstance(viewport, dict):
        return
    center = viewport.get("center")
    if not (isinstance(center, (list, tuple)) and len(center) >= 2):
        return
    try:
        lng = float(center[0])
        lat = float(center[1])
    except (ValueError, TypeError):
        return
    schedule_populate(lng, lat)
