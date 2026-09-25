"""Publication SVG Charts — 图表面板族的确定性矢量渲染（F14 WP2）。

``chart_panel`` 在 publication 矢量链的**纯片段生成器**：ChartPanelData →
SVG 组（``class="chrome-panel" data-kind="chart"``）。与前端 export-chrome
的 drawChromeChartPanel 同语义镜像（kind 支持面对账
``chart_kinds.CHART_KINDS`` 的 export_level；近似绘制属 degraded 并在装配
层披露）。纪律与 svg_marginalia 相同：canvas 坐标系、确定性输出、用户文本
全转义、行/点数有界（防病态 spec 放大出版版面）。

数据协议（前端 ChartPanelData 镜像 + 语料宽松形）：
``{type|kind, title, data|points: [{name|x, value|y, q1..max}], series?, stacked?, x_label?, y_label?}``
—— ``type``/``data`` 是前端权威键；``kind``/``points``/``x``/``y`` 为语料
宽松别名，渲染器双读（live 契约优先）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.svg_marginalia import (
    _fmt,
    _panel_card,
    escape_svg_text,
)

#: 面板几何（与 disclosure/statistics/table 卡同宽；高度按 kind 固定）。
CHART_WIDTH = 210.0
#: 单序列点数上限（超出截断 + 截断数随结果返回，装配层发披露）。
MAX_POINTS = 64
#: 序列数上限（grouped/stacked/radar/matrix 族）。
MAX_SERIES = 6
#: 类目轴条目上限。
MAX_BARS = 12

#: 分类色板（8 色，与前端 chart 默认色同族的确定性子集；不随数据变化）。
CHART_PALETTE = (
    "#2563eb", "#f59e0b", "#10b981", "#ef4444",
    "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16",
)

_AXIS_KINDS = frozenset({
    "bar", "histogram", "line", "area", "timeseries", "cumulative",
})
_POLAR_KINDS = frozenset({"pie", "donut", "rose"})


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(float(v)) else None


def _point_name(p: Dict[str, Any]) -> str:
    name = p.get("name")
    if isinstance(name, str) and name:
        return name
    x = p.get("x")
    return str(x) if x is not None and not isinstance(x, (dict, list)) else ""


def _point_value(p: Dict[str, Any]) -> Optional[float]:
    v = _num(p.get("value"))
    return v if v is not None else _num(p.get("y"))


def _parse_chart(raw: Any) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """chart 载荷 → (kind, title, points, series, stacked)。kind 可能空。"""
    if not isinstance(raw, dict):
        return "", "", [], [], False
    kind = raw.get("type") if isinstance(raw.get("type"), str) else raw.get("kind")
    kind = kind.strip() if isinstance(kind, str) else ""
    title = raw.get("title") if isinstance(raw.get("title"), str) else ""
    points = raw.get("data") if isinstance(raw.get("data"), list) else (
        raw.get("points") if isinstance(raw.get("points"), list) else [])
    points = [p for p in points if isinstance(p, dict)][:MAX_POINTS]
    series = [s for s in (raw.get("series") or []) if isinstance(s, dict)][
        :MAX_SERIES]
    series = [
        {"name": str(s.get("name") or ""),
         "data": [p for p in (s.get("data") or []) if isinstance(p, dict)][:MAX_POINTS]}
        for s in series
        if isinstance(s.get("data"), list) and s["data"]
    ]
    stacked = raw.get("stacked") is True
    return kind, title, points, series, stacked


def _text(x: float, y: float, s: str, *, size: float = 9.0,
          anchor: str = "start", fill: str = "#1e293b",
          weight: str = "normal") -> str:
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-family="sans-serif" '
        f'font-size="{_fmt(size)}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}">{escape_svg_text(s)}</text>'
    )


def _axis_frame(x: float, y: float, w: float, h: float, title: str,
                x_label: str, y_label: str) -> Tuple[str, float, float]:
    """轴框架：标题行 + 轴线；返回 (svg, 绘图原点 px, 绘图区宽)。"""
    head = 16.0
    parts = [_text(x, y + 10.0, title or "", size=11.0, weight="bold")]
    ox, oy = x + (24.0 if y_label else 6.0), y + head + h
    parts.append(
        f'<line x1="{_fmt(ox)}" y1="{_fmt(oy)}" x2="{_fmt(ox + w)}" y2="{_fmt(oy)}" '
        f'stroke="#94a3b8" stroke-width="1" />'
    )
    parts.append(
        f'<line x1="{_fmt(ox)}" y1="{_fmt(oy)}" x2="{_fmt(ox)}" y2="{_fmt(oy - h)}" '
        f'stroke="#94a3b8" stroke-width="1" />'
    )
    if x_label:
        parts.append(_text(ox + w / 2.0, oy + 12.0, x_label, anchor="middle", fill="#64748b"))
    if y_label:
        parts.append(
            f'<text x="{_fmt(x + 4)}" y="{_fmt(y + head + h / 2)}" font-family="sans-serif" '
            f'font-size="9" fill="#64748b" text-anchor="middle" '
            f'transform="rotate(-90 {_fmt(x + 4)} {_fmt(y + head + h / 2)})">'
            f'{escape_svg_text(y_label)}</text>'
        )
    return "".join(parts), ox, oy


def _render_bars(x: float, y: float, title: str, rows: List[List[Tuple[str, float]]],
                 *, horizontal: bool, stacked: bool, w: float = CHART_WIDTH,
                 h: float = 100.0) -> str:
    """柱族：rows = 类目 → [(序列名, 值)]。stacked 堆叠，否则并排/单序列。"""
    if not rows:
        return ""
    n_cat = min(len(rows), MAX_BARS)
    plot_w = w - 40.0
    max_val = max(
        (sum(v for _n, v in row) if stacked else max(v for _n, v in row))
        for row in rows[:n_cat]
    )
    if max_val <= 0:
        max_val = 1.0
    parts: List[str] = []
    if horizontal:
        band = h / max(n_cat, 1)
        for i, row in enumerate(rows[:n_cat]):
            yy = y + i * band
            if stacked:
                off = 0.0
                for si, (_n, v) in enumerate(row):
                    seg = v / max_val * plot_w
                    parts.append(
                        f'<rect x="{_fmt(x + 34 + off)}" y="{_fmt(yy + band * 0.2)}" '
                        f'width="{_fmt(max(seg, 0.5))}" height="{_fmt(band * 0.6)}" '
                        f'fill="{CHART_PALETTE[si % len(CHART_PALETTE)]}" />')
                    off += seg
            else:
                v = row[0][1]
                parts.append(
                    f'<rect x="{_fmt(x + 34)}" y="{_fmt(yy + band * 0.2)}" '
                    f'width="{_fmt(max(v / max_val * plot_w, 0.5))}" height="{_fmt(band * 0.6)}" '
                    f'fill="{CHART_PALETTE[0]}" />')
            parts.append(_text(x + 30, yy + band * 0.5 + 3.0, row[0][0][:10],
                               anchor="end", size=8.0))
        return "".join(parts)
    band = plot_w / max(n_cat, 1)
    for i, row in enumerate(rows[:n_cat]):
        xx = x + 6 + i * band
        parts.append(_text(xx + band / 2.0, y + h + 10.0, row[0][0][:6],
                           anchor="middle", size=8.0))
        if stacked:
            off = 0.0
            for si, (_n, v) in enumerate(row):
                seg_h = v / max_val * h
                parts.append(
                    f'<rect x="{_fmt(xx + band * 0.15)}" y="{_fmt(y + h - off - seg_h)}" '
                    f'width="{_fmt(band * 0.7)}" height="{_fmt(max(seg_h, 0.5))}" '
                    f'fill="{CHART_PALETTE[si % len(CHART_PALETTE)]}" />')
                off += seg_h
        else:
            if len(row) == 1:
                bar_h = row[0][1] / max_val * h
                parts.append(
                    f'<rect x="{_fmt(xx + band * 0.15)}" y="{_fmt(y + h - bar_h)}" '
                    f'width="{_fmt(band * 0.7)}" height="{_fmt(max(bar_h, 0.5))}" '
                    f'fill="{CHART_PALETTE[0]}" />')
            else:
                sub = band / max(len(row), 1)
                for si, (_n, v) in enumerate(row):
                    bar_h = v / max_val * h
                    parts.append(
                        f'<rect x="{_fmt(xx + si * sub + sub * 0.1)}" y="{_fmt(y + h - bar_h)}" '
                        f'width="{_fmt(sub * 0.8)}" height="{_fmt(max(bar_h, 0.5))}" '
                        f'fill="{CHART_PALETTE[si % len(CHART_PALETTE)]}" />')
    return "".join(parts)


def _render_line_family(x: float, y: float, title: str,
                        rows: List[List[Tuple[str, float]]], *, area: bool,
                        cumulative: bool, w: float = CHART_WIDTH,
                        h: float = 100.0) -> str:
    plot_w = w - 24.0
    series_values: List[List[float]] = []
    for si, row in enumerate(rows[:MAX_SERIES]):
        vals = [v for _n, v in row]
        if cumulative:
            acc: List[float] = []
            total = 0.0
            for v in vals:
                total += v
                acc.append(total)
            vals = acc
        series_values.append(vals)
    max_val = max((max(vs) for vs in series_values if vs), default=1.0)
    if max_val <= 0:
        max_val = 1.0
    head = 16.0
    parts = [_text(x, y + 10.0, title or "", size=11.0, weight="bold")]
    names = rows[0] if rows else []
    n = max(len(vs) for vs in series_values) if series_values else 0
    if n >= 2:
        for si, vs in enumerate(series_values):
            pts: List[str] = []
            path: List[str] = []
            for i, v in enumerate(vs[:MAX_POINTS]):
                px = x + 12 + i / (len(vs) - 1) * plot_w
                py = y + head + h - v / max_val * h
                pts.append(f"{_fmt(px)},{_fmt(py)}")
                path.append(f"{'M' if not path else 'L'}{_fmt(px)} {_fmt(py)}")
            color = CHART_PALETTE[si % len(CHART_PALETTE)]
            if area:
                parts.append(
                    f'<polygon points="{pts[0]} {" ".join(pts[1:])} '
                    f'{_fmt(x + 12 + plot_w)},{_fmt(y + head + h)} {_fmt(x + 12)},{_fmt(y + head + h)}" '
                    f'fill="{color}" fill-opacity="0.18" />')
            parts.append(
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.5" />')
            parts.append(
                "".join(
                    f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="1.6" fill="{color}" />'
                    for p in pts[:: max(1, len(pts) // 8)]
                ))
        for i in (0, n - 1):
            name = names[i][0] if i < len(names) else ""
            px = x + 12 + (i / max(n - 1, 1)) * plot_w
            parts.append(_text(px, y + head + h + 10.0, str(name)[:8],
                               anchor="middle", size=8.0, fill="#64748b"))
    return "".join(parts)


def _render_scatter(x: float, y: float, title: str, points: List[Dict[str, Any]],
                    *, w: float = CHART_WIDTH, h: float = 100.0) -> str:
    pts = [( _num(p.get("x")), _num(p.get("y"))) for p in points]
    pts = [(a, b) for a, b in pts if a is not None and b is not None][:MAX_POINTS]
    head = 16.0
    parts = [_text(x, y + 10.0, title or "", size=11.0, weight="bold")]
    if len(pts) >= 2:
        xs = [a for a, _b in pts]
        ys = [b for _a, b in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        sx = (x1 - x0) or 1.0
        sy = (y1 - y0) or 1.0
        for a, b in pts:
            px = x + 12 + (a - x0) / sx * (w - 30.0)
            py = y + head + h - (b - y0) / sy * (h - 8.0) - 4.0
            parts.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="2.4" '
                         f'fill="{CHART_PALETTE[0]}" fill-opacity="0.75" />')
    return "".join(parts)


def _render_polar(x: float, y: float, title: str, rows: List[Tuple[str, float]],
                  *, donut: bool, rose: bool, w: float = CHART_WIDTH) -> str:
    cx, cy, r = x + w / 2.0, y + 16.0 + 45.0, 44.0
    total = sum(max(v, 0.0) for _n, v in rows)
    parts = [_text(x, y + 10.0, title or "", size=11.0, weight="bold")]
    if total <= 0 or not rows:
        return "".join(parts)
    angle = -math.pi / 2.0
    shown = rows[:MAX_BARS]
    for i, (name, v) in enumerate(shown):
        frac = max(v, 0.0) / total
        color = CHART_PALETTE[i % len(CHART_PALETTE)]
        if rose:
            rr = r * frac
            x0, y0 = cx + rr * math.cos(angle), cy + rr * math.sin(angle)
            step = 2 * math.pi / max(len(shown), 1)
            x1, y1 = cx + rr * math.cos(angle + step), cy + rr * math.sin(angle + step)
            xi, yi = cx + 12 * math.cos(angle + step), cy + 12 * math.sin(angle + step)
            xj, yj = cx + 12 * math.cos(angle), cy + 12 * math.sin(angle)
            parts.append(
                f'<path d="M{_fmt(xj)} {_fmt(yj)} L{_fmt(x0)} {_fmt(y0)} '
                f'A{_fmt(rr)} {_fmt(rr)} 0 0 1 {_fmt(x1)} {_fmt(y1)} '
                f'L{_fmt(xi)} {_fmt(yi)} A12 12 0 0 0 {_fmt(xj)} {_fmt(yj)} Z" '
                f'fill="{color}" fill-opacity="0.85" />')
        else:
            a2 = angle + frac * 2 * math.pi
            large = 1 if (a2 - angle) > math.pi else 0
            x0, y0 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x1, y1 = cx + r * math.cos(a2), cy + r * math.sin(a2)
            if donut:
                ri = r * 0.55
                xi, yi = cx + ri * math.cos(a2), cy + ri * math.sin(a2)
                xj, yj = cx + ri * math.cos(angle), cy + ri * math.sin(angle)
                parts.append(
                    f'<path d="M{_fmt(x0)} {_fmt(y0)} A{r} {r} 0 {large} 1 {_fmt(x1)} {_fmt(y1)} '
                    f'L{_fmt(xi)} {_fmt(yi)} A{ri} {ri} 0 {large} 0 {_fmt(xj)} {_fmt(yj)} Z" '
                    f'fill="{color}" />')
            else:
                parts.append(
                    f'<path d="M{_fmt(cx)} {_fmt(cy)} L{_fmt(x0)} {_fmt(y0)} '
                    f'A{r} {r} 0 {large} 1 {_fmt(x1)} {_fmt(y1)} Z" fill="{color}" />')
        lx = cx + (r + 10) * math.cos(angle + frac * math.pi)
        ly = cy + (r + 10) * math.sin(angle + frac * math.pi)
        parts.append(_text(lx, ly + 3.0, f"{name[:6]} {_fmt(frac * 100)}%",
                           anchor="middle", size=7.5))
        angle = angle + frac * 2 * math.pi
    return "".join(parts)


def _render_special(x: float, y: float, title: str, kind: str,
                    points: List[Dict[str, Any]], series: List[Dict[str, Any]],
                    *, w: float = CHART_WIDTH) -> str:
    """box_plot / heat_matrix / kpi_card / ranking_list / radar（近似绘制）。"""
    parts = [_text(x, y + 10.0, title or "", size=11.0, weight="bold")]
    if kind == "kpi_card":
        p = points[0] if points else {}
        v = _point_value(p)
        parts.append(_text(x + w / 2.0, y + 62.0,
                           f"{v:.1f}" if v is not None else "—",
                           anchor="middle", size=26.0, weight="bold"))
        parts.append(_text(x + w / 2.0, y + 80.0, _point_name(p)[:16],
                           anchor="middle", size=9.0, fill="#64748b"))
        return "".join(parts)
    if kind == "ranking_list":
        rows = sorted(
            [(_point_name(p), _point_value(p) or 0.0) for p in points],
            key=lambda t: -t[1])[:MAX_BARS]
        max_v = max((v for _n, v in rows), default=1.0) or 1.0
        band = 14.0
        for i, (name, v) in enumerate(rows):
            yy = y + 20.0 + i * band
            parts.append(_text(x + 2, yy + 8.0, f"{i + 1}. {name[:10]}", size=8.0))
            parts.append(
                f'<rect x="{_fmt(x + 92)}" y="{_fmt(yy + 1)}" '
                f'width="{_fmt(max(v / max_v * (w - 96), 0.5))}" height="10" '
                f'fill="{CHART_PALETTE[0]}" />')
        return "".join(parts)
    if kind == "box_plot":
        row_h = 22.0
        for i, p in enumerate([p for p in points if isinstance(p, dict)][:MAX_BARS]):
            q1 = _num(p.get("q1"))
            q3 = _num(p.get("q3"))
            med = _point_value(p)
            lo = _num(p.get("min"))
            hi = _num(p.get("max"))
            vals = [v for v in (lo, q1, med, q3, hi) if v is not None]
            if len(vals) < 3:
                continue
            vmin, vmax = min(vals), max(vals)
            span = (vmax - vmin) or 1.0
            yy = y + 22.0 + i * row_h
            px = lambda val: x + 50 + (val - vmin) / span * (w - 60.0)  # noqa: E731
            parts.append(
                f'<line x1="{_fmt(px(lo if lo is not None else vmin))}" y1="{_fmt(yy + 8)}" '
                f'x2="{_fmt(px(hi if hi is not None else vmax))}" y2="{_fmt(yy + 8)}" stroke="#64748b" />')
            bx0, bx1 = px(q1 if q1 is not None else vmin), px(q3 if q3 is not None else vmax)
            parts.append(
                f'<rect x="{_fmt(bx0)}" y="{_fmt(yy + 2)}" width="{_fmt(max(bx1 - bx0, 2))}" '
                f'height="12" fill="{CHART_PALETTE[0]}" fill-opacity="0.35" stroke="#2563eb" />')
            parts.append(
                f'<line x1="{_fmt(px(med if med is not None else vmin))}" y1="{_fmt(yy + 2)}" '
                f'x2="{_fmt(px(med if med is not None else vmin))}" y2="{_fmt(yy + 14)}" '
                f'stroke="#1e293b" stroke-width="1.5" />')
            parts.append(_text(x + 4, yy + 11.0, _point_name(p)[:10], size=8.0))
        return "".join(parts)
    if kind == "heat_matrix":
        rows_data = [s["data"] for s in series] or ([points] if points else [])
        nrows = min(len(rows_data), MAX_SERIES)
        ncols = max((len(r) for r in rows_data[:nrows]), default=0)
        if nrows and ncols:
            all_vals = [
                _point_value(p) for r in rows_data[:nrows]
                for p in (r[:MAX_POINTS] if isinstance(r, list) else [])
                if isinstance(p, dict)
            ]
            all_vals = [v for v in all_vals if v is not None]
            vmin = min(all_vals) if all_vals else 0.0
            vmax = max(all_vals) if all_vals else 1.0
            span = (vmax - vmin) or 1.0
            cell_w = min(24.0, (w - 30.0) / max(ncols, 1))
            cell_h = 16.0
            for ri, r in enumerate(rows_data[:nrows]):
                for ci, p in enumerate((r[:MAX_POINTS] if isinstance(r, list) else [])[:ncols]):
                    if not isinstance(p, dict):
                        continue
                    v = _point_value(p)
                    if v is None:
                        continue
                    t = (v - vmin) / span
                    color = CHART_PALETTE[0] if t >= 0.5 else "#c6dbef"
                    parts.append(
                        f'<rect x="{_fmt(x + 24 + ci * cell_w)}" y="{_fmt(y + 20 + ri * cell_h)}" '
                        f'width="{_fmt(cell_w - 1)}" height="{_fmt(cell_h - 1)}" '
                        f'fill="{color}" fill-opacity="{_fmt(0.25 + 0.65 * t):.2f}" />')
        return "".join(parts)
    # radar（近似：≤8 轴的多边形网 + 序列折线）
    axes = [(_point_name(p), _point_value(p) or 0.0)
            for p in points if isinstance(p, dict)][:8]
    if len(axes) >= 3:
        cx, cy, r = x + w / 2.0, y + 16.0 + 45.0, 42.0
        vmax = max((v for _n, v in axes), default=1.0) or 1.0
        n = len(axes)
        for k in range(n):
            a = -math.pi / 2.0 + k * 2 * math.pi / n
            parts.append(
                f'<line x1="{_fmt(cx)}" y1="{_fmt(cy)}" '
                f'x2="{_fmt(cx + r * math.cos(a))}" y2="{_fmt(cy + r * math.sin(a))}" '
                f'stroke="#cbd5e1" stroke-width="0.5" />')
        for si, srow in enumerate([s["data"] for s in series] or [axes]):
            vals = [v for _n, v in srow][:n] if srow and isinstance(srow[0], tuple) else [
                _point_value(p) or 0.0 for p in srow[:n]]
            pts = [
                f"{_fmt(cx + (val / vmax) * r * math.cos(-math.pi / 2 + k * 2 * math.pi / n))},"
                f"{_fmt(cy + (val / vmax) * r * math.sin(-math.pi / 2 + k * 2 * math.pi / n))}"
                for k, val in enumerate(vals)
            ]
            parts.append(
                f'<polygon points="{" ".join(pts)}" fill="{CHART_PALETTE[si % len(CHART_PALETTE)]}" '
                f'fill-opacity="0.25" stroke="{CHART_PALETTE[si % len(CHART_PALETTE)]}" />')
    return "".join(parts)


def render_chart_panel(x: float, y: float, chart: Any,
                       *, width: float = CHART_WIDTH) -> Tuple[str, str, int]:
    """图表面板 → (svg 组, 状态, 截断数)。

    状态：``drawn``（含近似绘制）/ ``unsupported``（kind 词表外或 violin）/ 
    ``invalid``（载荷不合法 → 装配层按面板缺席 + chart_ref_unavailable 披露）。
    截断数 > 0 时装配层发 publication_layout_truncated。
    """
    kind, title, points, series, stacked = _parse_chart(chart)
    if not kind or (not points and not series):
        return ("", "invalid", 0)
    from app.lib.cartography.chart_kinds import resolve_chart_kind

    desc = resolve_chart_kind(kind)
    if desc is not None and desc.export_level == "unsupported":
        card = _panel_card(x, y, width, 48.0)
        body = (
            card
            + _text(x + 10.0, y + 20.0, title or "图表", size=10.0, weight="bold")
            + _text(x + 10.0, y + 36.0, f"图表类型 {kind} 暂不支持矢量导出",
                    size=9.0, fill="#b45309")
        )
        return (f'<g class="chrome-panel" data-kind="chart">{body}</g>', "unsupported", 0)

    rows: List[List[Tuple[str, float]]] = []
    truncated = 0
    if series:
        for s in series:
            row = [(_point_name(p), _point_value(p)) for p in s["data"]]
            row = [(n, v) for n, v in row if v is not None]
            truncated += max(0, len(s["data"]) - MAX_POINTS)
            if row:
                rows.append(row)
    else:
        row = [(_point_name(p), _point_value(p)) for p in points]
        row = [(n, v) for n, v in row if v is not None]
        truncated += max(0, len(points) - MAX_POINTS)
        if row:
            rows = [row]

    status = "drawn"
    body = ""
    h = 100.0
    if kind in ("pie", "donut", "rose"):
        flat = rows[0] if rows else []
        body = _render_polar(x, y, title, flat, donut=kind == "donut",
                             rose=kind == "rose")
        h = 130.0
    elif kind == "scatter":
        body = _render_scatter(x, y, title, points)
    elif kind == "horizontal_bar":
        body = _render_bars(x, y, title, rows, horizontal=True, stacked=False)
        h = 14.0 + 16.0 * min(len(rows), MAX_BARS)
    elif kind in ("bar", "histogram", "grouped_bar", "stacked_bar"):
        body = _render_bars(x, y, title, rows, horizontal=False,
                            stacked=kind == "stacked_bar" or stacked)
    elif kind in ("line", "area", "timeseries", "cumulative"):
        body = _render_line_family(x, y, title, rows,
                                   area=kind in ("area", "cumulative"),
                                   cumulative=kind == "cumulative")
    elif kind in ("box_plot", "heat_matrix", "kpi_card", "ranking_list", "radar"):
        body = _render_special(x, y, title, kind, points, series)
        h = 130.0
    else:
        # 词表外 kind：不虚构图形 —— 占位卡 + unsupported（同前端占位语义）
        card = _panel_card(x, y, width, 48.0)
        body = (
            card
            + _text(x + 10.0, y + 20.0, title or "图表", size=10.0, weight="bold")
            + _text(x + 10.0, y + 36.0, "图表类型暂不支持矢量导出", size=9.0, fill="#b45309")
        )
        status = "unsupported"

    if truncated and len(rows) > 1:
        truncated += sum(max(0, len(r) - MAX_BARS) for r in rows)
    elif rows:
        truncated += max(0, len(rows[0]) - MAX_BARS)

    group = f'<g class="chrome-panel" data-kind="chart">{body}</g>' if body else ""
    return (group, status, truncated)


def chart_panel_height(chart: Any) -> float:
    """装配前的确定性高度估计（stack 布局用；与渲染几何同表）。"""
    kind, _t, points, series, _s = _parse_chart(chart)
    if kind in ("pie", "donut", "rose", "box_plot", "heat_matrix", "kpi_card",
                "radar"):
        return 130.0
    if kind == "horizontal_bar":
        return 30.0 + 16.0 * min(len(series[0]["data"]) if series else len(points),
                                 MAX_BARS)
    return 116.0 + 22.0


__all__ = ["render_chart_panel", "chart_panel_height", "CHART_WIDTH"]
