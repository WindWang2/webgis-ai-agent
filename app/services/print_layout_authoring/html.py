"""G34 多画幅产品页的纸面整饰 overlay（G15/G25 的薄适配）。

G15 product 页的 PDF 面走 HTML 组合（WeasyPrint 内嵌 SVG 渲染器不支持
g transform / 多嵌套 svg），SVG 面是自包含单文件。本模块把**同一份作者
计划**同时投影到这两个消费面，复用 G15 既有 frame 身份/共享分级/槽位
runtime，绝不创建第二套 live runtime 或第二套 MapSpec。

``dpi=72`` 是刻意的：product 页外层坐标是 ``mm*PX_PER_MM``（逻辑 px，
无 DPI 缩放），而 draw plan 在 dpi=72 时 ``mm_to_canvas_units`` 恰为
``mm*4``，与 G15 外层 viewBox 同空间。
"""
from __future__ import annotations

import base64
import html as _html
from typing import Any, Dict, List, Tuple

from app.services.print_layout_authoring.compile import build_draw_plan


def _esc(v: Any) -> str:
    return _html.escape(str(v), quote=True)


def _diagram_img(c: Dict[str, Any]) -> str:
    """G47 diagram → 自包含 SVG data URI（纸面 PDF/PNG 路径）。

    只画真实矢量圆与参考轮廓；等比 contain 由编译期 scale 保证，
    这里固定 viewBox = 组件框，``width:100%;height:100%`` 不改变坐标比例。
    """
    d = c.get("diagram") or {}
    box = c.get("box") or {}
    x, y = float(box.get("x") or 0.0), float(box.get("y") or 0.0)
    w, h = float(box.get("width") or 1.0), float(box.get("height") or 1.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:.2f} {y:.2f} {w:.2f} {h:.2f}">',
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="#ffffff"/>',
        '<g fill="none" stroke="#94a3b8" stroke-width="0.8" opacity="0.7">',
    ]
    for d_path in d.get("reference_paths") or []:
        parts.append(f'<path d="{_esc(d_path)}"/>')
    parts.append("</g><g>")
    for circle in d.get("circles") or []:
        parts.append(
            f'<circle data-feature-id="{_esc(circle.get("feature_id"))}" '
            f'cx="{float(circle.get("x") or 0.0):.3f}" '
            f'cy="{float(circle.get("y") or 0.0):.3f}" '
            f'r="{float(circle.get("radius") or 0.0):.3f}" '
            f'fill="#c2410c" fill-opacity="0.78" stroke="#7c2d12" stroke-width="0.6"/>')
    parts.append("</g></svg>")
    svg = "".join(parts)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (f'<img data-g47-diagram="{_esc(c.get("id"))}" '
            f'alt="{_esc((d.get("legend") or {}).get("title") or "diagram")}" '
            f'src="data:image/svg+xml;base64,{b64}" '
            f'style="width:100%;height:100%;display:block"/>')


def authored_product_overlay(
    doc: Dict[str, Any],
    plan: Dict[str, Any],
    *,
    page_id: str,
    page_title: str,
    page_number: int,
    page_count: int,
    paper_mm: Tuple[float, float],
    geo_bounds: List[float],
) -> Dict[str, str]:
    """→ ``{"svg": <片段>, "html": <片段>}``；无计划或失败时返回空片段。"""
    if not isinstance(plan, dict) or not plan:
        return {"svg": "", "html": ""}
    try:
        draw = build_draw_plan(
            doc, plan, target_dpi=72, geo_bounds=geo_bounds,
            page_id=page_id, paper_mm=paper_mm,
            atlas_page={"title": page_title, "page_number": page_number,
                        "page_count": page_count},
        )
    except Exception:
        # 多画幅页不因整饰失败而丢掉既有地理画幅；由调用方发诊断披露。
        return {"svg": "", "html": "", "error": "print_layout_overlay_failed"}

    from app.services.mapspec_to_svg import _render_authored_chrome

    w_units = float(paper_mm[0]) * 4.0
    h_units = float(paper_mm[1]) * 4.0

    def _dummy_project(coord: Any) -> Tuple[str, str]:
        return ("0", "0")

    svg_fragment, _coverage = _render_authored_chrome(
        doc, draw, geo_bounds, w_units, h_units, _dummy_project)

    html_parts: List[str] = []
    for c in draw.get("components") or []:
        if not c.get("visible", True):
            continue
        f = c.get("frame_mm") or {}
        x, y = float(f.get("x") or 0.0), float(f.get("y") or 0.0)
        w, h = float(f.get("width") or 0.0), float(f.get("height") or 0.0)
        size_pt = float((c.get("text") or {}).get("size_pt") or 9.0)
        base = (
            f'position:absolute;left:{x:.2f}mm;top:{y:.2f}mm;'
            f'width:{w:.2f}mm;min-height:{h:.2f}mm;'
            f'font-family:{_esc(c.get("font_family") or "sans-serif")};'
            f'font-size:{size_pt:.2f}pt;color:{_esc(c.get("color") or "#1e293b")};'
            f'overflow:visible;white-space:pre-wrap;'
        )
        txt = c.get("text")
        if txt and txt.get("lines"):
            align = "center" if c.get("align") == "center" else (
                "right" if c.get("align") == "right" else "left")
            html_parts.append(
                f'<div data-g34-id="{_esc(c.get("id"))}" style="{base}text-align:{align}" '
                f'data-g34-lines="{len(txt["lines"])}">'
                + "".join(f'<div>{_esc(line)}</div>' for line in txt["lines"])
                + "</div>")
        lp = c.get("legend")
        if lp and lp.get("status") != "blocked":
            entries = list(lp.get("entries") or [])
            color = _esc(c.get("color") or "#1e293b")
            cells = "".join(
                f'<div style="display:inline-block;width:{100.0 / max(1, int(lp.get("columns") or 1)):.1f}%;'
                f'vertical-align:top;font-size:{size_pt * 0.9:.2f}pt">'
                f'<span style="display:inline-block;width:3mm;height:2mm;'
                f'background:{_esc(str(e.get("color") or "#888"))}"></span> '
                f'{_esc(str(e.get("label") or ""))}</div>'
                for e in entries)
            html_parts.append(
                f'<div data-g34-id="{_esc(c.get("id"))}" '
                f'data-g34-entries-total="{lp.get("entries_total")}" '
                f'data-g34-entries-rendered="{lp.get("entries_rendered")}" '
                f'style="{base}font-weight:normal">'
                f'<div style="font-weight:700">{_esc(lp.get("title") or "图例")}</div>'
                + (f'<div>单位：{_esc(lp.get("unit"))}</div>' if lp.get("unit") else "")
                + cells
                + "".join(f'<div style="font-size:{size_pt * 0.75:.2f}pt;color:#94a3b8">'
                          f'{_esc(n)}</div>' for n in (lp.get("notes") or []))
                + "</div>")
        if c.get("kind") == "scale_bar":
            label = str((draw.get("scale") or {}).get("nice_label") or "")
            html_parts.append(
                f'<div data-g34-id="{_esc(c.get("id"))}" style="{base}">'
                f'比例尺：{_esc(label)}</div>')
        if c.get("kind") == "diagram":
            html_parts.append(
                f'<div data-g47-diagram-frame="{_esc(c.get("id"))}" '
                f'style="position:absolute;left:{x:.2f}mm;top:{y:.2f}mm;'
                f'width:{w:.2f}mm;height:{h:.2f}mm;">{_diagram_img(c)}</div>')
    return {"svg": svg_fragment, "html": "".join(html_parts)}
