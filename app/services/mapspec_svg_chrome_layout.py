"""Anchor-aware SVG chrome positioning and title rendering.

G04（#2000）：chrome 挪位以**组件自身锚点**为唯一门（placement.anchor >
position > 类型默认，resolve_component 已归一）。历史锚点保持历史坐标与
逐字节 markup；非历史锚点（用户挪动或出版求解写入的一等事实）按锚点
角比例落位。全局 ``layout.constraint_layout`` 标记不再是门 —— 求解结果
不得作为第二事实源改变后续默认导出。本模块无 compiler imports。
"""
from typing import Optional

from app.lib.cartography.render_scene import ResolvedComponent
from app.lib.cartography.svg_marginalia import render_title_block as _render_title_block
from app.services.mapspec_svg_payloads import _escape_svg_attr, _fmt_num


_HISTORICAL_CHROME_ANCHOR = {
    "title": "top-center",
    "subtitle": "top-center",
    "north_arrow": "top-right",
    "scale_bar": "bottom-right",
    "legend": "bottom-left",
    "categorical_legend": "bottom-left",
    "attribution": "bottom-left",
}


def _constraint_chrome_xy(
    comp: Optional[ResolvedComponent],
    kind: str,
    canvas_w: float,
    canvas_h: float,
    margin: float,
) -> Optional[tuple[float, float]]:
    """Moved chrome only. Historical anchors keep the existing coordinates."""
    if comp is None:
        return None
    anchor = getattr(comp, "anchor", None)
    if anchor == _HISTORICAL_CHROME_ANCHOR.get(kind) or anchor in (None, "", "none"):
        return None
    corners = {
        "top-left": (0.0, 0.0),
        "top-center": (0.5, 0.0),
        "top-right": (1.0, 0.0),
        "bottom-left": (0.0, 1.0),
        "bottom-center": (0.5, 1.0),
        "bottom-right": (1.0, 1.0),
    }
    if anchor not in corners:
        return None
    fx, fy = corners[anchor]
    x = margin + fx * (canvas_w - 2 * margin)
    y = margin + fy * (canvas_h - 2 * margin)
    if kind == "north_arrow":
        x += -30.0 if fx == 1.0 else 30.0 if fx == 0.0 else 0.0
        y += 30.0 if fy == 0.0 else -30.0
    elif kind == "scale_bar":
        if fx == 1.0:
            x -= 150.0
        y += 20.0 if fy == 0.0 else -8.0
    elif kind in ("legend", "categorical_legend"):
        if fx == 1.0:
            x -= 160.0
        y += 8.0 if fy == 0.0 else -80.0
    elif kind == "attribution":
        x += -120.0 if fx == 1.0 else 4.0
        y += 14.0 if fy == 0.0 else -14.0
    elif kind in ("title", "subtitle"):
        if fx == 0.0:
            x += 150.0
        elif fx == 1.0:
            x -= 150.0
    return (x, y)


def _render_title_chrome(
    title: Optional[ResolvedComponent],
    subtitle: Optional[ResolvedComponent],
    canvas_w: float,
    canvas_h: float,
    m: float,
    *,
    temporal_note: str = "",
) -> tuple[list[str], list[str]]:
    """Return title markup and coverage, including the active temporal frame.

    ``temporal_note``：GOAL 03 M6 时序注记（调用方从 MapSpec ``temporal``
    块装配；启用时并入副标题 —— 导出图纸自带时间证据，缺 subtitle 时
    独立成行同款排版）。
    """
    parts: list[str] = []
    rendered: list[str] = []
    if title is not None or subtitle is not None or temporal_note:
        t_text = title.text if title else ""
        s_text = subtitle.text if subtitle else ""
        if temporal_note:
            s_text = f"{s_text} — {temporal_note}" if s_text else temporal_note
        moved_title = (
            _constraint_chrome_xy(title, "title", canvas_w, canvas_h, m)
            if title is not None else None
        )
        moved_sub = (
            _constraint_chrome_xy(subtitle, "subtitle", canvas_w, canvas_h, m)
            if subtitle is not None else None
        )
        if moved_title is None and moved_sub is None:
            tx = canvas_w / 2.0
            if t_text and s_text:
                parts.append(
                    f'<g class="chrome-title" text-anchor="middle">'
                    f'<text x="{_fmt_num(tx)}" y="{_fmt_num(m + 18.0)}" font-family="sans-serif" font-size="22" font-weight="bold" fill="#0f172a">{_escape_svg_attr(t_text)}</text>'
                    f'<text x="{_fmt_num(tx)}" y="{_fmt_num(m + 38.0)}" font-family="sans-serif" font-size="12" fill="#0f172a" opacity="0.75">{_escape_svg_attr(s_text)}</text>'
                    f"</g>"
                )
            elif t_text or s_text:
                parts.append(_render_title_block(
                    canvas_w / 2.0 - 150.0, m, t_text or "", "" if t_text else s_text,
                ))
        else:
            texts = []
            if t_text:
                tx, ty = moved_title if moved_title is not None else (canvas_w / 2.0, m + 18.0)
                texts.append(
                    f'<text x="{_fmt_num(tx)}" y="{_fmt_num(ty)}" font-family="sans-serif" font-size="22" font-weight="bold" fill="#0f172a">{_escape_svg_attr(t_text)}</text>'
                )
            if s_text:
                sx, sy = moved_sub if moved_sub is not None else (canvas_w / 2.0, m + 38.0)
                texts.append(
                    f'<text x="{_fmt_num(sx)}" y="{_fmt_num(sy)}" font-family="sans-serif" font-size="12" fill="#0f172a" opacity="0.75">{_escape_svg_attr(s_text)}</text>'
                )
            if texts:
                parts.append(
                    f'<g class="chrome-title" text-anchor="middle">{"".join(texts)}</g>'
                )
        if t_text:
            rendered.append("title")
        if s_text:
            rendered.append("subtitle")
    return parts, rendered
