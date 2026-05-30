"""Layer schema inference, caching, and formatting for chat context."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.services.session_data import session_data_manager
from app.utils.geojson import geojson_bbox, summarize_feature_properties
from .geometry import viewport_layer_relation
from .formatters import (
    _untrusted,
    _xml_fence,
    format_style_summary,
    TAG_UNTRUSTED_LAYER_NAME,
    TAG_UNTRUSTED_LAYER_ALIAS,
    TAG_UNTRUSTED_LAYER_TYPE,
)

logger = logging.getLogger(__name__)

_LAYER_SCHEMA_CACHE_MAX = 1024
# /review P2-1: per-(session, ref) cached schema. GeoJSON data behind a ref is
# immutable once stored (session_data_manager.store returns a new ref on each
# `put()`), so the inferred schema is also immutable.
_layer_schema_cache: dict[tuple[str, str], dict] = {}


def clear_layer_schema_cache(session_id: str | None = None) -> None:
    """Drop cached schemas. Pass session_id to drop only one session's entries."""
    if session_id is None:
        _layer_schema_cache.clear()
        return
    for key in [k for k in _layer_schema_cache if k[0] == session_id]:
        _layer_schema_cache.pop(key, None)


async def build_layer_schema(session_id: str, ref_id: str, sample_size: int = 5) -> dict | None:
    """从 session 里取 GeoJSON 数据，抽样推断 properties 字段名+类型 + 几何类型 + bbox。

    返回形如 {"geom":"Polygon", "count":123, "fields":{...}, "bbox":[w,s,e,n]}。
    LRU 已经在 manager.get() 内部维护，这里不再额外更新顺序。
    数据不存在或不是 FeatureCollection 时返回 None。

    /review P2-1: result cached per (session_id, ref_id) — refs are immutable.
    """
    cache_key = (session_id, ref_id)
    cached = _layer_schema_cache.get(cache_key)
    if cached is not None:
        return cached

    data = await session_data_manager.get(session_id, ref_id)
    if not isinstance(data, dict):
        return None
    features = data.get("features")
    if not isinstance(features, list) or not features:
        return None

    geom_types: set[str] = set()
    for feat in features:
        if isinstance(feat, dict):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            if isinstance(gtype, str):
                geom_types.add(gtype)

    # Use DRY utils from app.utils.geojson (P3-2)
    bbox = geojson_bbox(data)
    fields, _ = summarize_feature_properties(features, sample_size=sample_size)

    schema = {
        "geom": "/".join(sorted(geom_types)) if geom_types else None,
        "count": len(features),
        "fields": fields,
        "bbox": bbox,
    }

    # /review P2-1: write to cache. Bounded LRU-ish via simple oldest-eviction
    if len(_layer_schema_cache) >= _LAYER_SCHEMA_CACHE_MAX:
        try:
            _layer_schema_cache.pop(next(iter(_layer_schema_cache)))
        except StopIteration:
            pass
    _layer_schema_cache[cache_key] = schema
    return schema


def format_layer_schema(schema: dict, viewport_bounds: list[float] | None = None) -> str:
    """把 build_layer_schema 的输出渲染为单行紧凑文本，可选附加视口关系。"""
    parts: list[str] = []
    if schema.get("geom"):
        parts.append(f"geom={schema['geom']}")
    if schema.get("count") is not None:
        parts.append(f"n={schema['count']}")
    fields = schema.get("fields") or {}
    if fields:
        # 限制最多 8 个字段，避免上下文爆
        items = list(fields.items())[:8]
        field_str = ", ".join(f"{k}:{t}" for k, t in items)
        if len(fields) > 8:
            field_str += f", ...(+{len(fields) - 8})"
        parts.append(f"fields=[{field_str}]")

    bbox = schema.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        parts.append(f"bbox=[{bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f}]")
        relation = viewport_layer_relation(viewport_bounds, bbox)
        if relation:
            parts.append(relation)
    return " ".join(parts)


async def format_layer_lines(
    inventory: dict,
    active_layers: list[dict],
    session_id: str | None = None,
    viewport_bounds: list[float] | None = None,
) -> list[str]:
    """渲染图层一行式描述。inventory 优先，缺失时回退到前端上报。

    当传入 session_id 时，额外把每个 ref 的属性 schema (字段+类型+几何+bbox) 拼到末行；
    传 viewport_bounds 时再附加"在视口内/外/局部相交"。
    """
    out: list[str] = []
    if inventory:
        # Gather all schemas in parallel before the main loop
        schema_map: dict[str, dict | None] = {}
        if session_id:
            ref_ids_list = list(inventory.keys())
            schemas = await asyncio.gather(
                *[build_layer_schema(session_id, rid) for rid in ref_ids_list],
                return_exceptions=True,
            )
            schema_map = {
                rid: (s if isinstance(s, dict) else None)
                for rid, s in zip(ref_ids_list, schemas)
            }
            for rid, s in zip(ref_ids_list, schemas):
                if isinstance(s, BaseException):
                    logger.warning("build_layer_schema failed for ref=%s: %s", rid, s)

        visibility_map = {l.get("id"): l for l in active_layers if l.get("id")}
        for ref_id, alias in inventory.items():
            meta = visibility_map.get(ref_id) or next(
                (m for aid, m in visibility_map.items() if aid in ref_id or ref_id in aid),
                {},
            )
            visible = meta.get("visible")
            status = "可见" if visible is True else "隐藏" if visible is False else "未知"
            attrs = []
            if alias:
                # /review P1-4: alias is user-controlled
                attrs.append(f"别名={_xml_fence(TAG_UNTRUSTED_LAYER_ALIAS, alias)}")
            if meta.get("type"):
                attrs.append(f"类型={_xml_fence(TAG_UNTRUSTED_LAYER_TYPE, meta['type'])}")
            if meta.get("featureCount") is not None:
                attrs.append(f"要素={meta['featureCount']}")
            style_str = format_style_summary(meta.get("style"))
            if style_str:
                attrs.append(style_str)
            tail = f" [{', '.join(attrs)}]" if attrs else ""
            line = f"{ref_id}{tail} ({status})"
            schema = schema_map.get(ref_id)
            if schema:
                line += f" | {format_layer_schema(schema, viewport_bounds)}"
            out.append(line)
        return out

    for layer in active_layers:
        lid = layer.get("id", "unknown")
        name = layer.get("name", lid)
        attrs = []
        if layer.get("type"):
            attrs.append(f"类型={_xml_fence(TAG_UNTRUSTED_LAYER_TYPE, layer['type'])}")
        if layer.get("featureCount") is not None:
            attrs.append(f"要素={layer['featureCount']}")
        opacity = layer.get("opacity", 1.0)
        attrs.append(f"不透明度={opacity:.0%}")
        style_str = format_style_summary(layer.get("style"))
        if style_str:
            attrs.append(style_str)
        status = "可见" if layer.get("visible") else "隐藏"
        out.append(f"{_xml_fence(TAG_UNTRUSTED_LAYER_NAME, name)} (id={_xml_fence(TAG_UNTRUSTED_LAYER_NAME, lid)}, {', '.join(attrs)}) ({status})")
    return out
