"""Bounded, thread-safe LRU cache for the project context block.

The cache is keyed by ``(project_id, fingerprint_cache_key())`` so that
mutations to the project, its datasets, or its workflows invalidate
the entry implicitly — no explicit cache-bust calls are required.

Design notes (also in ``.planning/.../findings.md``):

- **No TTL.** A cache entry is valid only as long as the live DB
  fingerprint matches the stored one. We do not serve stale entries
  even by accident because the fingerprint read happens on every
  lookup; a fingerprint hash collision that yields different content
  is impossible under the DB's ``onupdate`` semantics.
- **No cross-process sharing.** Each process keeps its own LRU. The
  fingerprint read is the source of truth, so two processes holding
  the same entry is fine: both serve the same rendered block, and the
  fingerprint re-read on the next miss will rebuild on either.
- **No ``None`` caching.** A missing or unauthorised project returns
  an empty string and is *not* cached, so a re-creation (or auth
  grant) is picked up immediately.
- **Bounded capacity** (default 256). Each entry is the rendered text
  block plus a fingerprint — a few KB at most. The LRU evicts cold
  entries; no unbounded growth.
- **Thread-safe** via the same reentrant-lock primitive used in
  ``app/services/chat/llm_client.py``.

API:

- ``lookup(project_id, fingerprint_cache_key)`` — returns the cached
  rendered text or ``None`` on miss. ``None`` results are not cached.
- ``store(project_id, summary)`` — insert/replace an entry built from
  a full ``ProjectContextSummary``.
- ``invalidate(project_id)`` — explicit best-effort eviction, used by
  the mutation paths if they want to short-circuit the next miss.
  The cache is also correct without it.
- ``clear()`` — drop everything; only used in tests.
- ``stats()`` — counters for observability/tests.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional, Tuple

from app.services.project_context_types import ProjectContextSummary


class ProjectContextCache:
    """Process-local LRU keyed by ``(project_id, fingerprint)``."""

    def __init__(self, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        # ``OrderedDict`` is LRU-ordered via ``move_to_end`` on access.
        self._entries: "OrderedDict[Tuple[str, int], str]" = OrderedDict()
        self._lock = threading.RLock()
        # Observability counters (atomic enough for tests; protected by
        # the same RLock so we never tear a writer under a reader).
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def lookup(
        self,
        project_id: str,
        fingerprint_cache_key: int,
    ) -> Optional[str]:
        """Return the rendered text if the entry is present, else ``None``.

        A miss is the only signal the caller uses to decide whether
        to rebuild. ``None`` (project missing / unauthorised) is
        *never* cached, so an immediate retry after a recreate or
        auth grant is observed by the next fingerprint read.
        """
        key = (project_id, fingerprint_cache_key)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return cached
            self._misses += 1
            return None

    def store(self, project_id: str, summary: ProjectContextSummary) -> None:
        """Insert/replace an entry built from a full ``ProjectContextSummary``."""
        if not isinstance(summary, ProjectContextSummary):
            raise TypeError("store() requires a ProjectContextSummary")
        key = (project_id, summary.fingerprint().cache_key())
        with self._lock:
            self._entries[key] = summary.render()
            self._entries.move_to_end(key)
            self._evict_if_needed()

    def invalidate(self, project_id: str) -> int:
        """Drop every entry for ``project_id``. Returns the count evicted.

        Safe to call even when there are no entries. This is the
        explicit-invalidation hook for mutation paths; the cache is
        also correct without it because the fingerprint path detects
        staleness on the next lookup. The fingerprint is hashed from
        a tuple of (project_id, 3 datetime-integers, 2 ints), and a
        collision that yields a different rendered block is
        effectively impossible in practice (it would require
        identical aggregates at the same microsecond under
        different content), though we do not claim strict
        impossibility — the mutation-path explicit invalidation
        plus the per-second timestamp granularity are belt-and-
        suspenders.
        """
        with self._lock:
            evicted = 0
            for key in list(self._entries.keys()):
                if key[0] == project_id:
                    del self._entries[key]
                    evicted += 1
            return evicted

    def clear(self) -> None:
        """Drop everything. Tests only."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "capacity": self._capacity,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        # Called with self._lock held.
        while len(self._entries) > self._capacity:
            oldest_key = next(iter(self._entries))
            del self._entries[oldest_key]
            self._evictions += 1


# Module-level singleton. The cache is process-local and shared across
# every chat request on the same worker. Capacity 256 is intentionally
# large enough to cover a fleet of concurrent sessions on a single
# worker but bounded to keep memory predictable.
project_context_cache = ProjectContextCache(capacity=256)


__all__ = ["ProjectContextCache", "project_context_cache"]
