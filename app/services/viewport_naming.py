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
import time
from collections import OrderedDict, deque
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
_active_tasks: set[asyncio.Task] = set()

# ─── Rate limiting (/review P1-3) ──────────────────────────────────────
# Pre-existing unauth WS + new schedule_populate on every viewport_change =
# amplification: an attacker walking lng/lat by 0.05° defeats the LRU cache
# and forces unbounded outbound Nominatim calls. Nominatim's official ToS is
# 1 req/sec per IP; sustained traffic risks IP-ban. We cap at 30 calls/min
# globally (= 0.5 req/sec) which leaves headroom for legitimate panning.
_RATE_LIMIT_MAX_PER_MINUTE = 30
_RATE_WINDOW_SECONDS = 60.0
_rate_window: "deque[float]" = deque()

# Shared aiohttp.ClientSession (/review P2-3). The original code created a
# fresh ClientSession per call (~200–500ms TLS handshake every time). Reuse
# one process-level session: lazily created, never explicitly closed (Python
# process exit reclaims fd's). If you wire app lifespan, call _close_session()
# on shutdown.
_aiohttp_session: Optional["aiohttp.ClientSession"] = None  # noqa: F821 (string-only forward ref)
_aiohttp_session_lock: Optional[asyncio.Lock] = None


def _rate_limit_check() -> bool:
    """Return True if the current call is allowed under the token bucket.

    Sliding 60-second window. Purges expired timestamps lazily on each check.
    """
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    while _rate_window and _rate_window[0] < cutoff:
        _rate_window.popleft()
    if len(_rate_window) >= _RATE_LIMIT_MAX_PER_MINUTE:
        return False
    _rate_window.append(now)
    return True


async def _get_aiohttp_session():
    """Lazily create or return the shared aiohttp.ClientSession."""
    global _aiohttp_session, _aiohttp_session_lock
    import aiohttp
    from app.core.network import get_base_headers
    if _aiohttp_session_lock is None:
        _aiohttp_session_lock = asyncio.Lock()
    async with _aiohttp_session_lock:
        if _aiohttp_session is None or _aiohttp_session.closed:
            timeout = aiohttp.ClientTimeout(total=_LOOKUP_TIMEOUT_SEC)
            _aiohttp_session = aiohttp.ClientSession(
                headers=get_base_headers(),
                timeout=timeout,
            )
    return _aiohttp_session


async def _close_session() -> None:
    """Close the shared session. Call from FastAPI lifespan shutdown if wired."""
    global _aiohttp_session
    if _aiohttp_session is not None and not _aiohttp_session.closed:
        await _aiohttp_session.close()
    _aiohttp_session = None


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
    """实际调用 Nominatim 反查；失败返回 None。

    /review P2-3: uses a shared ClientSession (reused across calls) to avoid
    a fresh TCP/TLS handshake per lookup.
    """
    from app.core.config import settings
    from app.core.network import get_ssl_context

    url = settings.NOMINATIM_URL.replace("/search", "/reverse")
    params = {
        "lat": lat,
        "lon": lng,
        "format": "json",
        "accept-language": "zh",
        "zoom": 10,  # 城市/区县级精度足矣，不要返回门牌号
    }
    try:
        session = await _get_aiohttp_session()
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
    # /review P1-3: global token bucket. If we've already burned this minute's
    # Nominatim budget, skip — better to lose region naming than get IP-banned.
    if not _rate_limit_check():
        logger.debug(
            f"viewport_naming: rate-limit ({_RATE_LIMIT_MAX_PER_MINUTE}/min) exhausted, "
            f"skipping ({lng:.4f},{lat:.4f})"
        )
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
    task = loop.create_task(_populate(lng, lat))
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def wait_all_tasks() -> None:
    """异步等待当前所有在途的 Nominatim 反查后台任务完成（主要用于单元测试去抖）。"""
    if _active_tasks:
        await asyncio.gather(*_active_tasks, return_exceptions=True)


def clear_cache() -> None:
    """测试钩子。Also resets the /review P1-3 rate-limit window so tests start clean."""
    _cache.clear()
    _in_flight.clear()
    _rate_window.clear()
    for task in list(_active_tasks):
        if not task.done():
            try:
                task.cancel()
            except RuntimeError:
                pass
    _active_tasks.clear()


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
