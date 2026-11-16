"""G47 出版渲染（WP10）：同一冻结布局 → 自包含 SVG → PNG / PDF。

原则：

* **SVG 是唯一矢量真源**：真实 ``<circle>`` + ``data-feature-id``/``data-value``/
  ``data-radius``，几何可被独立回读核验（不是截图、不是位图贴图）。
* **PNG/PDF 从同一份 SVG 生成**：rsvg-convert 按 DPI 栅格化、WeasyPrint 按 mm
  排版；圆面积比例只由 SVG 的半径决定，DPI 只改像素尺寸，不做非等比拉伸。
* **图例/单位/披露/原图参照**都在 SVG 内：数量参考圆（最大值/1/4/1/16，半径
  逐级减半）、单位、源版本、零/缺失说明、示意坐标声明、邻接变化，以及原地理
  轮廓（浅灰，作为地理定位参照）。
* 未达发布资格（``publication_ready=False``）时打印显著草稿横幅，不伪装成品。
"""
from __future__ import annotations

import base64
import html
import math
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from app.services.cartogram_authoring.errors import AuthoringError

FILL = "#c2410c"
FILL_OPACITY = 0.78
STROKE = "#7c2d12"
REFERENCE_STROKE = "#94a3b8"
INK = "#0f172a"
MUTED = "#475569"
PAPER = "#ffffff"
FONT_STACK = ("'Noto Sans CJK SC','Noto Sans SC','Source Han Sans SC',"
              "'DejaVu Sans',sans-serif")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(v):
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.{digits}g}"


def render_svg(layout: Dict[str, Any], *, size_px: Optional[int] = None) -> str:
    """把布局 artifact 渲染成自包含 SVG。"""
    extent = float((layout.get("extent") or {}).get("width") or 1000.0)
    circles = list(layout.get("circles") or [])
    legend = layout.get("legend") or {}
    quantity = layout.get("quantity") or {}
    source = layout.get("source") or {}
    metrics = layout.get("metrics") or {}
    status = layout.get("status")
    ready = bool(layout.get("publication_ready"))
    unit = str(legend.get("unit") or quantity.get("unit") or "")
    title = str(legend.get("title") or quantity.get("field") or "Dorling cartogram")

    width = f' width="{int(size_px)}" height="{int(size_px)}"' if size_px else ""

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {extent:.2f} {extent:.2f}"'
        f'{width} role="img" aria-label="{_esc(title)}（Dorling 圆面积 cartogram）">')
    parts.append(f'<rect x="0" y="0" width="{extent:.2f}" height="{extent:.2f}" fill="{PAPER}"/>')

    # ── 原地理轮廓（参照；不是布局的一部分）──
    parts.append('<g id="original-reference" fill="none" '
                 f'stroke="{REFERENCE_STROKE}" stroke-width="1.2" opacity="0.75">')
    for item in (layout.get("reference") or {}).get("outlines") or []:
        fid = _esc(item.get("feature_id"))
        for ring in item.get("rings") or []:
            if len(ring) < 3:
                continue
            d = "M " + " L ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in ring) + " Z"
            parts.append(f'<path data-reference-id="{fid}" d="{d}"/>')
    parts.append("</g>")

    # ── 圆（真实矢量图元 + 可核身份）──
    parts.append('<g id="dorling-circles">')
    for c in circles:
        fid = _esc(c.get("feature_id"))
        val = c.get("value")
        label = _esc(c.get("label") or c.get("feature_id"))
        parts.append(
            f'<circle data-feature-id="{fid}" data-value="{_esc(val)}" '
            f'data-radius="{float(c.get("radius") or 0.0):.4f}" '
            f'cx="{float(c.get("x") or 0.0):.2f}" cy="{float(c.get("y") or 0.0):.2f}" '
            f'r="{float(c.get("radius") or 0.0):.2f}" fill="{FILL}" '
            f'fill-opacity="{FILL_OPACITY}" stroke="{STROKE}" stroke-width="1.1">'
            f'<title>{label}: {_fmt(val)} {_esc(unit)}</title></circle>')
    parts.append("</g>")

    # ── 标签（只标最大的若干圆，避免拥挤；不改变面积）──
    max_r = max((float(c.get("radius") or 0.0) for c in circles), default=0.0)
    parts.append(f'<g id="labels" font-family="{FONT_STACK}" font-size="11" '
                 f'fill="{INK}" text-anchor="middle">')
    for c in circles:
        if float(c.get("radius") or 0.0) < max_r * 0.42:
            continue
        parts.append(
            f'<text x="{float(c.get("x")):.2f}" y="{float(c.get("y")):.2f}" '
            f'data-label-id="{_esc(c.get("feature_id"))}">'
            f'{_esc(c.get("label") or c.get("feature_id"))}</text>')
    parts.append("</g>")

    # ── 图例 ──
    lx, ly, lw = 18.0, 18.0, extent * 0.34
    refs = list(legend.get("reference_circles") or [])
    # 图例用**一个**统一系数（不是逐圆 clamp）：参考圆之间的面积比必须与图面
    # 一致，否则读者无法用图例核对。可读性靠整体放大，不靠给单个圆兜底。
    raw_max_ref = max((float(r.get("radius") or 0.0) for r in refs), default=0.0)
    legend_factor = 0.32 if raw_max_ref <= 0 else min(0.32, 16.0 / raw_max_ref)
    legend_rmax = raw_max_ref * legend_factor
    row_h = max(22.0, 2.0 * legend_rmax + 8.0)
    legend_h = 96.0 + row_h * len(refs) + 16.0 * min(
        6, len(layout.get("disclosures") or []))
    parts.append(f'<g id="legend" transform="translate({lx:.2f},{ly:.2f})" '
                 f'font-family="{FONT_STACK}" fill="{INK}">')
    parts.append(f'<rect x="0" y="0" width="{lw:.2f}" height="{legend_h:.2f}" '
                 f'rx="6" fill="{PAPER}" fill-opacity="0.94" stroke="#cbd5e1"/>')
    parts.append(f'<text x="10" y="20" font-size="14" font-weight="700" '
                 f'data-legend-title="1">{_esc(title)}</text>')
    parts.append(f'<text x="10" y="38" font-size="11" fill="{MUTED}" '
                 f'data-legend-unit="1">单位：{_esc(unit)}　圆面积∝数量</text>')
    y = 56.0
    for ref in refs:
        r = float(ref.get("radius") or 0.0) * legend_factor
        parts.append(f'<circle cx="{18 + r:.2f}" cy="{y:.2f}" r="{r:.6f}" '
                     f'fill="{FILL}" fill-opacity="{FILL_OPACITY}" stroke="{STROKE}" '
                     f'stroke-width="0.8" data-legend-value="{_esc(ref.get("value"))}"/>')
        parts.append(f'<text x="{max(36.0, 18 + 2 * r + 8):.2f}" y="{y + 4:.2f}" '
                     f'font-size="10" fill="{MUTED}">{_fmt(ref.get("value"))} {_esc(unit)}</text>')
        y += row_h
    notes = [f"零值 {len(layout.get('zero_ids') or [])} 个（不画圆）",
             f"缺失 {len(layout.get('missing_ids') or [])} 个（不参与总量）",
             "圆心为示意排布，非经纬度；灰线为原地理参照",
             f"源 {source.get('ref_id')} @ rev {source.get('content_revision')}",
             f"原邻接保留 {((metrics.get('adjacency') or {}).get('retained'))}/"
             f"{((metrics.get('adjacency') or {}).get('original_edges'))}"]
    if not ready:
        notes.insert(0, f"草稿（{status}）：未达发布资格门")
    for note in notes[:6]:
        parts.append(f'<text x="10" y="{y:.2f}" font-size="9.5" fill="{MUTED}" '
                     f'data-disclosure="1">{_esc(note)}</text>')
        y += 16.0
    parts.append("</g>")

    if not ready:
        parts.append(
            f'<g id="draft-banner"><rect x="{extent - 240:.2f}" y="18" width="222" '
            f'height="26" rx="4" fill="#fee2e2" stroke="#b91c1c"/>'
            f'<text x="{extent - 232:.2f}" y="36" font-family="{FONT_STACK}" '
            f'font-size="12" fill="#b91c1c" data-draft-banner="1">草稿：未达发布资格门</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def svg_pixel_size(layout: Dict[str, Any], dpi: int) -> Tuple[int, int]:
    """按 DPI 换算像素边长（物理尺寸由纸面 mm 决定，比例不变）。"""
    paper_mm = 190.0  # 单幅示意图纸宽（mm）
    px = int(round(paper_mm / 25.4 * float(dpi)))
    return px, px


def render_png(layout: Dict[str, Any], *, dpi: int = 96) -> bytes:
    """SVG → PNG（rsvg-convert，单进程）。"""
    svg = render_svg(layout)
    binary = shutil.which("rsvg-convert")
    if binary is None:
        raise AuthoringError(
            "EXPORT_UNAVAILABLE",
            "rsvg-convert 不可用，无法生成 PNG（矢量 SVG 仍可下载）",
            status_code=503,
            correction_hint="安装 librsvg2-bin，或改用 SVG 导出。")
    w, h = svg_pixel_size(layout, dpi)
    with tempfile.TemporaryDirectory(prefix="g47png-") as tmp:
        src = os.path.join(tmp, "layout.svg")
        dst = os.path.join(tmp, "layout.png")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(svg)
        proc = subprocess.run(
            [binary, "--dpi-x", str(dpi), "--dpi-y", str(dpi),
             "-w", str(w), "-h", str(h), "-o", dst, src],
            capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0 or not os.path.exists(dst):
            raise AuthoringError(
                "EXPORT_FAILED",
                f"PNG 栅格化失败（rsvg-convert rc={proc.returncode}）",
                status_code=500, details={"stderr": (proc.stderr or "")[:300]})
        with open(dst, "rb") as fh:
            return fh.read()


def _pdf_html(svg: str, layout: Dict[str, Any]) -> str:
    title = _esc((layout.get("legend") or {}).get("title") or "Dorling cartogram")
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    # 190mm 见方的图面 + 页边距；`height:auto` 保证等比（不拉伸成椭圆）。
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>@page { size: A4 landscape; margin: 12mm; }"
        "body { margin:0; font-family: 'Noto Sans CJK SC', sans-serif; }"
        ".wrap { width: 190mm; }"
        "img { width: 190mm; height: auto; display:block; }"
        "</style></head><body><div class='wrap'>"
        f"<img src='data:image/svg+xml;base64,{b64}' alt='{title}'/>"
        "</div></body></html>")


def render_pdf(layout: Dict[str, Any]) -> bytes:
    """SVG → PDF（WeasyPrint；不可用时 typed unavailable，不伪造）。"""
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover - 依赖缺席时如实 unavailable
        raise AuthoringError(
            "EXPORT_UNAVAILABLE",
            "WeasyPrint 不可用，无法生成矢量 PDF（SVG/PNG 仍可下载）",
            status_code=503,
            correction_hint="安装 weasyprint 或改用 SVG 导出。") from exc
    svg = render_svg(layout)
    try:
        return HTML(string=_pdf_html(svg, layout)).write_pdf()
    except Exception as exc:
        raise AuthoringError(
            "EXPORT_FAILED", f"PDF 生成失败：{type(exc).__name__}",
            status_code=500) from exc


def render_export(
    layout: Dict[str, Any], fmt: str, *, dpi: int = 96,
) -> Tuple[bytes, str, str]:
    """→ (bytes, media_type, filename)。"""
    key = str(fmt or "").lower().lstrip(".")
    base = "dorling-cartogram-" + str(layout.get("candidate_digest") or "layout")[:12]
    if key == "svg":
        return render_svg(layout).encode("utf-8"), "image/svg+xml", base + ".svg"
    if key == "png":
        return render_png(layout, dpi=dpi), "image/png", base + ".png"
    if key == "pdf":
        return render_pdf(layout), "application/pdf", base + ".pdf"
    raise AuthoringError(
        "INVALID_PLAN", f"不支持的导出格式 {fmt!r}", field="format",
        correction_hint="使用 svg / png / pdf。")
