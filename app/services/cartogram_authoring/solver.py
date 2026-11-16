"""G47 Dorling 圆形 cartogram 求解器（WP05）。

方法：Dorling (1996) *Area Cartograms: Their Use and Creation*, pp.32-35,
fig.9a, app.B —— 用与数量成面积的圆代替区域，靠**重叠排斥**与**原邻接吸引**
调整圆心，直到圆之间基本不重叠且原邻接尽量保持。部分邻接会改变（这是方法的
性质，不是 bug），所以布局必须同时披露邻接保留率。

本实现的可核验取舍：

* **Jacobi 更新**：每轮先在当前状态上累计全部位移，再一次性应用 —— 要素行序
  不改变结果（任务 D06）。禁止原地顺序更新。
* **半径固定**：共同面积系数在求解期不变；绝不用"缩小圆"消除重叠（那会破坏
  面积比例，是伪成功）。
* **确定性破对称**：同心 anchor 用要素 id 的稳定散列取一个微小方向偏移，不用
  全局随机数；同输入多次运行结果逐位相同。
* **有界**：迭代数、时间、候选对、单步位移都有上界；到帽仍重叠 → 返回
  ``converged=False`` + ``stop_reason``，由上层给 typed 未收敛结果。
* **取消检查点**：每轮开头调 ``should_cancel``；协作取消抛 ``OperationCancelled``，
  绝不落"成功终态"。
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


class SolveCancelled(RuntimeError):
    """求解被协作取消（上层据此落 cancelled 终态）。"""


@dataclass
class SolveParams:
    max_iterations: int = 400
    repulsion: float = 1.0
    attraction: float = 0.35
    damping: float = 0.7
    max_step_ratio: float = 0.5
    tolerance: float = 1e-3
    #: 判定"仍重叠"的比例阈值（重叠面积 / 较小圆面积，用半径近似等价判断）。
    overlap_ratio_tolerance: float = 0.02
    max_seconds: float = 30.0
    #: 候选对硬帽（超过则提前停止并如实报告，不静默漏检）。
    max_candidate_pairs: int = 400_000


@dataclass
class SolveOutcome:
    positions: List[Point]
    iterations: int
    converged: bool
    stop_reason: str
    max_displacement: float
    elapsed_s: float
    candidate_pairs_last: int
    overlapping_pairs_last: int
    diagnostics: Dict[str, object] = field(default_factory=dict)

    def as_receipt(self) -> Dict[str, object]:
        return {
            "iterations": self.iterations,
            "converged": self.converged,
            "stop_reason": self.stop_reason,
            "max_displacement": round(self.max_displacement, 6),
            "elapsed_s": round(self.elapsed_s, 4),
            "candidate_pairs_last": self.candidate_pairs_last,
            "overlapping_pairs_last": self.overlapping_pairs_last,
            "diagnostics": self.diagnostics,
        }


def _stable_angle(feature_id: str) -> float:
    """稳定的确定性角度（0..2π），用于同心 anchor 的破对称。"""
    h = hashlib.sha256(feature_id.encode("utf-8")).digest()
    return (int.from_bytes(h[:4], "big") / 2**32) * 2.0 * math.pi


def _seed_positions(
    anchors: Sequence[Point], radii: Sequence[float], ids: Sequence[str],
) -> List[List[float]]:
    """同心 anchor 的确定性分离：偏移量与两圆半径相关，方向由 id 决定。"""
    seen: Dict[Tuple[float, float], int] = {}
    out: List[List[float]] = []
    for i, (ax, ay) in enumerate(anchors):
        key = (round(float(ax), 6), round(float(ay), 6))
        count = seen.get(key, 0)
        seen[key] = count + 1
        if count == 0:
            out.append([float(ax), float(ay)])
            continue
        angle = _stable_angle(ids[i])
        r = float(radii[i])
        # 偏移至少 0.75×(r_i + r_j) 的量级才可能脱离完全重叠；用 0.5r 起步，
        # 排斥迭代会继续推开，这里只负责"不是同一个点"。
        mag = max(1e-9, 0.5 * r + 1e-6 * count)
        out.append([float(ax) + math.cos(angle) * mag,
                    float(ay) + math.sin(angle) * mag])
    return out


class _Grid:
    """均匀网格空间索引（只优化候选查询；真实重叠仍按解析距离判断）。"""

    __slots__ = ("cell", "buckets")

    def __init__(self, cell: float) -> None:
        self.cell = max(1e-9, float(cell))
        self.buckets: Dict[Tuple[int, int], List[int]] = {}

    def build(self, positions: Sequence[Point]) -> None:
        self.buckets = {}
        cell = self.cell
        for i, (x, y) in enumerate(positions):
            key = (int(math.floor(x / cell)), int(math.floor(y / cell)))
            self.buckets.setdefault(key, []).append(i)

    def neighbors(self, i: int, positions: Sequence[Point]) -> List[int]:
        x, y = positions[i]
        cell = self.cell
        cx = int(math.floor(x / cell))
        cy = int(math.floor(y / cell))
        out: List[int] = []
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                bucket = self.buckets.get((cx + ox, cy + oy))
                if bucket:
                    out.extend(bucket)
        return out


def _pair_overlap_ratio(r_i: float, r_j: float, dist: float) -> float:
    """圆对的重叠面积 / 较小圆面积（解析 lens 面积）。"""
    if dist >= r_i + r_j:
        return 0.0
    if dist <= abs(r_i - r_j):
        # 一个是另一个的子集：重叠面积 = 较小圆。
        return 1.0
    # 标准两圆交叠面积。
    d = dist
    a1 = math.acos(max(-1.0, min(1.0, (d * d + r_i * r_i - r_j * r_j) / (2 * d * r_i))))
    a2 = math.acos(max(-1.0, min(1.0, (d * d + r_j * r_j - r_i * r_i) / (2 * d * r_j))))
    area = (r_i * r_i * (a1 - math.sin(2 * a1) / 2.0)
            + r_j * r_j * (a2 - math.sin(2 * a2) / 2.0))
    small = math.pi * min(r_i, r_j) ** 2
    if small <= 0:
        return 0.0
    return max(0.0, min(1.0, area / small))


def solve_dorling(
    *,
    anchors: Sequence[Point],
    radii: Sequence[float],
    feature_ids: Sequence[str],
    edges: Sequence[Tuple[int, int, float]],
    params: SolveParams,
    should_cancel: Optional[Callable[[], bool]] = None,
    cancellation_exception: Optional[Callable[[], BaseException]] = None,
) -> SolveOutcome:
    """执行有界 Dorling 迭代。

    Args:
        anchors: 每单元初始位置（投影坐标，仅作初始化参考）。
        radii: 每单元半径（由共同面积系数导出，**求解期不变**）。
        feature_ids: 稳定身份（排序/破对称用，不参与几何判断）。
        edges: ``(i, j, weight)``，weight∈(0,1]，来自真实共享边长度。
        should_cancel: 协作取消回调（每轮检查）。
    """
    n = len(anchors)
    if n != len(radii) or n != len(feature_ids):
        raise ValueError("anchors/radii/feature_ids 长度不一致")
    started = time.monotonic()
    if n == 0:
        return SolveOutcome([], 0, True, "empty", 0.0, 0.0, 0, 0)
    max_r = max(float(r) for r in radii)
    pos = _seed_positions(anchors, radii, feature_ids)
    if n == 1:
        return SolveOutcome([(pos[0][0], pos[0][1])], 0, True, "single_unit",
                            0.0, time.monotonic() - started, 0, 0)

    grid = _Grid(cell=2.0 * max_r)
    max_step = max(1e-12, params.max_step_ratio * max_r)
    stop_rel = max(1e-9, params.tolerance * max_r)
    converged = False
    stop_reason = "max_iterations"
    iterations = 0
    max_disp = float("inf")
    pairs_last = 0
    overlaps_last = 0
    pair_cap_hit = False

    adjacency: List[Tuple[int, int, float]] = [
        (int(i), int(j), float(w)) for i, j, w in edges if i != j
    ]

    for it in range(int(params.max_iterations)):
        iterations = it + 1
        if should_cancel is not None and should_cancel():
            exc_factory = cancellation_exception or (lambda: SolveCancelled("cancelled"))
            raise exc_factory()
        if time.monotonic() - started > params.max_seconds:
            stop_reason = "time_budget"
            break

        grid.build([(p[0], p[1]) for p in pos])
        flat = [(p[0], p[1]) for p in pos]
        dx = [0.0] * n
        dy = [0.0] * n
        pairs = 0
        overlaps = 0

        # ── 排斥：所有真实重叠对（Jacobi 累计） ──
        for i in range(n):
            xi, yi = pos[i]
            ri = float(radii[i])
            for j in grid.neighbors(i, flat):
                if j <= i:
                    continue
                xj, yj = pos[j]
                rj = float(radii[j])
                ddx = xj - xi
                ddy = yj - yi
                dist2 = ddx * ddx + ddy * ddy
                dist = math.sqrt(dist2)
                touch = ri + rj
                pairs += 1
                if pairs > params.max_candidate_pairs:
                    pair_cap_hit = True
                    break
                overlap = touch - dist
                if overlap <= 0.0:
                    continue
                overlaps += 1
                if dist < 1e-12:
                    angle = _stable_angle(feature_ids[i] + "|" + feature_ids[j])
                    nx, ny = math.cos(angle), math.sin(angle)
                else:
                    nx, ny = ddx / dist, ddy / dist
                push = 0.5 * overlap * params.repulsion
                dx[i] -= nx * push
                dy[i] -= ny * push
                dx[j] += nx * push
                dy[j] += ny * push
            if pair_cap_hit:
                break
        if pair_cap_hit:
            stop_reason = "candidate_pair_budget"
            pairs_last, overlaps_last = pairs, overlaps
            break

        # ── 吸引：原邻接目标为相切（d = r_i + r_j） ──
        for (i, j, w) in adjacency:
            if w <= 0.0:
                continue
            ddx = pos[j][0] - pos[i][0]
            ddy = pos[j][1] - pos[i][1]
            dist = math.hypot(ddx, ddy)
            target = float(radii[i]) + float(radii[j])
            err = dist - target
            if err <= 0.0:
                continue  # 重叠由排斥处理，这里不再加力
            if dist < 1e-12:
                angle = _stable_angle(feature_ids[i] + "|" + feature_ids[j])
                nx, ny = math.cos(angle), math.sin(angle)
            else:
                nx, ny = ddx / dist, ddy / dist
            pull = 0.5 * params.attraction * w * err
            dx[i] += nx * pull
            dy[i] += ny * pull
            dx[j] -= nx * pull
            dy[j] -= ny * pull

        # ── 应用（阻尼 + 单步上限；面积/半径为常量） ──
        step_max = 0.0
        for i in range(n):
            sx = dx[i] * params.damping
            sy = dy[i] * params.damping
            mag = math.hypot(sx, sy)
            if mag > max_step:
                k = max_step / mag
                sx *= k
                sy *= k
                mag = max_step
            pos[i][0] += sx
            pos[i][1] += sy
            step_max = max(step_max, mag)
        max_disp = step_max
        pairs_last, overlaps_last = pairs, overlaps

        if step_max <= stop_rel:
            # 稳定了，但仍要看是否还有超阈重叠：不再假装"移动小=成功"。
            significant = 0
            for i in range(n):
                xi, yi = pos[i]
                for j in range(i + 1, n):
                    dist = math.hypot(pos[j][0] - xi, pos[j][1] - yi)
                    ratio = _pair_overlap_ratio(
                        float(radii[i]), float(radii[j]), dist)
                    if ratio > params.overlap_ratio_tolerance:
                        significant += 1
            overlaps_last = significant
            if significant == 0:
                converged = True
                stop_reason = "converged"
            else:
                converged = False
                stop_reason = "stalled_with_overlap"
            break

    return SolveOutcome(
        positions=[(float(p[0]), float(p[1])) for p in pos],
        iterations=iterations,
        converged=bool(converged),
        stop_reason=stop_reason,
        max_displacement=float(max_disp if math.isfinite(max_disp) else 0.0),
        elapsed_s=time.monotonic() - started,
        candidate_pairs_last=int(pairs_last),
        overlapping_pairs_last=int(overlaps_last),
        diagnostics={
            "update_scheme": "jacobi",
            "radii_fixed": True,
            "max_radius": round(max_r, 6),
            "adjacency_edges": len(adjacency),
            "symmetry_break": "stable_hash(feature_id)",
        },
    )
