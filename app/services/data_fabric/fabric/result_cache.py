"""联邦查询结果缓存（ADR-0119 W12，Epic 03 Must-have H）。

键与失效（R-M2 收口）：
- 键 = sha256(scope_key + canonical(request) + Σ per-source fingerprint +
  engine)；**全局域连接禁止入缓存**（owner=None → 跨会话串结果风险归零）；
- 命中条件：TTL 内 ∧ 每源 fingerprint 与 catalog 当前一致（本地 catalog
  读，无网络）；fingerprint 失效的残余陈旧以 ``basis="ttl+fingerprint"``
  显式披露（Epic 红线：无 stale silent success —— 命中必披露）；
- 负缓存：仅 SourceUnreachable/SourceAuthFailed，TTL 30s、容量 64；
  预算超限/取消**绝不缓存**；
- 有界：条目 + 字节双界 LRU；owner 作用域进键（跨 owner 永不命中）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_NEGATIVE_TTL_S = 30.0
_NEGATIVE_MAX_ENTRIES = 64
#: 可负缓存的 typed 错误码（其余错误绝不缓存）。
_NEGATIVE_CODES = frozenset({"SOURCE_UNREACHABLE", "SOURCE_AUTH_FAILED"})


def canonical_request_payload(
    *,
    sources: Iterable[Any],
    joins: Iterable[Any],
    bbox: Any,
    limit: Any,
    order_strategy: Any,
    derive_projection: Any,
) -> Dict[str, Any]:
    """请求的确定性规范化（sort_keys 全程；engine 由调用方附入键）。"""
    def _src(s: Any) -> Any:
        return {
            "source_id": getattr(s, "source_id", None),
            "dataset_id": getattr(s, "dataset_id", None),
            "where_raw": getattr(s, "where", None)
            if isinstance(getattr(s, "where", None), str)
            else None,
            "fields": sorted(getattr(s, "fields", None) or []),
            "srs": getattr(s, "srs", None),
        }

    def _join(j: Any) -> Any:
        return {
            "kind": getattr(j, "kind", None),
            "l": getattr(j, "join_field_left", None),
            "r": getattr(j, "join_field_right", None),
            "op": getattr(j, "spatial_op", None),
            "gb": sorted(getattr(j, "group_by_right", None) or []),
            "aggs": sorted(
                getattr(j, "aggregates", None) or [],
                key=lambda a: json.dumps(a, sort_keys=True, default=str),
            ),
            "ls": getattr(j, "left_source_id", None),
            "rs": getattr(j, "right_source_id", None),
        }

    return {
        "sources": sorted((_src(s) for s in sources), key=lambda d: json.dumps(d, sort_keys=True, default=str)),
        "joins": [_join(j) for j in joins],
        "bbox": list(bbox) if bbox else None,
        "limit": limit,
        "order_strategy": order_strategy,
        "derive_projection": bool(derive_projection),
    }


def result_cache_key(
    *,
    scope_key: str,
    fingerprints: Dict[str, str],
    engine: str,
    request: Dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "scope": scope_key,
            "fingerprints": dict(sorted(fingerprints.items())),
            "engine": engine,
            "request": request,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResultCacheEntry:
    __slots__ = ("payload", "created_at", "ttl_s", "fingerprints", "bytes_len", "scope_key")

    def __init__(self, payload: Any, ttl_s: float, fingerprints: Dict[str, str], scope_key: str):
        self.payload = payload
        self.created_at = time.time()
        self.ttl_s = float(ttl_s)
        self.fingerprints = dict(fingerprints)
        self.bytes_len = len(json.dumps(payload, default=str).encode("utf-8"))
        self.scope_key = scope_key

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) - self.created_at > self.ttl_s


class FederatedResultCache:
    """进程内有界结果缓存（命中必披露；owner 隔离；全局域禁入）。"""

    def __init__(
        self,
        *,
        max_entries: Optional[int] = None,
        max_bytes: Optional[int] = None,
        ttl_s: Optional[float] = None,
    ):
        from app.services.data_fabric.fabric.probing import _setting

        self._max_entries = int(
            max_entries
            if max_entries is not None
            else _setting("DATA_FABRIC_V7_RESULT_CACHE_MAX_ENTRIES", 256)
        )
        self._max_bytes = int(
            max_bytes
            if max_bytes is not None
            else _setting("DATA_FABRIC_V7_RESULT_CACHE_MAX_BYTES", 64 * 1024 * 1024)
        )
        self._ttl_s = float(
            ttl_s if ttl_s is not None else _setting("DATA_FABRIC_V7_RESULT_CACHE_TTL_S", 300.0)
        )
        self._entries: "OrderedDict[str, ResultCacheEntry]" = OrderedDict()
        self._negative: "OrderedDict[str, tuple]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(
        self,
        key: str,
        *,
        current_fingerprints: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """返回命中载荷（含 ``result_cache`` 披露段）或 None。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.is_expired():
                self._drop_locked(key)
                self.misses += 1
                return None
            if current_fingerprints is not None and any(
                entry.fingerprints.get(sid) != fp
                for sid, fp in current_fingerprints.items()
            ):
                # 数据修订变化 → 不可信（异步失效后当 miss）
                self._drop_locked(key)
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            age = round(time.time() - entry.created_at, 3)
        payload = entry.payload
        if isinstance(payload, dict):
            disclosed = dict(payload)
            disclosed["result_cache"] = {
                "hit": True,
                "age_s": age,
                "ttl_s": entry.ttl_s,
                "basis": "ttl+fingerprint",
                "key": key[:16],
            }
            return disclosed
        return payload

    def put(
        self,
        key: str,
        payload: Any,
        *,
        fingerprints: Dict[str, str],
        scope_key: str,
    ) -> None:
        if scope_key == "org:_|owner:_|proj:_":
            return  # 全局域禁入（R-M2）
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return
        try:
            entry = ResultCacheEntry(payload, self._ttl_s, fingerprints, scope_key)
        except Exception:  # noqa: BLE001 - 不可序列化载荷不缓存
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.bytes_len
            self._entries[key] = entry
            self._bytes += entry.bytes_len
            while (len(self._entries) > self._max_entries) or (
                self._bytes > self._max_bytes and len(self._entries) > 1
            ):
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.bytes_len

    def put_negative(self, key: str, error_code: str) -> None:
        if error_code not in _NEGATIVE_CODES:
            return  # 预算/取消/其余错误绝不缓存
        with self._lock:
            self._negative[key] = (time.time(), error_code)
            self._negative.move_to_end(key)
            while len(self._negative) > _NEGATIVE_MAX_ENTRIES:
                self._negative.popitem(last=False)

    def get_negative(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._negative.get(key)
            if entry is None:
                return None
            ts, code = entry
            if time.time() - ts > _NEGATIVE_TTL_S:
                del self._negative[key]
                return None
            self._negative.move_to_end(key)
            return code

    def invalidate(self, key_prefix: Optional[str] = None) -> int:
        removed = 0
        with self._lock:
            for k in list(self._entries.keys()):
                if key_prefix is None or k.startswith(key_prefix):
                    self._drop_locked(k)
                    removed += 1
        return removed

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "negative_entries": len(self._negative),
            }

    def _drop_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.bytes_len


_cache: Optional[FederatedResultCache] = None
_cache_lock = threading.Lock()


def get_result_cache() -> FederatedResultCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = FederatedResultCache()
        return _cache


def reset_result_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


__all__ = [
    "FederatedResultCache",
    "canonical_request_payload",
    "get_result_cache",
    "reset_result_cache",
    "result_cache_key",
]
