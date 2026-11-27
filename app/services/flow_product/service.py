from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, Optional

from app.services.mapspec.intents import ApplyFlowProductIntent
from app.services.session_data import session_data_manager

from .builder import build_flow_product
from .contracts import FlowProductConfig, FlowProductError
from .presentation import (
    build_map_fragment,
    build_query_index,
    fingerprint,
    restyle_product,
)


class FlowServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_STYLE_FIELDS = {
    "palette", "width_min_px", "width_max_px", "weight_transform",
    "weight_domain", "normalize", "arrow_spacing_px", "arrow_size_px",
    "show_arrows", "show_nodes",
}


async def _resolve_source(session_id: str, ref: str) -> tuple[str, Any, Dict[str, Any]]:
    from app.tools._utils import resolve_ref_payload

    resolved = await session_data_manager.resolve_alias(session_id, ref)
    payload = await resolve_ref_payload(session_id, ref)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
    if descriptor is None:
        raise FlowServiceError("FLOW_SOURCE_NOT_FOUND", f"OD source {ref} is unavailable in this session")
    return resolved, payload, descriptor


async def _load_product(session_id: str, product_ref: str) -> Dict[str, Any]:
    product = await session_data_manager.get(session_id, product_ref)
    if not isinstance(product, dict) or product.get("schema_version") != "od_flow_product.v1":
        raise FlowServiceError("FLOW_PRODUCT_NOT_FOUND", "OD flow product was not found in this session")
    return product


async def _cached_product(session_id: str, alias: str) -> Optional[Dict[str, Any]]:
    ref = await session_data_manager.resolve_alias(session_id, alias)
    if ref == alias:
        return None
    product = await session_data_manager.get(session_id, ref)
    return product if isinstance(product, dict) else None


def _cache_alias(kind: str, key: str) -> str:
    return f"od-flow-{kind}-{key.split(':', 1)[-1][:40]}"


async def _store_product(
    session_id: str,
    product: Dict[str, Any],
    edge_fc: Dict[str, Any],
    node_fc: Dict[str, Any],
    config: FlowProductConfig,
) -> Dict[str, Any]:
    product = copy.deepcopy(product)
    product.pop("mapspec", None)
    product_ref = product.get("product_ref") or await session_data_manager.store(
        session_id, product, prefix="flow-product"
    )
    product["product_ref"] = product_ref
    for feature in edge_fc.get("features", []):
        props = feature.setdefault("properties", {})
        props["product_ref"] = product_ref
        props["flow_focus"] = "edge"
    for feature in node_fc.get("features", []):
        props = feature.setdefault("properties", {})
        props["product_ref"] = product_ref
        props["flow_focus"] = "node"
    if not product.get("edge_ref"):
        product["edge_ref"] = await session_data_manager.store(session_id, edge_fc, prefix="geojson")
    if not product.get("node_ref"):
        product["node_ref"] = await session_data_manager.store(session_id, node_fc, prefix="flow-nodes")
    product["query_index"] = build_query_index(edge_fc, node_fc)
    product["mapspec"] = build_map_fragment(product, config)
    if not await session_data_manager.overwrite(session_id, product_ref, product):
        raise FlowServiceError("FLOW_PRODUCT_STORE_FAILED", "OD flow product could not be persisted")
    return product


async def inspect_flow_source(session_id: str, config: FlowProductConfig) -> Dict[str, Any]:
    source_ref, payload, descriptor = await _resolve_source(session_id, config.od_table_ref)
    from app.tools.flow_tools import _extract_rows

    rows, source_kind = _extract_rows(payload)
    fields: Dict[str, str] = {}
    for row in rows[:20]:
        for key, value in row.items():
            fields.setdefault(key, type(value).__name__)
    def _sample_value(value: object) -> object:
        # 有界样例：长字符串截断，避免把超大字段值整块注入 agent 上下文
        # （键数与行数已各自有界；值是用户自身租户数据，仍设长度帽）。
        if isinstance(value, str) and len(value) > 200:
            return value[:200] + "…"
        return value

    sample = [
        {str(k)[:64]: _sample_value(v) for k, v in list(row.items())[:24]}
        for row in rows[:8]
    ]
    return {
        "success": True,
        "operation": "inspect",
        "source_ref": source_ref,
        "source_revision": int(descriptor.get("content_revision") or 0),
        "source_kind": source_kind,
        "row_count": descriptor.get("row_count") or descriptor.get("feature_count") or len(rows),
        "crs": (payload.get("crs") if isinstance(payload, dict) else None) or config.source_crs,
        "value_semantics": "cost" if isinstance(payload, dict) and payload.get("type") == "od_matrix" else config.value_semantics,
        "fields": fields,
        "sample": sample,
        "sample_truncated": len(rows) > len(sample),
    }


async def preview_flow_product(session_id: str, config: FlowProductConfig) -> Dict[str, Any]:
    source_ref, payload, descriptor = await _resolve_source(session_id, config.od_table_ref)
    node_ref = config.node_table_ref
    node_payload = None
    node_revision = 0
    if node_ref:
        node_ref, node_payload, node_descriptor = await _resolve_source(session_id, node_ref)
        node_revision = int(node_descriptor.get("content_revision") or 0)
    config = config.model_copy(update={"od_table_ref": source_ref, "node_table_ref": node_ref})
    source_revision = int(descriptor.get("content_revision") or 0)
    source_key = fingerprint({
        "od": payload,
        "source_revision": source_revision,
        "nodes": node_payload,
        "node_revision": node_revision,
    })
    full_key = fingerprint({"source": source_key, "config": config.model_dump(mode="json")})
    full_alias = _cache_alias("preview", full_key)
    cached = await _cached_product(session_id, full_alias)
    if cached is not None and cached.get("fingerprint") == full_key:
        return await _preview_envelope(session_id, cached, cache_hit="preview")

    analysis_config = {
        key: value for key, value in config.model_dump(mode="json").items()
        if key not in _STYLE_FIELDS
    }
    analysis_key = fingerprint({"source": source_key, "config": analysis_config})
    analysis_alias = _cache_alias("analysis", analysis_key)
    cached = await _cached_product(session_id, analysis_alias)
    if cached is not None:
        edge_fc = await session_data_manager.get(session_id, cached["edge_ref"])
        node_fc = await session_data_manager.get(session_id, cached["node_ref"])
        if not isinstance(edge_fc, dict) or not isinstance(node_fc, dict):
            raise FlowServiceError("FLOW_ARTIFACT_NOT_FOUND", "Cached OD flow artifacts are unavailable")
        product, edge_fc, encoding_changed = restyle_product(cached, edge_fc, node_fc, config)
        # 产品身份必须只由（源 + 完整 config）决定：restyle 路径与 build 路径
        # 共用同一 fingerprint（此前 restyle 另算，同 config 会因缓存路径不同
        # 而漂移，且 full_alias 永远不命中）。
        product["fingerprint"] = full_key
        if encoding_changed:
            product.pop("edge_ref", None)
        # 样式变体是独立产品：不得沿用分析缓存产品的 ref 覆盖写，
        # 否则先前 preview 的 ref 会被套上新样式（envelope 与 apply 不一致）。
        product.pop("product_ref", None)
        product = await _store_product(session_id, product, edge_fc, node_fc, config)
        cache_hit = "analysis"
    else:
        built = await asyncio.to_thread(
            build_flow_product,
            payload,
            config,
            source_scope=source_ref,
            source_revision=source_revision,
            node_payload=node_payload,
        )
        product = built.product
        product["fingerprint"] = full_key
        product = await _store_product(session_id, product, built.edge_fc, built.node_fc, config)
        cache_hit = "miss"
    await session_data_manager.set_alias(session_id, product["product_ref"], analysis_alias)
    await session_data_manager.set_alias(session_id, product["product_ref"], full_alias)
    return await _preview_envelope(session_id, product, cache_hit=cache_hit)


async def _preview_envelope(session_id: str, product: Dict[str, Any], *, cache_hit: str) -> Dict[str, Any]:
    state = await session_data_manager.get_map_state(session_id) or {}
    edge_count = int(product["stats"]["edges"]["displayed"])
    return {
        "success": True,
        "operation": "preview",
        "product_ref": product["product_ref"],
        "family_id": product["family_id"],
        "source_ref": product["source_ref"],
        "source_revision": product["source_revision"],
        "source_fingerprint": product["source_fingerprint"],
        "product_fingerprint": product["fingerprint"],
        "analysis_fingerprint": product["analysis_fingerprint"],
        "cache_hit": cache_hit,
        "aggregation_reused": cache_hit != "miss",
        "edge_ref": product["edge_ref"],
        "node_ref": product["node_ref"],
        "stats": product["stats"],
        "supported_modes": product["supported_modes"],
        "mode_availability": product["mode_availability"],
        "warnings": product["warnings"],
        "degradations": product["degradations"],
        "can_apply": edge_count > 0,
        "blocking_reasons": [] if edge_count else ["empty_display_set"],
        "mapspec_fragment": product["mapspec"],
        "mapspec_revision": int(state.get("_cartographic_mutation_revision") or 0),
    }


async def preview(session_id: str, config: FlowProductConfig) -> Dict[str, Any]:
    return await preview_flow_product(session_id, config)


async def apply_flow_product_preview(
    session_id: str,
    product_ref: str,
    *,
    expected_revision: int,
    mutation_id: Optional[str] = None,
    origin: str = "agent",
) -> Dict[str, Any]:
    product = await _load_product(session_id, product_ref)
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

    result = await MapSpecLifecycleEngine().apply_mutation(
        session_id,
        ApplyFlowProductIntent(product_ref=product_ref, family_id=product["family_id"]),
        origin=origin,  # type: ignore[arg-type]
        expected_revision=expected_revision,
        mutation_id=mutation_id,
    )
    payload = result.to_dict()
    payload.update({
        "success": not result.is_error and not result.superseded,
        "operation": "apply",
        "product_ref": product_ref,
        "family_id": product["family_id"],
        "stats": product["stats"],
    })
    return payload


def _page(items: list[Any], offset: int, limit: int) -> Dict[str, Any]:
    offset = max(0, offset)
    limit = max(1, min(200, limit))
    return {
        "items": items[offset:offset + limit],
        "offset": offset,
        "limit": limit,
        "matched_count": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


async def query_flow_product(
    session_id: str,
    product_ref: str,
    *,
    kind: str = "product",
    object_id: str = "",
    direction: str = "both",
    offset: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    product = await _load_product(session_id, product_ref)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, product["source_ref"])
    current_revision = int((descriptor or {}).get("content_revision") or 0)
    if current_revision != int(product["source_revision"]):
        raise FlowServiceError("FLOW_SOURCE_STALE", "OD source changed; clear the old focus and create a fresh preview")
    index = product.get("query_index") or {}
    if kind == "product":
        return {
            "success": True,
            "operation": "query",
            "kind": kind,
            "product_ref": product_ref,
            "stats": product["stats"],
            "supported_modes": product["supported_modes"],
            "mode_availability": product["mode_availability"],
        }
    if kind == "node":
        render_id = product.get("id_mapping", {}).get("legacy_to_render", {}).get(object_id, object_id)
        node = (index.get("nodes") or {}).get(render_id)
        if node is None:
            raise FlowServiceError("FLOW_NODE_NOT_FOUND", f"OD node {object_id} is not in the displayed product")
        edge_ids: set[str] = set()
        if direction in ("outbound", "both"):
            edge_ids.update((index.get("by_origin") or {}).get(render_id, []))
        if direction in ("inbound", "both"):
            edge_ids.update((index.get("by_destination") or {}).get(render_id, []))
        edges = [index["edges"][edge_id] for edge_id in sorted(edge_ids)]
        edges.sort(key=lambda props: (-float(props.get("weight") or 0.0), str(props.get("id"))))
        return {
            "success": True,
            "operation": "query",
            "kind": kind,
            "product_ref": product_ref,
            "node": node,
            "direction": direction,
            "totals_scope": product["nodes"]["totals_scope"],
            "edges": _page(edges, offset, limit),
        }
    if kind in ("edge", "members"):
        edge = (index.get("edges") or {}).get(object_id)
        if edge is None:
            raise FlowServiceError("FLOW_EDGE_NOT_FOUND", f"OD edge {object_id} is not in the displayed product")
        members = product.get("aggregation_members", {}).get(object_id, [])
        result = {
            "success": True,
            "operation": "query",
            "kind": kind,
            "product_ref": product_ref,
            "edge": edge,
            "members": _page(members, offset, limit),
        }
        return result
    raise FlowServiceError("FLOW_QUERY_INVALID", f"Unsupported OD flow query kind {kind}")


__all__ = [
    "FlowServiceError",
    "FlowProductError",
    "inspect_flow_source",
    "preview",
    "preview_flow_product",
    "apply_flow_product_preview",
    "query_flow_product",
]
