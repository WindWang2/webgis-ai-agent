"""G40 —— 规范化符号资产 → SVG 片段投影（纯函数，出版/图例共用）。

消费 `symbol_asset.SymbolAssetDocument` 规范形，产出：

- 点符号 ``<defs><g>`` + 要素处 ``<use>``（锚点/旋转/缩放变换链与
  MapLibre icon-anchor/icon-rotate/icon-size 同语义）；
- 面纹理 ``<pattern>``（userSpaceOnUse；hatch/cross_hatch/dots 程序化生成
  + svg_tile 平铺；背景色合同 bg_color=transparent 不画底）；
- 图例 swatch（同规范形、同参数，缩放进 swatch 盒）；
- 点符号变换后保守外接盒（G16 障碍接口 adapter；披露近似）。

单位合同（ADR-0290 corpus）：live CSS px；SVG 用户单位 = 逻辑 px ×
dpi_scale（与 mapspec_to_svg 的 radius×dpi_scale 同式）。纹理间距恒为
显示像素合同（不随 zoom/DPI 改变物理观感，采样像素按 DPI 变化）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.symbol_asset import (
    SymbolAssetDocument,
    SymbolPrimitive,
)

_FMT = "{:.3f}"


def _f(v: float) -> str:
    return _FMT.format(v)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── 点符号 defs ────────────────────────────────────────────────────────────

def _primitive_svg(p: SymbolPrimitive, *, color_override: Optional[str] = None) -> str:
    """单个规范图元 → SVG 元素（fill/stroke/opacity 直投）。"""
    attrs = f'fill="{_esc(color_override or p.fill)}"'
    if p.opacity < 1.0:
        attrs += f' fill-opacity="{_f(p.opacity)}"'
    if p.stroke not in ("none", "transparent"):
        attrs += (f' stroke="{_esc(p.stroke)}" stroke-width="{_f(p.stroke_width)}"')
    if p.op == "path" and p.d:
        d_parts: List[str] = []
        for cmd in p.d:
            d_parts.append(" ".join(str(c) for c in cmd))
        return f'<path d="{" ".join(d_parts)}" {attrs}/>'
    if p.op == "circle" and p.cx is not None and p.cy is not None and p.r is not None:
        return (f'<circle cx="{_f(p.cx)}" cy="{_f(p.cy)}" r="{_f(p.r)}" {attrs}/>')
    if p.op == "ellipse" and p.cx is not None and p.cy is not None:
        return (f'<ellipse cx="{_f(p.cx)}" cy="{_f(p.cy)}" '
                f'rx="{_f(p.rx or 0)}" ry="{_f(p.ry or 0)}" {attrs}/>')
    if p.op in ("polygon", "polyline") and p.points:
        pts = " ".join(f"{_f(x)},{_f(y)}" for x, y in p.points)
        if p.op == "polygon":
            return f'<polygon points="{pts}" {attrs}/>'
        return f'<polyline points="{pts}" fill="none" stroke="{_esc(p.stroke if p.stroke != "none" else "#000000")}" stroke-width="{_f(max(p.stroke_width, 0.1))}"/>'
    if p.op == "line" and p.x is not None and p.y is not None:
        return (f'<line x1="{_f(p.x)}" y1="{_f(p.y)}" '
                f'x2="{_f(p.x + (p.w or 0.0))}" y2="{_f(p.y + (p.h or 0.0))}" '
                f'stroke="{_esc(p.stroke if p.stroke != "none" else "#000000")}" '
                f'stroke-width="{_f(max(p.stroke_width, 0.1))}"/>')
    if p.op == "rect" and p.x is not None and p.y is not None:
        return (f'<rect x="{_f(p.x)}" y="{_f(p.y)}" width="{_f(p.w or 0)}" '
                f'height="{_f(p.h or 0)}" {attrs}/>')
    return ""


def point_symbol_def(defs_id: str, doc: SymbolAssetDocument, *,
                     color_override: Optional[str] = None) -> str:
    """点符号规范形 → ``<g id>`` defs 片段（viewBox 用户单位域）。"""
    body = "".join(
        _primitive_svg(p, color_override=color_override) for p in doc.primitives)
    return f'<g id="{_esc(defs_id)}">{body}</g>'


def point_symbol_transform(
    doc: SymbolAssetDocument, params: Dict[str, Any], *, dpi_scale: float,
) -> Tuple[str, Tuple[float, float, float, float]]:
    """锚点/旋转/缩放变换链 + 变换后保守外接盒（障碍 adapter 用）。

    变换 = translate(x,y)·rotate(r)·scale(s)·translate(-ax,-ay)；返回
    (transform 模板（不含 translate(x,y)，由调用方前置）, (w,h) 变换后尺寸)。
    """
    w, h = doc.intrinsic_size
    size_px = float(params.get("size_px", 16.0))
    rotation = float(params.get("rotation_deg", 0.0))
    anchor = str(params.get("anchor", "center"))
    ax_off, ay_off = doc.anchor_offset(anchor)
    anchor_x = w / 2.0 + ax_off
    anchor_y = h / 2.0 + ay_off
    s = (size_px / max(w, h)) * dpi_scale
    parts: List[str] = []
    if rotation:
        parts.append(f"rotate({_f(rotation)})")
    parts.append(f"scale({_f(s)})")
    parts.append(f"translate({_f(-anchor_x)} {_f(-anchor_y)})")
    # 旋转后的保守外接盒（以 anchor 为原点）：
    corners = [
        (-anchor_x * s, -anchor_y * s),
        ((w - anchor_x) * s, -anchor_y * s),
        ((w - anchor_x) * s, (h - anchor_y) * s),
        (-anchor_x * s, (h - anchor_y) * s),
    ]
    if rotation:
        rad = math.radians(rotation)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        corners = [(x * cos_r - y * sin_r, x * sin_r + y * cos_r)
                   for x, y in corners]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return " ".join(parts), (max(xs) - min(xs), max(ys) - min(ys))


def point_symbol_use(
    defs_id: str, x: float, y: float, doc: SymbolAssetDocument,
    params: Dict[str, Any], *, dpi_scale: float,
) -> str:
    """要素点 → ``<use>``（变换链见 point_symbol_transform）。"""
    inner, _ = point_symbol_transform(doc, params, dpi_scale=dpi_scale)
    return (f'<use href="#{_esc(defs_id)}" x="0" y="0" '
            f'transform="translate({_f(x)} {_f(y)}) {inner}"/>')


def point_symbol_obstacle_box(
    x: float, y: float, doc: SymbolAssetDocument, params: Dict[str, Any], *,
    dpi_scale: float,
) -> Tuple[float, float, float, float]:
    """变换后保守障碍盒（中心 ± 变换后半宽/半高；G16 v1 障碍接口形状）。"""
    _, (w, h) = point_symbol_transform(doc, params, dpi_scale=dpi_scale)
    return (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)


# ── 面纹理 pattern ─────────────────────────────────────────────────────────

def texture_pattern_def(
    pattern_id: str, doc: SymbolAssetDocument, params: Dict[str, Any], *,
    dpi_scale: float,
) -> str:
    """面纹理规范形 → ``<pattern>`` defs 片段（userSpaceOnUse）。

    间距/线宽 × dpi_scale（出版用户单位）；patternTransform 旋转实现
    angle_deg（间距按垂直距离不变）。背景：bg_color 非 transparent 时在
    pattern 盒内画底色矩形（S13 合同：透明背景不混入未知底）。
    """
    spacing = float(params.get("spacing_px", 12.0)) * dpi_scale
    stroke_w = float(params.get("stroke_width_px", 1.5)) * dpi_scale
    angle = float(params.get("angle_deg", 45.0))
    fg = str(params.get("fg_color", "#334155"))
    bg = str(params.get("bg_color", "transparent"))
    scale = float(params.get("pattern_scale", 1.0))
    spacing *= scale
    ptype = doc.pattern.type if doc.pattern else "hatch"
    transform = f' patternTransform="rotate({_f(angle)})"' if angle else ""

    bg_rect = (f'<rect x="0" y="0" width="{_f(spacing)}" height="{_f(spacing)}" '
               f'fill="{_esc(bg)}" stroke="none"/>') if bg not in ("transparent", "none") else ""

    if ptype == "dots":
        r = max(stroke_w / 2.0, 0.3)
        content = (f'{bg_rect}'
                   f'<circle cx="{_f(spacing / 2)}" cy="{_f(spacing / 2)}" '
                   f'r="{_f(r)}" fill="{_esc(fg)}" stroke="none"/>')
        # 点阵不需要旋转（各向同性）；transform 保留为空。
        return (f'<pattern id="{_esc(pattern_id)}" patternUnits="userSpaceOnUse" '
                f'width="{_f(spacing)}" height="{_f(spacing)}">{content}</pattern>')
    if ptype in ("hatch", "cross_hatch"):
        # 竖线族（spacing 间距）；cross_hatch 加横线族。pattern 盒取 spacing
        # 见方（线在 x=0/边缘，period=spacing）。
        lines = (
            f'<path d="M {_f(-stroke_w)} 0 L {_f(spacing + stroke_w)} 0" '
            f'stroke="{_esc(fg)}" stroke-width="{_f(stroke_w)}" fill="none"/>')
        box_h = spacing
        if ptype == "cross_hatch":
            lines += (
                f'<path d="M 0 {_f(-stroke_w)} L 0 {_f(spacing + stroke_w)}" '
                f'stroke="{_esc(fg)}" stroke-width="{_f(stroke_w)}" fill="none"/>')
        content = bg_rect + lines
        return (f'<pattern id="{_esc(pattern_id)}" patternUnits="userSpaceOnUse" '
                f'width="{_f(spacing)}" height="{_f(box_h)}"{transform}>'
                f'{content}</pattern>')
    # svg_tile：tile 周期平铺（周期 = tile_viewbox × pattern_scale ×
    # dpi_scale —— 与 live 光栅化 tile 同式，F9）。tile 图元**保留自身
    # 颜色**（fg_color 只作用于程序化 hatch/dots；导入 tile 的多色不被
    # 覆涂）。
    tile_w, tile_h = doc.pattern.tile_viewbox or (12.0, 12.0)
    tw = tile_w * scale * dpi_scale
    th = tile_h * scale * dpi_scale
    sx, sy = tw / tile_w, th / tile_h
    body = "".join(
        _primitive_svg(p) for p in (doc.pattern.tile_primitives or []))
    # N5（评审）：svg_tile 不应用 patternTransform —— tile 自带取向，angle
    # 仅程序化 hatch/dots 语义（live 光栅化同规则；此前导出侧误旋转）。
    # 背景矩形按完整周期尺寸（此前 spacing 方形只盖局部）。
    bg_full = (f'<rect x="0" y="0" width="{_f(tw)}" height="{_f(th)}" '
               f'fill="{_esc(bg)}" stroke="none"/>') \
        if bg not in ("transparent", "none") else ""
    return (f'<pattern id="{_esc(pattern_id)}" patternUnits="userSpaceOnUse" '
            f'width="{_f(tw)}" height="{_f(th)}">'
            f'{bg_full}'
            f'<g transform="scale({_f(sx)} {_f(sy)})">{body}</g></pattern>')


# ── 图例 swatch ────────────────────────────────────────────────────────────

def swatch_svg(
    doc: SymbolAssetDocument, params: Dict[str, Any], *,
    box_w: float, box_h: float, background: Optional[str] = None,
) -> str:
    """同语义 swatch：点符号按纵横比缩放进盒；纹理画 2×2 周期样本。"""
    parts: List[str] = []
    if background:
        parts.append(f'<rect x="0" y="0" width="{_f(box_w)}" height="{_f(box_h)}" '
                     f'fill="{_esc(background)}" stroke="none"/>')
    if doc.kind == "point_symbol":
        w, h = doc.intrinsic_size
        s = min(box_w / w, box_h / h)
        ox = (box_w - w * s) / 2.0
        oy = (box_h - h * s) / 2.0
        body = "".join(_primitive_svg(p) for p in doc.primitives)
        parts.append(
            f'<g transform="translate({_f(ox)} {_f(oy)}) scale({_f(s)})">{body}</g>')
    elif doc.pattern is not None and doc.pattern.type == "svg_tile":
        # F9（评审）：导入 tile 的 swatch 画 tile 本体（保留自身颜色；
        # 周期缩放进盒），不再误画通用晕线。
        tile_w, tile_h = doc.pattern.tile_viewbox or (12.0, 12.0)
        s = min(box_w / tile_w, box_h / tile_h)
        ox = (box_w - tile_w * s) / 2.0
        oy = (box_h - tile_h * s) / 2.0
        body = "".join(
            _primitive_svg(p) for p in (doc.pattern.tile_primitives or []))
        if background:
            pass  # 外层背景矩形已画
        parts.append(
            f'<g transform="translate({_f(ox)} {_f(oy)}) scale({_f(s)})">{body}</g>')
    else:
        # 纹理 swatch：用 spacing=盒内可读值重投 pattern 语义（角度/密度可辨）。
        ptype = doc.pattern.type if doc.pattern else "hatch"
        fg = str(params.get("fg_color", "#334155"))
        bg = str(params.get("bg_color", "transparent"))
        if bg not in ("transparent", "none"):
            parts.append(f'<rect x="0" y="0" width="{_f(box_w)}" height="{_f(box_h)}" '
                         f'fill="{_esc(bg)}" stroke="none"/>')
        angle = float(params.get("angle_deg", 45.0))
        spacing = float(params.get("spacing_px", 12.0)) * float(
            params.get("pattern_scale", 1.0))
        stroke_w = float(params.get("stroke_width_px", 1.5))
        if ptype == "dots":
            r = max(stroke_w / 2.0, 0.4)
            n = max(2, int(box_w / max(spacing, 2.0)) + 1)
            sp = box_w / n
            for i in range(n):
                for j in range(n):
                    parts.append(
                        f'<circle cx="{_f(sp * (i + 0.5))}" cy="{_f(sp * (j + 0.5))}" '
                        f'r="{_f(r)}" fill="{_esc(fg)}"/>')
        else:
            rad = math.radians(angle)
            # 覆盖盒的线族（沿角度方向，间距垂直量）：
            diag = math.hypot(box_w, box_h)
            nx = -math.sin(rad)
            ny = math.cos(rad)
            count = int(diag / max(spacing, 2.0)) + 2
            cx, cy = box_w / 2.0, box_h / 2.0
            for i in range(-count, count + 1):
                # 第 i 条线：过点 (cx + nx*i*spacing, cy + ny*i*spacing)，
                # 方向 (cos, sin)。
                px = cx + nx * i * spacing
                py = cy + ny * i * spacing
                dx, dy = math.cos(rad), math.sin(rad)
                parts.append(
                    f'<line x1="{_f(px - dx * diag / 2)}" y1="{_f(py - dy * diag / 2)}" '
                    f'x2="{_f(px + dx * diag / 2)}" y2="{_f(py + dy * diag / 2)}" '
                    f'stroke="{_esc(fg)}" stroke-width="{_f(stroke_w)}"/>')
                if ptype == "cross_hatch":
                    dx2, dy2 = -dy, dx
                    parts.append(
                        f'<line x1="{_f(px - dx2 * diag / 2)}" y1="{_f(py - dy2 * diag / 2)}" '
                        f'x2="{_f(px + dx2 * diag / 2)}" y2="{_f(py + dy2 * diag / 2)}" '
                        f'stroke="{_esc(fg)}" stroke-width="{_f(stroke_w)}"/>')
            if ptype == "cross_hatch":
                pass  # 上面循环内已双方向
    return f'<g class="cartoasset-swatch">{"".join(parts)}</g>'


__all__ = [
    "point_symbol_def", "point_symbol_use", "point_symbol_transform",
    "point_symbol_obstacle_box", "texture_pattern_def", "swatch_svg",
]


# ── PNG 栅格化 workaround（librsvg CLI 缩放丢 pattern 内容的实证回归） ────
#
# 实证（/tmp/g40-spike，librsvg 2.63.4）：`rsvg-convert -w W -h H` 的 W/H
# 与 SVG 根 width/height 不一致时，pattern 填充内容丢失（背景 rect 可存）。
# 规避：把根 width/height 改写为目标采样像素（viewBox 不动），按内在尺寸
# 渲染 —— 输出像素与 CLI 缩放等价且 pattern 完整（3x 缩放实证）。

def prepare_svg_for_rasterize(svg: str, w_px: int, h_px: int) -> str:
    """含 G40 pattern 标记时改写根 width/height 为采样像素（否则原样）。

    只在 SVG 含 ``url(#cartoap-``（本域 pattern 引用）时改写 —— 既有 G26/
    G34 PNG 路径字节与行为完全不变。
    """
    if "url(#cartoap-" not in svg:
        return svg
    import re

    out = re.sub(
        r'(<svg\b[^>]*?\swidth=")[^"]+(")',
        lambda m: m.group(1) + str(max(1, int(w_px))) + m.group(2),
        svg, count=1)
    out = re.sub(
        r'(<svg\b[^>]*?\sheight=")[^"]+(")',
        lambda m: m.group(1) + str(max(1, int(h_px))) + m.group(2),
        out, count=1)
    return out
