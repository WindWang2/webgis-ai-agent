"""Bounded SVG label expression evaluation and authored label expansion."""
import math as _math
from typing import Any, Callable, List, Tuple

def _svg_label_text(value: Any, props: dict, depth: int = 0) -> str | None:
    if depth > 16:
        return None
    if isinstance(value, str):
        import re
        return re.sub(r"\{([^{}]+)\}", lambda match: str(props.get(match[1], "")), value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if not isinstance(value, list) or not value:
        return None
    op, *args = value
    if op == "get" and args and isinstance(args[0], str):
        found = props.get(args[0])
        if isinstance(found, str):
            return found
        return _svg_label_text(found, {}, depth + 1) if isinstance(found, (int, float, bool)) else None
    if op in ("to-string", "literal") and args:
        return _svg_label_text(args[0], props, depth + 1)
    if op == "coalesce":
        return next((text for arg in args if (text := _svg_label_text(arg, props, depth + 1)) is not None), None)
    if op in ("concat", "format"):
        parts = [_svg_label_text(arg, props, depth + 1) for arg in (args[::2] if op == "format" else args)]
        return None if any(part is None for part in parts) else "".join(parts)
    return None


def _svg_layers(layers: list) -> list:
    out = []
    for layer in layers:
        out.append(layer)
        if not isinstance(layer, dict):
            continue
        label = layer.get("label")
        if not isinstance(label, dict) or not label.get("field") or layer.get("type") in ("raster", "heatmap", "background", "hillshade", "symbol", "text"):
            continue
        # G16（R1-P1）：__label_of 记录宿主原始层 id —— 障碍身份豁免与
        # 覆写失效披露都按原始层寻址（合成层 id ≠ 障碍层 id 曾致自身墨迹
        # 豁免失效）。该键只存在于导出函数的运行时层视图，不落盘。
        out.append({**layer, "id": f"{layer.get('id', '')}-label", "type": "symbol",
                    "__label_of": layer.get("id"),
                    "layout": {**(layer.get("layout") or {}), "text-field": ["get", label["field"]], "text-size": label.get("size", 12)},
                    "paint": {"text-color": label.get("color", "#000000"), "text-halo-color": label.get("haloColor", "#ffffff"), "text-halo-width": label.get("haloWidth", 1)}})
    return out




def valid_geographic_coordinates(value: Any) -> bool:
    """Validate a bounded geometry without recursive depth or vertex amplification."""
    import math
    stack = [(iter([value]), 0)]
    visited = 0
    while stack:
        visited += 1
        if visited > 200_000:
            return False
        iterator, depth = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        if not isinstance(item, (list, tuple)):
            return False
        if item and isinstance(item[0], (int, float)):
            if not (len(item) >= 2 and all(isinstance(coord, (int, float)) and not isinstance(coord, bool) and abs(coord) <= 180 and math.isfinite(coord) for coord in item[:2]) and abs(item[0]) <= 180 and abs(item[1]) <= 90):
                return False
        else:
            if depth >= 32:
                return False
            stack.append((iter(item), depth + 1))
    return True


#: 线标注 repeat 锚点上限（对齐 MapLibre symbol-spacing 多贴的有界子集）。
_LINE_REPEAT_MAX_ANCHORS = 4
#: repeat 锚点计算的参与坐标帽（R2 评审：单条百万坐标线的 project 逐点
#: 循环不可中断 —— 确定性跨步采样到帽内再算锚点，语义是等距近似的
#: 有界化，不改变正常线的输出）。
_LINE_REPEAT_MAX_POINTS = 20_000


def _line_repeat_anchors(
    coords: Any, project: Callable[[Any], Tuple[str, str]], _dpi_spacing: float
) -> List[Tuple[float, float, float]]:
    """symbol-placement=line 的等距 repeat 锚点（≤4，TS 孪生同构）。

    语义（对齐 live MapLibre 沿线多贴的确定性子集）：首个锚点在线弧长
    中点，随后向两侧按 ``spacing``（= MapLibre symbol-spacing 缺省 250px ×
    dpi_scale）扩展，超出线长即停；每个锚点带所在段的方位角（keep-upright
    由求解器负责）。总弧长 < 2×spacing 时只有中点单锚（与旧行为一致）。
    project() 返回 fmt_num 字符串 —— 全程 float() 还原。
    坐标帽采样：首点恒保留；尾点在一般 N 下落在两个采样格之间而被略去
    （弧长轻微低估，确定性且双孪生同式 —— R3 评审措辞修正）。
    """
    if not isinstance(coords, list) or len(coords) < 2 or _dpi_spacing <= 0:
        return []
    # 坐标帽：超长线确定性跨步采样（首/尾点恒保留），project 调用数有界。
    coords_list: List[Any] = coords
    if len(coords_list) > _LINE_REPEAT_MAX_POINTS:
        step = len(coords_list) / _LINE_REPEAT_MAX_POINTS
        coords_list = [coords_list[min(len(coords_list) - 1, int(k * step))]
                       for k in range(_LINE_REPEAT_MAX_POINTS)]
    pts: List[Tuple[float, float]] = []
    for c in coords_list:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            try:
                px, py = project(c)
                pts.append((float(px), float(py)))
            except Exception:
                return []
    if len(pts) < 2:
        return []
    seg_lens: List[float] = []
    total = 0.0
    for i in range(len(pts) - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        seg_len = _math.hypot(dx, dy)
        seg_lens.append(seg_len)
        total += seg_len
    if total <= 0 or total < 2 * _dpi_spacing:
        return []

    def _anchor_at(s: float) -> Tuple[float, float, float]:
        acc = 0.0
        for i, seg_len in enumerate(seg_lens):
            if acc + seg_len >= s or i == len(seg_lens) - 1:
                t = 0.0 if seg_len <= 0 else (s - acc) / seg_len
                t = min(1.0, max(0.0, t))
                x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
                y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
                ang = _math.degrees(_math.atan2(
                    pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0]))
                return (x, y, ang)
            acc += seg_len
        return pts[-1][0], pts[-1][1], 0.0  # pragma: no cover - 数值边界

    mid = total / 2.0
    positions = [mid]
    k = 1
    while len(positions) < _LINE_REPEAT_MAX_ANCHORS:
        added = False
        if mid + k * _dpi_spacing <= total:
            positions.append(mid + k * _dpi_spacing)
            added = True
        if len(positions) < _LINE_REPEAT_MAX_ANCHORS and mid - k * _dpi_spacing >= 0:
            positions.append(mid - k * _dpi_spacing)
            added = True
        if not added:
            break
        k += 1
    positions.sort()
    return [_anchor_at(s) for s in positions]
