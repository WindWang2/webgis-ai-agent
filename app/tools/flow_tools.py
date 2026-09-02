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

    od_table_ref: str = Field(..., json_schema_extra={"ref_cursor": True})
    origin_lng_field: str = "origin_lng"
    origin_lat_field: str = "origin_lat"
    destination_lng_field: str = "destination_lng"
    destination_lat_field: str = "destination_lat"
    weight_field: str = "weight"
    top_n: int = 500
    min_weight: float = 0.0
    aggregate: str = "none"
    normalize: bool = True
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
    try:
        from app.lib.cartography.palettes import COLOR_PALETTES

        colors = COLOR_PALETTES.get("Plasma") or []
        return [str(c) for c in colors[:8]]
    except Exception:  # noqa: BLE001 — legend is best-effort
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
            "Build a bounded OD flow line layer from structured OD input "
            "(origin/destination coordinates + weight). Supports top-N "
            "selection, weight threshold, bidirectional/origin/destination "
            "aggregation, and weight normalization. Output renders as a "
            "flow_od_arc map layer (width ← weight). Large inputs are fine: "
            "selection is O(N log N), never pairwise; output is capped."
        ),
        parameters={
            "type": "object",
            "properties": {
                "od_table_ref": {
                    "type": "string",
                    "description": "Session ref/alias of the OD table (rows with origin/destination coords + weight)",
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
                    "description": "aggregate duplicate pairs: none | bidirectional (A→B + B→A) | origin | destination",
                },
                "normalize": {"type": "boolean", "description": "add weight_norm ∈ [0,1] per flow"},
            },
            "required": ["od_table_ref"],
        },
        args_model=ODFlowEdgesArgs,
        tier=2, domains=["network"],
        tags=["od", "flow", "mobility"],
    )
    async def od_flow_edges(
        od_table_ref: str,
        origin_lng_field: str = "origin_lng",
        origin_lat_field: str = "origin_lat",
        destination_lng_field: str = "destination_lng",
        destination_lat_field: str = "destination_lat",
        weight_field: str = "weight",
        top_n: int = 500,
        min_weight: float = 0.0,
        aggregate: str = "none",
        normalize: bool = True,
        session_id: Optional[str] = None,
    ) -> dict:
        try:
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
                        "field": weight_field,
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
                    "weight_field": weight_field,
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
        except Exception as e:  # noqa: BLE001 — bounded, disclosed failure
            logger.error("[flow_tools] od_flow_edges failed: %s", e, exc_info=True)
            return {"success": False, "error": f"OD 流向构建失败: {e}"[:300]}


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
