from __future__ import annotations

from typing import Any, Dict, List


def _source_features(source: Any) -> List[Any] | None:
    if not isinstance(source, dict):
        return None
    payload = source.get("inlineData") or source.get("data")
    if not isinstance(payload, dict):
        return None
    features = payload.get("features")
    return features if isinstance(features, list) else None


def verify_flow_map_spec(
    spec: Dict[str, Any],
    *,
    rendered_svg: str = "",
    render_diagnostics: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    products: List[Dict[str, Any]] = []
    layers = [layer for layer in (spec.get("layers") or []) if isinstance(layer, dict)]
    sources = spec.get("sources") or {}

    def add(code: str, layer_id: str, detail: str) -> None:
        issues.append({"code": code, "severity": "error", "layer_id": layer_id, "detail": detail})

    for primary in layers:
        flow = primary.get("flow")
        if not isinstance(flow, dict):
            continue
        family = str(flow.get("family_id") or "")
        layer_id = str(primary.get("id") or "")
        mode = str(flow.get("direction_mode") or "")
        stats = flow.get("stats") or {}
        declared = int((stats.get("edges") or {}).get("displayed") or 0)
        source_id = str(primary.get("source") or "")
        features = _source_features(sources.get(source_id))
        if features is None:
            add("flow_source_unavailable", layer_id, f"source={source_id} is not hydrated")
            actual = None
        else:
            actual = len(features)
            if actual != declared:
                add(
                    "flow_edge_count_mismatch", layer_id,
                    f"declared={declared} actual={actual}",
                )
        direction_id = f"flow-{family}-direction"
        direction = next((layer for layer in layers if layer.get("id") == direction_id), None)
        direction_required = bool(flow.get("show_arrows")) and mode != "undirected-total" and declared > 0
        if direction_required and direction is None:
            add("flow_direction_missing", direction_id, f"{mode} requires a direction-mark layer")
        if mode == "undirected-total" and direction is not None:
            add("flow_direction_unexpected", direction_id, "undirected-total must not imply a direction")
        if direction is not None:
            layout = direction.get("layout") or {}
            if (
                direction.get("type") != "symbol"
                or direction.get("source") != source_id
                or layout.get("symbol-placement") != "line"
                or not layout.get("text-field")
                or layout.get("text-keep-upright") is not False
            ):
                add("flow_direction_invalid", direction_id, "direction layer does not share the edge path/tangent semantics")
        legend = primary.get("legend_spec") or {}
        encoding = flow.get("encoding") or {}
        # 图例契约：连续编码下图例 field 为编码域字段 weight_encoded（0–1），
        # 原始单位口径经 source_field/source_domain 披露；常量/退化域用 weight。
        # 校验必须同时盯住编码域与原始域——只认 field 名会把合法新契约误判
        # 成过期图例，放过 source_field/domain 漂移则是假一致。
        legend_field = legend.get("field")
        legend_source_ok = (
            legend.get("source_field") == encoding.get("source_field")
            and legend.get("source_domain") == encoding.get("domain")
        )
        if (
            legend_field not in ("weight", "weight_encoded")
            or (legend_field == "weight_encoded" and not legend_source_ok)
            or legend.get("direction_mode") != mode
            or legend.get("unit") != flow.get("weight_unit")
            or legend.get("transform") != encoding.get("transform")
        ):
            add("flow_legend_stale", layer_id, "legend field/mode/unit/transform differs from the flow product")
        if direction_required and rendered_svg and "▶" not in rendered_svg:
            add("flow_direction_render_failed", direction_id, "no direction mark reached the rendered SVG")
        products.append({
            "product_ref": flow.get("product_ref"),
            "family_id": family,
            "source_ref": flow.get("source_ref"),
            "source_revision": flow.get("source_revision"),
            "source_fingerprint": flow.get("source_fingerprint"),
            "direction_mode": mode,
            "filters": flow.get("filters") or {},
            "time_window": flow.get("time_window"),
            "encoding": encoding,
            "product_fingerprint": flow.get("product_fingerprint"),
            "declared_edges": declared,
            "actual_edges": actual,
        })
    for diagnostic in render_diagnostics or []:
        layer_id = str(diagnostic.get("layer_id") or "")
        if layer_id.endswith("-direction"):
            add("flow_direction_render_failed", layer_id, str(diagnostic.get("code") or "render failure"))
    unique_issues = list({(issue["code"], issue["layer_id"], issue["detail"]): issue for issue in issues}.values())
    return {
        "complete": not unique_issues,
        "issues": unique_issues,
        "products": products,
        "evaluated": bool(products),
    }


__all__ = ["verify_flow_map_spec"]
