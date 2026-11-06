"""Independent, atomic retention budgets for plan and checkpoint envelopes."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.session_control_refs import is_control_ref

MAX_CONTROL_REFS = 64
MAX_CONTROL_BYTES = 50 * 1024 * 1024
_HISTORY_ALIASES = ("plan-id:", "session-plan-id:")
_ACTIVE = frozenset({"pending", "proposed", "running", "partially_completed"})


def encoded_size(value: Any) -> int:
    from app.lib.numpy_json import numpy_json_default
    return len(json.dumps(value, ensure_ascii=False, default=numpy_json_default).encode())


def _referenced_controls(value: Any) -> set[str]:
    pending, refs, visited = [value], set(), 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > 100_000:
            raise ValueError("control reference protection traversal exceeds budget")
        if isinstance(item, str) and is_control_ref(item):
            refs.add(item)
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return refs


def eviction_plan(
    entries: Sequence[tuple[str, int, Any]], aliases: Mapping[str, str],
    incoming_ref: str, incoming_data: Any, incoming_bytes: int,
) -> list[str]:
    """Select old history only; reject before writes if live state cannot fit."""
    rows = [(ref, size, value) for ref, size, value in entries if ref != incoming_ref]
    rows.append((incoming_ref, incoming_bytes, incoming_data))
    protected = {incoming_ref}
    protected.update(ref for alias, ref in aliases.items() if not alias.startswith(_HISTORY_ALIASES))
    checkpoints = [ref for ref, _, _ in rows if ref.startswith("ref:checkpoint-")]
    protected.update(checkpoints[-1:])
    payloads = {ref: value for ref, _, value in rows}
    for ref, _, value in rows:
        if isinstance(value, dict) and value.get("__status__") in _ACTIVE:
            protected.add(ref)  # Legacy plan-mode has no current alias.
    pending = list(protected)
    seen: set[str] = set()
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        referenced = _referenced_controls(payloads.get(ref))
        protected.update(referenced)
        pending.extend(referenced - seen)
    count, total = len(rows), sum(size for _, size, _ in rows)
    removed = []
    for ref, size, _ in rows:
        if count <= MAX_CONTROL_REFS and total <= MAX_CONTROL_BYTES:
            break
        if ref not in protected:
            removed.append(ref)
            count -= 1
            total -= size
    if count > MAX_CONTROL_REFS or total > MAX_CONTROL_BYTES:
        raise ValueError("active session controls exceed independent retention budget")
    return removed


def trim_memory_for_write(store: Any, session_id: str, ref_id: str, data: Any, size: int) -> None:
    """Caller holds the memory store lock; validation precedes any eviction."""
    cache = store._store[session_id]
    sizes = store._ref_sizes[session_id]
    entries = [(ref, sizes.get(ref, 0), payload) for ref, payload in cache.items() if is_control_ref(ref)]
    victims = eviction_plan(entries, store._aliases.get(session_id, {}), ref_id, data, size)
    for ref in victims:
        cache.pop(ref, None)
        store._session_bytes[session_id] -= sizes.pop(ref, 0)
        store._remove_alias_by_ref(session_id, ref)
        store._descriptors.get(session_id, {}).pop(ref, None)
        store._ref_revisions.get(session_id, {}).pop(ref, None)
        store._invalidate_derived_caches(session_id, ref, reason="EVICT")


def _decode_rows(refs: list[str], values: list[Any]) -> list[tuple[str, int, Any]]:
    return [(ref, len(value.encode() if isinstance(value, str) else value), json.loads(value))
            for ref, value in zip(refs, values) if value is not None]


async def prepare_redis_write(
    pipe: Any, store: Any, session_id: str, ref_id: str, data: Any, payload_json: str,
) -> tuple[list[str], Mapping[str, str]]:
    """WATCH protects both retention decisions and all payload/alias writers."""
    order_key, aliases_key = store._refs_order_key(session_id), store._aliases_key(session_id)
    await pipe.watch(order_key, aliases_key)
    ordered = await pipe.zrange(order_key, 0, -1)
    refs = [ref.decode() if isinstance(ref, bytes) else ref for ref in ordered]
    refs = [ref for ref in refs if is_control_ref(ref)]
    keys = [store._data_key(session_id, ref) for ref in refs]
    if keys:
        await pipe.watch(*keys)
    values = await pipe.mget(keys) if keys else []
    entries = await asyncio.to_thread(_decode_rows, refs, values)
    raw_aliases = await pipe.hgetall(aliases_key)
    aliases = {(key.decode() if isinstance(key, bytes) else key):
               (value.decode() if isinstance(value, bytes) else value) for key, value in raw_aliases.items()}
    return eviction_plan(entries, aliases, ref_id, data, len(payload_json.encode())), aliases


def queue_redis_evictions(pipe: Any, store: Any, session_id: str, victims: list[str], aliases: Mapping[str, str]) -> None:
    """Queue complete ref cleanup in the incoming write's MULTI transaction."""
    for ref in victims:
        store._evict_ref(pipe, session_id, ref)
        pipe.hdel(store._ref_revisions_key(session_id), ref)
        pipe.hdel(store._ref_digests_key(session_id), ref)
        owned = [alias for alias, target in aliases.items() if target == ref]
        if owned:
            pipe.hdel(store._aliases_key(session_id), *owned)
