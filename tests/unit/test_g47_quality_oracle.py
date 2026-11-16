"""G47 独立质量 oracle（WP06）单测。

oracle 的价值在于**与求解器分离**：它只读最终布局（圆心/半径/数量/原邻接），
用解析几何重算一遍。所以这里的用例是"故意做坏的布局"，而不是"再跑一次求解器"：

* 半径被逐圆 clamp → 面积门必须失败并点名最差要素；
* 两圆重合 → 重叠门必须失败；
* 圆心被扔到很远但面积/重叠都合格 → 状态仍是 pass，位移只作披露
  （"移动小"不能替代"面积对、不重叠"）。
"""
from __future__ import annotations

import math

import pytest

from app.services.cartogram_authoring.quality import (
    Circle,
    GateSpec,
    _pair_candidates,
    evaluate_layout,
    gate_from_plan,
    summarize_for_receipt,
)
from tests.unit.g47_fixtures import plan

GATES = GateSpec(max_area_rel_error=0.01, max_pair_overlap_ratio=0.02,
                 max_overlapping_pair_ratio=0.01, max_displacement_ratio=8.0)


def _circle(fid, x, y, radius, value, anchor=None):
    return Circle(feature_id=fid, x=x, y=y, radius=radius, value=value,
                  anchor=anchor or (x, y))


def _evaluate(circles, *, coefficient=1.0, edges=(), gates=GATES,
              max_radius=None):
    return evaluate_layout(
        circles=circles, coefficient=coefficient, gates=gates,
        adjacency_edges=list(edges),
        max_radius=max_radius or max(c.radius for c in circles))


# ── 面积 ────────────────────────────────────────────────────────────────

def test_area_gate_passes_when_radius_matches_shared_coefficient():
    # k=1：r=sqrt(v) → 面积 = π·v
    circles = [_circle("a", 0, 0, 10.0, 100.0), _circle("b", 100, 0, 20.0, 400.0)]
    report = _evaluate(circles)
    assert report.status == "pass"
    assert report.failed == []
    assert report.area["max_rel_error"] < 1e-12
    assert report.area["evaluated"] == 2


def test_area_gate_catches_per_circle_clamp_and_names_the_worst_feature():
    """把 100 的圆画成 r=13（像是按"最小可见半径"抬过）→ 面积必须被抓到。"""
    circles = [
        _circle("ok", 0, 0, 10.0, 100.0),
        _circle("clamped", 200, 0, 13.0, 100.0),
    ]
    report = _evaluate(circles)
    assert report.status == "fail"
    assert report.failed == ["area"]
    assert report.area["worst_feature_id"] == "clamped"
    assert report.area["max_rel_error"] == pytest.approx(0.69, abs=1e-9)
    assert report.area["pass"] is False


def test_area_threshold_is_reported_with_formula():
    report = _evaluate([_circle("a", 0, 0, 10.0, 100.0)])
    area = report.area
    assert area["threshold"] == GATES.max_area_rel_error
    assert "pi*r^2" in area["formula"]
    assert area["unit"] == "relative (dimensionless)"


# ── 重叠 ────────────────────────────────────────────────────────────────

def test_overlap_gate_fails_on_coincident_circles():
    circles = [_circle("a", 0, 0, 10.0, 100.0), _circle("b", 0, 0, 10.0, 100.0)]
    report = _evaluate(circles)
    assert report.status == "fail"
    assert report.failed == ["overlap"]
    assert report.overlap["max_pair_overlap_ratio"] == pytest.approx(1.0)
    assert report.overlap["worst_pairs"][0]["overlap_ratio"] == pytest.approx(1.0)


def test_overlap_lens_area_is_analytic():
    """r=1、d=1：交叠面积 / 小圆面积 = (2π/3 − √3/2)/π ≈ 0.3910。"""
    expected = (2 * math.pi / 3 - math.sqrt(3) / 2) / math.pi
    report = _evaluate([_circle("a", 0, 0, 1.0, 1.0),
                        _circle("b", 1.0, 0, 1.0, 1.0)], max_radius=1.0)
    # 明细里按 6 位小数呈现，聚合值随之——精度足以判定阈值，且回执可读。
    assert report.overlap["max_pair_overlap_ratio"] == pytest.approx(expected, abs=1e-6)
    assert report.overlap["worst_pairs"][0]["overlap_ratio"] == pytest.approx(
        expected, abs=1e-6)


def test_tangent_circles_have_no_overlap():
    report = _evaluate([_circle("a", 0, 0, 10.0, 100.0),
                        _circle("b", 20.0, 0, 10.0, 100.0)])
    assert report.overlap["max_pair_overlap_ratio"] == 0.0
    assert report.overlap["pass"] is True


def test_single_gross_overlap_fails_even_when_fraction_gate_is_wide_open():
    """**回归**：只看"超阈圆对占比"会让 1 对 100% 重合通过。

    把占比门放到最宽（1.0，等于允许所有圆对超阈），单对硬门仍必须拦住：
    两个圆在图上就是叠在一起，占比再小也不可发布。
    """
    circles = [_circle(f"c{i}", i * 40.0, 0.0, 10.0, 100.0) for i in range(60)]
    circles[10] = _circle("c10", circles[9].x, circles[9].y, 10.0, 100.0)
    report = _evaluate(circles, gates=GateSpec(
        max_area_rel_error=0.01, max_pair_overlap_ratio=0.02,
        max_overlapping_pair_ratio=1.0, max_displacement_ratio=8.0))
    assert report.overlap["pairs_over_threshold"] == 1
    assert report.overlap["fraction_cap_violated"] is False
    assert report.overlap["pair_cap_violated"] is True
    assert report.overlap["pass"] is False
    assert report.failed == ["overlap"]


def test_overlapping_pair_ratio_gate_is_separate_from_magnitude():
    """两道门都要满足：单对超限 → fail；占比超限 → 也 fail。"""
    circles = [_circle(f"c{i}", i * 10.0, 0, 10.0, 100.0) for i in range(6)]
    report = _evaluate(circles, gates=GateSpec(
        max_area_rel_error=0.01, max_pair_overlap_ratio=0.02,
        max_overlapping_pair_ratio=0.5, max_displacement_ratio=8.0))
    assert report.overlap["pairs_over_threshold"] > 0
    assert report.overlap["pairs_over_threshold_ratio"] > 0
    assert report.overlap["pair_cap_violated"] is True
    assert report.overlap["pass"] is False
    assert report.gates["overlap_pair_cap"] is False


def test_candidate_pairs_never_miss_a_real_overlap():
    """网格候选是**超集**：任何真实重叠对都必须出现在候选里（不漏检）。"""
    circles = [_circle(f"c{i}", (i % 5) * 7.3, (i // 5) * 7.3,
                       5.0 + (i % 3), 100.0) for i in range(20)]
    candidates = set(_pair_candidates(circles))
    actual = {
        (i, j)
        for i in range(len(circles))
        for j in range(i + 1, len(circles))
        if math.dist((circles[i].x, circles[i].y),
                     (circles[j].x, circles[j].y))
        < circles[i].radius + circles[j].radius
    }
    assert actual <= candidates
    assert actual  # 用例本身确实有重叠


# ── 位移：只披露，不做发布硬门 ──────────────────────────────────────────

def test_large_displacement_is_disclosed_but_does_not_block_publication():
    """Dorling 布局本来就会大幅移动 —— 用"移动小"当判据会阻止正确结果。"""
    circles = [
        _circle("a", 1000.0, 0.0, 10.0, 100.0, anchor=(0.0, 0.0)),
        _circle("b", 2000.0, 0.0, 20.0, 400.0, anchor=(1.0, 0.0)),
    ]
    report = _evaluate(circles)
    assert report.displacement["max_ratio"] > GATES.max_displacement_ratio
    assert report.displacement["exceeds_disclosure_threshold"] is True
    assert report.displacement["normalized_by"] == "max_radius"
    # 关键：位移不进 failed，发布资格只由面积与重叠决定。
    assert report.status == "pass"
    assert report.failed == []
    assert report.gates["displacement_informational"] is False


def test_small_displacement_does_not_mask_overlap():
    """反向陷阱：圆几乎没动（"看起来稳"）但明显重叠 → 仍然 fail。"""
    circles = [_circle("a", 0.0, 0.0, 10.0, 100.0, anchor=(0.0, 0.0)),
               _circle("b", 1.0, 0.0, 10.0, 100.0, anchor=(1.0, 0.0))]
    report = _evaluate(circles)
    assert report.displacement["max_ratio"] < 0.2
    assert report.status == "fail"
    assert report.failed == ["overlap"]


# ── 邻接披露 ────────────────────────────────────────────────────────────

def test_lost_adjacency_is_counted_and_exemplified():
    circles = [_circle("a", 0.0, 0.0, 10.0, 100.0),
               _circle("b", 500.0, 0.0, 10.0, 100.0)]
    report = _evaluate(circles, edges=[("a", "b", 1.0)])
    assert report.adjacency["original_edges"] == 1
    assert report.adjacency["retained"] == 0
    assert report.adjacency["lost"] == 1
    assert report.adjacency["lost_examples"] == [{"a": "a", "b": "b"}]
    assert report.adjacency["retained_ratio"] == 0.0
    assert "声称保拓扑" in report.adjacency["contract"]


def test_retained_adjacency_uses_touch_tolerance():
    """半径和 + 容差内即算保留（布局里相切是目标，不是精确等式）。"""
    tol_ratio = 0.02
    max_r = 10.0
    gap = tol_ratio * max_r * 0.5
    circles = [_circle("a", 0.0, 0.0, 10.0, 100.0),
               _circle("b", 20.0 + gap, 0.0, 10.0, 100.0)]
    report = _evaluate(circles, edges=[("a", "b", 1.0)])
    assert report.adjacency["retained"] == 1
    assert report.adjacency["retained_ratio"] == 1.0


def test_adjacency_edges_to_unknown_units_are_ignored_not_counted():
    circles = [_circle("a", 0.0, 0.0, 10.0, 100.0)]
    report = _evaluate(circles, edges=[("a", "ghost", 1.0)])
    assert report.adjacency["original_edges"] == 0
    assert report.adjacency["retained_ratio"] is None


def test_new_overlapping_non_adjacent_pairs_are_reported():
    circles = [_circle("a", 0.0, 0.0, 10.0, 100.0),
               _circle("b", 5.0, 0.0, 10.0, 100.0)]
    report = _evaluate(circles, edges=[])
    assert report.adjacency["new_overlapping_non_adjacent_pairs"] == 1


# ── 契约与回执 ──────────────────────────────────────────────────────────

def test_empty_layout_is_not_evaluated():
    report = _evaluate(circles=[], max_radius=1.0)
    assert report.status == "not_evaluated"
    assert report.failed == ["NO_CIRCLES"]


def test_gate_from_plan_maps_every_threshold():
    p = plan()
    gates = gate_from_plan(p)
    assert gates.max_area_rel_error == p.quality_gate.max_area_rel_error
    assert gates.max_pair_overlap_ratio == p.quality_gate.max_pair_overlap_ratio
    assert gates.max_overlapping_pair_ratio == p.quality_gate.max_overlapping_pair_ratio
    assert gates.max_displacement_ratio == p.quality_gate.max_displacement_ratio


def test_receipt_is_bounded_and_states_the_oracle():
    circles = [_circle(f"c{i}", i * 100.0, 0.0, 1.0, 1.0) for i in range(4)]
    report = _evaluate(circles)
    receipt = summarize_for_receipt(report)
    assert "oracle" in receipt
    assert "independent of solver" in receipt["oracle"]
    assert "worst_pairs" not in receipt["overlap"]
    assert "lost_examples" not in receipt["adjacency"]
    assert set(receipt["gates"]) >= {"area", "overlap",
                                     "displacement_informational", "thresholds"}
