"""G47 求解器（WP05）单测：确定性、行序无关、有界、可取消。

Dorling 迭代是**确定性**算法：同一输入必须得到同一布局，否则"持久 artifact +
候选身份"就没有意义。这里用三个性质锁住它：

1. 重复执行 → 逐坐标完全相同（不是"接近"）；
2. 输入顺序打乱 → 按 id 对齐后仍相同（Jacobi 累计更新，不是按行序的 Gauss-Seidel）；
3. 同心初始位置 → 稳定哈希破对称（不用随机数）。
"""
from __future__ import annotations

import math
import random

import pytest

from app.services.cartogram_authoring.solver import (
    SolveCancelled,
    SolveParams,
    _pair_overlap_ratio,
    solve_dorling,
)


def _params(**overrides):
    base = dict(max_iterations=300, repulsion=1.0, attraction=0.35,
                damping=0.7, max_step_ratio=0.5, tolerance=1e-3,
                overlap_ratio_tolerance=0.02, max_seconds=20.0)
    base.update(overrides)
    return SolveParams(**base)


def _grid(n=3, spacing=12.0, radius=10.0):
    """n×n 圆心（间距小于半径和 → 初始必然重叠，逼出真实迭代）。"""
    anchors, radii, ids = [], [], []
    for r in range(n):
        for c in range(n):
            fid = f"r{r}c{c}"
            ids.append(fid)
            anchors.append((c * spacing, r * spacing))
            radii.append(radius)
    return anchors, radii, ids


def _edges_for_grid(n=3, weight=1.0):
    edges = []
    for r in range(n):
        for c in range(n):
            i = r * n + c
            if c + 1 < n:
                edges.append((i, i + 1, weight))
            if r + 1 < n:
                edges.append((i, i + n, weight))
    return edges


def _positions_by_id(outcome, ids):
    return {fid: outcome.positions[i] for i, fid in enumerate(ids)}


# ── 确定性 ──────────────────────────────────────────────────────────────

def test_same_input_gives_identical_layout():
    anchors, radii, ids = _grid()
    a = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                      edges=_edges_for_grid(), params=_params())
    b = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                      edges=_edges_for_grid(), params=_params())
    assert a.positions == b.positions          # 逐坐标相等，不是 approx
    assert a.iterations == b.iterations
    assert a.stop_reason == b.stop_reason


def test_shuffled_input_order_gives_same_layout_per_id():
    """Jacobi 累计更新：结果不该取决于哪一行先算。"""
    anchors, radii, ids = _grid()
    edges = _edges_for_grid()
    base = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                         edges=edges, params=_params())

    order = list(range(len(ids)))
    random.Random(20261115).shuffle(order)
    new_of_old = {old: new for new, old in enumerate(order)}
    shuffled = solve_dorling(
        anchors=[anchors[i] for i in order],
        radii=[radii[i] for i in order],
        feature_ids=[ids[i] for i in order],
        edges=[(new_of_old[i], new_of_old[j], w) for i, j, w in edges],
        params=_params())

    base_by_id = _positions_by_id(base, ids)
    shuf_by_id = {ids[old]: shuffled.positions[new_of_old[old]]
                  for old in range(len(ids))}
    max_r = max(radii)
    drift = max(math.dist(base_by_id[f], shuf_by_id[f]) for f in ids)
    # 只有浮点加法顺序差异，量级远小于 1e-6·max_radius。
    assert drift < 1e-6 * max_r, drift


def test_concentric_anchors_are_separated_deterministically():
    """三个圆心同点：靠 sha256(feature_id) 定角，不用随机数。"""
    anchors = [(0.0, 0.0)] * 3
    radii = [10.0, 10.0, 10.0]
    ids = ["alpha", "beta", "gamma"]
    first = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                          edges=[], params=_params())
    second = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                           edges=[], params=_params())
    assert first.positions == second.positions
    assert len({(round(p[0], 6), round(p[1], 6)) for p in first.positions}) == 3
    assert first.diagnostics["symmetry_break"] == "stable_hash(feature_id)"


# ── 收敛与有界 ──────────────────────────────────────────────────────────

def test_converges_and_removes_overlap():
    """收敛判据是**交叠比例**≤ 阈值，不是"严格相切"。

    相切在浮点迭代里是不稳定边界；合同允许 ≤2% 的残余 lens 面积，这样的
    圆在视觉与读图上不可分辨。这里同时验证"从必然重叠（比例 1.0）降到阈值内"。
    """
    anchors, radii, ids = _grid()
    out = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                        edges=_edges_for_grid(), params=_params())
    assert out.converged is True
    assert out.stop_reason == "converged"
    worst = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            d = math.dist(out.positions[i], out.positions[j])
            worst = max(worst, _pair_overlap_ratio(radii[i], radii[j], d))
    assert worst <= 0.02 + 1e-9, worst
    # 初始 3×3 网格间距 12 < 半径和 20 → 初始交叠比例为 1.0；必须有实质改善。
    assert worst < 1.0


def test_radii_are_never_modified_by_the_solver():
    """求解只动位置；面积由共同系数决定，求解期半径是常量。"""
    anchors, radii, ids = _grid(n=2)
    out = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                        edges=[], params=_params())
    assert len(out.positions) == len(radii)
    assert out.diagnostics["radii_fixed"] is True


def test_iteration_budget_is_reported_not_hidden():
    anchors, radii, ids = _grid()
    out = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                        edges=[], params=_params(max_iterations=1))
    assert out.iterations == 1
    assert out.converged is False
    assert out.stop_reason == "max_iterations"


def test_stalled_with_overlap_is_not_called_converged():
    """位移变小但仍有超阈重叠 → ``stalled_with_overlap``，不是 converged。"""
    # 两个极大半径圆挤在极小 tolerance 下：很快"动不了"但仍然重叠。
    anchors = [(0.0, 0.0), (1.0, 0.0)]
    radii = [100.0, 100.0]
    out = solve_dorling(anchors=anchors, radii=radii, feature_ids=["a", "b"],
                        edges=[], params=_params(max_iterations=3, damping=0.01,
                                                 max_step_ratio=0.001))
    assert out.converged is False
    assert out.stop_reason in {"stalled_with_overlap", "max_iterations"}
    assert out.overlapping_pairs_last >= 1


def test_single_unit_is_trivially_converged():
    out = solve_dorling(anchors=[(5.0, 5.0)], radii=[3.0],
                        feature_ids=["only"], edges=[], params=_params())
    assert out.stop_reason == "single_unit"
    assert out.converged is True
    assert out.positions == [(5.0, 5.0)]


def test_empty_input_is_trivially_converged():
    out = solve_dorling(anchors=[], radii=[], feature_ids=[], edges=[],
                        params=_params())
    assert out.stop_reason == "empty"
    assert out.positions == []


def test_mismatched_lengths_are_programmer_errors():
    with pytest.raises(ValueError):
        solve_dorling(anchors=[(0.0, 0.0)], radii=[1.0, 2.0],
                      feature_ids=["a"], edges=[], params=_params())


def test_time_budget_stops_early():
    anchors, radii, ids = _grid(n=4, spacing=1.0, radius=10.0)
    out = solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                        edges=[], params=_params(max_seconds=1e-9))
    assert out.stop_reason == "time_budget"
    assert out.converged is False


# ── 取消 ────────────────────────────────────────────────────────────────

def test_cancellation_raises_default_exception():
    anchors, radii, ids = _grid()
    with pytest.raises(SolveCancelled):
        solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                      edges=[], params=_params(),
                      should_cancel=lambda: True)


def test_cancellation_uses_upper_layer_exception():
    """作业层有自己的取消异常（落 cancelled 终态），不能被领域层硬编码盖掉。"""
    class _JobCancelled(RuntimeError):
        pass

    anchors, radii, ids = _grid()
    with pytest.raises(_JobCancelled):
        solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                      edges=[], params=_params(),
                      should_cancel=lambda: True,
                      cancellation_exception=lambda: _JobCancelled("job"))


def test_cancellation_is_checked_every_iteration():
    anchors, radii, ids = _grid()
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(SolveCancelled):
        solve_dorling(anchors=anchors, radii=radii, feature_ids=ids,
                      edges=[], params=_params(),
                      should_cancel=should_cancel)
    assert calls["n"] == 3


# ── 重叠比例（求解器内部判据，与质量 oracle 独立的第二实现）──────────────

def test_overlap_ratio_boundaries():
    assert _pair_overlap_ratio(1.0, 1.0, 2.0) == 0.0        # 外切
    assert _pair_overlap_ratio(1.0, 1.0, 3.0) == 0.0        # 分离
    assert _pair_overlap_ratio(1.0, 1.0, 0.0) == 1.0        # 完全重合
    assert _pair_overlap_ratio(5.0, 1.0, 0.5) == 1.0        # 包含 → 小圆全被覆盖


def test_half_overlap_ratio_is_analytic():
    """r=1、d=1 的两圆：交叠面积 ``2π/3-√3/2``，比例 ≈ 0.391。"""
    expected = (2 * math.pi / 3 - math.sqrt(3) / 2) / math.pi
    assert _pair_overlap_ratio(1.0, 1.0, 1.0) == pytest.approx(expected, rel=1e-12)
