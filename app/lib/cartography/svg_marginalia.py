"""Publication SVG Marginalia — backend chrome fragments (V6, ADR-0120 W7).

publication 级图面整饰的**纯片段生成器**（帧框/指北针/比例尺/图例框/
标题块/经纬网/locators）。全部片段在画布坐标系（孪生投影空间）内工作，
由孪生编译器在 ``include_chrome=True`` 时按 canonical scene 组件装配 ——
单一投影权威在孪生，本模块不做坐标换算（graticule 例外：bounds→canvas
由调用方注入 projector 闭包）。

几何形态与前端 ``svg-marginalia.ts`` 同族（golden 结构测试锁定）；
所有用户文本经 escape 后进入属性/内容（与孪生转义链同源）。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple


#: 画布内边距（chrome 与孪生 padding 语义分离：这是 chrome 的外框留白）。
CHROME_MARGIN = 24.0


def escape_svg_text(value: Any) -> str:
    """与孪生 _escape_svg_attr 同链（html.escape quote=True）。"""
    import html as _html

    return _html.escape(str(value), quote=True)


def _fmt(v: float) -> str:
    """孪生 _fmt_num 同口径（2 位截尾）。"""
    s = f"{float(v):.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# ── 帧框 / neatline ──────────────────────────────────────────────────────

def render_frame_border(width: float, height: float, *, margin: float = CHROME_MARGIN,
                        color: str = "#1e3a8a", academic: bool = True) -> str:
    """外框 + academic 内线（单/双框）。"""
    x, y = margin, margin
    w = max(width - margin * 2, 0.0)
    h = max(height - margin * 2, 0.0)
    out = f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" fill="none" stroke="{escape_svg_text(color)}" stroke-width="2" rx="4" />'
    if academic:
        inset = 4.0
        out += (
            f'<rect x="{_fmt(x + inset)}" y="{_fmt(y + inset)}" '
            f'width="{_fmt(max(w - inset * 2, 0))}" height="{_fmt(max(h - inset * 2, 0))}" '
            f'fill="none" stroke="{escape_svg_text(color)}" stroke-width="0.75" />'
        )
    return out


# ── 指北针 ───────────────────────────────────────────────────────────────

def render_north_arrow(cx: float, cy: float, *, size: float = 44.0,
                       color: str = "#2563eb") -> str:
    """指北针（(cx, cy) 为中心；三角+半影+N）。"""
    w = h = size
    half = w / 2.0
    top = cy - h / 2.0 + 6.0
    bottom = cy + h / 2.0 - 14.0
    mid = cy
    c = escape_svg_text(color)
    return (
        f'<g class="chrome-north-arrow">'
        f'<polygon points="{_fmt(cx)},{_fmt(top)} {_fmt(cx)},{_fmt(bottom)} {_fmt(cx - half + 6)},{_fmt(mid)}" fill="{c}" />'
        f'<polygon points="{_fmt(cx)},{_fmt(top)} {_fmt(cx)},{_fmt(bottom)} {_fmt(cx + half - 6)},{_fmt(mid)}" fill="#ffffff" stroke="{c}" stroke-width="1" />'
        f'<text x="{_fmt(cx)}" y="{_fmt(cy + h / 2.0 - 2.0)}" font-family="sans-serif" font-size="12" font-weight="bold" fill="{c}" text-anchor="middle">N</text>'
        f"</g>"
    )


# ── 比例尺（投影感知：Web Mercator center-lat 口径）──────────────────────

_NICE_STEPS = (1.0, 2.0, 5.0)


def nice_scale_distance(max_meters: float) -> Tuple[float, str]:
    """1-2-5 序列取 ≤ max_meters 的最大整洁距离，返回 (米, 标签)。"""
    if max_meters <= 0 or not math.isfinite(max_meters):
        return (0.0, "")
    exp = int(math.floor(math.log10(max_meters)))
    base = 10.0 ** exp
    for mult in (5.0, 2.0, 1.0):
        candidate = base * mult
        if candidate <= max_meters * (1.0 + 1e-9):
            break
    if candidate >= 1000.0:
        label_value = candidate / 1000.0
        label = f"{_fmt(label_value)} km"
    else:
        label = f"{_fmt(candidate)} m"
    return (candidate, label)


def meters_per_pixel(bounds: List[float], canvas_width: float) -> Optional[float]:
    """Web Mercator 地面分辨率（画布中心纬度口径；WGS84 赤道周长）。

    ``mpp = (lng_span/360) × C × cos(lat_center) / canvas_px`` —— cos 修正
    抵消 Mercator 随纬度的放大（比例尺"投影感知"的核心项）。
    """
    try:
        w, s, e, n = bounds
        lng_span = float(e) - float(w)
        if lng_span <= 0 or canvas_width <= 0:
            return None
        center_lat_rad = math.radians((float(s) + float(n)) / 2.0)
        ground_m = (lng_span / 360.0) * 40075016.686 * math.cos(center_lat_rad)
        mpp = ground_m / canvas_width
        return mpp if mpp > 0 and math.isfinite(mpp) else None
    except Exception:
        return None


def render_scale_bar(x: float, y: float, bounds: List[float], canvas_width: float, *,
                     target_px: float = 120.0, color: str = "#1e293b") -> str:
    """比例尺：按中心纬度地面分辨率取整洁距离（projection-aware）。

    Mercator 高纬放大在中心纬度口径内部分抵消；3D 视角另有
    terrain_3d_scale_caveat 披露（词表既有码）。
    """
    mpp = meters_per_pixel(bounds, canvas_width)
    if mpp is None:
        return ""
    meters = nice_scale_distance(mpp * target_px)
    if meters[0] <= 0:
        return ""
    length_px = meters[0] / mpp
    c = escape_svg_text(color)
    mid = x + length_px / 2.0
    return (
        f'<g class="chrome-scale-bar">'
        f'<line x1="{_fmt(x)}" y1="{_fmt(y)}" x2="{_fmt(x + length_px)}" y2="{_fmt(y)}" stroke="{c}" stroke-width="2" stroke-linecap="square" />'
        f'<line x1="{_fmt(x)}" y1="{_fmt(y - 6)}" x2="{_fmt(x)}" y2="{_fmt(y)}" stroke="{c}" stroke-width="2" />'
        f'<line x1="{_fmt(mid)}" y1="{_fmt(y - 4)}" x2="{_fmt(mid)}" y2="{_fmt(y)}" stroke="{c}" stroke-width="1.5" />'
        f'<line x1="{_fmt(x + length_px)}" y1="{_fmt(y - 6)}" x2="{_fmt(x + length_px)}" y2="{_fmt(y)}" stroke="{c}" stroke-width="2" />'
        f'<text x="{_fmt(mid)}" y="{_fmt(y - 10)}" font-family="sans-serif" font-size="10" font-weight="600" fill="{c}" text-anchor="middle">{escape_svg_text(meters[1])}</text>'
        f"</g>"
    )


# ── 图例框（条目来自 derive_legend_items 单源）───────────────────────────

def render_legend_box(x: float, y: float, legend: Dict[str, Any], *,
                      max_items: int = 12, width: float = 176.0) -> str:
    """图例卡：title + entries（超出 max_items 截断 + 披露由调用方发）。"""
    entries: List[Dict[str, str]] = legend.get("entries") or []
    shown = entries[:max_items]
    if not shown:
        return ""
    item_h = 20.0
    padding = 12.0
    height = padding * 2 + 18.0 + len(shown) * item_h
    title = legend.get("title") or "图例"
    parts = [
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(width)}" height="{_fmt(height)}" fill="rgba(255, 255, 255, 0.9)" stroke="#cbd5e1" stroke-width="1" rx="6" />',
        f'<text x="{_fmt(x + padding)}" y="{_fmt(y + padding + 10)}" font-family="sans-serif" font-size="12" font-weight="bold" fill="#1e293b">{escape_svg_text(title)}</text>',
    ]
    for i, e in enumerate(shown):
        iy = y + padding + 22.0 + i * item_h
        parts.append(
            f'<rect x="{_fmt(x + padding)}" y="{_fmt(iy - 10)}" width="14" height="10" '
            f'fill="{escape_svg_text(e.get("color", "#888"))}" rx="1" />'
        )
        parts.append(
            f'<text x="{_fmt(x + padding + 24)}" y="{_fmt(iy)}" font-family="sans-serif" '
            f'font-size="11" fill="#1e293b">{escape_svg_text(e.get("label", ""))}</text>'
        )
    return f'<g class="chrome-legend">{"".join(parts)}</g>'


# ── 标题块 ───────────────────────────────────────────────────────────────

def render_title_block(x: float, y: float, title: str, subtitle: str = "") -> str:
    out = (
        f'<text x="{_fmt(x)}" y="{_fmt(y + 18.0)}" font-family="sans-serif" font-size="22" '
        f'font-weight="bold" fill="#0f172a">{escape_svg_text(title)}</text>'
    )
    if subtitle:
        out += (
            f'<text x="{_fmt(x)}" y="{_fmt(y + 38.0)}" font-family="sans-serif" font-size="12" '
            f'fill="#0f172a" opacity="0.75">{escape_svg_text(subtitle)}</text>'
        )
    return f'<g class="chrome-title">{out}</g>'


def render_attribution(x: float, y: float, text: str) -> str:
    return (
        f'<g class="chrome-attribution"><text x="{_fmt(x)}" y="{_fmt(y)}" '
        f'font-family="sans-serif" font-size="10" fill="#94a3b8">{escape_svg_text(text)}</text></g>'
    )


# ── 经纬网（bounds + projector 注入）────────────────────────────────────

def _nice_step(span: float, target_lines: int = 6) -> float:
    if span <= 0:
        return 1.0
    raw = span / max(target_lines, 1)
    exp = int(math.floor(math.log10(raw)))
    base = 10.0 ** exp
    for mult in (1.0, 2.0, 5.0, 10.0):
        if base * mult >= raw:
            return base * mult
    return base * 10.0


def _fmt_degree(v: float, axis: str) -> str:
    hemi = "E" if axis == "lng" and v >= 0 else "W" if axis == "lng" else "N" if v >= 0 else "S"
    return f"{_fmt(abs(v))}°{hemi}"


def render_graticule(bounds: List[float], project: Callable[[Tuple[float, float]], Tuple[Any, Any]],
                     *, lines: int = 6, color: str = "#64748b") -> str:
    """经纬网：nice-step 经纬线（Mercator 下经线直、纬线直）+ 度标注。"""
    try:
        w, s, e, n = (float(v) for v in bounds)
    except Exception:
        return ""
    if e <= w or n <= s:
        return ""
    lng_step = _nice_step(e - w, lines)
    lat_step = _nice_step(n - s, lines)
    parts: List[str] = ['<g class="chrome-graticule">']
    lng = math.ceil(w / lng_step) * lng_step
    while lng < e:
        p1 = project((lng, s))
        p2 = project((lng, n))
        parts.append(
            f'<line x1="{p1[0]}" y1="{p1[1]}" x2="{p2[0]}" y2="{p2[1]}" '
            f'stroke="{escape_svg_text(color)}" stroke-width="0.5" stroke-dasharray="4 3" opacity="0.6" />'
        )
        parts.append(
            f'<text x="{p2[0]}" y="{_fmt(float(p2[1]) - 4)}" font-family="sans-serif" font-size="9" '
            f'fill="{escape_svg_text(color)}" text-anchor="middle">{escape_svg_text(_fmt_degree(lng, "lng"))}</text>'
        )
        lng += lng_step
    lat = math.ceil(s / lat_step) * lat_step
    while lat < n:
        p1 = project((w, lat))
        p2 = project((e, lat))
        parts.append(
            f'<line x1="{p1[0]}" y1="{p1[1]}" x2="{p2[0]}" y2="{p2[1]}" '
            f'stroke="{escape_svg_text(color)}" stroke-width="0.5" stroke-dasharray="4 3" opacity="0.6" />'
        )
        parts.append(
            f'<text x="{_fmt(float(p1[0]) + 4)}" y="{p1[1]}" font-family="sans-serif" font-size="9" '
            f'fill="{escape_svg_text(color)}" text-anchor="start">{escape_svg_text(_fmt_degree(lat, "lat"))}</text>'
        )
        lat += lat_step
    parts.append("</g>")
    return "".join(parts)


# ── 区位插图（locator：上下文范围 + 主图范围框）──────────────────────────

def render_inset_locator(x: float, y: float, size: Tuple[float, float],
                         context_bounds: List[float], main_bounds: List[float],
                         *, color: str = "#1e3a8a") -> str:
    """区位插图（locator 形态）：上下文范围内绘制主图范围框。

    context_bounds 缺省语义 = 主图 bounds 的 4 倍外扩（调用方决定）；
    无可用上下文（退化范围）→ 返回空串（不画无意义的框）。
    """
    try:
        cw, cs, ce, cn = (float(v) for v in context_bounds)
        mw, ms, me, mn = (float(v) for v in main_bounds)
    except Exception:
        return ""
    w, h = size
    lng_span = ce - cw
    lat_span = cn - cs
    if lng_span <= 0 or lat_span <= 0:
        return ""
    def to_px(lng: float, lat: float) -> Tuple[float, float]:
        return (
            x + (lng - cw) / lng_span * w,
            y + (cn - lat) / lat_span * h,
        )
    mx0, my0 = to_px(mw, mn)
    mx1, my1 = to_px(me, ms)
    c = escape_svg_text(color)
    parts = [
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
        f'fill="rgba(255,255,255,0.9)" stroke="#cbd5e1" stroke-width="1" rx="4" />',
        f'<rect x="{_fmt(mx0)}" y="{_fmt(my0)}" width="{_fmt(max(mx1 - mx0, 2))}" '
        f'height="{_fmt(max(my1 - my0, 2))}" fill="rgba(37, 99, 235, 0.25)" stroke="{c}" stroke-width="1.5" />',
        f'<text x="{_fmt(x + 6)}" y="{_fmt(y + h - 6)}" font-family="sans-serif" font-size="9" '
        f'fill="#64748b">主图范围</text>',
    ]
    return f'<g class="chrome-inset">{"".join(parts)}</g>'
