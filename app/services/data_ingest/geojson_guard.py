"""恶意/病态 GeoJSON 输入守卫（G03 GD-09 / ADR-0286 D7）。

背景（BASELINE 断链 9）：IngestPipeline 的既有防线只有载荷大小帽 + FC
形状检查——NaN/±Infinity 坐标、超深嵌套结构（解析栈/递归耗尽）、超大
属性字符串（内存放大）可以静默入库并污染下游（mvt/stats/分析）。

守卫语义（类型化拒绝，绝不静默吞）：
- 非有限坐标（NaN/±Inf）→ ``INVALID_COORDINATES_NONFINITE``；
- 嵌套深度 > 64（属性树 **与** 坐标树共用上限）→ ``STRUCTURE_TOO_DEEP``；
- 单属性字符串 > 256KB → ``PROPERTY_STRING_OVERSIZED``；
- 预算耗尽（病态宽结构）→ ``GUARD_BUDGET_EXCEEDED``——**类型化拒绝**，
  绝不静默跳过（评审 F1：LIFO 遍历序 + 预算耗尽静默 return 会把恶意
  值藏在垫料之后绕过全部检查；修复后耗尽即拒绝）。

性能纪律：O(可达节点) 单遍；坐标扫描与属性扫描**独立预算**（评审 F1：
共享预算会让大属性树吃掉坐标检查的配额）。坐标预算按载荷规模放
（真实重几何——海岸线/边界——常见数万顶点，不得误拒）：每要素
1,000,000 步 ≈ 30 万顶点；属性预算 20,000 步。合法大 FC（5 万要素 ×
普通属性）秒级（见 test_guard_bounded_on_large_valid_fc）。
拒绝发生在 store 之前——无 ref 铸造、无补偿回滚对象。
"""
from __future__ import annotations

import math
from typing import Any

#: 类型化错误码（机读；dispatch/质量面可解释）。
CODE_NONFINITE = "INVALID_COORDINATES_NONFINITE"
CODE_TOO_DEEP = "STRUCTURE_TOO_DEEP"
CODE_OVERSIZED = "PROPERTY_STRING_OVERSIZED"
CODE_BUDGET = "GUARD_BUDGET_EXCEEDED"

#: 嵌套深度帽（属性树 + geometry 坐标树共用；正常 GeoJSON 远低于此：
#: MultiPolygon 坐标嵌套 4 层、属性常规 ≤10 层）。
MAX_NESTING_DEPTH = 64
#: 单属性字符串字节帽（256KB；正常名称/地址/备注远低于此）。
MAX_PROPERTY_STRING_BYTES = 256 * 1024
#: 单要素坐标扫描步数帽（≈33 万顶点；真实重几何不得误拒——评审 F1）。
MAX_COORD_STEPS_PER_FEATURE = 1_000_000
#: 单要素属性树扫描步数帽（病态宽属性结构 → 类型化拒绝，不静默跳过）。
MAX_PROP_STEPS_PER_FEATURE = 20_000


class GeojsonGuardError(Exception):
    """守卫拒绝（类型化；``code`` 是机读码，message 用户可解释）。"""

    def __init__(self, code: str, message: str, *, feature_index: int = -1):
        super().__init__(message)
        self.code = code
        self.feature_index = feature_index


def _check_coordinates(node: Any) -> None:
    """坐标树：非有限值 + 嵌套深度检查（迭代 DFS；独立预算，耗尽即拒）。

    坐标树的合法形态是数字叶 + 少量 list 嵌套（Point 2 层 /
    MultiPolygon 4 层）——深度 >64 的坐标树是病态结构，直接 TOO_DEEP。
    """
    budget = MAX_COORD_STEPS_PER_FEATURE
    # 栈元素：(node, depth)
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        budget -= 1
        if budget < 0:
            raise GeojsonGuardError(
                CODE_BUDGET,
                f"坐标结构超过扫描预算（≈{MAX_COORD_STEPS_PER_FEATURE} 节点）"
                "——病态宽几何，类型化拒绝（绝不静默跳过检查）",
            )
        if depth > MAX_NESTING_DEPTH:
            raise GeojsonGuardError(
                CODE_TOO_DEEP,
                f"坐标嵌套深度超过 {MAX_NESTING_DEPTH}（疑似恶意载荷）",
            )
        if isinstance(current, bool):
            continue
        if isinstance(current, (int, float)):
            if not math.isfinite(float(current)):
                raise GeojsonGuardError(
                    CODE_NONFINITE,
                    "坐标包含非有限数值（NaN/±Inf）——不得入库",
                )
        elif isinstance(current, (list, tuple)):
            for v in current:
                stack.append((v, depth + 1))
        # dict 在坐标树中不合法；深度/尺寸判据已覆盖，不在此重复。


def _check_properties(node: Any) -> None:
    """属性树：深度 + 超大字符串检查（迭代 DFS；独立预算，耗尽即拒）。"""
    budget = MAX_PROP_STEPS_PER_FEATURE
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        budget -= 1
        if budget < 0:
            raise GeojsonGuardError(
                CODE_BUDGET,
                f"属性结构超过扫描预算（≈{MAX_PROP_STEPS_PER_FEATURE} 节点）"
                "——病态宽结构，类型化拒绝（绝不静默跳过检查）",
            )
        if depth > MAX_NESTING_DEPTH:
            raise GeojsonGuardError(
                CODE_TOO_DEEP,
                f"结构嵌套深度超过 {MAX_NESTING_DEPTH}（疑似恶意载荷）",
            )
        if isinstance(current, str):
            if len(current.encode("utf-8", errors="ignore")) > (
                MAX_PROPERTY_STRING_BYTES
            ):
                raise GeojsonGuardError(
                    CODE_OVERSIZED,
                    f"属性字符串超过 {MAX_PROPERTY_STRING_BYTES // 1024}KB"
                    "（疑似载荷放大攻击）",
                )
        elif isinstance(current, dict):
            for v in current.values():
                stack.append((v, depth + 1))
        elif isinstance(current, (list, tuple)):
            for v in current:
                stack.append((v, depth + 1))


def validate_feature_collection(fc: Any) -> None:
    """FC 形状守卫入口（O(features) 有界；拒绝 → GeojsonGuardError）。

    只做**结构性/数值性**检查，不做语义判断（CRS/bbox 诚实性由
    profiler/quality 既有面负责）。坐标与属性扫描独立预算，任何一路
    耗尽都是类型化拒绝——不存在静默跳过的路径。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return  # 形状判定是 detect 步的既有职责（UNSUPPORTED_SHAPE）
    features = fc.get("features")
    if not isinstance(features, list):
        return
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        geom = feature.get("geometry")
        if isinstance(geom, dict):
            coords = geom.get("coordinates")
            if coords is not None:
                try:
                    _check_coordinates(coords)
                except GeojsonGuardError as e:
                    e.feature_index = idx
                    raise
        props = feature.get("properties")
        if props is not None:
            try:
                _check_properties(props)
            except GeojsonGuardError as e:
                e.feature_index = idx
                raise


__all__ = [
    "GeojsonGuardError",
    "CODE_NONFINITE",
    "CODE_TOO_DEEP",
    "CODE_OVERSIZED",
    "CODE_BUDGET",
    "MAX_NESTING_DEPTH",
    "MAX_PROPERTY_STRING_BYTES",
    "MAX_COORD_STEPS_PER_FEATURE",
    "MAX_PROP_STEPS_PER_FEATURE",
    "validate_feature_collection",
]
