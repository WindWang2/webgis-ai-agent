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


def _dumps_compact(obj: Any) -> str:
    """P-7（#880）：数据面 compact 序列化（无空白）——pretty 对 50k 要素层
    放大 ~1.8x 体积且编码更慢；前端只做 JSON.parse，空白完全无用。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


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


async def _serialize_compact(data: Any) -> bytes:
    """P-7（#880）：compact 数据面序列化（分块 + to_thread，与 pretty 路径
    同款事件循环保护）。输出字节等价于
    ``json.dumps(data, ensure_ascii=False, separators=(",", ":"))``。"""
    if not isinstance(data, dict) or not data:
        return (await asyncio.to_thread(_dumps_compact, data)).encode("utf-8")

    parts: list = ["{"]
    items = list(data.items())
    for idx, (key, val) in enumerate(items):
        comma = "," if idx < len(items) - 1 else ""
        key_frag = json.dumps(key, ensure_ascii=False)
        if isinstance(val, list) and len(val) > _GEOJSON_CHUNK_MIN_ITEMS:
            parts.append(f"{key_frag}:[")
            total = len(val)
            for start in range(0, total, _GEOJSON_BATCH_ITEMS):
                chunk = val[start : start + _GEOJSON_BATCH_ITEMS]

                def _encode_chunk(c: list) -> List[str]:
                    return [_dumps_compact(el) for el in c]

                batch = await asyncio.to_thread(_encode_chunk, chunk)
                tail = "," if start + _GEOJSON_BATCH_ITEMS < total else ""
                parts.append(",".join(batch) + tail)
            parts.append(f"]{comma}")
        else:
            vfrag = await asyncio.to_thread(_dumps_compact, val)
            parts.append(f"{key_frag}:{vfrag}{comma}")
    parts.append("}")

    body = await asyncio.to_thread(lambda: "".join(parts).encode("utf-8"))
    return body


async def serialize_geojson(data: Any, *, pretty: bool = True) -> bytes:
    """Chunked, byte-identical replacement for json.dumps(data, indent=2).

    Must be awaited from an async context; every C-encoder call is dispatched
    to a worker thread so the event loop never stalls for a full encode.

    P-7（#880）：``pretty=False`` 走 compact 路径（数据面 REST 端点），
    输出等价于 ``json.dumps(data, separators=(",", ":"))``；默认
    ``pretty=True`` 保持既有字节契约（人工导出/调试端点）。
    """
    if not pretty:
        return await _serialize_compact(data)
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