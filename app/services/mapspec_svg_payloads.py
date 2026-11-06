"""Pure SVG value formatting and component-payload interpretation helpers.

Kept apart from projection and drawing so payload bounds and escaping can be
verified without assembling a full map export.
"""
import html as _html
import logging
import math as _math
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.svg_marginalia import (
    ANNOTATION_MAX_LINES as _ANNOTATION_MAX_LINES,
)

logger = logging.getLogger(__name__)


def _escape_svg_attr(value: Any) -> str:
    """Escape a value for safe interpolation into an SVG attribute.

    Mirrors the JS twin's escapeSvgAttr: MapSpec paint values flow into
    attributes like fill="...", so a crafted value containing `"` could break
    out of the attribute (attribute-injection / XSS). quote=True escapes both
    double and single quotes.
    """
    return _html.escape(str(value), quote=True)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float, returning default if invalid, None, NaN, or Inf."""
    try:
        if val is None:
            return default
        f = float(val)
        return f if _math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _fmt_num(v: Any) -> str:
    """Format a number for byte-identical parity with the TS twin.

    Both twins reduce every numeric SVG attribute to a canonical "minimal" form:
    round to 2 decimals, then strip trailing zeros and a dangling decimal point.
    So ``50.0`` -> ``"50"``, ``1.0`` -> ``"1"``, ``0.6`` -> ``"0.6"``,
    ``20.83`` -> ``"20.83"``. Mirrors ``fmtNum`` in the TS twin exactly
    (``f"{v:.2f}".rstrip('0').rstrip('.')``). The parity tests pin the expected
    strings, so any divergence fails the suite.
    """
    try:
        v_float = float(v)
        if not _math.isfinite(v_float):
            return "0"
        s = f"{v_float:.2f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    except Exception:
        return "0"


def _table_height(rows: List[Any], total: int) -> float:
    shown = min(len(rows), 8)
    tail = 1 if total > shown else 0
    return 32.0 + 20.0 + (shown + 1) * 14.0 + (12.0 if tail else 0.0)


def _panel_payload(comp: Any, key: str) -> Any:
    """组件 options 的协议容器（仅 dict/list 有效载荷）。"""
    v = (comp.options or {}).get(key)
    return v if isinstance(v, (dict, list)) and v else None


def _parse_stats_rows(comp: Any) -> List[str]:
    """statistics_panel options.stats → "label：value unit" 行（≤12）。"""
    stats = _panel_payload(comp, "stats")
    if not isinstance(stats, dict):
        return []
    rows: List[str] = []
    for item in stats.get("items") or []:
        if not isinstance(item, dict) or len(rows) >= 12:
            break
        label = item.get("label")
        value = item.get("value")
        unit = item.get("unit")
        if label is None and value is None:
            continue
        parts = []
        if isinstance(label, str) and label:
            parts.append(label)
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            parts.append(f"{value}")
        if isinstance(unit, str) and unit:
            parts.append(unit)
        if parts:
            rows.append("：".join(parts[0:1]) + (
                f" {parts[1]}" if len(parts) > 1 else "") + (
                f" {parts[2]}" if len(parts) > 2 else ""))
    return rows


def _parse_disclosure(comp: Any) -> Tuple[str, List[str], bool, Tuple[int, ...]]:
    """披露族 → (title, rows, accent, strike_rows)。空 rows = 面板缺席。"""
    opts = comp.options or {}
    if comp.type == "methodology_note":
        warnings = opts.get("warnings")
        rows: List[str] = []
        if isinstance(warnings, list):
            for item in warnings:
                if len(rows) >= 16:
                    break
                if isinstance(item, str) and item.strip():
                    rows.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        code = item.get("code")
                        prefix = f"{code} " if isinstance(code, str) and code else ""
                        rows.append(f"{prefix}{text}")
        return ("方法论披露", rows, True, ())
    if comp.type == "uncertainty_panel":
        unc = opts.get("uncertainty")
        if not isinstance(unc, dict):
            return ("", [], False, ())
        rows = []
        for item in unc.get("items") or []:
            if len(rows) >= 16:
                break
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            if not (isinstance(label, str) and label.strip()):
                continue
            kind = item.get("kind")
            kind_s = f"{kind} · " if isinstance(kind, str) and kind else ""
            detail = item.get("detail")
            detail_s = f"：{detail}" if isinstance(detail, str) and detail else ""
            rows.append(f"{kind_s}{label}{detail_s}")
        interval = unc.get("interval")
        if isinstance(interval, (list, tuple)) and len(interval) >= 2 and len(rows) < 16:
            rows.append(f"置信区间：[{interval[0]}, {interval[1]}]")
        confidence = unc.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and len(rows) < 16:
            rows.append(f"置信度：{confidence:g}")
        for key in ("sampleNote", "sample_limit"):
            note = unc.get(key)
            if isinstance(note, str) and note.strip() and len(rows) < 16:
                rows.append(note)
        return ("不确定性", rows, False, ())
    if comp.type == "decision_panel":
        decision = opts.get("decision")
        title = "决策"
        if isinstance(decision, dict):
            method = decision.get("method")
            if isinstance(method, str) and method.strip():
                title = f"决策（{method}）"
        rows = []
        strikes: List[int] = []
        weight_source = decision.get("weightSource") if isinstance(decision, dict) else None
        if isinstance(weight_source, str) and weight_source.strip():
            rows.append(f"权重来源：{weight_source}")
        row_list = (
            decision.get("rows") if isinstance(decision, dict) else None
        ) or (opts.get("rows") if isinstance(opts.get("rows"), list) else None) or []
        seq = 0
        for item in row_list:
            if not isinstance(item, dict) or len(rows) >= 16:
                break
            name = item.get("name")
            if not (isinstance(name, str) and name.strip()):
                continue
            seq += 1
            rank = item.get("rank")
            rank_s = f"{rank}. " if isinstance(rank, (int, float)) else f"{seq}. "
            score = item.get("score")
            score_s = f" — {score}" if isinstance(score, (int, float, str)) and not isinstance(score, bool) else ""
            vetoed = item.get("basis") == "vetoed" or item.get("vetoed") is True
            if vetoed:
                strikes.append(len(rows))
            rows.append(f"{rank_s}{name}{score_s}")
        vetoes = decision.get("vetoes") if isinstance(decision, dict) else None
        if isinstance(vetoes, list):
            vs = [v for v in vetoes if isinstance(v, str) and v.strip()]
            if vs:
                rows.append("硬约束否决：")
                for v in vs:
                    if len(rows) >= 16:
                        break
                    rows.append(f"· {v}")
        return (title, rows, False, tuple(strikes))
    return ("", [], False, ())


def _parse_scene_disclosure(
    mapspec: Dict[str, Any], comp: Any,
) -> Tuple[str, List[str]]:
    """G3D05：scene_disclosure → (title, rows)，内容派生自 spec.scene。

    矢量孪生通道语义：本通道渲染的是**平面 2D 投影**——scene.mode=3d
    时如实披露"矢量导出已降级"；比例尺在本通道可信（无透视），与
    canvas 透视截图通道（比例尺仅供参考）语义区分、不矛盾。
    无 scene 块时返回空 rows（面板缺席，不虚构场景状态）。
    """
    scene = mapspec.get("scene") if isinstance(mapspec, dict) else None
    if not isinstance(scene, dict):
        return ("场景披露", [])
    rows: List[str] = []
    mode = scene.get("mode")
    if mode == "3d":
        rows.append("矢量导出为 2D 投影（三维透视不可矢量化，已降级）")
        rows.append("原场景模式：3D 透视")
    elif mode == "2.5d":
        rows.append("矢量导出为 2D 投影（2.5D 场景已按平面表达）")
        rows.append("原场景模式：2.5D")
    else:
        rows.append("场景模式：2D")
    terrain = scene.get("terrain")
    if isinstance(terrain, dict):
        exag = terrain.get("exaggeration")
        if isinstance(exag, (int, float)) and not isinstance(exag, bool):
            exag_s = f"地形夸张系数：{exag:g}×"
            if float(exag) > 1.0:
                exag_s += "（垂直比例夸张，非真实坡度）"
            rows.append(exag_s)
        v_unit = terrain.get("vertical_unit")
        if isinstance(v_unit, str) and v_unit:
            rows.append(f"垂直单位：{v_unit}")
        elev_ref = terrain.get("elevation_ref")
        if isinstance(elev_ref, str) and elev_ref:
            rows.append(f"高程基准：{elev_ref}")
        elif rows and rows[0].startswith("矢量导出"):
            rows.append("高程基准：未声明")
    opts = comp.options or {}
    for note in (opts.get("extraNotes") or [])[:4]:
        if isinstance(note, str) and note.strip():
            rows.append(note)
    return ("场景披露", rows[:16])


def _profile_to_chart(comp: Any) -> Optional[Dict[str, Any]]:
    """G3D05：elevation_profile options.points → 矢量图表链 chart dict。

    距离（m）格式化为 km 标签、高程为值；剖面本质是折线图，复用
    ``render_chart_panel``（与 chart_panel 同渲染器，无第二套绘制）。
    points 缺席/非法 → None（装配层记 profile_ref_unavailable）。
    """
    opts = comp.options or {}
    points = opts.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return None
    chart_points: List[Dict[str, Any]] = []
    for p in points:
        if not isinstance(p, dict):
            return None
        d, e = p.get("distance_m"), p.get("elevation_m")
        if not isinstance(d, (int, float)) or not isinstance(e, (int, float)):
            return None
        if isinstance(d, bool) or isinstance(e, bool):
            return None
        chart_points.append({
            "name": f"{float(d) / 1000.0:.2f}km"
            if float(d) >= 1000.0 else f"{float(d):.0f}m",
            "value": float(e),
        })
    unit = opts.get("unit") if isinstance(opts.get("unit"), str) else "m"
    title = "高程剖面"
    datum = opts.get("verticalDatum")
    if isinstance(datum, str) and datum:
        title += f"（基准：{datum}）"
    return {
        "type": "line",
        "title": title,
        "points": chart_points,
        "yAxisLabel": f"高程（{unit}）",
        "xAxisLabel": "线路里程",
    }


def _parse_annotation(comp: Any) -> Tuple[List[str], List[Tuple[float, float]]]:
    """annotation → (静态行, callout 锚点列表)。行 ≤8、callout ≤8。"""
    opts = comp.options or {}
    lines: List[str] = []
    if comp.text:
        lines = [ln for ln in comp.text.split("\n") if ln.strip()][
            :_ANNOTATION_MAX_LINES]
    anchors: List[Tuple[float, float]] = []
    coord = opts.get("anchorCoordinate")
    if isinstance(coord, dict):
        lng, lat = coord.get("lng", coord.get("lon")), coord.get("lat")
        try:
            anchors.append((float(lng), float(lat)))
        except (TypeError, ValueError):
            logger.debug("SVG annotation omitted an invalid object anchor")
    elif isinstance(coord, (list, tuple)) and len(coord) >= 2:
        try:
            anchors.append((float(coord[0]), float(coord[1])))
        except (TypeError, ValueError):
            logger.debug("SVG annotation omitted an invalid coordinate anchor")
    return lines, anchors[:_ANNOTATION_MAX_LINES]


def _colorbar_spec(mapspec: Dict[str, Any], comp: Any) -> Optional[Dict[str, Any]]:
    """colorbar 数据源：绑定 layer 的 continuous legend_spec 优先，options 内联兜底
    （前端 ``el.legendSpec`` 同语义；E-5：无 palette = 不可绘制）。"""
    layer_id = comp.layer_id or (comp.options or {}).get("layerId")
    if isinstance(layer_id, str) and layer_id:
        for layer in mapspec.get("layers", []) or []:
            if isinstance(layer, dict) and layer.get("id") == layer_id:
                spec = layer.get("legend_spec")
                if isinstance(spec, dict) and spec.get("type") == "continuous":
                    return spec
    opts = comp.options or {}
    if isinstance(opts.get("palette_colors"), list) and opts["palette_colors"]:
        inline = {k: opts[k] for k in ("palette_colors", "min", "max", "unit",
                                       "field", "nodata", "title")
                  if opts.get(k) is not None}
        inline["title"] = inline.get("title", comp.text or "")
        return inline
    return None


def _table_payload(comp: Any) -> Optional[Tuple[List[str], List[Any], int]]:
    """table_panel 内联快照 → (columns, rows, total)。缺内联（ref 未水合）→ None。"""
    table = _panel_payload(comp, "table")
    if isinstance(table, dict):
        columns = [str(c) for c in (table.get("columns") or []) if c]
        rows = [r for r in (table.get("rows") or []) if isinstance(r, list)]
        if rows:
            return (columns, rows, len(rows))
    if isinstance(table, list) and table:
        first = table[0]
        if isinstance(first, dict):
            columns = [str(k) for k in first.keys()][:6]
            rows = [[row.get(k) for k in first.keys()] for row in table
                    if isinstance(row, dict)]
            return (columns, rows, len(rows))
        rows = [r if isinstance(r, list) else [r] for r in table]
        return ([], rows, len(rows))
    return None
