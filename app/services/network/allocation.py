"""
Network Location-Allocation Service Component.
Implements P-Median and Max Coverage facility location allocation models.

Foundation V3：p-median / p-center 的精确 MILP 变体（scipy.optimize.milp，
HiGHS 分支定界）与既有「小实例 C(m,p) 枚举 + 大实例启发式」并列 —— 精确
MILP 路径有独立规模闸（需求×候选 ≤ 25000 且候选 ≤ 500），超限抛
ResourceScaleMismatch 指向启发式路径（诚实拒绝，绝不静默回退）。

science-v3（审计 04 域 R2/F8）：max_coverage 补齐同款精确 MILP（MCLP，
Church & ReVelle 1974）—— 三目标至此全部有 exact 路径；R3：服务面统一
接入 n×m OD 代价矩阵规模闸（scale_guard.od_matrix_scale_guard），
物化之前先拒绝。
"""
from __future__ import annotations
import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from app.lib.gis.scientific_errors import ResourceScaleMismatch, UnsupportedMethod
from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Facility,
    DemandPoint,
    NetworkAnalysisResult,
)
from app.services.network.scale_guard import od_matrix_scale_guard
from app.services.network.snapping import PointSnappingService
from app.services.network.od_matrix import NetworkODMatrixService

logger = logging.getLogger(__name__)

# GIS-11: exact enumeration of C(m, p) combinations is only tractable for small
# inputs. C(50,5) ≈ 2.1M and C(100,10) ≈ 1.7e13 — the old code materialized the
# full list and hung indefinitely on realistic inputs. Above this threshold the
# solver switches to the classic polynomial heuristics (Teitz-Bart vertex
# substitution for p-median, greedy add for max-coverage). The threshold is
# generous enough to keep exact answers for small problems while bounding the
# worst case: 20000 combinations × O(n·p) evaluation is fast.
_MAX_EXACT_COMBINATIONS = 20000


def _exact_combination_count(m_fac: int, p_count: int) -> int:
    """Number of C(m, p) combinations, capped to avoid overflow."""
    if p_count > m_fac or p_count < 0:
        return 0
    # Use math.comb semantics without importing math: iterative product.
    p = min(p_count, m_fac - p_count)
    count = 1
    for i in range(1, p + 1):
        count = count * (m_fac - p + i) // i
        if count > _MAX_EXACT_COMBINATIONS * 2:
            return count  # early exit, we only need the threshold decision
    return count


# ── Foundation V3：精确 MILP（HiGHS）规模闸 ─────────────────────────
# 变量数 = 候选数 y + 可达对 x：乘积闸直接约束 MILP 规模；候选闸约束分
# 支定界树宽。两个闸都在**任何分配之前**检查；超限抛 ResourceScaleMismatch
# 并把调用方指向启发式路径（solver="heuristic"）—— 诚实拒绝，不静默回退。
_MILP_MAX_PRODUCT = 25000
_MILP_MAX_CANDIDATES = 500

# scipy.optimize.milp 结果状态码 → 诚实披露文本（HiGHS 透传 message 一并返回）。
_MILP_STATUS_TEXT = {
    0: "optimal",
    1: "iteration_or_time_limit",
    2: "infeasible",
    3: "unbounded",
    4: "other",
}


def _milp_scale_guard(n_dem: int, m_fac: int) -> None:
    """Exact-MILP scale guard: refuse BEFORE any model allocation."""
    if m_fac > _MILP_MAX_CANDIDATES:
        raise ResourceScaleMismatch(
            f"精确 MILP 的候选设施数 {m_fac} 超出上限 {_MILP_MAX_CANDIDATES}"
            f"（规模闸：候选 ≤ {_MILP_MAX_CANDIDATES} 且 需求×候选 ≤ {_MILP_MAX_PRODUCT}）",
            estimated=f"candidates={m_fac}",
            limit=f"candidates<={_MILP_MAX_CANDIDATES}",
            correction_hint=(
                "use the heuristic path (location_allocation solver='heuristic', "
                "Teitz-Bart / greedy) or subnet the candidate set"
            ),
        )
    product = n_dem * m_fac
    if product > _MILP_MAX_PRODUCT:
        raise ResourceScaleMismatch(
            f"精确 MILP 的需求×候选规模 {n_dem}×{m_fac}={product} 超出上限 {_MILP_MAX_PRODUCT}"
            f"（规模闸：候选 ≤ {_MILP_MAX_CANDIDATES} 且 需求×候选 ≤ {_MILP_MAX_PRODUCT}）",
            estimated=f"n_demand*n_candidates={product}",
            limit=f"<={_MILP_MAX_PRODUCT}",
            correction_hint=(
                "use the heuristic path (location_allocation solver='heuristic', "
                "Teitz-Bart / greedy) or shrink the problem"
            ),
        )


def _validate_milp_inputs(
    cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
) -> Tuple[np.ndarray, np.ndarray]:
    """代价矩阵/权重校验 → numpy 数组（矩形、行数一致、p 值域）。"""
    if not cost_matrix or not demand_weights:
        raise ValueError("代价矩阵与需求权重不能为空")
    n_dem = len(demand_weights)
    m_fac = len(cost_matrix[0])
    if any(len(row) != m_fac for row in cost_matrix):
        raise ValueError("代价矩阵必须为矩形（n_demand × n_candidates）")
    if len(cost_matrix) != n_dem:
        raise ValueError("代价矩阵行数与需求权重数量不一致")
    if not 1 <= p_count <= m_fac:
        raise ValueError(f"p_count={p_count} 必须落在 [1, {m_fac}]")
    C = np.asarray(cost_matrix, dtype=float)
    w = np.asarray(demand_weights, dtype=float)
    return C, w


def _milp_solve_stats(res: Any) -> Dict[str, Optional[float]]:
    """HiGHS MIP 统计透传（solve_stats 披露；非有限值 → None 而非伪造）。"""
    def _num(value: Any) -> Optional[float]:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if np.isfinite(f) else None

    node_count = getattr(res, "mip_node_count", None)
    return {
        "mip_gap": _num(getattr(res, "mip_gap", None)),
        "mip_node_count": int(node_count) if node_count is not None else None,
        "mip_dual_bound": _num(getattr(res, "mip_dual_bound", None)),
        "lp_objective": _num(getattr(res, "fun", None)),
    }


def solve_p_median_milp(
    cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
) -> Dict[str, Any]:
    """精确 p-中位（0/1 MILP，scipy.optimize.milp / HiGHS 后端）。

    变量：y_f（开站，恰 p 个）+ x_{i,f}（指派，仅可达对）。
    min Σ w_i·c_{i,f}·x_{i,f}；s.t. Σ_f x_{i,f}=1 ∀可指派需求 i；
    x_{i,f} ≤ y_f；Σ_f y_f = p。
    不可达对直接从模型剔除（不引入 1e9 惩罚近似）；全程不可达需求点
    不进模型、由 unassigned_demand_indices 披露 —— 与启发式语义一致。

    确定性：HiGHS 对固定输入确定性复现（无随机成分）。
    规模闸：候选 ≤ 500 且 需求×候选 ≤ 25000，超限抛 ResourceScaleMismatch。
    """
    C, w = _validate_milp_inputs(cost_matrix, demand_weights, p_count)
    n_dem, m_fac = C.shape
    _milp_scale_guard(n_dem, m_fac)

    finite = np.isfinite(C)
    coverable = finite.any(axis=1)
    pairs = np.argwhere(finite)  # row-major：按需求 i 有序
    n_pairs = int(len(pairs))
    n_vars = m_fac + n_pairs

    # 目标向量：y 权 0，x 权 w_i·c_{i,f} —— 需求加权 p-中位目标，与既有
    # 枚举/启发式的 min Σ w_i·min_j C_ij 同一目标（权重不进模型会解出
    # 「无加权意义」的站组：x 系数漏乘 w_i 时 HiGHS 最小化的是无权和）。
    c_obj = np.concatenate(
        [np.zeros(m_fac), w[pairs[:, 0]] * C[pairs[:, 0], pairs[:, 1]]]
    )

    rows: List[int] = []
    cols: List[int] = []
    data: List[float] = []
    lb: List[float] = []
    ub: List[float] = []
    r = 0
    # (1) 指派约束：每个可指派需求 Σ_f x_{i,f} = 1（可指派需求必有 ≥1 可达对）
    for k in range(n_pairs):
        rows.append(r)
        cols.append(m_fac + k)
        data.append(1.0)
        if k + 1 == n_pairs or pairs[k + 1][0] != pairs[k][0]:
            lb.append(1.0)
            ub.append(1.0)
            r += 1
    # (2) 链接约束：x_{i,f} − y_f ≤ 0
    for k, (_i, j) in enumerate(pairs):
        rows.append(r)
        cols.append(m_fac + k)
        data.append(1.0)
        rows.append(r)
        cols.append(int(j))
        data.append(-1.0)
        lb.append(-np.inf)
        ub.append(0.0)
        r += 1
    # (3) 基数约束：Σ_f y_f = p
    for j in range(m_fac):
        rows.append(r)
        cols.append(j)
        data.append(1.0)
    lb.append(float(p_count))
    ub.append(float(p_count))
    r += 1

    A = coo_matrix((data, (rows, cols)), shape=(r, n_vars)).tocsr()
    res = milp(
        c=c_obj,
        constraints=LinearConstraint(A, np.asarray(lb), np.asarray(ub)),
        integrality=np.ones(n_vars),
        bounds=Bounds(np.zeros(n_vars), np.ones(n_vars)),
    )

    y = np.asarray(res.x[:m_fac], dtype=float)
    selected = tuple(int(j) for j in np.where(y > 0.5)[0])
    # 从整数解按枚举同语义重算目标（可指派需求的有限最小成本加权和）；
    # lp_objective（求解器原值）进 solve_stats 披露。
    if selected:
        min_cost = C[:, selected].min(axis=1)
    else:  # pragma: no cover — p≥1 时 HiGHS 必开站
        min_cost = np.full(n_dem, np.inf)
    objective = float(np.sum(w[coverable] * min_cost[coverable])) if coverable.any() else 0.0

    return {
        "selected": selected,
        "objective_value": objective,
        "status_code": int(res.status),
        "optimality": _MILP_STATUS_TEXT.get(int(res.status), "other"),
        "highs_message": str(res.message),
        "solve_stats": _milp_solve_stats(res),
        "unassigned_demand_indices": [int(i) for i in np.where(~coverable)[0]],
        "model_stats": {
            "n_variables": int(n_vars),
            "n_constraints": int(r),
            "n_pairs": n_pairs,
        },
    }


def solve_p_center_milp(
    cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
) -> Dict[str, Any]:
    """精确 p-中心（0/1 MILP + Big-M，scipy.optimize.milp / HiGHS 后端）。

    变量：z（最大服务成本）+ y_f（开站，恰 p 个）+ x_{i,f}（指派，仅可达对）。
    min z；s.t. Σ_f x_{i,f}=1 ∀可指派需求 i；x_{i,f} ≤ y_f；Σ_f y_f = p；
    z ≥ c_{i,f}·x_{i,f} − BigM·(1 − x_{i,f})，BigM = 最大有限代价。
    不可达需求不参与 max 目标（inf 不是服务成本）、由
    unassigned_demand_indices 披露 —— 与既有 p-center 枚举/启发式语义一致。
    """
    C, w = _validate_milp_inputs(cost_matrix, demand_weights, p_count)
    n_dem, m_fac = C.shape
    _milp_scale_guard(n_dem, m_fac)

    finite = np.isfinite(C)
    coverable = finite.any(axis=1)
    pairs = np.argwhere(finite)
    n_pairs = int(len(pairs))
    big_m = float(np.max(C[pairs[:, 0], pairs[:, 1]])) if n_pairs else 0.0
    # 变量序：z(1) + y(m) + x(K)；x 列基 = 1 + m + k，y 列基 = 1 + j
    n_vars = 1 + m_fac + n_pairs
    x_col = 1 + m_fac

    c_obj = np.zeros(n_vars)
    c_obj[0] = 1.0  # min z

    rows: List[int] = []
    cols: List[int] = []
    data: List[float] = []
    lb: List[float] = []
    ub: List[float] = []
    r = 0
    # (1) 指派约束：Σ_f x_{i,f} = 1
    for k in range(n_pairs):
        rows.append(r)
        cols.append(x_col + k)
        data.append(1.0)
        if k + 1 == n_pairs or pairs[k + 1][0] != pairs[k][0]:
            lb.append(1.0)
            ub.append(1.0)
            r += 1
    # (2) 链接约束：x_{i,f} − y_f ≤ 0
    for k, (_i, j) in enumerate(pairs):
        rows.append(r)
        cols.append(x_col + k)
        data.append(1.0)
        rows.append(r)
        cols.append(1 + int(j))
        data.append(-1.0)
        lb.append(-np.inf)
        ub.append(0.0)
        r += 1
    # (3) 基数约束：Σ_f y_f = p
    for j in range(m_fac):
        rows.append(r)
        cols.append(1 + j)
        data.append(1.0)
    lb.append(float(p_count))
    ub.append(float(p_count))
    r += 1
    # (4) Big-M 中心约束：z − (c_{i,f}+BigM)·x_{i,f} ≥ −BigM
    for k, (_i, _j) in enumerate(pairs):
        c_ij = float(C[pairs[k][0], pairs[k][1]])
        rows.append(r)
        cols.append(0)
        data.append(1.0)
        rows.append(r)
        cols.append(x_col + k)
        data.append(-(c_ij + big_m))
        lb.append(-big_m)
        ub.append(np.inf)
        r += 1

    A = coo_matrix((data, (rows, cols)), shape=(r, n_vars)).tocsr()
    bounds_ub = np.concatenate([[big_m], np.ones(1 + m_fac + n_pairs - 1)])
    res = milp(
        c=c_obj,
        constraints=LinearConstraint(A, np.asarray(lb), np.asarray(ub)),
        integrality=np.ones(n_vars),
        bounds=Bounds(np.zeros(n_vars), bounds_ub),
    )

    y = np.asarray(res.x[1:1 + m_fac], dtype=float)
    selected = tuple(int(j) for j in np.where(y > 0.5)[0])
    # 与既有 p_center_objective 同语义重算：max 只覆盖可指派需求（打平的
    # 总加权成本作次级披露；MILP 主目标仅 z —— 披露于 summary）。
    if selected:
        min_cost = C[:, selected].min(axis=1)
    else:  # pragma: no cover — p≥1 时 HiGHS 必开站
        min_cost = np.full(n_dem, np.inf)
    objective = float(np.max(min_cost[coverable])) if coverable.any() else 0.0
    total_weighted = float(np.sum(w[coverable] * min_cost[coverable])) if coverable.any() else 0.0

    return {
        "selected": selected,
        "objective_value": objective,
        "total_weighted_cost": total_weighted,
        "big_m": big_m,
        "status_code": int(res.status),
        "optimality": _MILP_STATUS_TEXT.get(int(res.status), "other"),
        "highs_message": str(res.message),
        "solve_stats": _milp_solve_stats(res),
        "unassigned_demand_indices": [int(i) for i in np.where(~coverable)[0]],
        "model_stats": {
            "n_variables": int(n_vars),
            "n_constraints": int(r),
            "n_pairs": n_pairs,
        },
    }


def solve_max_coverage_milp(
    cost_matrix: List[List[float]],
    demand_weights: List[float],
    p_count: int,
    cutoff: float,
) -> Dict[str, Any]:
    """精确最大覆盖 MCLP（0/1 MILP，scipy.optimize.milp / HiGHS 后端）。

    science-v3（审计 04 域 R2）：三目标中最后补齐的 exact 路径 —— 与
    p-median/p-center MILP 同一求解模式与规模闸。出处 Church & ReVelle
    1974（MCLP 原始文献）。

    变量：y_f（开站，恰 p 个）+ z_i（需求 i 被覆盖）。
    max Σ w_i·z_i；s.t. z_i ≤ Σ_{j∈N_i} y_j ∀ N_i≠∅；Σ_f y_f = p；
    N_i = {j: c_ij ≤ cutoff}（覆盖集；cutoff 外/不可达候选不进集合）。
    scipy.optimize.milp 是最小化器：目标向量取 −w，求解后按整数解重算
    覆盖权重（与 C(m,p) 枚举同语义：Σ w_i·[min_{j∈S} C_ij ≤ cutoff]）。
    覆盖集为空的需求不可能被覆盖：不进模型、由 unassigned_demand_indices
    披露 —— 与枚举/启发式语义一致。

    确定性：HiGHS 对固定输入确定性复现（无随机成分）。
    规模闸：候选 ≤ 500 且 需求×候选 ≤ 25000，超限抛 ResourceScaleMismatch。
    """
    C, w = _validate_milp_inputs(cost_matrix, demand_weights, p_count)
    n_dem, m_fac = C.shape
    _milp_scale_guard(n_dem, m_fac)

    within = np.isfinite(C) & (C <= float(cutoff))
    coverable = within.any(axis=1)
    n_pairs = int(within.sum())
    n_vars = m_fac + n_dem

    # 目标：max Σ w_i·z_i → scipy 最小化 c^T x，z 段系数取 −w
    c_obj = np.concatenate([np.zeros(m_fac), -w])

    rows: List[int] = []
    cols: List[int] = []
    data: List[float] = []
    lb: List[float] = []
    ub: List[float] = []
    r = 0
    # (1) 覆盖约束（每需求一行）：z_i − Σ_{j∈N_i} y_j ≤ 0。z_i 每行只出现
    # 一次 —— coo_matrix 会对同 (row,col) 重复项求和，按覆盖对逐项写 z_i
    # 会把它放大成 |N_i|（写入过松的约束、解出非最优站组，测试对拍捕获）。
    for i in range(n_dem):
        if not coverable[i]:
            continue  # 覆盖集为空：不可能被覆盖，不进模型（unassigned 披露）
        rows.append(r)
        cols.append(m_fac + i)
        data.append(1.0)
        for j in np.flatnonzero(within[i]):
            rows.append(r)
            cols.append(int(j))
            data.append(-1.0)
        lb.append(-np.inf)
        ub.append(0.0)
        r += 1
    # (2) 基数约束：Σ_f y_f = p
    for j in range(m_fac):
        rows.append(r)
        cols.append(j)
        data.append(1.0)
    lb.append(float(p_count))
    ub.append(float(p_count))
    r += 1

    A = coo_matrix((data, (rows, cols)), shape=(r, n_vars)).tocsr()
    res = milp(
        c=c_obj,
        constraints=LinearConstraint(A, np.asarray(lb), np.asarray(ub)),
        integrality=np.ones(n_vars),
        bounds=Bounds(np.zeros(n_vars), np.ones(n_vars)),
    )

    y = np.asarray(res.x[:m_fac], dtype=float)
    selected = tuple(int(j) for j in np.where(y > 0.5)[0])
    # 与枚举同语义重算覆盖目标（求解器原值经 solve_stats/lp_objective 披露）；
    # cutoff 边界含等号（min_c ≤ cutoff 计覆盖，与启发式/枚举一致）。
    if selected:
        min_cost = C[:, selected].min(axis=1)
    else:  # pragma: no cover — p≥1 时 HiGHS 必开站
        min_cost = np.full(n_dem, np.inf)
    covered = min_cost <= float(cutoff)
    objective = float(np.sum(w[covered]))

    return {
        "selected": selected,
        "objective_value": objective,
        "covered_demand_count": int(covered.sum()),
        "cutoff": float(cutoff),
        "status_code": int(res.status),
        "optimality": _MILP_STATUS_TEXT.get(int(res.status), "other"),
        "highs_message": str(res.message),
        "solve_stats": _milp_solve_stats(res),
        "unassigned_demand_indices": [int(i) for i in np.where(~coverable)[0]],
        "model_stats": {
            "n_variables": int(n_vars),
            "n_constraints": int(r),
            "n_pairs": n_pairs,
        },
    }


class NetworkLocationAllocationService:
    """
    Service for selecting optimal facility locations using P-Median or Max Coverage models.
    """

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    # --- GIS-11: polynomial heuristics for large instances ---

    def _solve_p_median_heuristic(
        self, cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
    ) -> Tuple[int, ...]:
        """Teitz-Bart vertex substitution for p-median.

        Starts from the first p facilities, then repeatedly tries replacing one
        selected facility with one unselected candidate when it lowers the
        weighted sum of min-costs. O(passes × p × (m-p) × n); converges in a
        small number of passes on real road networks.
        """
        m_fac = len(cost_matrix[0]) if cost_matrix else 0
        if p_count <= 0 or m_fac == 0:
            return ()

        def evaluate(subset: Tuple[int, ...]) -> float:
            total = 0.0
            for i, w in enumerate(demand_weights):
                min_c = min(cost_matrix[i][j] for j in subset)
                total += (1e9 if min_c == float("inf") else min_c) * w
            return total

        best_subset = tuple(range(p_count))
        best_cost = evaluate(best_subset)

        improved = True
        passes = 0
        while improved and passes < 10:
            improved = False
            passes += 1
            for out_idx in range(p_count):
                for cand in range(m_fac):
                    if cand in best_subset:
                        continue
                    trial = list(best_subset)
                    trial[out_idx] = cand
                    trial_cost = evaluate(tuple(trial))
                    if trial_cost < best_cost - 1e-12:
                        best_subset = tuple(trial)
                        best_cost = trial_cost
                        improved = True
        return best_subset

    def _solve_p_center_heuristic(
        self, cost_matrix: List[List[float]], demand_weights: List[float], p_count: int
    ) -> Tuple[int, ...]:
        """Greedy-add start + vertex-substitution swaps for p-center.

        Initialization is greedy-add: repeatedly add the candidate that
        minimizes the max-min objective (classic k-center greedy) — the raw
        "first p facilities" start stalls in local optima on corridor
        networks. The subsequent vertex-substitution phase accepts a swap
        when it lowers the (max_cost, total_cost) lexicographic objective,
        ≤10 passes. Unreachable demand is excluded from max_cost (disclosed
        as unassigned) but penalized in total_cost so the search prefers
        subsets that reach more demand.
        """
        m_fac = len(cost_matrix[0]) if cost_matrix else 0
        if p_count <= 0 or m_fac == 0:
            return ()

        def evaluate(subset: Tuple[int, ...]) -> Tuple[float, float]:
            max_c = 0.0
            total = 0.0
            for i, w in enumerate(demand_weights):
                min_c = min(cost_matrix[i][j] for j in subset) if subset else float("inf")
                if min_c == float("inf"):
                    total += 1e9 * w  # 结构性不可达惩罚进 total，不进 max
                else:
                    max_c = max(max_c, min_c)
                    total += min_c * w
            return max_c, total

        # Greedy-add initialization: at each step pick the candidate whose
        # addition minimizes the objective of the GROWN set (candidates are
        # compared against each other — the max component is not comparable
        # across set sizes). Deterministic: strict <, first wins ties.
        best_subset: Tuple[int, ...] = ()
        for _ in range(p_count):
            best_cand = -1
            best_obj: Optional[Tuple[float, float]] = None
            for cand in range(m_fac):
                if cand in best_subset:
                    continue
                obj = evaluate(best_subset + (cand,))
                if best_obj is None or obj < best_obj:
                    best_obj = obj
                    best_cand = cand
            if best_cand < 0:
                break  # no candidate left (m_fac < p is clamped upstream)
            best_subset = best_subset + (best_cand,)
        best_obj = evaluate(best_subset)

        improved = True
        passes = 0
        while improved and passes < 10:
            improved = False
            passes += 1
            for out_idx in range(len(best_subset)):
                for cand in range(m_fac):
                    if cand in best_subset:
                        continue
                    trial = list(best_subset)
                    trial[out_idx] = cand
                    trial_obj = evaluate(tuple(trial))
                    if trial_obj < best_obj:
                        best_subset = tuple(trial)
                        best_obj = trial_obj
                        improved = True
        return best_subset

    def _solve_max_coverage_heuristic(
        self,
        cost_matrix: List[List[float]],
        demand_weights: List[float],
        p_count: int,
        cutoff: float,
    ) -> Tuple[int, ...]:
        """Greedy-add for max coverage: at each step pick the facility that
        covers the most currently-uncovered demand weight."""
        m_fac = len(cost_matrix[0]) if cost_matrix else 0
        if p_count <= 0 or m_fac == 0:
            return ()

        n_dem = len(demand_weights)
        covered = [False] * n_dem
        selected: List[int] = []

        for _ in range(p_count):
            best_j = -1
            best_gain = -1.0
            for j in range(m_fac):
                if j in selected:
                    continue
                gain = 0.0
                for i in range(n_dem):
                    if not covered[i] and cost_matrix[i][j] <= cutoff:
                        gain += demand_weights[i]
                if gain > best_gain:
                    best_gain = gain
                    best_j = j
            if best_j < 0:
                break
            selected.append(best_j)
            for i in range(n_dem):
                if not covered[i] and cost_matrix[i][best_j] <= cutoff:
                    covered[i] = True

        return tuple(selected)

    def _od_cost_matrix(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
    ) -> List[List[float]]:
        """需求×候选的路网代价矩阵（不可达 = inf）。

        location_allocation（枚举/启发式）与精确 MILP 路径共用同一 OD
        语义 —— 同一请求两条路径的代价输入逐位一致。
        """
        orig_coords = [(d.geometry["coordinates"][0], d.geometry["coordinates"][1]) for d in demand_points]
        dest_coords = [(f.geometry["coordinates"][0], f.geometry["coordinates"][1]) for f in candidate_facilities]

        od_pairs = self.od_service.network_od_matrix(
            origins=orig_coords,
            destinations=dest_coords,
            graph=graph,
            network_dataset=network_dataset,
            profile=profile,
        )

        n_dem = len(demand_points)
        m_fac = len(candidate_facilities)

        # Build Cost Matrix cost_matrix[i][j]
        cost_matrix: List[List[float]] = [[float("inf")] * m_fac for _ in range(n_dem)]
        for i in range(n_dem):
            for j in range(m_fac):
                idx = i * m_fac + j
                if idx < len(od_pairs) and od_pairs[idx].reachable:
                    cost_matrix[i][j] = od_pairs[idx].travel_time_s
        return cost_matrix

    def location_allocation(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        problem_type: str = "p_median",
        cutoff_cost: Optional[float] = None,
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
        solver: str = "auto",
    ) -> NetworkAnalysisResult:
        """
        Solves Location-Allocation problem (P-Median, Max Coverage or P-Center).

        Args:
            candidate_facilities: List of candidate Facility objects.
            demand_points: List of DemandPoint objects.
            p_count: Number of facilities to select.
            problem_type: 'p_median', 'max_coverage' or 'p_center'.
            cutoff_cost: Optional cost cutoff threshold.
            graph: NetworkX DiGraph.
            network_dataset: NetworkDataset model.
            profile: TravelProfile.
            solver: Foundation V3 —— 'auto'（历史行为：小实例 C(m,p) 枚举、
                大实例启发式）| 'heuristic'（强制 Teitz-Bart / 贪婪）|
                'exact_milp'（强制 HiGHS 精确式，p_median / max_coverage /
                p_center 三目标；超规模闸抛 ResourceScaleMismatch，不静默回退）。

        Returns:
            NetworkAnalysisResult containing allocated facilities and assignments.
        """
        if solver not in ("auto", "heuristic", "exact_milp"):
            raise UnsupportedMethod(
                f"未知求解路径 {solver!r}（合法：auto | heuristic | exact_milp）",
                correction_hint="choose solver='auto' (default), 'heuristic', or 'exact_milp'",
            )
        problem_norm = problem_type.lower()
        if solver == "exact_milp":
            if problem_norm == "p_median":
                return self.p_median_exact(
                    candidate_facilities=candidate_facilities,
                    demand_points=demand_points,
                    p_count=p_count,
                    graph=graph,
                    network_dataset=network_dataset,
                    profile=profile,
                )
            if problem_norm == "p_center":
                return self.p_center_exact(
                    candidate_facilities=candidate_facilities,
                    demand_points=demand_points,
                    p_count=p_count,
                    graph=graph,
                    network_dataset=network_dataset,
                    profile=profile,
                )
            if problem_norm == "max_coverage":
                # science-v3（审计 R2）：MCLP 精确 MILP —— 三目标至此全部
                # 有 exact 路径；不再 UnsupportedMethod。
                return self.max_coverage_exact(
                    candidate_facilities=candidate_facilities,
                    demand_points=demand_points,
                    p_count=p_count,
                    cutoff_cost=cutoff_cost,
                    graph=graph,
                    network_dataset=network_dataset,
                    profile=profile,
                )
            raise UnsupportedMethod(
                f"问题类型 {problem_type!r} 不提供 exact_milp 精确求解器"
                "（MILP 精确式覆盖 p_median / max_coverage / p_center）",
                correction_hint=(
                    "use one of p_median | max_coverage | p_center for "
                    "solver='exact_milp'"
                ),
            )

        if not candidate_facilities or not demand_points or p_count <= 0:
            return NetworkAnalysisResult(
                analysis_type="location_allocation",
                status="success",
                summary={"problem_type": problem_type, "p_count": p_count, "selected_count": 0},
            )

        # science-v3 R3：n×m OD 代价矩阵统一规模闸 —— 与求解路径无关的
        # 资源包络，任何矩阵物化之前诚实拒绝（不静默回退/截断）。
        od_matrix_scale_guard(
            len(demand_points), len(candidate_facilities),
            context="location_allocation",
        )

        p_count = min(p_count, len(candidate_facilities))
        n_dem = len(demand_points)
        m_fac = len(candidate_facilities)

        cost_matrix = self._od_cost_matrix(
            candidate_facilities, demand_points,
            graph=graph, network_dataset=network_dataset, profile=profile,
        )

        # GIS-11: exact enumeration for tractable instances; polynomial
        # heuristics (Teitz-Bart / greedy-add) beyond that so real inputs
        # (e.g. choose 5 of 80 candidates) terminate instead of hanging.
        # solver='heuristic' 强制走启发式（跳过枚举分支，披露不变）。
        n_combos = _exact_combination_count(m_fac, p_count)
        use_exact = solver == "auto" and n_combos <= _MAX_EXACT_COMBINATIONS
        solver_used = "exact" if use_exact else "heuristic"

        demand_weights = [d.weight for d in demand_points]

        # p-center（Hakimi 1964 max-min）目标：可达需求的最大服务成本最小化。
        # 不可达需求不参与目标（inf 不是服务成本），事后以 unassigned 披露；
        # max 打平时用总加权成本做次级判据，避免局部搜索在平台上停滞。
        if problem_norm == "p_center":

            def p_center_objective(subset: Tuple[int, ...]) -> Tuple[float, float]:
                max_c = 0.0
                total = 0.0
                for i, w in enumerate(demand_weights):
                    min_c = min(cost_matrix[i][j] for j in subset)
                    if min_c == float("inf"):
                        total += 1e9 * w
                    else:
                        max_c = max(max_c, min_c)
                        total += min_c * w
                return max_c, total

        if problem_norm == "max_coverage":
            cutoff = cutoff_cost if cutoff_cost is not None else 900.0  # default 15 min
            if use_exact:
                best_coverage = -1.0
                best_subset = tuple(range(p_count))
                for combo in itertools.combinations(range(m_fac), p_count):
                    coverage = 0.0
                    for i in range(n_dem):
                        min_c = min(cost_matrix[i][j] for j in combo)
                        if min_c <= cutoff:
                            coverage += demand_weights[i]
                    if coverage > best_coverage:
                        best_coverage = coverage
                        best_subset = combo
            else:
                logger.info(
                    "location_allocation max_coverage: C(%d,%d)=%d combinations exceed exact "
                    "limit (%d); using greedy-add heuristic",
                    m_fac, p_count, n_combos, _MAX_EXACT_COMBINATIONS,
                )
                best_subset = self._solve_max_coverage_heuristic(
                    cost_matrix, demand_weights, p_count, cutoff
                )
        elif problem_norm == "p_center":
            if use_exact:
                best_obj: Tuple[float, float] = (float("inf"), float("inf"))
                best_subset = tuple(range(p_count))
                for combo in itertools.combinations(range(m_fac), p_count):
                    obj = p_center_objective(combo)
                    if obj < best_obj:
                        best_obj = obj
                        best_subset = combo
            else:
                logger.info(
                    "location_allocation p_center: C(%d,%d)=%d combinations exceed exact "
                    "limit (%d); using greedy + vertex-substitution heuristic",
                    m_fac, p_count, n_combos, _MAX_EXACT_COMBINATIONS,
                )
                best_subset = self._solve_p_center_heuristic(
                    cost_matrix, demand_weights, p_count
                )
        else:
            # P-Median: minimize sum_i w_i * min_{j in S} C_{i,j}
            if use_exact:
                best_impedance = float("inf")
                best_subset = tuple(range(p_count))
                for combo in itertools.combinations(range(m_fac), p_count):
                    total_w_cost = 0.0
                    for i in range(n_dem):
                        min_c = min(cost_matrix[i][j] for j in combo)
                        if min_c == float("inf"):
                            total_w_cost += 1e9 * demand_weights[i]
                        else:
                            total_w_cost += min_c * demand_weights[i]
                    if total_w_cost < best_impedance:
                        best_impedance = total_w_cost
                        best_subset = combo
            else:
                logger.info(
                    "location_allocation p_median: C(%d,%d)=%d combinations exceed exact "
                    "limit (%d); using Teitz-Bart heuristic",
                    m_fac, p_count, n_combos, _MAX_EXACT_COMBINATIONS,
                )
                best_subset = self._solve_p_median_heuristic(
                    cost_matrix, demand_weights, p_count
                )

        # Generate allocation results; unreachable demand points are
        # collected as unassigned rather than silently assigned to the first
        # facility (inf cost would otherwise make min() pick index 0).
        unassigned_ids: List[str] = []
        allocated_facilities: List[Dict[str, Any]] = []
        for fac_idx in best_subset:
            fac = candidate_facilities[fac_idx]
            assigned_demands: List[str] = []
            total_assigned_weight = 0.0

            for i in range(n_dem):
                best_cost = min(cost_matrix[i][j] for j in best_subset)
                if best_cost == float("inf"):
                    # 不可达需求点统一在后置遍历收进 unassigned_ids（此处
                    # 跳过；曾有一段只对首个设施生效的死分支，已删——#693）
                    continue
                best_fac_idx = min(best_subset, key=lambda j: cost_matrix[i][j])
                if best_fac_idx == fac_idx:
                    assigned_demands.append(demand_points[i].demand_id)
                    total_assigned_weight += demand_points[i].weight

            allocated_facilities.append({
                "facility_id": fac.facility_id,
                "name": fac.name,
                "geometry": fac.geometry,
                "assigned_demand_count": len(assigned_demands),
                "assigned_total_weight": total_assigned_weight,
                "assigned_demand_ids": assigned_demands,
            })

        for i in range(n_dem):
            best_cost = min(cost_matrix[i][j] for j in best_subset) if best_subset else float("inf")
            if best_cost == float("inf"):
                unassigned_ids.append(demand_points[i].demand_id)

        summary = {
            "problem_type": problem_type,
            "p_count": p_count,
            "selected_facilities_count": len(best_subset),
            "total_demand_count": n_dem,
            "candidate_facility_count": m_fac,
            # GIS-11: "exact" (enumerated C(m,p)) or "heuristic" (Teitz-Bart /
            # greedy-add). Heuristic results are near-optimal, not guaranteed
            # optimal — explicit so consumers can weigh the trade-off.
            "solver": solver_used,
            "unassigned_count": len(unassigned_ids),
            "unassigned_ids": unassigned_ids,
        }
        if solver != "auto":
            # 显式请求的求解路径如实回显（auto 历史行为不新增键）。
            summary["solver_request"] = solver

        if problem_norm == "p_center":
            # p-center 披露：目标值 = 可达需求的最大服务成本（不可达需求已
            # 从目标剔除并列入 unassigned_ids —— inf 不冒充服务成本）。
            max_service_cost, total_weighted_cost = p_center_objective(best_subset)
            summary["max_service_cost"] = round(max_service_cost, 2)
            summary["total_weighted_cost"] = round(total_weighted_cost, 2)

        return NetworkAnalysisResult(
            analysis_type="location_allocation",
            status="success",
            summary=summary,
            allocated_facilities=allocated_facilities,
        )

    # --- Foundation V3：精确 MILP 服务面（签名镜像 location_allocation）---

    def p_median_exact(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        cutoff_cost: Optional[float] = None,  # 签名对齐；p-median 精确式无 cutoff 语义
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
    ) -> NetworkAnalysisResult:
        """精确 p-中位（HiGHS MILP）：输入签名与 location_allocation 镜像。

        目标 min Σ w_i·min_{j∈S} C_ij 的全局最优；代价矩阵与启发式路径同源
        （_od_cost_matrix）。规模闸（候选 ≤ 500 且 需求×候选 ≤ 25000）超限
        抛 ResourceScaleMismatch —— 诚实拒绝，不静默回退启发式。
        """
        if not candidate_facilities or not demand_points or p_count <= 0:
            return NetworkAnalysisResult(
                analysis_type="location_allocation",
                status="success",
                summary={"problem_type": "p_median", "p_count": p_count,
                         "selected_count": 0, "solver": "milp_highs"},
            )
        p_count = min(p_count, len(candidate_facilities))
        # R3：OD 矩阵物化之前过统一规模闸（MILP 模型闸在求解器入口兜底）。
        od_matrix_scale_guard(
            len(demand_points), len(candidate_facilities),
            context="p_median_exact",
            max_pairs=_MILP_MAX_PRODUCT,
        )
        cost_matrix = self._od_cost_matrix(
            candidate_facilities, demand_points,
            graph=graph, network_dataset=network_dataset, profile=profile,
        )
        milp_out = solve_p_median_milp(
            cost_matrix, [d.weight for d in demand_points], p_count
        )
        return self._exact_milp_result(
            candidate_facilities, demand_points, p_count, "p_median",
            cost_matrix, milp_out,
        )

    def p_center_exact(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        cutoff_cost: Optional[float] = None,  # 签名对齐；p-center 精确式无 cutoff 语义
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
    ) -> NetworkAnalysisResult:
        """精确 p-中心（HiGHS MILP，Big-M 最大服务成本式）。

        目标 = 最小化可指派需求的最大服务成本（与既有 p-center 枚举/启发
        式同语义：不可达需求不进 max 目标、以 unassigned 披露）。规模闸同
        p_median_exact，超限诚实拒绝。
        """
        if not candidate_facilities or not demand_points or p_count <= 0:
            return NetworkAnalysisResult(
                analysis_type="location_allocation",
                status="success",
                summary={"problem_type": "p_center", "p_count": p_count,
                         "selected_count": 0, "solver": "milp_highs"},
            )
        p_count = min(p_count, len(candidate_facilities))
        # R3：OD 矩阵物化之前过统一规模闸（MILP 模型闸在求解器入口兜底）。
        od_matrix_scale_guard(
            len(demand_points), len(candidate_facilities),
            context="p_center_exact",
            max_pairs=_MILP_MAX_PRODUCT,
        )
        cost_matrix = self._od_cost_matrix(
            candidate_facilities, demand_points,
            graph=graph, network_dataset=network_dataset, profile=profile,
        )
        milp_out = solve_p_center_milp(
            cost_matrix, [d.weight for d in demand_points], p_count
        )
        return self._exact_milp_result(
            candidate_facilities, demand_points, p_count, "p_center",
            cost_matrix, milp_out,
        )

    def max_coverage_exact(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        cutoff_cost: Optional[float] = None,
        graph: Optional[nx.DiGraph] = None,
        network_dataset: Optional[NetworkDataset] = None,
        profile: Optional[TravelProfile] = None,
    ) -> NetworkAnalysisResult:
        """精确最大覆盖 MCLP（HiGHS MILP）：输入签名与 location_allocation 镜像。

        目标 max Σ w_i·[cutoff 内被覆盖] 的全局最优（Church & ReVelle 1974）；
        cutoff 缺省 900s（=15min，与启发式 max_coverage 路径同缺省）。代价
        矩阵与启发式路径同源（_od_cost_matrix）。规模闸同 p_median_exact，
        超限诚实拒绝。
        """
        if not candidate_facilities or not demand_points or p_count <= 0:
            return NetworkAnalysisResult(
                analysis_type="location_allocation",
                status="success",
                summary={"problem_type": "max_coverage", "p_count": p_count,
                         "selected_count": 0, "solver": "milp_highs"},
            )
        p_count = min(p_count, len(candidate_facilities))
        cutoff = float(cutoff_cost) if cutoff_cost is not None else 900.0
        # R3：OD 矩阵物化之前过统一规模闸（MILP 模型闸在求解器入口兜底）。
        od_matrix_scale_guard(
            len(demand_points), len(candidate_facilities),
            context="max_coverage_exact",
            max_pairs=_MILP_MAX_PRODUCT,
        )
        cost_matrix = self._od_cost_matrix(
            candidate_facilities, demand_points,
            graph=graph, network_dataset=network_dataset, profile=profile,
        )
        milp_out = solve_max_coverage_milp(
            cost_matrix, [d.weight for d in demand_points], p_count, cutoff
        )
        return self._exact_milp_result(
            candidate_facilities, demand_points, p_count, "max_coverage",
            cost_matrix, milp_out,
        )

    def _exact_milp_result(
        self,
        candidate_facilities: List[Facility],
        demand_points: List[DemandPoint],
        p_count: int,
        problem_type: str,
        cost_matrix: List[List[float]],
        milp_out: Dict[str, Any],
    ) -> NetworkAnalysisResult:
        """MILP 求解输出 → NetworkAnalysisResult（与启发式同形状 + 求解器披露）。

        指派语义与启发式一致：每个可指派需求 → 选中设施中成本最小者
        （平手取索引最小）。不可达需求点统一进 unassigned_ids。
        """
        n_dem = len(demand_points)
        selected = milp_out["selected"]
        unassigned_indices = set(milp_out["unassigned_demand_indices"])

        allocated_facilities: List[Dict[str, Any]] = []
        for fac_idx in selected:
            fac = candidate_facilities[fac_idx]
            assigned_demands: List[str] = []
            total_assigned_weight = 0.0
            for i in range(n_dem):
                if i in unassigned_indices:
                    continue
                costs = cost_matrix[i]
                best_fac_idx = min(selected, key=lambda j: costs[j])
                if best_fac_idx == fac_idx:
                    assigned_demands.append(demand_points[i].demand_id)
                    total_assigned_weight += demand_points[i].weight
            allocated_facilities.append({
                "facility_id": fac.facility_id,
                "name": fac.name,
                "geometry": fac.geometry,
                "assigned_demand_count": len(assigned_demands),
                "assigned_total_weight": total_assigned_weight,
                "assigned_demand_ids": assigned_demands,
            })

        unassigned_ids = [demand_points[i].demand_id for i in sorted(unassigned_indices)]

        summary: Dict[str, Any] = {
            "problem_type": problem_type,
            "p_count": p_count,
            "selected_facilities_count": len(selected),
            "total_demand_count": n_dem,
            "candidate_facility_count": len(candidate_facilities),
            # 精确 MILP 披露：求解器 / 最优性状态（HiGHS 透传）/ 目标值 /
            # 求解统计 —— 绝不让近似结果冒充精确。
            "solver": "milp_highs",
            "solver_request": "exact_milp",
            "objective_value": round(float(milp_out["objective_value"]), 6),
            "optimality": milp_out["optimality"],
            "highs_status": milp_out["highs_message"],
            "solve_stats": milp_out["solve_stats"],
            "model_stats": milp_out["model_stats"],
            "unassigned_count": len(unassigned_ids),
            "unassigned_ids": unassigned_ids,
        }
        if problem_type == "p_center":
            summary["max_service_cost"] = round(float(milp_out["objective_value"]), 2)
            summary["total_weighted_cost"] = round(float(milp_out["total_weighted_cost"]), 2)
            summary["big_m"] = float(milp_out["big_m"])
        if problem_type == "max_coverage":
            # MCLP 披露：覆盖半径（活动阻抗单位）与被覆盖需求点数
            # （objective_value 即覆盖需求权重，与枚举/启发式同语义）。
            summary["cutoff_cost"] = float(milp_out["cutoff"])
            summary["covered_demand_count"] = int(milp_out["covered_demand_count"])

        return NetworkAnalysisResult(
            analysis_type="location_allocation",
            status="success",
            summary=summary,
            allocated_facilities=allocated_facilities,
        )
