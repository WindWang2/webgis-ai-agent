"""恶意/病态 GeoJSON 输入守卫（G03 GD-09 / ADR-0286 D7）。

背景（BASELINE 断链 9）：IngestPipeline 的既有防线只有载荷大小帽 + FC
形状检查——NaN/±Infinity 坐标、超深嵌套结构（解析栈/递归耗尽）、超大
属性字符串（内存放大）可以静默入库并污染下游（mvt/stats/分析）。

守卫语义（类型化拒绝，绝不静默吞）：
- 非有限坐标（NaN/±Inf）→ ``INVALID_COORDINATES_NONFINITE``；
- 嵌套深度 > 64 → ``STRUCTURE_TOO_DEEP``（深度帽同时剪枝，保证守卫
  本身对恶意深结构也是 O(可达节点) 有界）；
- 单属性字符串 > 256KB → ``PROPERTY_STRING_OVERSIZED``。

性能纪律：O(features) 单遍、每要素有界步数（深度帽）；合法大 FC
（5 万要素）守卫秒级（见 test_guard_bounded_on_large_valid_fc）。
拒绝发生在 store 之前——无 ref 铸造、无补偿回滚对象。
"""
from __future__ import annotations

import math
from typing import Any

#: 类型化错误码（机读；dispatch/质量面可解释）。
CODE_NONFINITE = "INVALID_COORDINATES_NONFINITE"
CODE_TOO_DEEP = "STRUCTURE_TOO_DEEP"
CODE_OVERSIZED = "PROPERTY_STRING_OVERSIZED"

#: 嵌套深度帽（属性树 + geometry 坐标树共用；正常 GeoJSON 远低于此）。
MAX_NESTING_DEPTH = 64
#: 单属性字符串字节帽（256KB；正常名称/地址/备注远低于此）。
MAX_PROPERTY_STRING_BYTES = 256 * 1024
#: 单要素守卫步数帽（防病态宽结构：万级属性键的合法形状也有界通过）。
MAX_STEPS_PER_FEATURE = 20_000


class GeojsonGuardError(Exception):
    """守卫拒绝（类型化；``code`` 是机读码，message 用户可解释）。"""

    def __init__(self, code: str, message: str, *, feature_index: int = -1):
        super().__init__(message)
        self.code = code
        self.feature_index = feature_index


def _check_depth_guard(node: Any, depth: int, budget: list) -> None:
    """有界深度检查（迭代栈；budget 计步防病态宽结构）。"""
    stack: list[tuple[Any, int]] = [(node, depth)]
    while stack:
        current, d = stack.pop()
        budget[0] -= 1
        if budget[0] < 0:
            return  # 步数帽：不再深扫（深度/尺寸判据继续对其余要素生效）
        if d > MAX_NESTING_DEPTH:
            raise GeojsonGuardError(
                CODE_TOO_DEEP,
                f"结构嵌套深度超过 {MAX_NESTING_DEPTH}（疑似恶意载荷）",
            )
        if isinstance(current, dict):
            for v in current.values():
                stack.append((v, d + 1))
        elif isinstance(current, (list, tuple)):
            for v in current:
                stack.append((v, d + 1))
        elif isinstance(current, str):
            if len(current.encode("utf-8", errors="ignore")) > (
                MAX_PROPERTY_STRING_BYTES
            ):
                raise GeojsonGuardError(
                    CODE_OVERSIZED,
                    f"属性字符串超过 {MAX_PROPERTY_STRING_BYTES // 1024}KB"
                    "（疑似载荷放大攻击）",
                )


def _check_coordinates_finite(node: Any, budget: list) -> None:
    """坐标树非有限值检查（数字叶节点；bool 是 int 子类须排除）。"""
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        budget[0] -= 1
        if budget[0] < 0:
            return
        if isinstance(current, bool):
            continue
        if isinstance(current, (int, float)):
            if not math.isfinite(float(current)):
                raise GeojsonGuardError(
                    CODE_NONFINITE,
                    "坐标包含非有限数值（NaN/±Inf）——不得入库",
                )
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        # dict 在坐标树中不合法；深度/尺寸判据已另行覆盖，不在此重复。


def validate_feature_collection(fc: Any) -> None:
    """FC 形状守卫入口（O(features) 有界；拒绝 → GeojsonGuardError）。

    只做**结构性/数值性**检查，不做语义判断（CRS/bbox 诚实性由
    profiler/quality 既有面负责）。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return  # 形状判定是 detect 步的既有职责（UNSUPPORTED_SHAPE）
    features = fc.get("features")
    if not isinstance(features, list):
        return
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        budget = [MAX_STEPS_PER_FEATURE]
        geom = feature.get("geometry")
        if isinstance(geom, dict):
            coords = geom.get("coordinates")
            if coords is not None:
                _check_coordinates_finite(coords, budget)
        _check_depth_guard(feature.get("properties"), 0, budget)


__all__ = [
    "GeojsonGuardError",
    "CODE_NONFINITE",
    "CODE_TOO_DEEP",
    "CODE_OVERSIZED",
    "MAX_NESTING_DEPTH",
    "MAX_PROPERTY_STRING_BYTES",
    "validate_feature_collection",
]
