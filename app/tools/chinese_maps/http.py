"""HTTP 客户端 + provider 路由：amap/baidu/tianditu 各家 GET 封装 + fallback 顺序。

M2: 从单体 chinese_maps.py 抽出。register_chinese_map_tools 仍在 __init__.py。
"""
import asyncio
import json
import logging

import aiohttp

from app.core.config import settings
from app.services.provider_health import (
    tracked_provider_get,
    check_amap_status,
    check_baidu_status,
    check_tianditu_status,
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


def _speed_mps(mode: str) -> float:
    """各模式的典型速度（米/秒），用于等时圈半径估算。"""
    return {"driving": 13.9, "walking": 1.4, "riding": 4.2}[mode]


# ── Provider fallback dispatch ───────────────────────────────────


# The set of transport/parse errors that should trigger fallback to the next
# provider rather than propagating to the LLM. Matches the per-tool except
# clauses the 9 LLM tools previously inlined (architecture-review F1).
# asyncio.TimeoutError: tracked_provider_get 的 total timeout（py3.11+ 即内建
# TimeoutError，OSError 子类）不属于 ClientError 家族，必须单列（issue #432）。
_FALLBACK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    json.JSONDecodeError,
    KeyError,
    ValueError,
    TypeError,
)


async def with_fallback(
    preferred: str,
    call,
    *,
    exclude: set[str] | None = None,
    no_key_msg: str = "未配置任何地图 API Key",
    tool_name: str = "",
) -> dict:
    """Run a per-provider capability call with automatic fallback.

    Collapses the 9× ``_dispatch + _fallback_order + try/except`` scaffold that
    was duplicated across ``register_chinese_map_tools``. For each provider in
    fallback order, skips it when its API key is absent, otherwise invokes
    ``await call(provider)``; on a transport/parse error OR a provider-level
    ``{"error": ...}`` result (tracked GET 对 HTTP 418 WAF 拦截 / 配额超限 /
    业务失败返回 error dict 而非异常), logs and tries the next provider.
    Returns the first successful result, or ``{"error": ...}`` when no
    configured provider succeeded.

    Args:
        preferred: the provider the caller asked for (tried first).
        call: ``async (provider: str) -> dict`` — the per-provider capability
            invocation. Receives the provider name so it can index the dispatch
            table / provider map.
        exclude: providers to skip entirely (e.g. ``{"tianditu"}`` for
            capabilities it doesn't support — the declarative capability matrix
            lives in the Protocol, not here).
        no_key_msg: the error string returned when no provider had a key or all
            failed. Per-tool overrides preserve prior messages
            (e.g. plan_route's "未配置高德或百度 API Key…").
        tool_name: used only for the warning log, to aid debugging.
    """
    label = f"{tool_name} " if tool_name else ""
    first_error: str | None = None

    def _note_failure(detail: str) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = detail

    for p in _fallback_order(preferred, exclude):
        if not _has_provider(p):
            continue
        try:
            result = await call(p)
        except _FALLBACK_ERRORS as e:
            logger.warning(f"{label}{p} failed: {e}")
            _note_failure(str(e))
            continue
        if isinstance(result, dict) and "error" in result:
            # 天地图 WAF 频控返回 HTTP 418、配额/业务失败走 business_checker —
            # 这些在 tracked_provider_get 里是正常返回的 error dict，不抛异常。
            # 不在此触发回退的话，首选 provider 被频控时整项能力直接不可用。
            logger.warning(f"{label}{p} failed: {result.get('error')}")
            _note_failure(str(result.get("error")))
            continue
        return result
    if first_error is not None:
        return {"error": f"{no_key_msg}（{first_error}）"}
    return {"error": no_key_msg}


# ── POI around / polygon / input tips / transit / traffic ────────


async def _amap_get(endpoint: str, params: dict) -> dict:
    params["key"] = settings.AMAP_API_KEY
    params["output"] = "json"
    url = f"{_AMAP_BASE}{endpoint}"
    return await tracked_provider_get(
        "amap", url, params, business_checker=check_amap_status
    )


async def _baidu_get(endpoint: str, params: dict) -> dict:
    params["ak"] = settings.BAIDU_MAP_AK
    params["output"] = "json"
    url = f"{_BAIDU_BASE}{endpoint}"
    return await tracked_provider_get(
        "baidu", url, params, business_checker=check_baidu_status
    )


async def _tianditu_get(endpoint: str, params: dict) -> dict:
    if "tk" not in params:
        params["tk"] = settings.TIANDITU_TOKEN
    url = f"{_TIANDITU_BASE}{endpoint}"
    return await tracked_provider_get(
        "tianditu", url, params, business_checker=check_tianditu_status
    )



