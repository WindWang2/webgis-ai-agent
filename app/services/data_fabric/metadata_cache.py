"""Safe TTL cache for Data Fabric metadata (Section 37).

A result/metadata cache is only safe if its key is fully determined by
(source, dataset, query spec, auth/tenant scope, dataset fingerprint). Omitting
the auth/tenant scope leaks one caller's result to another (cross-tenant P0).
This module centralizes that contract so callers cannot forget the scope.

Used today for ``describe()`` metadata; the query-result cache + HTTP
conditional requests (ETag / If-None-Match) are a documented follow-up — they
need adapter response-header plumbing that this PR does not introduce.
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


def cache_key(*parts: Any) -> str:
    """Stable SHA-256 cache key from arbitrary parts.

    Every caller MUST include the auth/tenant scope as one of the parts — that
    is what prevents cross-tenant leakage. Fingerprinting the parts (rather than
    concatenating raw) avoids delimiter-collision and keeps keys fixed-width.
    """
    blob = json.dumps(list(parts), default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class _Entry:
    value: Any
    expires_at: float


class SafeTTLCache:
    """Thread-safe TTL cache. Bounded by max_entries (LRU-ish eviction)."""

    def __init__(self, default_ttl: float = 30.0, max_entries: int = 4096, clock: Callable[[], float] = time.monotonic):
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._clock = clock
        self._store: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = self._clock()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._store.pop(key, None)
                return None
            return entry.value

    def put(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        now = self._clock()
        with self._lock:
            self._store[key] = _Entry(value=value, expires_at=now + (ttl if ttl is not None else self.default_ttl))
            # Bounded: drop a few oldest if over capacity.
            if len(self._store) > self.max_entries:
                overflow = len(self._store) - self.max_entries
                # Evict by nearest expiry (cheap approximation of LRU for TTL cache).
                for k in sorted(self._store, key=lambda kk: self._store[kk].expires_at)[:overflow]:
                    self._store.pop(k, None)

    def invalidate(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)

    def __len__(self) -> int:
        return len(self._store)


# Per-process describe cache. source_key + dataset_id + scope are all required.
_describe_cache = SafeTTLCache(default_ttl=30.0)


def cached_describe(
    describe_fn: Callable[[str], Any],
    source_key: str,
    dataset_id: str,
    scope: str,
    ttl: Optional[float] = None,
    cache: Optional[SafeTTLCache] = None,
) -> Any:
    """Call ``describe_fn(dataset_id)`` under a tenant-scoped TTL cache.

    ``scope`` is the auth/tenant namespace (REQUIRED) — it is part of the key so
    one tenant's cached descriptor is never served to another.
    """
    # NOTE: use `is not None`, not `or` — SafeTTLCache defines __len__, so an
    # empty cache is falsey and `cache or _describe_cache` would silently use the
    # global cache (cross-test / cross-tenant pollution).
    c = cache if cache is not None else _describe_cache
    key = cache_key(source_key, dataset_id, scope)
    hit = c.get(key)
    if hit is not None:
        return hit
    value = describe_fn(dataset_id)
    c.put(key, value, ttl=ttl)
    return value


__all__ = ["SafeTTLCache", "cache_key", "cached_describe", "_describe_cache"]
