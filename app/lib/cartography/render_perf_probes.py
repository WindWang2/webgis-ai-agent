"""RenderPerfProbes —— 渲染性能探针的 versioned 契约（F13，ADR-0214 D5）。

前端 ``frontend/lib/telemetry/render-probes.ts`` 采集的 TTFR / patch
latency / 数据面计数随 observation ``perf`` 块上行 —— 本模块是服务端
的有界归一门：**纯数字/布尔/封闭键**，非法载荷整体降级为 ``None``
（按证据缺席处理，与 canvas 同纪律）；绝不接收 GeoJSON / 特征数据 /
自由文本。

trace 通道 = finalizer 消费的既有 ``_cartographic_observation``（增维
不换通道）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

PERF_SCHEMA_VERSION = "render_perf_probes.v1"

#: 数据面计数键（与 frontend/lib/data-plane/observability.ts 计数同源）。
_DATA_PLANE_KEYS = (
    "requests", "cacheHits", "etag304", "fetchOk",
    "fetchFailed", "cancelled", "deduped", "evictions",
)

#: 毫秒级时延上界（10 分钟 —— 超过即不可信的挂钟/系统时基漂移）。
_MAX_LATENCY_MS = 600_000.0
#: 字节计数上界（16GiB —— 防御性；真实预算 256MiB 量级）。
_MAX_BYTES = 16 * 1024**3


def _bounded_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value < 0:
        return 0
    return min(int(value), 1_000_000_000)


def _bounded_ms(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v < 0 or v > _MAX_LATENCY_MS or v != v:  # NaN/负值/超界 → 缺席
        return None
    return round(v, 1)


def normalize_render_perf_block(payload: Any) -> Optional[Dict[str, Any]]:
    """客户端 perf 载荷 → 有界投影；结构非法 → None（诚实缺席）。

    版本不符 → None（versioned 契约，不猜语义）。多发键不透传。
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != PERF_SCHEMA_VERSION:
        return None
    out: Dict[str, Any] = {"schema_version": PERF_SCHEMA_VERSION}
    for key in ("ttfr_ms", "patch_latency_ms"):
        value = _bounded_ms(payload.get(key))
        if value is not None:
            out[key] = value
    if payload.get("map_idle") is not None:
        out["map_idle"] = bool(payload.get("map_idle"))
    out["render_failures"] = _bounded_count(payload.get("render_failures"))
    data_plane_raw = payload.get("data_plane")
    if data_plane_raw is not None and not isinstance(data_plane_raw, dict):
        data_plane_raw = None
    out["data_plane"] = {
        key: _bounded_count((data_plane_raw or {}).get(key))
        for key in _DATA_PLANE_KEYS
    }
    cache_bytes = payload.get("cache_bytes")
    if isinstance(cache_bytes, bool) or not isinstance(cache_bytes, (int, float)):
        cache_bytes = 0
    out["cache_bytes"] = min(max(0, int(cache_bytes)), _MAX_BYTES)
    return out


__all__ = [
    "PERF_SCHEMA_VERSION",
    "normalize_render_perf_block",
]
