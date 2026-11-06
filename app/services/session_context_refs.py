"""Backend-persisted content deduplication for non-layer context payloads."""
from __future__ import annotations

from app.lib.runtime.ref_cursors import CONTEXT_OFFLOAD_REF_PREFIX
from app.services.distributed_lock import session_lock_registry
from app.services.session_data_protocol import SessionStoreProtocol, is_unavailable_ref


async def store_context_payload(
    store: SessionStoreProtocol, session_id: str, payload: object, content_hash: str,
) -> str | None:
    if not session_id:
        return None
    alias = f"__context_offload:{content_hash}"
    # The alias and payload live in the shared store, not a process cache.
    # Short mutation locks use different keys and remain usable by the caller.
    async with session_lock_registry.lock(
        f"context-offload:{session_id}:{content_hash}",
        fail_on_degraded=True, fail_on_lost=True,
    ) as lock:
        cached = await store.resolve_alias(session_id, alias)
        if cached != alias:
            if cached.startswith(CONTEXT_OFFLOAD_REF_PREFIX) and await store.ref_exists(session_id, cached):
                return cached
            # Normal backend eviction expires aliases together with payloads.
            # A foreign/uncertain alias is never borrowed or overwritten.
            return None
        ref = await store.store(session_id, payload, prefix="contextoffload")
        if is_unavailable_ref(ref) or not ref.startswith(CONTEXT_OFFLOAD_REF_PREFIX):
            return None
        published = False
        try:
            if getattr(lock, "lost", False) or not await store.ref_exists(session_id, ref):
                return None
            if await store.set_alias_if_absent(session_id, ref, alias):
                published = True
                if not getattr(lock, "lost", False) and await store.ref_exists(session_id, ref):
                    return ref
                return None
            winner = await store.resolve_alias(session_id, alias)
            if winner == ref:
                published = True
            if (not getattr(lock, "lost", False) and winner.startswith(CONTEXT_OFFLOAD_REF_PREFIX)
                    and await store.ref_exists(session_id, winner)):
                return winner
            return None
        finally:
            # Never delete a published ref that a successor may already use.
            # An uncertain/lost lease claims no ticket; normal unpublished
            # failures clean up their private candidate while still fenced.
            if not published and not getattr(lock, "lost", False):
                await store.delete_ref(session_id, ref)
