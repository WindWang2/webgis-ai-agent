"""OD Flow Analysis tools (ADR-0092 Phase D).

``od_flow_edges`` is the missing D1/D2/D3 link: it turns structured OD input
(OD pair rows with origin/destination coordinates + weight) into a bounded,
weight-carrying line FeatureCollection that renders as the (now native)
``flow_od_arc`` MapModel.

Complexity contract (§11 performance red lines):
- aggregation / top-N selection is O(N log N) at worst (heap-select);
- **no pairwise (O(N²)) construction** — the input is already an OD edge
  list; re-expanding it into an origin×destination matrix is forbidden;
- output is bounded: ``top_n`` (hard cap ``OD_FLOW_MAX_EDGES``) + optional
  weight threshold.
"""
from __future__ import annotations

import heapq
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.utils.best_effort import best_effort
from app.services.flow_product.contracts import FlowProductConfig, FlowProductError
from app.services.flow_product.service import (
    FlowServiceError,
    apply_flow_product_preview,
    inspect_flow_source,
    preview,
    query_flow_product,
)

logger = logging.getLogger(__name__)

#: Hard output cap — even an explicit top_n above this is clamped (bounded).
OD_FLOW_MAX_EDGES = 5000
_MAX_INPUT_ROWS = 500_000
_COORD_DECIMALS = 6

_AGGREGATION_MODES = ("none", "bidirectional", "origin", "destination")


class ODFlowEdgesArgs(BaseModel):
    """Args model — od_table_ref is a declared ref cursor: the registry's
    transparent alias resolution must NOT inline the OD payload into the
    argument (the tool resolves the ref itself, fetch-on-demand)."""

    od_table_ref: str = Field("", json_schema_extra={"ref_cursor": True})
    origin_lng_field: str = "origin_lng"
    origin_lat_field: str = "origin_lat"
    destination_lng_field: str = "destination_lng"
    destination_lat_field: str = "destination_lat"
    weight_field: str = "weight"
    top_n: int = 500
    min_weight: float = 0.0
    aggregate: str = "none"
    normalize: bool = True
    operation: str = "build"
    flow: Optional[FlowProductConfig] = None
    product_ref: str = Field("", json_schema_extra={"ref_cursor": True})
    expected_revision: Optional[int] = None
    mutation_id: Optional[str] = None
    query_kind: str = "product"
    object_id: str = ""
    focus_direction: str = "both"
    # Bounds mirror the declared parameters schema (limit∈[1,200]/offset≥0):
    # dispatch validates through this args model, not the LLM-facing schema,
    # so an unconstrained int would let an out-of-range paging value reach
    # query_flow_product instead of surfacing a self-healing validation error.
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)
    # NOTE: session_id is intentionally NOT a model field — the registry
    # injects it from the dispatch context AFTER validation, so an
    # LLM/workflow-authored session_id is rejected here instead of reaching
    # the session store (cross-session read guard).


def _haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _extract_rows(payload: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Accept od_table docs, plain row lists, or FCs with OD properties."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)], "rows"
    if not isinstance(payload, dict):
        return [], ""
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)], "od_table"
    features = payload.get("features")
    if isinstance(features, list) and payload.get("type") == "FeatureCollection":
        # Zero-copy: rows are only read (never mutated) downstream — the
        # per-feature properties dict() copy doubled peak memory at 500k rows.
        return [
            f["properties"]
            for f in features
            if isinstance(f, dict) and isinstance(f.get("properties"), dict)
        ], "feature_collection"
    return [], ""


def _plasma_colors() -> List[str]:
    """Single color source for the flow legend/paint (MapModel default palette)."""
    # #1549：legend is best-effort；经 best_effort 可见降级。
    with best_effort(
        "plasma-palette-read",
        "tools-flow-plasma-palette-best-effort",
    ):
        from app.lib.cartography.palettes import COLOR_PALETTES

        colors = COLOR_PALETTES.get("Plasma") or []
        return [str(c) for c in colors[:8]]
    return []


class _FlowAccumulator:
    """One aggregated flow (O(1) per input row; coordinates are first-wins
    unless the aggregation mode pins them)."""

    __slots__ = ("weight", "o", "d", "o_id", "d_id")

    def __init__(self) -> None:
        self.weight = 0.0
        self.o: Optional[Tuple[float, float]] = None
        self.d: Optional[Tuple[float, float]] = None
        self.o_id = ""
        self.d_id = ""


def register_flow_tools(registry: ToolRegistry) -> None:

    @tool(registry,
        name="od_flow_edges",
        description=(
            "Build or edit a bounded OD flow product from real OD rows "
            "(coordinates or explicit node IDs + flow weight). Legacy build keeps "
            "straight lines. operation=inspect|preview|apply|query adds directed, "
            "undirected-total and net products with stable identities, node focus, "
            "gross/net scope evidence, curves and visible direction markers. Net never "
            "treats an unobserved reverse as zero; cost/time/distance matrices are not "
            "flow volumes. Curves are decorative, never travel routes. Input/output "
            "are capped and no pairwise matrix or external provider is used."
        ),
        parameters={
            "type": "object",
            "properties": {
                "od_table_ref": {
                    "type": "string",
                    "description": "Session ref/alias of the OD table (required for inspect/preview or apply without product_ref)",
                },
                "origin_lng_field": {"type": "string", "description": "origin longitude field (default origin_lng)"},
                "origin_lat_field": {"type": "string", "description": "origin latitude field (default origin_lat)"},
                "destination_lng_field": {"type": "string", "description": "destination longitude field (default destination_lng)"},
                "destination_lat_field": {"type": "string", "description": "destination latitude field (default destination_lat)"},
                "weight_field": {"type": "string", "description": "weight field (default weight)"},
                "top_n": {"type": "integer", "description": "keep the N heaviest flows (default 500, cap 5000)"},
                "min_weight": {"type": "number", "description": "drop flows below this weight (default 0)"},
                "aggregate": {
                    "type": "string",
                    "enum": list(_AGGREGATION_MODES),
                    "description": "legacy build aggregation; origin/destination pseudo-lines are rejected with node-stat migration guidance",
                },
                "normalize": {"type": "boolean", "description": "add weight_norm ∈ [0,1] per flow"},
                "operation": {"type": "string", "enum": ["build", "inspect", "preview", "apply", "query"]},
                "flow": FlowProductConfig.model_json_schema(),
                "product_ref": {"type": "string", "description": "session-owned preview ref for apply/query"},
                "expected_revision": {"type": "integer", "description": "MapSpec CAS revision from preview or current state"},
                "mutation_id": {"type": "string", "description": "optional idempotent apply key"},
                "query_kind": {"type": "string", "enum": ["product", "node", "edge", "members"]},
                "object_id": {"type": "string", "description": "node/edge render ID or legacy business node ID"},
                "focus_direction": {"type": "string", "enum": ["inbound", "outbound", "both"]},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": [],
        },
        args_model=ODFlowEdgesArgs,
        tier=2, domains=["network"],
        tags=["od", "flow", "mobility"],
        side_effect="deterministic_compute",
        network=False,  # Only resolves existing session data; no routing/provider API.
        deterministic=True,
        # Core dispatch stores the complete FC under a session-owned ref.
        # The edge-count cap does not bound arbitrary origin/destination IDs.
        output_semantic_type="geojson_fc",
        result_size_policy="ref_offload",
    )
    async def od_flow_edges(
        od_table_ref: str = "",
        origin_lng_field: str = "origin_lng",
        origin_lat_field: str = "origin_lat",
        destination_lng_field: str = "destination_lng",
        destination_lat_field: str = "destination_lat",
        weight_field: str = "weight",
        top_n: int = 500,
        min_weight: float = 0.0,
        aggregate: str = "none",
        normalize: bool = True,
        operation: str = "build",
        flow: Optional[FlowProductConfig] = None,
        product_ref: str = "",
        expected_revision: Optional[int] = None,
        mutation_id: Optional[str] = None,
        query_kind: str = "product",
        object_id: str = "",
        focus_direction: str = "both",
        offset: int = 0,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> dict:
        try:
            if operation != "build":
                return await _run_flow_operation(
                    session_id,
                    operation=operation,
                    od_table_ref=od_table_ref,
                    origin_fields=(origin_lng_field, origin_lat_field, destination_lng_field, destination_lat_field),
                    weight_field=weight_field,
                    top_n=top_n,
                    min_weight=min_weight,
                    aggregate=aggregate,
                    normalize=normalize,
                    flow=flow,
                    product_ref=product_ref,
                    expected_revision=expected_revision,
                    mutation_id=mutation_id,
                    query_kind=query_kind,
                    object_id=object_id,
                    focus_direction=focus_direction,
                    offset=offset,
                    limit=limit,
                )
            if aggregate in ("origin", "destination"):
                return {
                    "success": False,
                    "error": f"aggregate={aggregate} 的首端点伪总流线语义已停用",
                    "correction_hint": "请改用 operation=preview 的节点 inbound/outbound/total 统计；旧产物保持可读，但不新造指向首个端点的总流线",
                }
            if aggregate not in _AGGREGATION_MODES:
                return {
                    "success": False,
                    "error": f"未知聚合模式 '{aggregate}'（可选 {list(_AGGREGATION_MODES)}）",
                    "correction_hint": "使用 none / bidirectional / origin / destination 之一",
                }
            payload = await _resolve_payload(od_table_ref, session_id)
            rows, source_kind = _extract_rows(payload)
            if not rows:
                return {
                    "success": False,
                    "error": "OD 输入为空或结构不识别（需要 rows 列表或带 OD 属性的 FeatureCollection）",
                    "correction_hint": (
                        "OD 表需要 origin_lng/origin_lat/destination_lng/"
                        "destination_lat/weight 字段（D1 契约；字段名可用参数覆盖）"
                    ),
                }
            if len(rows) > _MAX_INPUT_ROWS:
                return {
                    "success": False,
                    "error": f"OD 行数 {len(rows)} 超过处理上限 {_MAX_INPUT_ROWS}",
                }

            kept_n = max(0, min(int(top_n), OD_FLOW_MAX_EDGES))
            if kept_n <= 0:
                return {"success": False, "error": "top_n 必须为正整数"}

            flows, skipped = _aggregate_rows(
                rows,
                (origin_lng_field, origin_lat_field,
                 destination_lng_field, destination_lat_field),
                weight_field,
                aggregate,
                min_weight,
            )
            # ── O(N log k) bounded top-N ──────────────────────────────
            selected = heapq.nlargest(
                kept_n, flows,
                key=lambda fa: (fa.weight, fa.o_id, fa.d_id),
            )

            # Weight domain spans the whole (thresholded) population, not
            # just the selected top-N: a top-N slice of uniform weights would
            # otherwise degenerate to min==max and break the color/width
            # channels (interpolate needs a strictly increasing domain).
            pop_max = max((fa.weight for fa in flows), default=0.0)
            pop_min = min((fa.weight for fa in flows), default=0.0)
            w_max = pop_max
            w_min = pop_min
            span = (w_max - w_min) or 1.0
            degenerate = pop_max <= pop_min
            features: List[Dict[str, Any]] = []
            for fa in selected:
                olng, olat = fa.o or (0.0, 0.0)
                dlng, dlat = fa.d or (0.0, 0.0)
                props = {
                    "id": f"{fa.o_id or f'{olng:.4f}'}->{fa.d_id or f'{dlng:.4f}'}",
                    "origin_id": fa.o_id,
                    "destination_id": fa.d_id,
                    "origin_lng": olng, "origin_lat": olat,
                    "destination_lng": dlng, "destination_lat": dlat,
                    "weight": round(fa.weight, 6),
                }
                if normalize:
                    props["weight_norm"] = round((fa.weight - w_min) / span, 6)
                props["distance_km"] = round(_haversine_km(olng, olat, dlng, dlat), 3)
                features.append({
                    "type": "Feature",
                    "id": props["id"],
                    "geometry": {"type": "LineString", "coordinates": [
                        [olng, olat], [dlng, dlat],
                    ]},
                    "properties": props,
                })

            fc: Dict[str, Any] = {
                "type": "FeatureCollection",
                "features": features,
                "command": "add_layer",
                "type_hint": "flow_od_arc",
                "legend_spec": (
                    {
                        "type": "continuous",
                        "field": "weight",
                        "source_field": weight_field,
                        "min": w_min,
                        "max": w_max,
                        "palette_colors": _plasma_colors(),
                        "title": "流量权重",
                    }
                    if not degenerate
                    else None
                ),
                "metadata": {
                    "flow": True,
                    "weight_field": "weight",
                    "source_weight_field": weight_field,
                    "weight_min": w_min,
                    "weight_max": w_max,
                    "width_max_px": 8.0,
                    "opacity": 0.85,
                    "total_edges": len(rows),
                    "skipped_rows": skipped,
                    "total_pairs": len(flows),
                    "kept": len(features),
                    "aggregation": aggregate,
                    "source_kind": source_kind,
                    "note": "弧线为起终点直线段（非真实路径）；方向由 origin→destination 语义表达",
                },
            }
            return fc
        except (FlowServiceError, FlowProductError) as e:
            return {
                "success": False,
                "error": str(e)[:300],
                "code": getattr(e, "code", "FLOW_INPUT_INVALID"),
                "correction_hint": "检查 OD 字段绑定、方向/缺失反向口径与源 revision 后重试",
            }
        except Exception as e:  # noqa: BLE001 — bounded, disclosed failure
            logger.error("[flow_tools] od_flow_edges failed: %s", e, exc_info=True)
            return {"success": False, "error": f"OD 流向构建失败: {e}"[:300]}


def _flow_operation_config(
    od_table_ref: str,
    origin_fields: Tuple[str, str, str, str],
    weight_field: str,
    top_n: int,
    min_weight: float,
    aggregate: str,
    normalize: bool,
    flow: Optional[FlowProductConfig],
) -> FlowProductConfig:
    if flow is not None:
        if isinstance(flow, dict):
            flow = FlowProductConfig(**flow)
        if flow.od_table_ref and od_table_ref and flow.od_table_ref != od_table_ref:
            raise FlowServiceError("FLOW_SOURCE_MISMATCH", "Top-level od_table_ref and flow.od_table_ref differ")
        if aggregate in ("origin", "destination"):
            raise FlowServiceError("FLOW_LEGACY_AGGREGATE_INVALID", "origin/destination pseudo-lines cannot be combined with a flow product")
        return flow.model_copy(update={"od_table_ref": flow.od_table_ref or od_table_ref})
    if aggregate in ("origin", "destination"):
        raise FlowServiceError("FLOW_LEGACY_AGGREGATE_INVALID", "Use node inbound/outbound statistics instead of origin/destination pseudo-lines")
    olng, olat, dlng, dlat = origin_fields
    return FlowProductConfig(
        od_table_ref=od_table_ref,
        origin_lng_field=olng,
        origin_lat_field=olat,
        destination_lng_field=dlng,
        destination_lat_field=dlat,
        weight_field=weight_field,
        weight_unit="count",
        direction_mode="undirected-total" if aggregate == "bidirectional" else "directed",
        top_n=top_n,
        min_weight=min_weight,
        normalize=normalize,
        show_arrows=False,
    )


async def _run_flow_operation(
    session_id: Optional[str],
    *,
    operation: str,
    od_table_ref: str,
    origin_fields: Tuple[str, str, str, str],
    weight_field: str,
    top_n: int,
    min_weight: float,
    aggregate: str,
    normalize: bool,
    flow: Optional[FlowProductConfig],
    product_ref: str,
    expected_revision: Optional[int],
    mutation_id: Optional[str],
    query_kind: str,
    object_id: str,
    focus_direction: str,
    offset: int,
    limit: int,
) -> dict:
    if not session_id:
        return {"success": False, "error": "OD flow operation requires a session", "code": "FLOW_SESSION_REQUIRED"}
    if operation == "query":
        if not product_ref:
            return {"success": False, "error": "query requires product_ref", "code": "FLOW_PRODUCT_REQUIRED"}
        return await query_flow_product(
            session_id, product_ref,
            kind=query_kind, object_id=object_id, direction=focus_direction,
            offset=offset, limit=limit,
        )
    if operation not in ("inspect", "preview", "apply"):
        return {"success": False, "error": f"Unknown OD flow operation {operation}", "code": "FLOW_OPERATION_INVALID"}
    if operation == "apply" and product_ref:
        if expected_revision is None:
            return {"success": False, "error": "apply requires expected_revision from preview/current MapSpec", "code": "FLOW_REVISION_REQUIRED"}
        return await apply_flow_product_preview(
            session_id, product_ref,
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            origin="agent",
        )
    config = _flow_operation_config(
        od_table_ref, origin_fields, weight_field, top_n, min_weight,
        aggregate, normalize, flow,
    )
    if not config.od_table_ref:
        return {"success": False, "error": f"{operation} requires od_table_ref", "code": "FLOW_SOURCE_REQUIRED"}
    if operation == "inspect":
        return await inspect_flow_source(session_id, config)
    preview_result = await preview(session_id, config)
    if operation == "preview":
        return preview_result
    revision = expected_revision
    if revision is None:
        revision = preview_result["mapspec_revision"]
    return await apply_flow_product_preview(
        session_id, preview_result["product_ref"],
        expected_revision=revision,
        mutation_id=mutation_id,
        origin="agent",
    )


async def _resolve_payload(od_table_ref: str, session_id: Optional[str]) -> Any:
    from app.tools._utils import resolve_ref_payload

    return await resolve_ref_payload(session_id or "", od_table_ref)


def _aggregate_rows(
    rows: List[Dict[str, Any]],
    fields: Tuple[str, str, str, str],
    weight_field: str,
    aggregate: str,
    min_weight: float,
) -> Tuple[List[_FlowAccumulator], int]:
    """O(N) aggregation into unique flows (no pairwise construction).

    Returns (flows_over_threshold, skipped_row_count)."""
    skipped = 0
    olng_f, olat_f, dlng_f, dlat_f = fields
    buckets: Dict[Tuple[Any, ...], _FlowAccumulator] = {}

    for row in rows:
        try:
            olng = float(row.get(olng_f))
            olat = float(row.get(olat_f))
            dlng = float(row.get(dlng_f))
            dlat = float(row.get(dlat_f))
            w = float(row.get(weight_field) or 0.0)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not (-180 <= olng <= 180 and -90 <= olat <= 90
                and -180 <= dlng <= 180 and -90 <= dlat <= 90):
            skipped += 1
            continue
        o = (round(olng, _COORD_DECIMALS), round(olat, _COORD_DECIMALS))
        d = (round(dlng, _COORD_DECIMALS), round(dlat, _COORD_DECIMALS))
        o_id = str(row.get("origin_id") or "")
        d_id = str(row.get("destination_id") or "")

        if aggregate == "bidirectional":
            # Canonical orientation: (min, max) so A→B and B→A merge.
            lo, hi = (o, d) if o <= d else (d, o)
            key: Tuple[Any, ...] = (lo, hi)
        elif aggregate == "origin":
            key = (o,)
        elif aggregate == "destination":
            key = (d,)
        else:
            # none: every input row is its own flow (id-based when present).
            key = (o_id or o, d_id or d)

        acc = buckets.get(key)
        if acc is None:
            acc = _FlowAccumulator()
            buckets[key] = acc
        acc.weight += w
        if aggregate == "bidirectional":
            acc.o, acc.d = lo, hi
        elif aggregate == "origin":
            acc.o = o
            if acc.d is None:
                acc.d = d
        elif aggregate == "destination":
            acc.d = d
            if acc.o is None:
                acc.o = o
        else:
            acc.o, acc.d = o, d
        if not acc.o_id and o_id:
            acc.o_id = o_id
        if not acc.d_id and d_id:
            acc.d_id = d_id

    return [acc for acc in buckets.values() if acc.weight >= min_weight], skipped
