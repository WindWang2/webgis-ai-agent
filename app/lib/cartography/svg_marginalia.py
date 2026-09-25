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
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(target_lines, 1)
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
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
    # R2-C1 修复：定长循环（索引 × 步长），杜绝浮点累积不前进的永续挂死
    # （1e16 + 0.5 ties-to-even 回到自身 → while 永真）；条数上界 2*lines+1。
    lng_start = math.ceil(w / lng_step) * lng_step
    lng_count = max(0, min(int((e - lng_start) / lng_step) + 1, 2 * lines + 2))
    for i in range(lng_count):
        lng = lng_start + i * lng_step
        if not (w < lng < e):
            continue
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
    lat_start = math.ceil(s / lat_step) * lat_step
    lat_count = max(0, min(int((n - lat_start) / lat_step) + 1, 2 * lines + 2))
    for j in range(lat_count):
        lat = lat_start + j * lat_step
        if not (s < lat < n):
            continue
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


# ── F14 WP2：publication 面板/色带/注记族（canvas drawChrome* 同语义镜像）──
# 数据协议与有界性见 docs/dev/publication-export-parity-design.md §D2；
# SVG marker 契约（chrome-colorbar / chrome-annotation / chrome-panel[data-kind]）
# 由 export_semantic_corpus 语料 + 矩阵一致性用例锁定。

#: 面板卡片宽（canvas drawChrome 缺省 250 的出版收窄版）。
PANEL_WIDTH = 210.0
#: 面板/注记行数上限（超出截断 + publication_layout_truncated 披露）。
PANEL_MAX_ROWS = 16
STATS_MAX_ROWS = 12
ANNOTATION_MAX_LINES = 8
#: 色带离散块上限。
COLORBAR_MAX_STOPS = 24


def _panel_card(x: float, y: float, w: float, h: float) -> str:
    """面板底卡（白色半透明 + 边框；与图例卡同族形态）。"""
    return (
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
        f'fill="rgba(255, 255, 255, 0.92)" stroke="#cbd5e1" stroke-width="1" rx="6" />'
    )


def _panel_text(x: float, y: float, s: str, *, size: float = 9.0,
                anchor: str = "start", fill: str = "#1e293b",
                weight: str = "normal", strike: bool = False) -> str:
    deco = ' text-decoration="line-through"' if strike else ""
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-family="sans-serif" '
        f'font-size="{_fmt(size)}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}"{deco}>{escape_svg_text(s)}</text>'
    )


def render_disclosure_panel(x: float, y: float, kind: str, title: str,
                            rows: List[str], *, accent: bool = False,
                            strike_rows: Tuple[int, ...] = (),
                            width: float = PANEL_WIDTH) -> str:
    """披露族（methodology/uncertainty/decision）→ chrome-panel 卡。

    rows 已由调用方按协议解析并有界（≤16）；strike_rows 为删除线行号
    （decision 的 vetoed 行 —— 与 live data-basis 同语义）。
    """
    row_h = 14.0
    head = 22.0
    h = 12.0 + head + len(rows) * row_h
    parts = [_panel_card(x, y, width, h)]
    if accent:
        parts.append(
            f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="3" height="{_fmt(h)}" '
            f'fill="rgba(217, 119, 6, 0.95)" />')
    parts.append(_panel_text(x + 10.0, y + head - 4.0, title or "", size=10.5,
                             weight="bold"))
    strikes = set(strike_rows)
    for i, row in enumerate(rows):
        parts.append(_panel_text(x + 10.0, y + head + 6.0 + i * row_h, row,
                                 size=8.5, strike=i in strikes))
    return f'<g class="chrome-panel" data-kind="{escape_svg_text(kind)}">{"".join(parts)}</g>'


def render_statistics_panel(x: float, y: float, title: str,
                            items: List[str], *, width: float = PANEL_WIDTH) -> str:
    """统计卡 → chrome-panel data-kind="statistics"。items = "label: value" 行。"""
    row_h = 16.0
    head = 22.0
    h = 12.0 + head + len(items) * row_h
    parts = [_panel_card(x, y, width, h)]
    parts.append(_panel_text(x + 10.0, y + head - 4.0, title or "统计", size=10.5,
                             weight="bold"))
    for i, item in enumerate(items):
        parts.append(_panel_text(x + 10.0, y + head + 6.0 + i * row_h, item, size=9.0))
    return f'<g class="chrome-panel" data-kind="statistics">{"".join(parts)}</g>'


def render_table_panel(x: float, y: float, title: str, columns: List[str],
                       rows: List[List[str]], total_rows: int = 0, *,
                       width: float = PANEL_WIDTH) -> str:
    """表格快照卡 → chrome-panel data-kind="table"（有界快照 + 总量尾注）。"""
    row_h = 14.0
    head = 20.0
    shown = rows[:8]
    tail = ""
    if total_rows > len(shown):
        tail = f"… 共 {total_rows} 行"
    h = 12.0 + head + (len(shown) + 1) * row_h + (12.0 if tail else 0.0)
    parts = [_panel_card(x, y, width, h)]
    parts.append(_panel_text(x + 10.0, y + head - 4.0, title or "数据表",
                             size=10.5, weight="bold"))
    cols = columns[:6]
    col_w = (width - 20.0) / max(len(cols), 1)
    header_y = y + head + 8.0
    for ci, col in enumerate(cols):
        parts.append(_panel_text(x + 10.0 + ci * col_w, header_y, col[:12],
                                 size=8.5, weight="bold", fill="#334155"))
    for ri, row in enumerate(shown):
        yy = header_y + (ri + 1) * row_h
        for ci in range(len(cols)):
            cell = str(row[ci]) if ci < len(row) else ""
            parts.append(_panel_text(x + 10.0 + ci * col_w, yy, cell[:12], size=8.0))
    if tail:
        parts.append(_panel_text(x + 10.0, y + h - 6.0, tail, size=8.0,
                                 fill="#64748b"))
    return f'<g class="chrome-panel" data-kind="table">{"".join(parts)}</g>'


def render_annotation_card(x: float, y: float, lines: List[str], *,
                           width: float = PANEL_WIDTH) -> str:
    """注记静态卡 → chrome-annotation（≤8 行；callout 锚定由装配层投影）。"""
    line_h = 15.0
    h = 10.0 + len(lines) * line_h
    parts = [_panel_card(x, y, width, h)]
    parts.append(
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="3" height="{_fmt(h)}" '
        f'fill="rgba(37, 99, 235, 0.55)" />')
    for i, line in enumerate(lines):
        parts.append(_panel_text(x + 10.0, y + 14.0 + i * line_h, line, size=8.5))
    return f'<g class="chrome-annotation">{"".join(parts)}</g>'


def render_callout(x: float, y: float, lines: List[str]) -> str:
    """地理锚定 callout（anchorCoordinate 经投影后的箭头注记）。

    (x, y) 为锚点像素；卡片右上偏移 + 短引线 —— 无 bounds 可投影时装配层
    降级 render_annotation_card（与 live 的 bounds 缺席降级同语义）。
    """
    card = render_annotation_card(x + 10.0, y - 14.0, lines)
    leader = (
        f'<line x1="{_fmt(x)}" y1="{_fmt(y)}" x2="{_fmt(x + 10.0)}" '
        f'y2="{_fmt(y - 4.0)}" stroke="#2563eb" stroke-width="1" />'
        f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="2.2" fill="#2563eb" />'
    )
    return card + leader


def render_colorbar(x: float, y: float, spec: Dict[str, Any], *,
                    width: float = PANEL_WIDTH, vertical: bool = False,
                    stepped: bool = False, title_override: str = "") -> str:
    """连续色带 → chrome-colorbar（legend_spec / options 双通道的统一渲染）。

    - spec：``{palette_colors[], min?, max?, unit?, field?, title?, nodata?}``
      （绑定 layer 的 legend_spec 优先，组件 options 内联兜底 —— 前端
      ``el.legendSpec`` 同语义；E-5：无 palette 不绘制，缺 min/max 画裸条）。
    - 变体：vertical（方向）/ stepped（离散色阶块，与 live stepped 等分同语义）。
    """
    if not isinstance(spec, dict):
        return ""
    colors = [c for c in (spec.get("palette_colors") or [])
              if isinstance(c, str) and c][:COLORBAR_MAX_STOPS]
    if not colors:
        return ""
    ramp_w, ramp_h = width - 20.0, 12.0
    head = 18.0
    total_h = head + ramp_h + 16.0 + (14.0 if spec.get("nodata") else 0.0)
    parts = [_panel_card(x, y, width, total_h)]
    title = title_override or spec.get("title") or (
        f"字段: {spec.get('field')}" if spec.get("field") else "")
    if title:
        parts.append(_panel_text(x + 10.0, y + head - 6.0, str(title)[:24],
                                 size=10.0, weight="bold"))
    rx, ry = x + 10.0, y + head
    ramp = colors if len(colors) >= 2 else [colors[0], colors[0]]
    if stepped:
        n = len(ramp)
        for i, c in enumerate(ramp):
            if vertical:
                cell = ramp_h / n
                parts.append(
                    f'<rect x="{_fmt(rx)}" y="{_fmt(ry + i * cell)}" width="{_fmt(ramp_w)}" '
                    f'height="{_fmt(cell)}" fill="{escape_svg_text(c)}" />')
            else:
                cell = ramp_w / n
                parts.append(
                    f'<rect x="{_fmt(rx + i * cell)}" y="{_fmt(ry)}" width="{_fmt(cell)}" '
                    f'height="{_fmt(ramp_h)}" fill="{escape_svg_text(c)}" />')
    else:
        stops = "".join(
            f'<stop offset="{_fmt(i / (len(ramp) - 1) * 100)}%" stop-color="{escape_svg_text(c)}" />'
            for i, c in enumerate(ramp)
        )
        parts.append(
            f'<defs><linearGradient id="chrome-cb-grad" x1="0" y1="0" '
            f'x2="{0 if vertical else 1}" y2="{1 if vertical else 0}">'
            f"{stops}</linearGradient></defs>")
        parts.append(
            f'<rect x="{_fmt(rx)}" y="{_fmt(ry)}" width="{_fmt(ramp_w)}" height="{_fmt(ramp_h)}" '
            f'fill="url(#chrome-cb-grad)" stroke="rgba(128,128,128,0.4)" stroke-width="0.5" />')
    vmin, vmax = spec.get("min"), spec.get("max")
    unit = str(spec.get("unit") or "")
    if isinstance(vmin, (int, float)) and isinstance(vmax, (int, float)) and vmin != vmax:
        lo = f"{vmin:g}{(' ' + unit) if unit else ''}"
        hi = f"{vmax:g}{(' ' + unit) if unit else ''}"
        if vertical:
            parts.append(_panel_text(rx + ramp_w + 2.0, ry + 8.0, hi, size=7.5))
            parts.append(_panel_text(rx + ramp_w + 2.0, ry + ramp_h, lo, size=7.5))
        else:
            parts.append(_panel_text(rx, ry + ramp_h + 9.0, lo, size=7.5))
            parts.append(_panel_text(rx + ramp_w, ry + ramp_h + 9.0, hi, size=7.5,
                                     anchor="end"))
    nodata = spec.get("nodata")
    if isinstance(nodata, dict) and nodata.get("color"):
        label = nodata.get("label") or "无数据"
        parts.append(
            f'<rect x="{_fmt(rx)}" y="{_fmt(ry + ramp_h + 14.0)}" width="12" height="8" '
            f'fill="{escape_svg_text(str(nodata["color"]))}" rx="1" />')
        parts.append(_panel_text(rx + 18.0, ry + ramp_h + 21.0, str(label)[:14], size=7.5))
    return f'<g class="chrome-colorbar">{"".join(parts)}</g>'


def colorbar_height(spec: Any) -> float:
    """色带装配前高度估计（stack 布局用；与渲染几何同表）。"""
    if not isinstance(spec, dict) or not [
        c for c in (spec.get("palette_colors") or []) if isinstance(c, str)
    ]:
        return 0.0
    return 50.0 + (14.0 if isinstance(spec.get("nodata"), dict) else 0.0)
