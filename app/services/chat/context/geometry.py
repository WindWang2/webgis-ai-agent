"""BBox and geometry relations for chat context construction."""
from __future__ import annotations

def _bbox_intersects(a: list[float], b: list[float]) -> bool:
    """两个 [w,s,e,n] 是否相交（含边界接触）。"""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _bbox_contains(outer: list[float], inner: list[float]) -> bool:
    """outer 是否完全包住 inner。"""
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def viewport_layer_relation(viewport_bounds: list[float] | None, layer_bbox: list[float] | None) -> str | None:
    """判断图层 bbox 相对当前视口的位置关系。

    返回 4 种之一：
    - "在视口内"  — 视口完全包住图层
    - "局部相交"  — 有交集但视口未完全包住
    - "在视口外"  — 无交集
    - None       — 任一边界缺失，无法判断
    """
    if not (isinstance(viewport_bounds, list) and len(viewport_bounds) >= 4):
        return None
    if not (isinstance(layer_bbox, list) and len(layer_bbox) >= 4):
        return None
    v = [float(x) for x in viewport_bounds[:4]]
    l = [float(x) for x in layer_bbox[:4]]
    if not _bbox_intersects(v, l):
        return "在视口外"
    if _bbox_contains(v, l):
        return "在视口内"
    return "局部相交"
