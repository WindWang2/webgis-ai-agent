"""G34 作者计划 → **真实绘制计划**（WP11 的核心适配器）。

这是「模型 → IR → 绘制器」调用图的中间一段：

```
MapSpec.layout.print_layout
  → PageLayoutIR.print_layout（publication_ir.plan_publication_pages）
  → compile_publication_page(print_layout=...)
  → mapspec_to_svg.compile_mapspec_to_svg_detailed(print_layout=..., paper_mm=...)
  → build_draw_plan(...)  ← 本模块
  → _render_chrome_groups(draw_plan) / project() / render_legend_box_authored()
  → 真实 SVG / PDF / PNG 字节
```

本模块只产生**绘制参数**（user unit 几何 + 字体 + 文本行 + 图例排版 +
地图槽等比映射），不产生第二套渲染引擎。旧路径 ``print_layout=None`` 时
完全不调用本模块，导出字节不变。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.print_layout_schema import PRINT_RENDER_PATH
from app.lib.cartography.svg_marginalia import meters_per_pixel, nice_scale_distance
from app.services.print_layout_authoring.fonts import resolve_font, wrap_text
from app.services.print_layout_authoring.legend import layout_legend, read_legend_content
from app.services.print_layout_authoring.pages import page_plan
from app.services.print_layout_authoring.template import binding_context, resolve_binding
from app.services.print_layout_authoring.units import (
    mm_to_canvas_units, mm_to_pt, pt_to_canvas_units, pt_to_mm,
)

DRAW_PLAN_VERSION = "print_layout_draw.v1"
DEFAULT_TYPOGRAPHY: Dict[str, Any] = {
    "font_id": "noto-sans-sc-embedded", "size_pt": 9.0, "line_height": 1.35,
    "weight": "normal", "style": "normal", "align": "left",
    "letter_spacing_pt": 0.0, "color": "#1e293b",
    "wrap_mode": "word", "overflow_policy": "expand",
}


def _merc_y(lat: float) -> float:
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    return math.log(math.tan(math.pi / 4.0 + (lat * math.pi) / 360.0))


def _inv_merc_y(y: float) -> float:
    return (math.atan(math.exp(float(y))) - math.pi / 4.0) * 360.0 / math.pi


def build_map_projection(geo_bounds: Sequence[float], rect: Dict[str, float],
                         fit: str) -> Dict[str, Any]:
    """地图槽等比映射：单一 scale 同时作用于经度和墨卡托 Y（禁止非等比拉伸）。

    ``contain`` = 保全范围 + 留白（有效范围外扩）；``crop`` = 明确裁切
    （有效范围为子集，由 clipPath 裁掉槽外部分）。两者都返回可由 oracle
    复核的 ``effective_extent`` 与 ``units_per_radian``。
    """
    min_x, min_y, max_x, max_y = (float(v) for v in geo_bounds)
    rx = math.radians(max_x - min_x)
    rm = _merc_y(max_y) - _merc_y(min_y)
    rx = rx if math.isfinite(rx) and rx > 0 else 1e-6
    rm = rm if math.isfinite(rm) and rm > 0 else 1e-6
    w, h = float(rect["width"]), float(rect["height"])
    sx, sy = w / rx, h / rm
    # contain = 整幅可见（取 min，留白）；crop = 填满槽（取 max，明确裁切）。
    # 两者都用**同一个** s 作用于经度与墨卡托 Y → 无非等比拉伸。
    s = min(sx, sy) if fit == "contain" else max(sx, sy)
    left_pad = (w - rx * s) / 2.0
    top_pad = (h - rm * s) / 2.0
    x0 = float(rect["x"]) + left_pad
    y0 = float(rect["y"]) + top_pad
    merc_max = _merc_y(max_y)

    def px(lon: float) -> float:
        return x0 + (math.radians(float(lon)) - math.radians(min_x)) * s

    def py(lat: float) -> float:
        return y0 + (merc_max - _merc_y(float(lat))) * s

    eff_w = math.degrees(math.radians(min_x) - left_pad / s)
    eff_e = math.degrees(math.radians(min_x) + (w - left_pad) / s)
    merc_s = merc_max - (h - top_pad) / s
    merc_n = merc_max + top_pad / s
    effective_extent = [
        round(eff_w, 8), round(_inv_merc_y(merc_s), 8),
        round(eff_e, 8), round(_inv_merc_y(merc_n), 8),
    ]
    return {
        "fit": fit,
        "units_per_radian": s,
        "sx": sx, "sy": sy,
        "uniform_scale": True,
        "non_uniform_stretch": False,
        "left_padding": left_pad, "top_padding": top_pad,
        "map_rect": {k: float(rect[k]) for k in ("x", "y", "width", "height")},
        "effective_extent": effective_extent,
        "source_extent": [min_x, min_y, max_x, max_y],
        "x": px, "y": py,
        "mpp": meters_per_pixel(effective_extent, w),
    }


def _component_typography(c: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {**DEFAULT_TYPOGRAPHY, **(c.get("typography") or {})}


def _text_of(c: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    spec = c.get("text")
    if not isinstance(spec, dict):
        return {"value": "", "resolved": True, "missing": False, "code": ""}
    if str(spec.get("mode") or "literal") == "literal":
        return {"value": str(spec.get("value") or ""), "resolved": True,
                "missing": False, "code": ""}
    return resolve_binding(
        str(spec.get("binding") or "page_title"), ctx,
        fmt=str(spec.get("format") or "plain"),
        placeholder=str(spec.get("missing_placeholder") or "〔数据缺失〕"),
    )


def build_draw_plan(
    mapspec: Dict[str, Any],
    plan: Dict[str, Any],
    *,
    target_dpi: Any,
    geo_bounds: Sequence[float],
    page_id: str = "",
    paper_mm: Optional[Tuple[float, float]] = None,
    atlas_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """作者计划 → 绘制计划（所有几何单位 = SVG viewBox user unit）。"""
    from app.services.print_layout_authoring.units import clamp_dpi

    dpi = clamp_dpi(target_dpi)
    upm = mm_to_canvas_units(1.0, dpi)
    paper = plan.get("paper") or {}
    paper_w = float((paper_mm[0] if paper_mm else paper.get("width_mm")) or 297.0)
    paper_h = float((paper_mm[1] if paper_mm else paper.get("height_mm")) or 210.0)

    # 页级覆写（确定性优先级；复位只清本领域）
    effective_plan = plan
    page_info: Dict[str, Any] = {}
    if page_id and (plan.get("atlas") or {}).get("pages"):
        pp = page_plan(plan, page_id)
        if pp.get("found"):
            page_info = pp
            effective_plan = {**plan, "components": pp["components"]}
    if atlas_page:
        page_info = {**page_info, **{k: v for k, v in atlas_page.items()
                                     if k in ("title", "page_number", "page_count", "binding_digest")}}

    legend_content = read_legend_content(mapspec)
    ctx = binding_context(
        mapspec, {"title": page_info.get("title", "")},
        page_number=int(page_info.get("page_number") or 1),
        page_count=int(page_info.get("page_count") or 1),
        unit=str(legend_content.get("unit") or ""),
    )

    components: List[Dict[str, Any]] = []
    map_rect: Optional[Dict[str, float]] = None
    map_fit = "contain"
    diagnostics: List[Dict[str, str]] = []
    geometry_mm: Dict[str, Any] = {}

    for c in sorted(effective_plan.get("components") or [],
                    key=lambda x: (int(x.get("z") or 0), str(x.get("id") or ""))):
        cid = str(c.get("id") or "")
        kind = str(c.get("kind") or "")
        f = c.get("frame") or {}
        box = {
            "x": float(f.get("x") or 0.0) * upm,
            "y": float(f.get("y") or 0.0) * upm,
            "width": float(f.get("width") or 1.0) * upm,
            "height": float(f.get("height") or 1.0) * upm,
        }
        geometry_mm[cid] = {k: float(f.get(k) or 0.0)
                            for k in ("x", "y", "width", "height")}
        typo = _component_typography(c, effective_plan)
        font = resolve_font(str(typo["font_id"]), str(typo["weight"]),
                            str(typo["style"]))
        size_units = pt_to_canvas_units(float(typo["size_pt"]), dpi)
        line_units = size_units * float(typo["line_height"])
        draw: Dict[str, Any] = {
            "id": cid, "kind": kind, "box": box, "frame_mm": geometry_mm[cid],
            "z": int(c.get("z") or 0), "locked": bool(c.get("locked")),
            "visible": bool(c.get("visible", True)),
            "font_family": font.get("css_stack") or font["family"],
            "font_weight": font["weight_applied"],
            "font_style": font["style_applied"],
            "font_size_units": size_units,
            "line_units": line_units,
            "letter_spacing_units": pt_to_canvas_units(
                float(typo["letter_spacing_pt"]), dpi),
            "color": str(typo["color"]),
            "align": str(typo["align"]),
            "font_status": font,
            "text": None, "legend": None,
        }
        tv = _text_of(c, ctx)
        # 受控动态文本：页码 / 目录来自最终启用页序（不用旧数组下标）。
        if kind == "page_number" and not tv.get("value"):
            tv = {
                "value": f"{int(page_info.get('page_number') or 1)} / "
                         f"{int(page_info.get('page_count') or 1)}",
                "resolved": True, "missing": False, "code": "",
            }
        if kind == "toc" and not tv.get("value"):
            toc_rows = list(page_info.get("toc") or [])
            tv = {
                "value": "\n".join(
                    f"{r['page_number']}  {r['title']}" for r in toc_rows) or "〔无启用页〕",
                "resolved": True, "missing": False, "code": "",
            }
        if tv.get("value"):
            tl = wrap_text(
                tv["value"],
                font_id=font["applied_font_id"] or "noto-sans-sc-embedded",
                size_pt=float(typo["size_pt"]),
                line_height=float(typo["line_height"]),
                letter_spacing_pt=float(typo["letter_spacing_pt"]),
                weight=font["weight_applied"], style=font["style_applied"],
                box_width_mm=float(f.get("width") or 1.0),
                box_height_mm=float(f.get("height") or 1.0),
                wrap_mode=str(typo["wrap_mode"]),
                overflow_policy=str(typo["overflow_policy"]),
            )
            draw["text"] = {
                "value": tv["value"], "lines": tl["lines"],
                "size_pt": float(typo["size_pt"]),
                "line_count": len(tl["lines"]),
                "overflow": tl["overflow"], "status": tl["status"],
                "missing": bool(tv.get("missing")),
                "missing_glyphs": tl["font"]["missing_glyphs"],
                "used_height_mm": round(pt_to_mm(tl["used_height_pt"]), 4),
                "expanded_height_mm": tl["expanded_height_mm"],
                "continuation_lines": tl["continuation_lines"],
            }
            if tl["status"] == "blocked":
                diagnostics.append({"code": "TEXT_OVERFLOW_BLOCKED", "component_id": cid})
        if kind == "legend":
            lay = {**(effective_plan.get("legend_defaults") or {}), **(c.get("legend") or {})}
            content = read_legend_content(mapspec, cid,
                                          group_by_layer=bool(lay.get("group_by_layer")))
            lp = layout_legend(content, lay, f, typo, dpi)
            draw["legend"] = lp
            if not lp.get("complete"):
                diagnostics.append({"code": lp.get("code") or "PRINT_LEGEND_INCOMPLETE",
                                    "component_id": cid})
        if kind == "map_slot":
            map_rect = box
            map_fit = str((c.get("map_slot") or {}).get("fit") or "contain")
            draw["map_slot"] = c.get("map_slot") or {}
        components.append(draw)

    # 无地图槽时仍以整页安全区为地图矩形（保持旧地理语义，不拉伸）
    if map_rect is None:
        sa = effective_plan.get("safe_area") or {}
        map_rect = {
            "x": float(sa.get("left") or 0.0) * upm,
            "y": float(sa.get("top") or 0.0) * upm,
            "width": (paper_w - float(sa.get("left") or 0.0)
                      - float(sa.get("right") or 0.0)) * upm,
            "height": (paper_h - float(sa.get("top") or 0.0)
                       - float(sa.get("bottom") or 0.0)) * upm,
        }
    projection = build_map_projection(geo_bounds, map_rect, map_fit)
    mpp = projection["mpp"]
    nice = nice_scale_distance(mpp * mm_to_canvas_units(30.0, dpi)) if mpp else (0, "")
    scale_receipt = {
        "meters_per_unit": round(mpp or 0.0, 8),
        "nice_distance_m": float(nice[0]) if nice else 0.0,
        "nice_label": str(nice[1]) if len(nice) > 1 else "",
        "target_bar_mm": 30.0,
        "source_extent": projection["source_extent"],
        "effective_extent": projection["effective_extent"],
        "fit": map_fit,
        "uniform_scale": True,
    }
    return {
        "version": DRAW_PLAN_VERSION,
        "render_path": PRINT_RENDER_PATH,
        "page_id": page_id,
        "dpi": dpi,
        "paper_mm": {"width": paper_w, "height": paper_h},
        "units_per_mm": upm,
        "components": components,
        "map_rect": map_rect,
        "map_fit": map_fit,
        "toc": list(page_info.get("toc") or []),
        "page_number": int(page_info.get("page_number") or 1),
        "page_count": int(page_info.get("page_count") or 1),
        "projection": {k: v for k, v in projection.items()
                       if k not in ("x", "y")},
        "projection_fn": projection,  # 仅编译期使用，不进序列化回执
        "scale": scale_receipt,
        "diagnostics": diagnostics,
        "geometry_receipt": {
            "paper_mm": {"width": paper_w, "height": paper_h},
            "component_boxes_mm": geometry_mm,
            "map_rect_mm": {k: round(v / upm, 6) for k, v in map_rect.items()},
            "dpi": dpi,
            "units_per_mm": upm,
        },
    }


def serializable_receipt(draw_plan: Dict[str, Any]) -> Dict[str, Any]:
    """UI / Pi 消费的有界回执（不含整页 SVG、字体 bytes、栅格数组）。"""
    return {
        "version": draw_plan.get("version"),
        "page_id": draw_plan.get("page_id"),
        "dpi": draw_plan.get("dpi"),
        "paper_mm": draw_plan.get("paper_mm"),
        "component_boxes_mm": draw_plan.get("geometry_receipt", {}).get("component_boxes_mm", {}),
        "map_rect_mm": draw_plan.get("geometry_receipt", {}).get("map_rect_mm", {}),
        "effective_extent": draw_plan.get("scale", {}).get("effective_extent"),
        "scale": draw_plan.get("scale"),
        "text": [
            {"component_id": c["id"], "lines": (c.get("text") or {}).get("lines", []),
             "overflow": (c.get("text") or {}).get("overflow", False),
             "status": (c.get("text") or {}).get("status"),
             "font": {"requested_font_id": c["font_status"]["requested_font_id"],
                      "applied_font_id": c["font_status"]["applied_font_id"],
                      "issues": c["font_status"]["issues"],
                      "missing_glyphs": (c.get("text") or {}).get("missing_glyphs", [])}}
            for c in draw_plan.get("components", []) if c.get("text")
        ],
        "legend": [
            {"component_id": c["id"], "status": (c.get("legend") or {}).get("status"),
             "entries_total": (c.get("legend") or {}).get("entries_total"),
             "entries_rendered": (c.get("legend") or {}).get("entries_rendered"),
             "complete": (c.get("legend") or {}).get("complete"),
             "columns": (c.get("legend") or {}).get("columns"),
             "has_nodata": (c.get("legend") or {}).get("has_nodata"),
             "has_out_of_range": (c.get("legend") or {}).get("has_out_of_range")}
            for c in draw_plan.get("components", []) if c.get("legend")
        ],
        "diagnostics": draw_plan.get("diagnostics", []),
    }
