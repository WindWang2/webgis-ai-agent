"""Chunked, event-loop-safe JSON serialization for large GeoJSON payloads.

#427 / #590: Python 3.13's C JSON encoder holds the GIL for the WHOLE encode,
so even ``asyncio.to_thread(json.dumps, ...)`` leaves the event loop stalled
for the full duration (measured in #427: 26 MB body → 0.5 s loop gap; 45 MB →
2.3 s). Top-level list values (GeoJSON ``features``) are therefore encoded in
bounded worker-thread batches with an ``await`` between batches: each batch
holds the GIL for only a few ms, keeping loop gaps small while all concurrent
SSE/WS streams stay responsive.

The data-plane REST endpoints (``/layers/data/{ref_id}``, ``/uploads/{id}/
geojson``) and the GeoJSON export route share this serializer. Output is
byte-identical to ``json.dumps(data, ensure_ascii=False, indent=2)`` — pinned
by tests/test_event_loop_offload_427.py across FeatureCollections (chunked
and small), empty containers, non-ASCII, floats and nested shapes.
"""

import asyncio
import json
from typing import Any, List


def _dumps_pretty(obj: Any) -> str:
    """Canonical GeoJSON serialization format (single-value fragment)."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _reindent(text: str, pad: str) -> str:
    """Shift a serialized JSON fragment one indent level deeper (all lines
    after the first get `pad` prefixed) — exactly how json.dumps(indent=2)
    lays out nested values."""
    if "\n" not in text:
        return text
    first, *rest = text.split("\n")
    return first + "".join("\n" + pad + line for line in rest)


# Top-level lists bigger than this are encoded in bounded worker-thread batches
# (below it a single dumps is cheaper than the thread dispatches).
_GEOJSON_CHUNK_MIN_ITEMS = 2000
_GEOJSON_BATCH_ITEMS = 512


def _encode_batch(elements: list, pad: str) -> List[str]:
    """Serialize one batch of top-level list elements at one indent level.

    Runs in a worker thread: each C-encoder call holds the GIL for only a few
    ms, so the event loop can keep servicing timers/SSE between batches."""
    return [_reindent(_dumps_pretty(el), pad) for el in elements]


def _encode_value(value: Any, pad: str) -> str:
    """Serialize a non-chunked top-level value at one indent level."""
    return _reindent(_dumps_pretty(value), pad)


async def serialize_geojson(data: Any) -> bytes:
    """Chunked, byte-identical replacement for json.dumps(data, indent=2).

    Must be awaited from an async context; every C-encoder call is dispatched
    to a worker thread so the event loop never stalls for a full encode.
    """
    if not isinstance(data, dict) or not data:
        return (await asyncio.to_thread(_dumps_pretty, data)).encode("utf-8")

    parts: list = ["{"]
    items = list(data.items())
    for idx, (key, val) in enumerate(items):
        comma = "," if idx < len(items) - 1 else ""
        key_frag = json.dumps(key, ensure_ascii=False)
        if isinstance(val, list) and len(val) > _GEOJSON_CHUNK_MIN_ITEMS:
            # Chunked path: elements live at indent depth 2 (4 spaces).
            parts.append(f"\n  {key_frag}: [")
            total = len(val)
            for start in range(0, total, _GEOJSON_BATCH_ITEMS):
                batch = await asyncio.to_thread(
                    _encode_batch, val[start : start + _GEOJSON_BATCH_ITEMS], "    "
                )
                for j, frag in enumerate(batch):
                    pos = start + j
                    parts.append(
                        "\n    " + frag + ("," if pos < total - 1 else "")
                    )
            parts.append(f"\n  ]{comma}")
        else:
            vfrag = await asyncio.to_thread(_encode_value, val, "  ")
            parts.append(f"\n  {key_frag}: {vfrag}{comma}")
    parts.append("\n}")

    body = await asyncio.to_thread(lambda: "".join(parts).encode("utf-8"))
    return body