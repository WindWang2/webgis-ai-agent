"""G47 独立几何质量 oracle（WP06）。

**与求解器分离**：只读已完成的布局 artifact（圆心/半径/数量/原邻接），重新用
解析几何算一遍。求解器自己的收敛判据不能当作质量证据。

输出五组事实，全部带单位/归一基准：

* ``area``：每圆面积 ``πr²`` 与 ``π k v`` 的相对误差（最大/均值/最差要素）；
* ``overlap``：圆对重叠面积比例（重叠面积/较小圆面积）的最大值与超阈对数；
* ``displacement``：圆心相对 anchor 的位移 / 最大半径（最大/p95/均值）；
* ``adjacency``：原邻接保留/丢失/新增接近对（允许变化，但必须披露）；
* ``gates``：是否达到计划的质量门（``pass`` / ``fail`` / ``not_evaluated``）。

"移动很小"不等于"无重叠"：两者是独立指标，任何一项不达标都不得标
publication-ready。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

MAX_REPORTED_OFFENDERS = 12
#: artifact 里半径/坐标的落盘小数位。oracle **按同一精度**复核，否则它证明的是
#: 内存里那组数的比例，而不是读者/下游真正拿到的那组数（量化误差会被静默放过）。
RADIUS_DECIMALS = 6


@dataclass(frozen=True)
class Circle:
    feature_id: str
    x: float
    y: float
    radius: float
    value: float
    anchor: Tuple[float, float]


@dataclass(frozen=True)
class GateSpec:
    max_area_rel_error: float
    max_pair_overlap_ratio: float
    max_overlapping_pair_ratio: float
    max_displacement_ratio: float


@dataclass
class QualityReport:
    status: str                        # pass | fail | not_evaluated
    failed: List[str] = field(default_factory=list)
    area: Dict[str, Any] = field(default_factory=dict)
    overlap: Dict[str, Any] = field(default_factory=dict)
    displacement: Dict[str, Any] = field(default_factory=dict)
    adjacency: Dict[str, Any] = field(default_factory=dict)
    gates: Dict[str, Any] = field(default_factory=dict)
    evaluated_pairs: int = 0

    def as_receipt(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "failed": self.failed,
            "area": self.area,
            "overlap": self.overlap,
            "displacement": self.displacement,
            "adjacency": self.adjacency,
            "gates": self.gates,
            "evaluated_pairs": self.evaluated_pairs,
            "oracle": "analytic circle area / center distance (independent of solver)",
        }


def _lens_overlap_area(r1: float, r2: float, d: float) -> float:
    """两圆交叠面积（解析）。d>=r1+r2 → 0；包含 → 较小圆面积。"""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    a1 = math.acos(max(-1.0, min(1.0, (d * d + r1 * r1 - r2 * r2) / (2 * d * r1))))
    a2 = math.acos(max(-1.0, min(1.0, (d * d + r2 * r2 - r1 * r1) / (2 * d * r2))))
    return (r1 * r1 * (a1 - math.sin(2 * a1) / 2.0)
            + r2 * r2 * (a2 - math.sin(2 * a2) / 2.0))


def _pair_candidates(circles: Sequence[Circle]) -> List[Tuple[int, int]]:
    """网格候选（cell = 2×max_r，保证任何重叠对都在相邻格内，不漏检）。"""
    n = len(circles)
    if n < 2:
        return []
    max_r = max(c.radius for c in circles)
    cell = max(1e-9, 2.0 * max_r)
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for i, c in enumerate(circles):
        key = (int(math.floor(c.x / cell)), int(math.floor(c.y / cell)))
        buckets.setdefault(key, []).append(i)
    pairs: List[Tuple[int, int]] = []
    seen: set = set()
    for i, c in enumerate(circles):
        cx = int(math.floor(c.x / cell))
        cy = int(math.floor(c.y / cell))
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                for j in buckets.get((cx + ox, cy + oy), ()):
                    if j <= i:
                        continue
                    key = (i, j)
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(key)
    pairs.sort()
    return pairs


def evaluate_layout(
    *,
    circles: Sequence[Circle],
    coefficient: float,
    gates: GateSpec,
    adjacency_edges: Sequence[Tuple[str, str, float]],
    max_radius: float,
    touch_tolerance_ratio: float = 0.02,
    radius_decimals: int = RADIUS_DECIMALS,
) -> QualityReport:
    """独立复核。输入是布局 artifact 的最终（已归一）坐标与半径。

    ``radius_decimals`` 必须与 artifact 序列化精度一致：oracle 先把半径量化到
    该精度再算面积比例，量到的是**落盘后**的真值（否则 round 带来的比例失真
    会被"内存里还没 round"掩盖）。
    """
    report = QualityReport(status="not_evaluated")
    reported_max_r = max(1e-12, float(max_radius))
    if not circles:
        report.failed = ["NO_CIRCLES"]
        report.area = {"evaluated": 0}
        return report

    # 落盘精度的半径（面积/重叠都按它算，保证与 artifact 逐位一致）
    radii = [round(float(c.radius), radius_decimals) for c in circles]

    # ── 面积比例 ──
    worst = (0.0, "")
    total_rel = 0.0
    for c, r in zip(circles, radii):
        expected = math.pi * coefficient * c.value
        if expected <= 0:
            continue
        rel = abs(math.pi * r * r - expected) / expected
        total_rel += rel
        if rel > worst[0]:
            worst = (rel, c.feature_id)
    n = max(1, len(circles))
    area_fail = worst[0] > gates.max_area_rel_error
    report.area = {
        "evaluated": len(circles),
        "max_rel_error": round(worst[0], 9),
        "mean_rel_error": round(total_rel / n, 9),
        "worst_feature_id": worst[1] or None,
        "threshold": gates.max_area_rel_error,
        "unit": "relative (dimensionless)",
        "formula": "|pi*r^2 - pi*k*value| / (pi*k*value)",
        "radius_decimals": radius_decimals,
        "pass": not area_fail,
    }

    # ── 重叠 ──
    # 两道门各自独立统计，**互不派生**：
    #   * ``max_pair_overlap_ratio``：任一圆对的真实最大重叠比例（硬门）；
    #   * ``max_overlapping_pair_ratio``：重叠超过「相切容差」的圆对占比。
    # 曾经把两者的阈值混用，导致占比门恒被硬门蕴含（死门），且
    # ``max_pair_overlap_ratio`` 只统计超阈对、把真实最大值报成 0。
    pairs = _pair_candidates(circles)
    measured: List[Dict[str, Any]] = []
    total_overlap = 0.0
    total_area = sum(math.pi * r * r for r in radii)
    for i, j in pairs:
        a, b = circles[i], circles[j]
        d = math.hypot(a.x - b.x, a.y - b.y)
        if d >= radii[i] + radii[j]:
            continue
        overlap = _lens_overlap_area(radii[i], radii[j], d)
        small = math.pi * min(radii[i], radii[j]) ** 2
        ratio = overlap / small if small > 0 else 0.0
        total_overlap += overlap
        measured.append({"a": a.feature_id, "b": b.feature_id,
                         "overlap_ratio": round(ratio, 6),
                         "distance": round(d, 6)})
    measured.sort(key=lambda x: -x["overlap_ratio"])
    over_fraction = [m for m in measured
                     if m["overlap_ratio"] > touch_tolerance_ratio]
    possible = max(1, len(pairs))
    pair_ratio = len(over_fraction) / possible
    max_overlap_any = float(measured[0]["overlap_ratio"]) if measured else 0.0
    pair_cap_violated = max_overlap_any > gates.max_pair_overlap_ratio
    fraction_cap_violated = pair_ratio > gates.max_overlapping_pair_ratio
    overlap_fail = pair_cap_violated or fraction_cap_violated
    report.overlap = {
        "evaluated_pairs": len(pairs),
        "overlapping_pairs": len(measured),
        "pairs_over_threshold": len(over_fraction),
        "pairs_over_threshold_ratio": round(pair_ratio, 9),
        "max_pair_overlap_ratio": round(max_overlap_any, 9),
        "total_overlap_area_ratio": round(total_overlap / total_area, 9) if total_area else 0.0,
        "worst_pairs": measured[:MAX_REPORTED_OFFENDERS],
        "threshold_pair_overlap_ratio": gates.max_pair_overlap_ratio,
        "threshold_pairs_ratio": gates.max_overlapping_pair_ratio,
        "fraction_pair_threshold": touch_tolerance_ratio,
        "fraction_denominator": "candidate pairs (grid neighbours that could touch)",
        "pair_cap_violated": pair_cap_violated,
        "fraction_cap_violated": fraction_cap_violated,
        "pass": not overlap_fail,
        "unit": "ratio (overlap area / smaller circle area)",
    }
    report.evaluated_pairs = len(pairs)

    # ── 位移 ──
    disps = [math.hypot(c.x - c.anchor[0], c.y - c.anchor[1]) / reported_max_r
             for c in circles]
    disps_sorted = sorted(disps)
    p95 = disps_sorted[min(len(disps_sorted) - 1,
                           int(round(0.95 * (len(disps_sorted) - 1))))]
    disp_fail = (disps_sorted[-1] > gates.max_displacement_ratio)
    report.displacement = {
        "max_ratio": round(disps_sorted[-1], 6),
        "p95_ratio": round(p95, 6),
        "mean_ratio": round(sum(disps) / len(disps), 6),
        "normalized_by": "max_radius",
        "disclosure_threshold": gates.max_displacement_ratio,
        "exceeds_disclosure_threshold": bool(disp_fail),
        "note": "位移只作披露/诊断（Dorling 布局本来就会大幅移动）；"
                "『移动很小』不能替代重叠/面积判据",
    }

    # ── 邻接 ──
    index = {c.feature_id: (c, r) for c, r in zip(circles, radii)}
    tol = touch_tolerance_ratio * reported_max_r
    retained = 0
    lost: List[Dict[str, str]] = []
    total_edges = 0
    for a_id, b_id, _w in adjacency_edges:
        a, b = index.get(a_id), index.get(b_id)
        if a is None or b is None:
            continue
        total_edges += 1
        (ca, ra), (cb, rb) = a, b
        d = math.hypot(ca.x - cb.x, ca.y - cb.y)
        if d <= ra + rb + tol:
            retained += 1
        else:
            lost.append({"a": a_id, "b": b_id})
    original = {(min(a, b), max(a, b)) for a, b, _w in adjacency_edges}
    new_near = [p for p in measured
                if (min(p["a"], p["b"]), max(p["a"], p["b"])) not in original]
    report.adjacency = {
        "original_edges": total_edges,
        "retained": retained,
        "lost": len(lost),
        "retained_ratio": round(retained / total_edges, 6) if total_edges else None,
        "lost_examples": lost[:MAX_REPORTED_OFFENDERS],
        "new_overlapping_non_adjacent_pairs": len(new_near),
        "touch_tolerance": round(tol, 6),
        "contract": "原邻接允许变化（Dorling 方法性质），必须披露而非声称保拓扑",
    }

    report.gates = {
        "area": not area_fail,
        "overlap": not overlap_fail,
        "overlap_pair_cap": not pair_cap_violated,
        "overlap_fraction_cap": not fraction_cap_violated,
        "displacement_informational": not disp_fail,
        "thresholds": {
            "max_area_rel_error": gates.max_area_rel_error,
            "max_pair_overlap_ratio": gates.max_pair_overlap_ratio,
            "max_overlapping_pair_ratio": gates.max_overlapping_pair_ratio,
            "max_displacement_ratio": gates.max_displacement_ratio,
        },
    }
    # 发布资格只由面积与重叠两项硬判据决定；位移是披露事实（方法本来就要移动）。
    failed = [k for k in ("area", "overlap") if not report.gates[k]]
    report.failed = failed
    report.status = "pass" if not failed else "fail"
    return report


def gate_from_plan(plan) -> GateSpec:  # noqa: ANN001 - 避免与 contract 循环导入
    g = plan.quality_gate
    return GateSpec(
        max_area_rel_error=float(g.max_area_rel_error),
        max_pair_overlap_ratio=float(g.max_pair_overlap_ratio),
        max_overlapping_pair_ratio=float(g.max_overlapping_pair_ratio),
        max_displacement_ratio=float(g.max_displacement_ratio),
    )


def summarize_for_receipt(report: QualityReport) -> Dict[str, Any]:
    """有界回执（Pi/UI 共用）：不含全量圆对或几何。"""
    out = report.as_receipt()
    if isinstance(out.get("overlap"), dict):
        out["overlap"].pop("worst_pairs", None)
    if isinstance(out.get("adjacency"), dict):
        out["adjacency"].pop("lost_examples", None)
    return out


__all__ = [
    "Circle", "GateSpec", "QualityReport", "evaluate_layout",
    "gate_from_plan", "summarize_for_receipt",
    "MAX_REPORTED_OFFENDERS", "RADIUS_DECIMALS",
]
