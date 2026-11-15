"""G41 有界缓存（WP11）。

键包含：源 ref + 单调 content_revision + 内容指纹 + 两字段/估计字段 + 单位
+ 度量类型 + 全部计划参数 + 算法版本。旧源刷新不会自动沿用新统计标签而保留
旧 paint。缓存只放有界摘要（统计口径/类表/图例 digest），绝不放完整 feature
数组或全量媒体。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

_MAX_ENTRIES = 64
_MAX_BYTES = 2 * 1024 * 1024
_lock = threading.Lock()
_cache: "Dict[Tuple[Any, ...], Dict[str, Any]]" = {}
_bytes = 0


def cache_key(
    *,
    ref_id: str,
    content_revision: int,
    content_fingerprint: str,
    plan_digest: str,
    algorithm_version: str,
    fields: Tuple[str, ...],
    units: Tuple[str, ...],
    measure_kind: str,
) -> Tuple[Any, ...]:
    return (
        ref_id, content_revision, content_fingerprint, plan_digest,
        algorithm_version, tuple(fields), tuple(units), measure_kind,
    )


def get(key: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
    with _lock:
        value = _cache.get(key)
        return dict(value) if value is not None else None


def put(key: Tuple[Any, ...], value: Dict[str, Any]) -> None:
    global _bytes
    import json
    try:
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        size = 4096
    if size > _MAX_BYTES:
        return
    with _lock:
        if key in _cache:
            return
        while _cache and (len(_cache) >= _MAX_ENTRIES or _bytes + size > _MAX_BYTES):
            _, evicted = _cache.popitem()
            try:
                _bytes -= len(json.dumps(evicted, ensure_ascii=False,
                                        default=str).encode("utf-8"))
            except Exception:
                pass
        _cache[key] = dict(value)
        _bytes += size


def clear() -> None:
    global _bytes
    with _lock:
        _cache.clear()
        _bytes = 0
