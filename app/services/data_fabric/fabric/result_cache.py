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
        # R2-C1：where 的**全部受支持形态**都必须进键 —— str 原样、dict/AST
        # 取规范化谓词 JSON。只认 str 会让同键不同过滤条件静默串结果。
        where = getattr(s, "where", None)
        where_canonical: Any = None
        if isinstance(where, str):
            where_canonical = {"raw": where}
        elif isinstance(where, dict):
            where_canonical = where
        elif where is not None and hasattr(where, "op"):
            from app.services.data_fabric.query.predicates import (
                predicate_to_canonical_dict,
            )

            where_canonical = predicate_to_canonical_dict(where)
        return {
            "source_id": getattr(s, "source_id", None),
            "dataset_id": getattr(s, "dataset_id", None),
            "where": where_canonical,
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
        # ── V8（ADR-0132 Phase F）──
        self._single = SingleFlight()
        self._backend = _backend_from_settings()

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
                remote = self._backend_get(key)
                if remote is not None:
                    self.hits += 1
                    return remote
                return None
            if entry.is_expired():
                self._drop_locked(key)
                self.misses += 1
                remote = self._backend_get(key)
                if remote is not None:
                    self.hits += 1
                    return remote
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

    #: 单条载荷行数预检上限（超大结果不缓存 —— 计量即序列化的成本保护）。
    MAX_CACHED_ROWS = 2_000

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
        rows = payload.get("rows")
        if isinstance(rows, list) and len(rows) > self.MAX_CACHED_ROWS:
            return  # R2-M3：超大结果直接不缓存（宁可 miss 不可驻留巨条目）
        try:
            entry = ResultCacheEntry(payload, self._ttl_s, fingerprints, scope_key)
        except Exception:  # noqa: BLE001 - 不可序列化载荷不缓存
            return
        if entry.bytes_len > self._max_bytes:
            return  # R2-M3：单条超界不驻留（旧逻辑永不逐出是泄漏面）
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
        self._backend_put(key, payload, ttl_s=self._ttl_s)

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
            out = {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "negative_entries": len(self._negative),
            }
        out["singleflight_inflight"] = self._single.inflight()
        out["distributed_backend"] = type(self._backend).__name__ if self._backend else None
        if self._backend is not None:
            out["backend_failures"] = getattr(self._backend, "failures", 0)
        return out

    def single_flight(self) -> SingleFlight:
        """stampede 保护的 per-key 单飞（execute_chain_v6 的 miss 路径用）。"""
        return self._single

    # ── V8：分布式二线（全部 fail-open；键已含 fingerprints —— 命中即一致）──

    def _backend_get(self, key: str) -> Optional[Dict[str, Any]]:
        if self._backend is None:
            return None
        try:
            raw = self._backend.get(key)
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001 - 反序列化失败按 miss
            return None
        if isinstance(payload, dict):
            payload["result_cache"] = {
                "hit": True,
                "age_s": None,  # 分布式 TTL 不回传创建时刻 —— 诚实未知
                "ttl_s": self._ttl_s,
                "basis": "ttl+fingerprint+distributed",
                "key": key[:16],
            }
        return payload

    def _backend_put(self, key: str, payload: Any, *, ttl_s: float) -> None:
        if self._backend is None:
            return
        try:
            self._backend.put(
                key, json.dumps(payload, default=str), ttl_s=ttl_s
            )
        except Exception:  # noqa: BLE001 - 写穿失败不影响本地缓存语义
            pass

    def _drop_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.bytes_len


# ── V8（ADR-0132 Phase F）：stampede 保护 + 可选分布式后端 ─────────────────


class _Flight:
    __slots__ = ("event", "result", "exc", "done")

    def __init__(self):
        import threading as _t

        self.event = _t.Event()
        self.result = None
        self.exc = None
        self.done = False


class SingleFlight:
    """per-key 单飞（cache stampede 保护）。

    首个调用者成为 owner 执行 ``fn``；并发同键调用者在 ``max_wait_s`` 界内
    等 owner 的结果（拿到即复用，不重复打远端）。等待超时/owner 失败时
    调用者**自行执行**（降级为无单飞 —— 保护是延迟优化，绝不变成可用性
    单点）。条目数有界：超界退化直执行。
    """

    def __init__(self, *, max_wait_s: Optional[float] = None, max_entries: int = 256):
        import threading as _t

        if max_wait_s is None:
            from app.services.data_fabric.fabric.probing import _setting

            max_wait_s = _setting("DATA_FABRIC_V8_RESULT_CACHE_SINGLEFLIGHT_WAIT_S", 10.0)
        self._max_wait_s = float(max_wait_s)
        self._max_entries = int(max_entries)
        self._flights: "OrderedDict[str, _Flight]" = OrderedDict()
        self._lock = _t.Lock()

    def run(self, key: str, fn):
        """单飞执行 ``fn``；返回 (result, shared)。shared=True = 复用 owner 结果。"""
        with self._lock:
            flight = self._flights.get(key)
            if flight is None and len(self._flights) < self._max_entries:
                flight = _Flight()
                self._flights[key] = flight
                owner = True
            else:
                owner = flight is None  # 超界 → 自行执行
        if owner and flight is not None:
            try:
                result = fn()
                with self._lock:
                    flight.result = result
                    flight.done = True
                    flight.event.set()
                    self._flights.pop(key, None)
                return result, False
            except BaseException as exc:
                with self._lock:
                    flight.exc = exc
                    flight.done = True
                    flight.event.set()
                    self._flights.pop(key, None)
                # owner 失败：等方自行执行（不传播同一异常 —— 各自独立重试
                # 避免同因失败放大；owner 路径原样上抛由调用方契约处理）。
                raise
        # waiter：有界等待 owner 结果。
        if flight is not None:
            signaled = flight.event.wait(self._max_wait_s)
            if signaled and flight.done and flight.exc is None:
                return flight.result, True
            # 超时或 owner 失败 → 自行执行（等价无单飞）。
        return fn(), False

    def inflight(self) -> int:
        with self._lock:
            return len(self._flights)


class ResultCacheBackend:
    """分布式后端 seam（可选；V8 additive）。

    契约：全部方法 fail-open（后端故障 = 本地 LRU 继续服务）；值是 JSON
    字符串（载荷已按 ``json.dumps(default=str)`` 计量，可序列化性成立）。
    """

    def get(self, key: str) -> Optional[str]:  # pragma: no cover - 接口
        raise NotImplementedError

    def put(self, key: str, value: str, *, ttl_s: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def ping(self) -> bool:  # pragma: no cover
        raise NotImplementedError


class RedisResultCacheBackend(ResultCacheBackend):
    """Redis 后端（惰性连接；双界超时；故障 fail-open 计数披露）。"""

    def __init__(self, *, url: Optional[str] = None, socket_timeout_s: float = 0.25):
        import threading as _t

        if url is None:
            from app.services.data_fabric.fabric.probing import _setting

            url = _setting("DATA_FABRIC_V8_RESULT_CACHE_REDIS_URL", "")                 or _setting("REDIS_URL", "")
        self._url = url
        self._socket_timeout_s = float(socket_timeout_s)
        self._client = None
        self._client_lock = _t.Lock()
        self.failures = 0

    def _get_client(self):
        with self._client_lock:
            if self._client is None:
                import redis as _redis

                self._client = _redis.Redis.from_url(
                    self._url,
                    socket_connect_timeout=self._socket_timeout_s,
                    socket_timeout=self._socket_timeout_s,
                    decode_responses=True,
                )
            return self._client

    def get(self, key: str) -> Optional[str]:
        try:
            return self._get_client().get(f"fabric_rc:{key}")
        except Exception:  # noqa: BLE001 - 后端故障 fail-open
            self.failures += 1
            return None

    def put(self, key: str, value: str, *, ttl_s: float) -> None:
        try:
            self._get_client().setex(f"fabric_rc:{key}", max(1, int(ttl_s)), value)
        except Exception:  # noqa: BLE001
            self.failures += 1

    def ping(self) -> bool:
        try:
            return bool(self._get_client().ping())
        except Exception:  # noqa: BLE001
            self.failures += 1
            return False


def _backend_from_settings():
    """settings → 后端实例（memory = None；redis 故障惰性，绝不阻断导入）。"""
    from app.services.data_fabric.fabric.probing import _setting

    mode = str(_setting("DATA_FABRIC_V8_RESULT_CACHE_BACKEND", "memory") or "memory")
    if mode.strip().lower() != "redis":
        return None
    try:
        return RedisResultCacheBackend()
    except Exception:  # noqa: BLE001 - redis 包缺失 → 本地 LRU
        return None


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
