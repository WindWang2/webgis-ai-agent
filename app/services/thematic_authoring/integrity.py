"""G33 表达完整性 oracle：地图 paint 与图例 legend_spec 必须同源一致。

产品提交（thematic_authoring.service.commit_expression）从**同一**候选
legend_spec 一次投影 paint 与 legend；本 oracle 独立重算该投影并对比，
用于测试证明「只更新 legend 或只改 paint」的分叉实现必然被检出
（验收 22），并可供巡检/评审工具复用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def assert_expression_consistent(layer: Dict[str, Any]) -> List[str]:
    """返回问题清单（空 = 一致）。不抛异常，便于批量巡检。"""
    problems: List[str] = []
    legend = layer.get("legend_spec")
    if not isinstance(legend, dict) or not legend.get("type"):
        return problems  # 非专题层不在 oracle 范围
    from app.lib.cartography.thematic_spec import spec_to_paint

    expected_paint, warnings = spec_to_paint(legend)
    paint = layer.get("paint")
    actual_color = (paint or {}).get("color") if isinstance(paint, dict) else None
    if expected_paint is None:
        problems.append(
            f"legend_spec 无法投影为 paint（{warnings}）——层却声称专题表达")
        return problems
    if actual_color != expected_paint:
        problems.append(
            "paint.color 与 legend_spec 投影分叉：地图与图例不同源"
            f"（paint={_brief(actual_color)} vs legend→{_brief(expected_paint)}）")
    # legend 自身完整性：graduated 断点/颜色/标签长度一致
    if legend.get("type") == "graduated":
        breaks = legend.get("breaks") or []
        colors = legend.get("palette_colors") or []
        labels = legend.get("labels") or []
        if len(breaks) >= 2 and (len(colors) != len(breaks) - 1 or len(labels) != len(breaks) - 1):
            problems.append(
                f"legend_spec 内部分叉：breaks={len(breaks)} colors={len(colors)} "
                f"labels={len(labels)}（图例条目与类数不一致）")
    return problems


def _brief(value: Any) -> str:
    if isinstance(value, dict):
        keys = sorted(value.keys())
        return f"StyleMethod({value.get('method')}, {keys})"
    return repr(value)[:80]


__all__ = ["assert_expression_consistent"]
