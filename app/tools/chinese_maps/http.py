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


# The TRANSIENT provider-failure errors that trigger fallback to the next
# provider (unified semantics, issues #432 + #479):
# - transport failures: the aiohttp ClientError family (connect resets,
#   ServerTimeoutError for connect/sock-read timeouts, …)
# - total timeouts: asyncio.TimeoutError — on py3.11+ the builtin TimeoutError
#   (an OSError subclass), NOT a ClientError, so it must be listed explicitly
# - response parse failures: json.JSONDecodeError
# Our own programming/schema errors (KeyError/ValueError/TypeError) deliberately
# do NOT trigger fallback: they must propagate and surface as code bugs instead
# of being misclassified as provider failures (#479).
_FALLBACK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    json.JSONDecodeError,
)


# ── Fallback 语义标注（#683） ──────────────────────────────────────────


def _fallback_semantic_note(from_provider: str, to_provider: str, city: str | None = None) -> str:
    parts: list[str] = []
    if from_provider != to_provider:
        # Tianditu 特有的语义差异提示
        if to_provider == "tianditu":
            parts.append("已回退至 Tianditu：仅支持数字 adcode 城市过滤（中文城市名需可解析为 adcode，否则显式失败而非全球检索）；返回要素仅含 name/address/tel，无城市归属字段")
        if from_provider == "tianditu":
            parts.append("已从 Tianditu 回退：目标 provider 支持城市名过滤与更丰富的 POI 属性（name/address/type/tel/city/district）")
        if to_provider == "baidu":
            parts.append("已回退至 Baidu：单页容量 ≤20（Amap 单页 ≤25）")
        if from_provider == "baidu" and to_provider == "amap":
            parts.append("已从 Baidu 回退至 Amap：单页容量 ≤25")
        if city:
            parts.append(f"city 过滤保持性：原始 city='{city}'；Tianditu 需 adcode 解析一致性已在 provider 层显式校验")
    note = "；".join(parts) if parts else f"已从 {from_provider} 回退至 {to_provider}"
    return note


def _is_provider_error_dict(result: object) -> bool:
    """True only for genuine provider-level failure payloads.

    tracked_provider_get returns ``{"error": "<message>"}`` (never raises) for
    HTTP != 200 / WAF 418 / quota / business-check failures. Requires a
    non-empty **string** error value: business payloads that merely happen to
    carry an ``error`` key (``None``, numeric, nested) are not provider
    failures and must not trigger fallback (#479).
    """
    err = result.get("error") if isinstance(result, dict) else None
    return isinstance(err, str) and bool(err)


async def with_fallback(
    preferred: str,
    call,
    *,
    exclude: set[str] | None = None,
    no_key_msg: str = "未配置任何地图 API Key",
    tool_name: str = "",
    city: str | None = None,
) -> dict:
    """Run a per-provider capability call with automatic fallback.

    Collapses the 9× ``_dispatch + _fallback_order + try/except`` scaffold that
    was duplicated across ``register_chinese_map_tools``. For each provider in
    fallback order, skips it when its API key is absent, otherwise invokes
    ``await call(provider)``.

    Fallback fires ONLY on transient provider failures:

    - ``_FALLBACK_ERRORS`` exceptions (transport / total timeout / JSON parse);
    - a provider-level ``{"error": "<non-empty str>"}`` result — tracked GET
      对 HTTP 418 WAF 拦截 / 配额超限 / 业务失败返回 error dict 而非异常
      (f545d7c; narrowed to non-empty string values in #479).

    Our own code bugs (KeyError/ValueError/TypeError, business payloads with a
    benign ``error`` field) do NOT fall back — they propagate / return as-is so
    schema drift surfaces as itself instead of a misleading provider failure
    (#479). Returns the first successful result, or ``{"error": ...}`` when no
    configured provider succeeded. 当发生跨 provider 回退时，结果 envelope 会
    显式标注 ``provider_switched``/``fallback_note`` 语义差异（#683）。

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
    attempted: list[str] = []

    def _note_failure(detail: str) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = detail

    for p in _fallback_order(preferred, exclude):
        if not _has_provider(p):
            continue
        attempted.append(p)
        try:
            result = await call(p)
        except _FALLBACK_ERRORS as e:
            logger.warning(f"{label}{p} failed: {e}")
            _note_failure(str(e))
            continue
        if _is_provider_error_dict(result):
            # 天地图 WAF 频控返回 HTTP 418、配额/业务失败走 business_checker —
            # 这些在 tracked_provider_get 里是正常返回的 error dict，不抛异常。
            # 不在此触发回退的话，首选 provider 被频控时整项能力直接不可用。
            logger.warning(f"{label}{p} failed: {result.get('error')}")
            _note_failure(str(result.get("error")))
            continue
        # 成功路径：若发生过回退，在 envelope 显式标注语义差异（#683）
        # 仅对 FeatureCollection/有 provider 的业务结果标注，避免破坏单元测试的最小 dict 断言
        if len(attempted) > 1 and isinstance(result, dict) and result.get("type") == "FeatureCollection":
            origin = attempted[0]
            note = _fallback_semantic_note(origin, p, city=city)
            result = dict(result)
            result["provider_switched"] = True
            result["fallback_note"] = note
            result["fallback_from"] = origin
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



