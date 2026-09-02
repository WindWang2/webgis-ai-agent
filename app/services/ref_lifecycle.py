"""V5-C: unified ref lifecycle — single invalidation contract.

The authoritative ref payload lives in a session store (Memory or Redis).
Everything else — ref_payload_cache, spatial_index_cache, tile_lru_cache,
descriptors, L1 metadata — is a DERIVED projection. #1111 was the canonical
accident: a write path forgot one of the three process-local caches and
ghost data kept serving.

``ref_lifecycle`` is the ONLY sanctioned way to transition a ref's state.
Reasons are explicit; each emits a bounded observability event (V5-G) and
drops exactly the derived state that must not outlive the transition.

invalidate ≠ delete: lifecycle invalidation only clears projections; the
authoritative payload deletion stays with the store (``delete_ref``).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class RefInvalidationReason(str, Enum):
    """Why a ref's derived state is being dropped (V5-C taxonomy)."""

    OVERWRITE = "OVERWRITE"    # same ref_id re-written (plan checkpoints, rollback restore)
    DELETE = "DELETE"          # ref explicitly removed
    EVICT = "EVICT"            # capacity/byte pressure evicted the ref
    EXPIRE = "EXPIRE"          # TTL elapsed (Redis DATA_TTL)
    ROLLBACK = "ROLLBACK"      # checkpoint restore restored an older payload
    REPLACE = "REPLACE"        # alias/slot semantic replacement


class RefLifecycleEvent(str, Enum):
    """Bounded observability events (V5-G) — ids only, never payloads."""

    REF_STORED = "ref_stored"
    REF_OVERWRITTEN = "ref_overwritten"
    REF_INVALIDATED = "ref_invalidated"
    REF_DELETED = "ref_deleted"
    REF_EVICTED = "ref_evicted"


def _emit(event: RefLifecycleEvent, session_id: str, ref_id: str, reason: Optional[str]) -> None:
    # Bounded by design: one line, ids + reason only. Never the payload.
    logger.info(
        "ref_lifecycle event=%s session=%s ref=%s reason=%s",
        event.value, session_id, ref_id, reason or "-",
    )


def invalidate_ref_caches(
    session_id: str,
    ref_ids: list[str],
    reason: RefInvalidationReason = RefInvalidationReason.REPLACE,
    include_payload_cache: bool = True,
) -> int:
    """Drop every process-local projection of the given refs.

    The single invalidation contract (V5-C). Both backends route every
    write/evict/delete path through here. Returns the number of ref entries
    invalidated (for tests/observability).
    """
    from app.services.mvt import spatial_index_cache, tile_lru_cache

    count = 0
    for ref_id in ref_ids:
        spatial_index_cache.invalidate_ref(session_id, ref_id)
        tile_lru_cache.invalidate_ref(session_id, ref_id)
        if include_payload_cache:
            from app.services.ref_payload_cache import ref_payload_cache
            ref_payload_cache.invalidate(session_id, ref_id)
        _emit(RefLifecycleEvent.REF_INVALIDATED, session_id, ref_id, reason.value)
        count += 1
    return count


def emit_ref_event(
    event: RefLifecycleEvent,
    session_id: str,
    ref_id: str,
    reason: Optional[str] = None,
) -> None:
    """Public bounded-event hook for store-level transitions (V5-G)."""
    _emit(event, session_id, ref_id, reason)
