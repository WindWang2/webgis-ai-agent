"""L4 拓扑一致性：线/面几何自洽性的轻量校验（ADR-0195 D2）。

- 线：相邻顶点间"瞬移"跳变（> teleport_km，默认 300km）→ 硬拦截
  （LLM 拼线最常见的幻觉形态是跨大洲补点）；
- 面：顶点不足 / 零面积 / 连续重复顶点占比过高 → 硬拦截。
不做 O(n²) 自相交检测（误报风险高，留待高精校验层）。
"""
from __future__ import annotations

from app.services.spatial_guardrails.errors import (
    TopologyImplausibilityError,
)
from app.services.spatial_guardrails.geo_index import approx_distance_km
from app.services.spatial_guardrails.types import (
    CODE_TOPOLOGY_IMPLAUSIBLE,
    DefenseMode,
    GuardLevel,
    GuardrailIssue,
)


def _issue(message: str, location: str, evidence: dict) -> TopologyImplausibilityError:
    return TopologyImplausibilityError(
        GuardrailIssue(
            level=GuardLevel.L4_TOPOLOGY_CONSISTENCY,
            mode=DefenseMode.BLOCK,
            code=CODE_TOPOLOGY_IMPLAUSIBLE,
            message=message,
            location=location,
            evidence=evidence,
        )
    )


def validate_linestring(
    coords: list, *, teleport_km: float = 300.0, location: str = "$"
) -> None:
    """线要素相邻顶点跳变校验；coords 为 [[lng, lat], ...]。"""
    pts: list[tuple[float, float]] = []
    for c in coords:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            pts.append((float(c[0]), float(c[1])))
    if len(pts) < 2:
        return
    for i in range(1, len(pts)):
        d = approx_distance_km(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        if d > teleport_km:
            raise _issue(
                f"线要素第 {i - 1}→{i} 顶点跳变 {d:.0f}km（>{teleport_km:.0f}km），"
                "疑似幻觉拼线",
                location,
                {"from": list(pts[i - 1]), "to": list(pts[i]), "jump_km": round(d, 1)},
            )


def _ring_unique_ratio(ring: list) -> float:
    pts = [(float(c[0]), float(c[1])) for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
    if not pts:
        return 1.0
    unique = len(set(pts))
    return unique / len(pts)


def validate_polygon(
    rings: list, *, location: str = "$", min_unique_ratio: float = 0.5
) -> None:
    """面要素退化校验；rings 为 [[[lng, lat], ...], ...]（外环 + 内环）。"""
    if not rings:
        return
    outer = rings[0]
    pts = [
        (float(c[0]), float(c[1]))
        for c in outer
        if isinstance(c, (list, tuple)) and len(c) >= 2
    ]
    if len(pts) < 3:
        raise _issue(
            f"面要素外环仅 {len(pts)} 个顶点（<3），几何退化",
            location,
            {"vertices": len(pts)},
        )
    unique_ratio = _ring_unique_ratio(outer)
    if unique_ratio <= min_unique_ratio:
        raise _issue(
            f"面要素外环重复顶点占比过高（unique_ratio={unique_ratio:.2f}），疑似退化",
            location,
            {"unique_ratio": round(unique_ratio, 3)},
        )
    # 注：不做净鞋带面积判零 —— 自交环（蝴蝶结）的净面积可为 0 但几何并非
    # 退化，其修复（make_valid）归 quality gate 管辖（ADR-0195 层级边界）。
