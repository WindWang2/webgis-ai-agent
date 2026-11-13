"""G34 图例**纸面**排版（WP06）。

G33 继续拥有 ``legend_spec`` 的条目/颜色/断点/类别值/单位**内容语义**。
本模块只做：从 canonical legend_spec 读全部条目（nodata / 范围外 / unit /
notes 一个不少）→ 受控列数/间距/样本尺寸/分组/稳定顺序 → 空间不足时
扩框 / 续排 / 明确阻断。

硬纪律：**绝不 slice 丢尾项**。所有条目必须进入产物；``entries_total``
与 ``entries_rendered`` 相等才允许导出。比例符号样本共享同一比例尺度，
不因分栏分别缩放。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from app.lib.cartography.render_scene import derive_legend_items
from app.lib.cartography.thematic_spec import normalize_legend_spec
from app.services.print_layout_authoring.fonts import wrap_text
from app.services.print_layout_authoring.units import (
    mm_to_canvas_units, mm_to_pt, pt_to_mm,
)

MAX_LEGEND_ENTRIES = 256
DEFAULT_COLUMNS = 1


def _legend_specs_of(mapspec: Dict[str, Any],
                     component_id: str = "") -> List[Dict[str, Any]]:
    """从 MapSpec 收集 canonical legend_spec（layer 面优先，layout.legend 兜底）。

    返回 ``[{layer_id, spec}]``；组件显式 ``options.layerId`` 时只取该层。
    多个图层 = 多个分组，条目一条不丢（WP06 图层分组）。
    """
    layout = mapspec.get("layout") if isinstance(mapspec, dict) else None
    wanted_layer: Optional[str] = None
    if isinstance(layout, dict):
        for c in layout.get("components") or []:
            if isinstance(c, dict) and (not component_id or c.get("id") == component_id):
                opts = c.get("options") if isinstance(c.get("options"), dict) else {}
                if isinstance(opts.get("layerId"), str):
                    wanted_layer = opts["layerId"]
                break
    out: List[Dict[str, Any]] = []
    layers = mapspec.get("layers") if isinstance(mapspec, dict) else None
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if wanted_layer and layer.get("id") != wanted_layer:
                continue
            raw: Optional[Dict[str, Any]] = None
            for key in ("legend_spec", "legendSpec"):
                if isinstance(layer.get(key), dict) and layer.get(key):
                    raw = layer[key]
                    break
            if raw is None:
                paint = layer.get("paint")
                if isinstance(paint, dict) and isinstance(paint.get("legend_spec"), dict):
                    raw = paint["legend_spec"]
            if isinstance(raw, dict):
                spec = normalize_legend_spec(raw)
                if isinstance(spec, dict):
                    out.append({"layer_id": str(layer.get("id") or ""), "spec": spec})
    if not out and isinstance(layout, dict):
        spec = layout.get("legend")
        if isinstance(spec, dict) and (spec.get("entries") or spec.get("categories")):
            normalized = normalize_legend_spec(spec)
            if isinstance(normalized, dict):
                out.append({"layer_id": "", "spec": normalized})
    return out


def _proportional_samples(spec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只读抽取比例符号样本；G33 未声明则返回空，不伪造数据。"""
    if not isinstance(spec, dict):
        return []
    raw = spec.get("proportional_symbols")
    if not isinstance(raw, list):
        raw = spec.get("proportionalSymbols")
    out: List[Dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else [])[:32]:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            continue
        if float(value) <= 0:
            continue
        out.append({
            "label": str(item.get("label") or "")[:128],
            "value": float(value),
            "color": str(item.get("color") or "#334155")[:32],
        })
    return out


def read_legend_content(mapspec: Dict[str, Any],
                        component_id: str = "",
                        group_by_layer: bool = False) -> Dict[str, Any]:
    """返回完整图例内容（含 nodata / 范围外 / unit / notes / 比例样本）。

    条目来自 ``derive_legend_items`` 这一 canonical 单源；G34 不重算断点、
    不改写类别值、不写回 ``legend_spec``。
    """
    specs = _legend_specs_of(mapspec, component_id)
    entries: List[Dict[str, Any]] = []
    titles: List[str] = []
    units: List[str] = []
    notes: List[str] = []
    samples: List[Dict[str, Any]] = []
    layer_ids: List[str] = []
    has_nodata = False
    kind = "categorical"
    out_of_range_seen = False
    for item in specs:
        spec = item["spec"]
        layer_ids.append(item["layer_id"])
        samples.extend(_proportional_samples(spec))
        model = derive_legend_items(spec)
        if model is None:
            continue
        # 范围外条目：canonical 单源在 graduated/cropped 图上会声明
        # out_of_range(_label)；显式保留为独立条目，绝不并入 note 后丢语义。
        oor = spec.get("out_of_range") or spec.get("outOfRange")
        if isinstance(oor, dict) and oor.get("color"):
            entries.append({
                "label": str(oor.get("label")
                             or spec.get("out_of_range_label")
                             or "范围外（已截断）"),
                "color": str(oor["color"]),
                "kind": "out_of_range",
                "layer_id": item["layer_id"],
            })
            out_of_range_seen = True
        titles.append(str(model.get("title") or "图例"))
        if model.get("unit"):
            units.append(str(model["unit"]))
        notes.extend(str(n) for n in (model.get("notes") or []))
        has_nodata = has_nodata or bool(model.get("hasNodata"))
        kind = str(model.get("kind") or kind)
        for e in model.get("entries") or []:
            if not isinstance(e, dict):
                continue
            label = str(e.get("label") or "")
            if group_by_layer and item["layer_id"] and len(specs) > 1:
                label = f"[{item['layer_id']}] {label}"
            entries.append({
                "label": label,
                "color": str(e.get("color") or "#888888"),
                "kind": str(e.get("kind") or "class"),
                "layer_id": item["layer_id"],
            })
    # 比例符号专属图例：canonical 条目为空时用样本生成可读标签（仍不伪造数值）。
    if not entries and samples:
        for s in samples:
            entries.append({
                "label": s["label"] or f"{s['value']:g}",
                "color": s["color"], "kind": "proportional", "layer_id": "",
            })
    labels = " ".join(e["label"] for e in entries)
    return {
        "available": bool(entries or samples),
        "title": titles[0] if titles else ("图例" if entries else ""),
        "unit": units[0] if units else "",
        "notes": notes[:4],
        "entries": entries[:MAX_LEGEND_ENTRIES],
        "entries_total": len(entries),
        "has_nodata": has_nodata or any(e["kind"] == "nodata" for e in entries),
        "has_out_of_range": out_of_range_seen or any(
            e["kind"] == "out_of_range" for e in entries),
        "proportional_samples": samples[:32],
        "source_layer_ids": layer_ids[:16],
        "kind": kind,
        "groups": len(specs),
    }


def _row_height_pt(layout: Dict[str, Any], typography: Dict[str, Any]) -> float:
    size_pt = float(typography.get("size_pt") or 9.0)
    line_pt = size_pt * float(typography.get("line_height") or 1.35)
    swatch_pt = mm_to_pt(float(layout.get("swatch_mm") or 4.0))
    symbol_pt = mm_to_pt(float(layout.get("symbol_mm") or 6.0))
    gap_pt = mm_to_pt(float(layout.get("row_gap_mm") or 1.5))
    return max(line_pt, swatch_pt, symbol_pt) + gap_pt


def layout_legend(
    content: Dict[str, Any],
    layout: Dict[str, Any],
    frame_mm: Dict[str, Any],
    typography: Dict[str, Any],
    dpi: Any,
) -> Dict[str, Any]:
    """计算真实排版计划（单位 = SVG user unit；条目零 slice）。"""
    from app.services.print_layout_authoring.units import pt_to_canvas_units

    columns = max(1, min(4, int(layout.get("columns") or DEFAULT_COLUMNS)))
    column_gap_mm = float(layout.get("column_gap_mm") or 4.0)
    swatch_mm = float(layout.get("swatch_mm") or 4.0)
    symbol_mm = float(layout.get("symbol_mm") or 6.0)
    continuation = str(layout.get("continuation") or "expand")
    frame_w = float(frame_mm.get("width") or 100.0)
    frame_h = float(frame_mm.get("height") or 20.0)

    entries: List[Dict[str, Any]] = list(content.get("entries") or [])
    total = len(entries)
    col_w_mm = (frame_w - column_gap_mm * (columns - 1)) / columns
    if col_w_mm <= 1.0:
        return {
            "status": "blocked", "code": "PRINT_LEGEND_COLUMNS_TOO_NARROW",
            "entries_total": total, "entries_rendered": 0, "columns": columns,
            "boxes": [], "notes": content.get("notes") or [],
        }

    size_pt = float(typography.get("size_pt") or 9.0)
    title_layout = wrap_text(
        str(content.get("title") or "图例"),
        font_id=str(typography.get("font_id") or "noto-sans-sc-embedded"),
        size_pt=size_pt + 1.0,
        line_height=float(typography.get("line_height") or 1.35),
        box_width_mm=frame_w, box_height_mm=frame_h,
        overflow_policy="expand",
    )
    header_pt = mm_to_pt(3.0) + len(title_layout["lines"]) * title_layout["line_height_pt"]
    if str(content.get("unit") or ""):
        header_pt += mm_to_pt(3.5)
    notes = list(content.get("notes") or [])
    footer_pt = len(notes) * mm_to_pt(3.0)
    row_pt = _row_height_pt(layout, typography)
    frame_pt = mm_to_pt(frame_h)
    capacity = max(1, int(math.floor((frame_pt - header_pt - footer_pt) / row_pt)))
    rows_per_column = max(1, int(math.ceil(total / columns))) if total else 1
    needed_pt = header_pt + rows_per_column * row_pt + footer_pt
    needed_mm = pt_to_mm(needed_pt)

    # 比例符号：全表共享一个比例尺度（不因列分别缩放）。
    samples = list(content.get("proportional_samples") or [])
    max_value = max((float(s["value"]) for s in samples), default=0.0)
    max_radius_mm = symbol_mm / 2.0
    proportional: List[Dict[str, Any]] = []
    for s in samples:
        ratio = math.sqrt(s["value"] / max_value) if max_value > 0 else 0.0
        proportional.append({
            "label": s["label"], "value": s["value"], "color": s["color"],
            "radius_mm": round(max_radius_mm * ratio, 6),
            "radius_units": round(mm_to_canvas_units(max_radius_mm * ratio, dpi), 6),
            "scale": "area_sqrt_shared",
        })
    # 普通色块尺寸同样全表共享
    swatch_units = mm_to_canvas_units(swatch_mm, dpi)
    symbol_units = mm_to_canvas_units(symbol_mm, dpi)
    row_units = pt_to_canvas_units(row_pt, dpi)
    header_units = pt_to_canvas_units(header_pt, dpi)
    footer_units = pt_to_canvas_units(footer_pt, dpi)
    col_units = mm_to_canvas_units(col_w_mm, dpi)
    gap_units = mm_to_canvas_units(column_gap_mm, dpi)

    def _box(start: int, count: int, box_index: int, height_mm: float) -> Dict[str, Any]:
        return {
            "box_index": box_index,
            "entry_start": start,
            "entry_count": count,
            "columns": columns,
            "column_width_units": round(col_units, 6),
            "column_gap_units": round(gap_units, 6),
            "row_units": round(row_units, 6),
            "header_units": round(header_units, 6),
            "footer_units": round(footer_units, 6),
            "height_units": round(mm_to_canvas_units(height_mm, dpi), 6),
            "height_mm": round(height_mm, 6),
            "swatch_units": round(swatch_units, 6),
            "symbol_units": round(symbol_units, 6),
            "proportional": proportional,
        }

    if needed_mm <= frame_h + 1e-6:
        status = "ok"
        boxes = [_box(0, total, 0, frame_h)]
    elif continuation == "block":
        return {
            "status": "blocked", "code": "PRINT_LEGEND_OVERFLOW_BLOCKED",
            "entries_total": total, "entries_rendered": 0, "columns": columns,
            "needed_height_mm": round(needed_mm, 4),
            "frame_height_mm": frame_h, "boxes": [], "proportional": proportional,
            "notes": notes,
        }
    elif continuation == "expand":
        # 扩框：必须仍在纸面内（调用方把新的高度回写组件框并复核安全区）。
        status = "expanded"
        boxes = [_box(0, total, 0, needed_mm)]
    else:  # continue：按框容量切成多个整饰续排盒，绝不 slice
        per_box = max(1, capacity * columns)
        boxes = []
        start = 0
        idx = 0
        while start < total:
            count = min(per_box, total - start)
            boxes.append(_box(start, count, idx, frame_h))
            start += count
            idx += 1
            if idx > 16:  # 有界续排，超出即明确阻断
                return {
                    "status": "blocked", "code": "PRINT_LEGEND_CONTINUATION_BUDGET",
                    "entries_total": total, "entries_rendered": 0,
                    "columns": columns, "boxes": [], "proportional": proportional,
                    "notes": notes,
                }
        status = "continued"
    return {
        "status": status,
        "code": "" if status != "blocked" else "PRINT_LEGEND_OVERFLOW_BLOCKED",
        "entries_total": total,
        "entries_rendered": sum(b["entry_count"] for b in boxes),
        "complete": sum(b["entry_count"] for b in boxes) == total,
        "columns": columns,
        "rows_per_column": rows_per_column,
        "needed_height_mm": round(needed_mm, 4),
        "frame_height_mm": frame_h,
        "boxes": boxes,
        "entries": entries,
        "title": content.get("title") or "图例",
        "title_lines": title_layout["lines"],
        "unit": content.get("unit") or "",
        "notes": notes,
        "has_nodata": bool(content.get("has_nodata")),
        "has_out_of_range": bool(content.get("has_out_of_range")),
        "proportional": proportional,
        "group_by_layer": bool(layout.get("group_by_layer")),
    }
