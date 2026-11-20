from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable

from .contracts import FlowProductConfig
from .mutation import flow_layer_ids


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def encode_weight(weight: float, transform: str, domain: Iterable[float]) -> float:
    import math

    d0, d1 = (float(v) for v in domain)
    if not math.isfinite(weight) or not math.isfinite(d0) or not math.isfinite(d1) or d1 <= d0:
        return 0.0
    if transform == "sqrt":
        value = (math.sqrt(max(0.0, weight)) - math.sqrt(max(0.0, d0))) / (math.sqrt(d1) - math.sqrt(d0))
    elif transform == "log1p":
        value = (math.log1p(max(0.0, weight)) - math.log1p(max(0.0, d0))) / (math.log1p(d1) - math.log1p(d0))
    else:
        value = (weight - d0) / (d1 - d0)
    return round(min(1.0, max(0.0, value)), 6)


def _paint(domain: list[float], config: FlowProductConfig, palette: list[str]) -> Dict[str, Any]:
    d0, d1 = domain
    if d1 <= d0:
        return {
            "width": config.width_min_px,
            "color": {
                "method": "match", "field": "weight",
                "cases": [[d0, palette[0]]], "default": palette[0],
            },
            "opacity": 0.85,
        }
    return {
        "width": {
            "method": "interpolate", "field": "weight_encoded",
            "stops": [[0.0, config.width_min_px], [1.0, config.width_max_px]],
        },
        "color": {
            "method": "interpolate", "field": "weight_encoded",
            "stops": [[0.0, palette[0]], [1.0, palette[-1]]],
        },
        "opacity": 0.85,
    }


def build_map_fragment(product: Dict[str, Any], config: FlowProductConfig) -> Dict[str, Any]:
    family_id = str(product["family_id"])
    edge_ref = str(product["edge_ref"])
    node_ref = str(product["node_ref"])
    edge_source = f"flow-{family_id}-edges-source"
    node_source = f"flow-{family_id}-nodes-source"
    legend = product["legend"]
    domain = [float(v) for v in legend["domain"]]
    palette = list(legend.get("palette") or ["#0d0887", "#f0f921"])
    stats = product["stats"]
    mode = config.direction_mode
    direction_label = {
        "directed": "分向流量",
        "undirected-total": "无向合计 gross",
        "net": "净流幅度 |net|",
    }[mode]
    retained = stats["gross"].get("retained")
    coverage = "未知" if retained is None else f"{retained * 100:.1f}%"
    constant = domain[1] <= domain[0]
    legend_spec = {
        "type": "categorical" if constant else "continuous",
        "field": "weight" if constant else "weight_encoded",
        "source_field": config.weight_field,
        "source_domain": domain,
        "min": domain[0] if constant else 0.0,
        "max": domain[1] if constant else 1.0,
        "palette_colors": [palette[0]] if constant else [palette[0], palette[-1]],
        **({"categories": [{"key": domain[0], "color": palette[0], "label": str(domain[0])}]} if constant else {}),
        "title": f"{direction_label}（{config.weight_unit or 'count'}；保留 gross {coverage}）",
        "direction_mode": mode,
        "unit": config.weight_unit or "count",
        "transform": config.weight_transform,
        "fixed_domain": legend["fixed_domain"],
        "time_window": (
            {
                "field": config.time_field,
                "start": config.time_start,
                "end": config.time_end,
                "end_inclusive": False,
                "timezone": config.timezone,
            }
            if config.time_field else None
        ),
        "note": "直线/装饰曲线仅表达 OD 联系，不代表真实路线或旅行距离",
    }
    flow = {
        "schema_version": "od_flow.v1",
        "product_ref": product["product_ref"],
        "family_id": family_id,
        "source_ref": product["source_ref"],
        "source_revision": int(product["source_revision"]),
        "source_fingerprint": product["source_fingerprint"],
        "product_fingerprint": product["fingerprint"],
        "direction_mode": mode,
        "geometry": config.geometry,
        "show_arrows": bool(config.show_arrows and mode != "undirected-total"),
        "weight_field": "weight",
        "source_weight_field": config.weight_field,
        "weight_unit": config.weight_unit or "count",
        "supported_modes": list(product["supported_modes"]),
        "node_totals_scope": config.node_totals_scope,
        "stats": stats,
        "encoding": {
            **legend,
            "width_min_px": config.width_min_px,
            "width_max_px": config.width_max_px,
            "arrow_spacing_px": config.arrow_spacing_px,
            "arrow_size_px": config.arrow_size_px,
        },
        "time_window": legend_spec["time_window"],
        "filters": {
            "category_field": config.category_field,
            "category_values": config.category_values,
            "dimension_fields": config.dimension_fields,
            "min_weight": config.min_weight,
            "top_n": config.top_n,
        },
    }
    edge_layer = {
        "id": f"flow-{family_id}-edges",
        "type": "line",
        "source": edge_source,
        "paint": _paint(domain, config, palette),
        "legend_spec": legend_spec,
        "flow": flow,
        "data_binding": {"field": "weight", "field_type": "number"},
        "metadata": {"flow_family_id": family_id, "flow_part": "edges"},
        "provenance": {"result_ref": edge_ref, "product_ref": product["product_ref"]},
    }
    layers = [edge_layer]
    if flow["show_arrows"]:
        layers.append({
            "id": f"flow-{family_id}-direction",
            "type": "symbol",
            "source": edge_source,
            "layout": {
                "symbol-placement": "line",
                "symbol-spacing": config.arrow_spacing_px,
                "text-field": "▶",
                "text-size": config.arrow_size_px,
                "text-rotation-alignment": "map",
                "text-keep-upright": False,
                "text-allow-overlap": True,
                "text-ignore-placement": True,
            },
            "paint": {
                "text-color": palette[-1],
                "text-halo-color": "#ffffff",
                "text-halo-width": 1.0,
            },
            "metadata": {"flow_family_id": family_id, "flow_part": "direction"},
        })
    sources = {edge_source: {"type": "geojson", "ref_id": edge_ref}}
    if config.show_nodes and node_ref:
        max_radius = max((n["radius_value"] for n in product.get("query_index", {}).get("nodes", {}).values()), default=1.0)
        layers.append({
            "id": f"flow-{family_id}-nodes",
            "type": "circle",
            "source": node_source,
            "paint": {
                "circle-radius": {
                    "method": "interpolate", "field": "radius_value",
                    "stops": [[0.0, 3.0], [max(1.0, max_radius), 16.0]],
                },
                "circle-color": "#2563eb",
                "circle-opacity": 0.78,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1.0,
            },
            "label": {"field": "name", "size": 11},
            "metadata": {"flow_family_id": family_id, "flow_part": "nodes"},
        })
        sources[node_source] = {"type": "geojson", "ref_id": node_ref}
    expected = set(flow_layer_ids(family_id))
    if {layer["id"] for layer in layers} - expected:
        raise ValueError("unexpected flow layer")
    return {"sources": sources, "layers": layers}


def build_query_index(edge_fc: Dict[str, Any], node_fc: Dict[str, Any]) -> Dict[str, Any]:
    edges: Dict[str, Any] = {}
    nodes: Dict[str, Any] = {}
    by_origin: Dict[str, list[str]] = {}
    by_destination: Dict[str, list[str]] = {}
    for feature in edge_fc.get("features", []):
        props = feature.get("properties") or {}
        edge_id = str(feature.get("id") or props.get("id"))
        if not edge_id:
            continue
        edges[edge_id] = props
        for key, field in (("origin_node_id", by_origin), ("destination_node_id", by_destination)):
            node_id = str(props.get(key) or "")
            if node_id:
                field.setdefault(node_id, []).append(edge_id)
    for feature in node_fc.get("features", []):
        props = feature.get("properties") or {}
        node_id = str(feature.get("id") or props.get("node_id"))
        if node_id:
            nodes[node_id] = props
    for index in (by_origin, by_destination):
        for edge_ids in index.values():
            edge_ids.sort()
    return {
        "edges": edges,
        "nodes": nodes,
        "by_origin": by_origin,
        "by_destination": by_destination,
    }


def restyle_product(
    product: Dict[str, Any],
    edge_fc: Dict[str, Any],
    node_fc: Dict[str, Any],
    config: FlowProductConfig,
) -> tuple[Dict[str, Any], Dict[str, Any], bool]:
    updated = copy.deepcopy(product)
    edges = copy.deepcopy(edge_fc)
    domain = list(config.weight_domain or product["weight_domain"])
    encoding_changed = (
        domain != list(product["weight_domain"])
        or config.weight_transform != product["weight_transform"]
        or config.normalize != bool(product["config"].get("normalize", True))
    )
    if encoding_changed:
        for feature in edges.get("features", []):
            props = feature.get("properties") or {}
            weight = float(props.get("weight") or 0.0)
            props["weight_encoded"] = encode_weight(weight, config.weight_transform, domain)
            if config.normalize:
                props["weight_norm"] = encode_weight(weight, "linear", domain)
            else:
                props.pop("weight_norm", None)
    updated["weight_transform"] = config.weight_transform
    updated["weight_domain"] = domain
    updated["weight_unit"] = config.weight_unit
    updated["legend"].update({
        "field": "weight",
        "source_field": config.weight_field,
        "unit": config.weight_unit,
        "transform": config.weight_transform,
        "domain": domain,
        "fixed_domain": config.weight_domain is not None,
        "palette": config.palette or product["legend"]["palette"],
    })
    updated["config"] = config.model_dump(mode="json")
    updated["geometry"]["arrows"] = {
        "show": config.show_arrows,
        "placement": "line",
        "spacing_px": config.arrow_spacing_px,
        "size_px": config.arrow_size_px,
    }
    updated["query_index"] = build_query_index(edges, node_fc)
    updated["fingerprint"] = fingerprint({
        "analysis": product["analysis_fingerprint"],
        "domain": domain,
        "config": updated["config"],
        "edge_encoding_changed": encoding_changed,
    })
    return updated, edges, encoding_changed
