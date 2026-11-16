"""G47 → G34 纸面 adapter（WP10 的窄接缝）。

把**已冻结**的布局 artifact 转成一个受控 ``diagram`` 纸面组件（打印版式
``print_layout_schema.PrintDiagramSpec``）：

* 真实 ``circles``（示意坐标 + 半径 + 数量 + 稳定 id）—— 渲染真源；
* ``reference_paths``：原地理轮廓的 SVG path（仅地理定位参照，有界）；
* ``layout_ref``/``layout_fingerprint``：可追溯的成果身份；
* 图例元数据与披露文本（单位、零/缺失、示意坐标声明、邻接变化）。

不复制 G34 的排版系统、不改 ``map_slot`` 语义：示意图不是地图槽，永远不带
CRS/地理范围字段。
"""
from __future__ import annotations

from typing import Any, Dict, List

MAX_COMPONENT_CIRCLES = 400
MAX_REFERENCE_PATHS = 600
MAX_DISCLOSURES = 8


def _ring_path(ring: List[List[float]]) -> str:
    pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 3:
        return ""
    head = f"M {float(pts[0][0]):.2f} {float(pts[0][1]):.2f}"
    body = " L ".join(f"{float(p[0]):.2f} {float(p[1]):.2f}" for p in pts[1:])
    return f"{head} L {body} Z"


def diagram_spec_from_layout(layout: Dict[str, Any]) -> Dict[str, Any]:
    """布局 artifact → PrintDiagramSpec 载荷（有界；超出如实 truncated）。"""
    circles_in = list(layout.get("circles") or [])
    truncated = len(circles_in) > MAX_COMPONENT_CIRCLES
    circles = [{
        "feature_id": str(c.get("feature_id")),
        "label": str(c.get("label") or "")[:80],
        "x": float(c.get("x") or 0.0),
        "y": float(c.get("y") or 0.0),
        "radius": float(c.get("radius") or 0.0),
        "value": float(c.get("value") or 0.0),
    } for c in circles_in[:MAX_COMPONENT_CIRCLES]]

    paths: List[str] = []
    for outline in (layout.get("reference") or {}).get("outlines") or []:
        for ring in outline.get("rings") or []:
            if len(paths) >= MAX_REFERENCE_PATHS:
                truncated = True
                break
            path = _ring_path(ring)
            if path:
                paths.append(path)
        if len(paths) >= MAX_REFERENCE_PATHS:
            break

    legend = layout.get("legend") or {}
    refs = [{"value": float(r.get("value") or 0.0),
             "radius": float(r.get("radius") or 0.0)}
            for r in legend.get("reference_circles") or []][:3]
    return {
        "layout_ref": None,
        "layout_fingerprint": (layout.get("metrics") or {}).get("layout_fingerprint"),
        "extent": float((layout.get("extent") or {}).get("width") or 1000.0),
        "fit": "contain",
        "circles": circles,
        "reference_paths": paths,
        "legend_title": str(legend.get("title") or "")[:120],
        "legend_unit": str(legend.get("unit") or "")[:24],
        "reference_circles": refs,
        "disclosures": [str(d)[:300] for d in
                        (layout.get("disclosures") or [])[:MAX_DISCLOSURES]],
        "truncated": truncated,
    }


def diagram_component(
    layout: Dict[str, Any], *, component_id: str = "diagram-1",
    frame: Dict[str, float] | None = None, layout_ref: str | None = None,
) -> Dict[str, Any]:
    """构造可直接放进打印版式计划的 diagram 组件（受控、加性）。"""
    spec = diagram_spec_from_layout(layout)
    spec["layout_ref"] = layout_ref
    extent = spec["extent"]
    return {
        "id": component_id,
        "kind": "diagram",
        "frame": frame or {"x": 20.0, "y": 20.0,
                           "width": min(170.0, extent), "height": min(170.0, extent)},
        "z": 1,
        "diagram": spec,
    }
