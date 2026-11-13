"""Anchor-aware SVG chrome positioning and title rendering.

G04（#2000）：chrome 挪位以**组件自身锚点**为唯一门（placement.anchor >
position > 类型默认，resolve_component 已归一）。历史锚点保持历史坐标与
逐字节 markup；非历史锚点（用户挪动或出版求解写入的一等事实）按锚点
角比例落位。全局 ``layout.constraint_layout`` 标记不再是门 —— 求解结果
不得作为第二事实源改变后续默认导出。本模块无 compiler imports。

G34 additive：``render_authored_text_block`` 消费纸面作者计划的毫米框 +
真实字体度量换行结果，产出真正的多行 ``<text>``。仅当 draw_plan 在场时
调用；legacy 路径逐字节不变。
"""
from typing import Any, Dict, Optional

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


# ── G34：纸面作者文字块（真实毫米框 + 真实度量换行）─────────────────────

def render_authored_text_block(draw: Dict[str, Any]) -> str:
    """作者文字组件 → 真实 SVG 多行文本。

    ``draw`` = ``build_draw_plan`` 的单个 component 项；几何单位已是 SVG
    viewBox user unit（由 mm→unit 单一 corpus 换算），字号单位由 pt→unit
    换算，因此 96/150/300 DPI 下物理毫米与 pt 恒定。

    ``data-g34-*`` 属性是产物 oracle 的独立回读面（不是 CSS truncate，
    所有行都真实输出；``lines`` 与 ``data-g34-lines`` 可逐项核对）。
    """
    box = draw.get("box") or {}
    text = draw.get("text") or {}
    lines = list(text.get("lines") or [])
    if not lines:
        return ""
    status = str(text.get("status") or "ok")
    if status == "blocked":
        # 明确阻断：service 层应已拒绝导出；渲染面绝不静默截字。
        return ""
    x = float(box.get("x") or 0.0)
    y = float(box.get("y") or 0.0)
    w = float(box.get("width") or 0.0)
    h = float(box.get("height") or 0.0)
    size = float(draw.get("font_size_units") or 10.0)
    line_units = float(draw.get("line_units") or size * 1.35)
    family = str(draw.get("font_family") or "sans-serif")
    weight = str(draw.get("font_weight") or "normal")
    style = str(draw.get("font_style") or "normal")
    color = str(draw.get("color") or "#1e293b")
    align = str(draw.get("align") or "left")
    spacing = float(draw.get("letter_spacing_units") or 0.0)
    anchor = "start" if align == "left" else "middle" if align == "center" else "end"
    tx = x if align == "left" else (x + w / 2.0) if align == "center" else (x + w)
    frame = draw.get("frame_mm") or {}
    parts = [
        f'<g class="chrome-print-text" '
        f'data-g34-id="{_escape_svg_attr(str(draw.get("id") or ""))}" '
        f'data-g34-kind="{_escape_svg_attr(str(draw.get("kind") or ""))}" '
        f'data-g34-x-mm="{_fmt_num(float(frame.get("x") or 0.0))}" '
        f'data-g34-y-mm="{_fmt_num(float(frame.get("y") or 0.0))}" '
        f'data-g34-width-mm="{_fmt_num(float(frame.get("width") or 0.0))}" '
        f'data-g34-height-mm="{_fmt_num(float(frame.get("height") or 0.0))}" '
        f'data-g34-lines="{len(lines)}" '
        f'data-g34-overflow="{"true" if text.get("overflow") else "false"}" '
        f'data-g34-font="{_escape_svg_attr(family)}" '
        f'data-g34-size-pt="{_fmt_num(float(text.get("size_pt") or 0.0))}" '
        f'text-anchor="{anchor}">'
    ]
    for i, line in enumerate(lines):
        baseline = y + size + i * line_units
        parts.append(
            f'<text x="{_fmt_num(tx)}" y="{_fmt_num(baseline)}" '
            f'font-family="{_escape_svg_attr(family)}" '
            f'font-size="{_fmt_num(size)}" font-weight="{_escape_svg_attr(weight)}" '
            f'font-style="{_escape_svg_attr(style)}" '
            f'letter-spacing="{_fmt_num(spacing)}" '
            f'fill="{_escape_svg_attr(color)}" '
            f'data-g34-line="{i + 1}">{_escape_svg_attr(line)}</text>'
        )
    parts.append("</g>")
    return "".join(parts)
