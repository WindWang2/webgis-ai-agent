"""Sequential Gaussian Simulation（SGS）—— 条件高斯模拟（science-v4 W5）。

克里金给出「最优估计 + 方差」，但方差面不是分布：风险制图 / P10-P50-
P90 / 不确定性传播需要**多实现采样**。SGS 是标准答案：在 normal-score
域沿随机路径逐节点做条件 SK（条件信息 = 原始样本 + 已模拟节点），模拟
值加入条件集后继续，最后经后向变换回到原始值域。

科学契约（descriptor ``interpolation.sgs``）：

- **可复现**：``caller_seeded`` —— 单一 ``numpy`` PCG64 流，随机路径与
  模拟噪声同源；同 seed 逐位一致（conformance 固定）；
- **资源有界**：``n_realizations × n_targets`` 预算硬闸（先拒绝，不
  OOM）；邻域 ≤ MAX_NEIGHBORS；
- **可取消**：逐 realization 边界 checkpoint（``chunk_boundary``）；
- **不确定性有源**：ensemble 统计（P10/P50/P90/E-type）来自真实多实现，
  绝不虚构；
- 条件 SK 复用 :mod:`kriging` 的求解机器与 normal-score 变换（单一事实
  源，不复制地统计代码）。

近似语义（approximate=True）：逐节点只条件于 k 近邻（全局条件集近似）
+ 模拟路径随机化 —— 多实现统计是蒙特卡洛近似，非精确分布。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    ResourceScaleMismatch,
)

from app.lib.geo_analysis.kriging import (
    MAX_NEIGHBORS,
    VariogramFit,
    apply_anisotropy,
    fit_variogram,
    normal_score_transform,
    _gamma,
)

logger = logging.getLogger(__name__)

# ── resource ceilings（descriptor resource_envelope 对齐）─────────────────
SGS_MAX_REALIZATIONS = 2_000            # 实现数上限
# review R2-M7：逐节点 Python 条件循环（O(k³)/节点 + chunk 树重建）的
# 可操作规模上限 —— 4M 格点在纯 Python 下不可完成，收紧到 20 万。
SGS_MAX_TARGETS = 200_000               # reference 路径目标格点上限
SGS_MAX_ENSEMBLE_CELLS = 20_000_000     # n_realizations × n_targets 硬顶
SGS_SIM_CHUNK = 1_024                   # reference 模拟节点分块（条件树重建节奏）
# ── science-v5 W5：batched 变体 ──────────────────────────────────────────
SGS_BATCHED_MAX_TARGETS = 2_000_000     # batched 路径绝对上限（O(R·T) 输出矩阵外的工作集有界）
SGS_BATCHED_CHUNK = 256                 # batched chunk（(B,2k,2k) 协方差栈 @k=24 ≈ 4.7MB）
SGS_BATCHED_DEFAULT_GROUPS = 8          # 默认路径组数（组间路径方差回入 ensemble）


@dataclass
class SGSEnsemble:
    """多实现 ensemble（逐目标统计）。

    ``realizations`` 仅在调用方显式请求时携带（O(R×N) 内存契约：默认
    只输出统计量，绝不静默搬运大矩阵）。
    """

    targets: np.ndarray                     # (N, 2) metric targets
    mean: np.ndarray                        # (N,) E-type 均值
    std: np.ndarray                         # (N,) 实现间标准差（ddof=1）
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    n_realizations: int
    seed: int
    variogram: VariogramFit                 # normal-score 域的拟合
    transform_info: dict                    # 变换状态摘要（不含完整 ECDF）
    disclosures: list[str]
    realizations: Optional[np.ndarray] = None  # (R, N) 原始值域
    n_degenerate_nodes: int = 0             # 病态邻域回退计数（review R2-M3）
    backend: str = "numpy_reference"        # 实现变体 id（science-v5 W5）

    def to_dict(self) -> dict:
        def rng(a: np.ndarray) -> list[float]:
            return [round(float(a.min()), 6), round(float(a.max()), 6)]

        return {
            "n_realizations": int(self.n_realizations),
            "seed": int(self.seed),
            "backend": self.backend,
            "n_targets": int(len(self.mean)),
            "ensemble_mean_range": rng(self.mean),
            "ensemble_std_range": rng(self.std),
            "p10_range": rng(self.p10),
            "p50_range": rng(self.p50),
            "p90_range": rng(self.p90),
            "variogram": self.variogram.params(),
            "transform": dict(self.transform_info),
            "n_degenerate_nodes": int(self.n_degenerate_nodes),
            "disclosures": list(self.disclosures),
        }


def sequential_gaussian_simulation(
    pts_metric: np.ndarray,
    values: np.ndarray,
    target_pts: np.ndarray,
    n_realizations: int = 100,
    seed: int = 42,
    variogram: Optional[VariogramFit] = None,
    k: int = 16,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    return_realizations: bool = False,
) -> SGSEnsemble:
    """条件序贯高斯模拟（normal-score 域 SK + 随机路径 + 后向变换）。

    算法（Goovaerts 1997 标准流程的 k 邻域近似）：

    1. normal-score 变换 → 标准正态分数；
    2. 在变换域拟合变异函数（或消费调用方传入的拟合）；
    3. 每个 realization：随机路径走目标格点；逐节点用「原始样本 + 已
       模拟节点」的 k 近邻做协方差形式 SK（均值 0），模拟值 = 预测 +
       √var·N(0,1)；条件树每 ``SGS_SIM_CHUNK`` 个节点增量重建（近似
       语义：新模拟值的可见性滞后 ≤1 chunk，disclosed）；
    4. 后向变换回原始值域；ensemble 统计量从实现矩阵计算。

    Raises:
        InsufficientSamples / DegenerateData: 样本或变换退化。
        ResourceScaleMismatch: 实现/格点/ensemble 预算超限。
    """
    pts_metric = np.asarray(pts_metric, dtype=float)
    values = np.asarray(values, dtype=float)
    target_pts = np.asarray(target_pts, dtype=float)
    n_realizations = int(n_realizations)
    if not 1 <= n_realizations <= SGS_MAX_REALIZATIONS:
        raise ResourceScaleMismatch(
            f"n_realizations 必须在 [1, {SGS_MAX_REALIZATIONS}]，got {n_realizations}",
            estimated=f"{n_realizations} realizations",
            limit=f"≤{SGS_MAX_REALIZATIONS}",
            correction_hint="降低实现数或分批调用（不同 seed 合并 ensemble）",
        )
    n_t = len(target_pts)
    if n_t > SGS_MAX_TARGETS:
        raise ResourceScaleMismatch(
            f"目标格点 {n_t:,} 超过 SGS 上限 {SGS_MAX_TARGETS:,}",
            estimated=f"{n_t} targets", limit=f"≤{SGS_MAX_TARGETS}",
            correction_hint="降低 H3 分辨率或缩小范围")
    ensemble_cells = n_realizations * n_t
    if ensemble_cells > SGS_MAX_ENSEMBLE_CELLS:
        raise ResourceScaleMismatch(
            f"ensemble 预算 {n_realizations}×{n_t}={ensemble_cells:,} 单元"
            f"超过上限 {SGS_MAX_ENSEMBLE_CELLS:,}",
            estimated=f"{ensemble_cells} cells",
            limit=f"≤{SGS_MAX_ENSEMBLE_CELLS}",
            correction_hint="降低实现数或目标格点数（分辨率/范围）")
    if len(values) < 8:
        raise InsufficientSamples(
            f"SGS 至少需要 8 个样本（与克里金同底），got {len(values)}")

    # 1) normal-score 域
    z_scores, ns_state = normal_score_transform(values)

    # 2) 变换域变异函数（有界拟合；或消费调用方拟合）
    if variogram is None:
        variogram = fit_variogram(
            pts_metric, z_scores, model="auto",
            anisotropy_angle=anisotropy_angle,
            anisotropy_ratio=anisotropy_ratio)

    k = int(max(2, min(k, MAX_NEIGHBORS, len(values))))
    rng = np.random.default_rng(seed)  # 单流：路径 + 噪声（caller_seeded）
    pts_t = apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio)
    targets_t = apply_anisotropy(target_pts, anisotropy_angle, anisotropy_ratio)
    data_tree = cKDTree(pts_t)

    # 预取每目标的原始样本 k 近邻（条件集中不变的部分）
    d0, i0 = data_tree.query(targets_t, k=k)
    d0 = np.asarray(d0, dtype=float).reshape(n_t, k)
    i0 = np.asarray(i0, dtype=int).reshape(n_t, k)

    g = variogram
    structures = getattr(g, "structures", None)
    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    total_sill = abs(float(g.sill)) + abs(float(g.nugget))

    def cov(h: np.ndarray) -> np.ndarray:
        """协方差 C(h) = total_sill − γ(h)（normal-score 域）。"""
        return total_sill - _gamma(g.model, h, g.sill, g.range_m, g.nugget,
                                   nu=g.nu, structures=structures)

    realizations = np.empty((n_realizations, n_t), dtype=float)
    n_degenerate_nodes = 0
    for r in cancellable(range(n_realizations), every=1):
        path = rng.permutation(n_t)
        sim_values = np.empty(n_t, dtype=float)   # normal-score 域
        sim_coords = np.empty((n_t, 2), dtype=float)
        n_sim = 0
        sim_tree: Optional[cKDTree] = None
        sim_k_d = np.empty((0,), dtype=float)
        sim_k_i = np.empty((0,), dtype=int)
        for start in range(0, n_t, SGS_SIM_CHUNK):
            # 条件树增量重建（chunk 节奏）：模拟值可见性滞后 ≤1 chunk ——
            # k 邻域近似语义的一部分（approximate=True，disclosed）。
            if n_sim:
                sim_tree = cKDTree(sim_coords[:n_sim])
            for t_idx in path[start:start + SGS_SIM_CHUNK]:
                if sim_tree is not None and n_sim:
                    k_sim = min(k, n_sim)
                    sd, si = sim_tree.query(targets_t[t_idx], k=k_sim)
                    sim_k_d = np.atleast_1d(np.asarray(sd, dtype=float))
                    sim_k_i = np.atleast_1d(np.asarray(si, dtype=int))
                else:
                    sim_k_d = np.empty((0,), dtype=float)
                    sim_k_i = np.empty((0,), dtype=int)
                k_sim = len(sim_k_d)

                nb_xy = pts_t[i0[t_idx]]
                nb_z = z_scores[i0[t_idx]]
                c0_data = cov(d0[t_idx])
                m = k + k_sim
                if m == 0:  # 不可达（k≥2 时不会发生；防御性保底）
                    sim_values[t_idx] = rng.standard_normal()
                    sim_coords[n_sim] = targets_t[t_idx]
                    n_sim += 1
                    continue
                C = np.empty((m, m), dtype=float)
                diff = nb_xy[:, None, :] - nb_xy[None, :, :]
                C[:k, :k] = cov(np.sqrt((diff ** 2).sum(axis=-1)))
                np.fill_diagonal(C[:k, :k], total_sill - ridge)
                cond_vals = nb_z
                rhs = c0_data
                if k_sim:
                    sim_xy = sim_coords[sim_k_i]
                    cross = np.empty((k, k_sim), dtype=float)
                    for j, p in enumerate(sim_xy):
                        cross[:, j] = cov(np.sqrt(((nb_xy - p) ** 2).sum(axis=1)))
                    C[:k, k:] = cross
                    C[k:, :k] = cross.T
                    # science-v5 W5 修复（本域 P1）：模拟节点间互相关取真实
                    # 协方差——原对角近似把空间相近的已模拟点当互不相关，
                    # 协方差模型不一致 → 系统非正定 → 序贯区制（>1 chunk）
                    # 权重/方差爆炸（var ≫ sill）。≤1 chunk 时 sim 条件集为
                    # 空、行为不变（既有 oracle/测试锚定该区制）。
                    d_ss = np.sqrt(((sim_xy[:, None, :] - sim_xy[None, :, :])
                                    ** 2).sum(-1))
                    C[k:, k:] = cov(d_ss)
                    np.fill_diagonal(C[k:, k:], total_sill - ridge)
                    # W5 修复（条件值索引）：sim_k_i 是条件树**位置**索引，
                    # 取模拟值必须经 path 映射回目标序号。
                    cond_sim_vals = sim_values[path[sim_k_i]]
                    cond_vals = np.concatenate([nb_z, cond_sim_vals])
                    rhs = np.concatenate([c0_data, cov(sim_k_d)])
                try:
                    sol = np.linalg.solve(C, rhs)
                    if not np.isfinite(sol).all():
                        raise np.linalg.LinAlgError("non-finite")
                    pred = float(sol @ cond_vals)
                    var = max(total_sill - float(sol @ rhs), 0.0)
                    sim_values[t_idx] = pred + np.sqrt(var) * rng.standard_normal()
                except np.linalg.LinAlgError:
                    # 病态邻域：条件值经验分布近似抽样（counted，从不静默
                    # —— review R2-M3：回退频次进入 disclosures/metadata）
                    n_degenerate_nodes += 1
                    sim_values[t_idx] = (
                        float(np.mean(cond_vals))
                        + float(np.std(cond_vals) + 1e-9) * rng.standard_normal())
                sim_coords[n_sim] = targets_t[t_idx]
                n_sim += 1
        realizations[r] = ns_state.backward(sim_values)

    disclosures = [
        "SGS k-neighbourhood approximation: conditioning set = k nearest "
        "data + k nearest simulated (tree rebuilt every "
        f"{SGS_SIM_CHUNK} nodes) — ensemble statistics are Monte Carlo "
        "estimates, not exact distributions",
    ]
    if n_degenerate_nodes:
        disclosures.append(
            f"{n_degenerate_nodes} node draws fell back to empirical "
            "sampling of the conditioning values (ill-conditioned systems, "
            "counted)")

    mean = realizations.mean(axis=0)
    std = (realizations.std(axis=0, ddof=1) if n_realizations > 1
           else np.zeros(n_t))
    p10, p50, p90 = np.percentile(realizations, [10, 50, 90], axis=0)
    return SGSEnsemble(
        targets=target_pts,
        mean=mean, std=std, p10=p10, p50=p50, p90=p90,
        n_realizations=n_realizations,
        seed=int(seed),
        variogram=variogram,
        transform_info={
            **ns_state.to_dict(),
            "domain": "normal_score (rank-gaussian)",
        },
        disclosures=disclosures,
        n_degenerate_nodes=int(n_degenerate_nodes),
        realizations=(realizations if return_realizations else None),
        backend="numpy_reference",
    )


def sequential_gaussian_simulation_batched(
    pts_metric: np.ndarray,
    values: np.ndarray,
    target_pts: np.ndarray,
    n_realizations: int = 100,
    seed: int = 42,
    variogram: Optional[VariogramFit] = None,
    k: int = 16,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    return_realizations: bool = False,
    n_path_groups: int = SGS_BATCHED_DEFAULT_GROUPS,
    chunk_size: int = SGS_BATCHED_CHUNK,
) -> SGSEnsemble:
    """批量 SGS（science-v5 W5，variant ``numpy_batched``）。

    与 :func:`sequential_gaussian_simulation`（reference，逐节点逐实现
    路径）同一科学目标与同一条件语义（chunk 开始时重建条件树 = chunk 内
    条件独立近似），但把 chunk 内逐节点 Python 循环推到批量结论：

    1. **P 组共享路径**（``n_path_groups``，默认 8）：R 个实现分 P 组，
       组内实现共享同一随机路径、组间路径不同——组间路径方差重新进入
       ensemble（单一路径会系统性低估 std：E[Z|path] 丢掉 between-path
       分量，架构挑战 R0-#4）。solve 数从 O(R·T) 降到 O(P·T/B)。
    2. 协方差矩阵只依赖几何 → 每 (组, chunk) 一次 ``(B, m, m)`` 堆叠
       solve，权重/方差组内跨实现复用；
    3. 逐实现条件值不同（已模拟节点）→ ``pred_r = w·cond_r``（einsum），
       ``draw_r = pred_r + √var·ε_r``（ε 逐实现独立）。

    数值契约：同 seed 双跑**逐位一致**（自身确定性）；与 reference 的
    一致性是**统计 differential**（RNG 消费序列不同，逐位一致不可达也
    不可伪装——披露于 disclosures）。隔离条件 = LinAlgError ∨ 非有限
    （同 LMC/ST：逐节点回退到条件值经验抽样，counted）。

    资源契约：n_t 动态上限 = min(SGS_BATCHED_MAX_TARGETS,
    SGS_MAX_ENSEMBLE_CELLS // R)（ensemble 预算是真约束——挑战 R0-#5；
    realizations 工作矩阵 O(R·T) 是必然内存，如实进预算）。
    """
    pts_metric = np.asarray(pts_metric, dtype=float)
    values = np.asarray(values, dtype=float)
    target_pts = np.asarray(target_pts, dtype=float)
    n_realizations = int(n_realizations)
    if not 1 <= n_realizations <= SGS_MAX_REALIZATIONS:
        raise ResourceScaleMismatch(
            f"n_realizations 必须在 [1, {SGS_MAX_REALIZATIONS}]，got {n_realizations}",
            estimated=f"{n_realizations} realizations",
            limit=f"≤{SGS_MAX_REALIZATIONS}",
            correction_hint="降低实现数或分批调用（不同 seed 合并 ensemble）",
        )
    n_t = len(target_pts)
    dynamic_cap = max(
        1, min(SGS_BATCHED_MAX_TARGETS,
               SGS_MAX_ENSEMBLE_CELLS // max(n_realizations, 1)))
    if n_t > dynamic_cap:
        raise ResourceScaleMismatch(
            f"目标格点 {n_t:,} 超过 batched SGS 动态上限 {dynamic_cap:,}"
            f"（= min(绝对上限 {SGS_BATCHED_MAX_TARGETS:,}, ensemble 预算 "
            f"{SGS_MAX_ENSEMBLE_CELLS:,}/{n_realizations}）",
            estimated=f"{n_t} targets", limit=f"≤{dynamic_cap}",
            correction_hint="降低实现数或目标格点数（分辨率/范围）")
    ensemble_cells = n_realizations * n_t
    if ensemble_cells > SGS_MAX_ENSEMBLE_CELLS:
        raise ResourceScaleMismatch(
            f"ensemble 预算 {n_realizations}×{n_t}={ensemble_cells:,} 单元"
            f"超过上限 {SGS_MAX_ENSEMBLE_CELLS:,}",
            estimated=f"{ensemble_cells} cells",
            limit=f"≤{SGS_MAX_ENSEMBLE_CELLS}",
            correction_hint="降低实现数或目标格点数（分辨率/范围）")
    if len(values) < 8:
        raise InsufficientSamples(
            f"SGS 至少需要 8 个样本（与克里金同底），got {len(values)}")

    n_groups = int(max(1, min(n_path_groups, n_realizations)))
    k = int(max(2, min(k, MAX_NEIGHBORS, len(values))))
    # chunk ≥ k 钳制（R2-#4）：chunk < k 会让前几个 chunk 落入
    # k_sim < k 的病态区制（近邻条件集结构劣化，ensemble 对 OK 的锚定
    # 实测跌至 ~0.6 且随 R 不收敛——reference 的 1024-chunk 恒 ≥ k，
    # 该区制在两路径语义一致前提之外，直接消灭）。
    chunk_size = int(max(k, min(chunk_size, n_t if n_t else k)))

    z_scores, ns_state = normal_score_transform(values)
    if variogram is None:
        variogram = fit_variogram(
            pts_metric, z_scores, model="auto",
            anisotropy_angle=anisotropy_angle,
            anisotropy_ratio=anisotropy_ratio)

    rng = np.random.default_rng(seed)  # 单流：组路径 + 组内噪声（caller_seeded）
    pts_t = apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio)
    targets_t = apply_anisotropy(target_pts, anisotropy_angle, anisotropy_ratio)
    data_tree = cKDTree(pts_t)
    d0, i0 = data_tree.query(targets_t, k=k)
    d0 = np.asarray(d0, dtype=float).reshape(n_t, k)
    i0 = np.asarray(i0, dtype=int).reshape(n_t, k)

    g = variogram
    structures = getattr(g, "structures", None)
    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    total_sill = abs(float(g.sill)) + abs(float(g.nugget))

    def cov(h: np.ndarray) -> np.ndarray:
        return total_sill - _gamma(g.model, h, g.sill, g.range_m, g.nugget,
                                   nu=g.nu, structures=structures)

    realizations = np.empty((n_realizations, n_t), dtype=float)
    n_degenerate_nodes = 0

    # 组大小尽量均匀（前 r_extra 组各 +1）
    base, r_extra = divmod(n_realizations, n_groups)
    row_cursor = 0
    for g_i in range(n_groups):
        r_g = base + (1 if g_i < r_extra else 0)
        rows = np.arange(row_cursor, row_cursor + r_g)
        row_cursor += r_g

        path = rng.permutation(n_t)              # 组共享路径
        sim_values = np.empty((r_g, n_t), dtype=float)   # normal-score 域
        sim_coords = np.empty((n_t, 2), dtype=float)
        n_sim = 0

        for start in cancellable(range(0, n_t, chunk_size), every=1):
            chunk = path[start:start + chunk_size]
            b = len(chunk)
            chunk_xy = targets_t[chunk]           # (B, 2)

            # 条件树（chunk 开始时重建——与 reference 同一近似语义）：
            # 批量查询 chunk 全部节点的 k_sim 最近已模拟节点
            if n_sim:
                sim_tree = cKDTree(sim_coords[:n_sim])
                k_sim = int(min(k, n_sim))
                d_sim, i_sim = sim_tree.query(chunk_xy, k=k_sim)
                d_sim = np.atleast_2d(np.asarray(d_sim, dtype=float))
                i_sim = np.atleast_2d(np.asarray(i_sim, dtype=int))
                if d_sim.shape[0] == 1 and b > 1:   # numpy 2.x 单查询形状防御
                    d_sim = np.broadcast_to(d_sim, (b, k_sim)).copy()
                    i_sim = np.broadcast_to(i_sim, (b, k_sim)).copy()
            else:
                k_sim = 0
                d_sim = np.empty((b, 0), dtype=float)
                i_sim = np.empty((b, 0), dtype=int)
            m = k + k_sim

            # ── 几何量（组内跨实现复用；与实现值无关）─────────────────
            nb_xy = pts_t[i0[chunk]]              # (B, k, 2)
            diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
            C = np.empty((b, m, m), dtype=float)
            C[:, :k, :k] = cov(np.sqrt((diff ** 2).sum(-1)))
            diag = np.arange(k)
            C[:, diag, diag] = total_sill - ridge
            rhs = np.empty((b, m), dtype=float)
            rhs[:, :k] = cov(d0[chunk])
            if k_sim:
                sim_xy = sim_coords[i_sim]        # (B, k_sim, 2)
                cross_d = np.sqrt(
                    ((nb_xy[:, :, None, :] - sim_xy[:, None, :, :]) ** 2)
                    .sum(-1))                     # (B, k, k_sim)
                cross = cov(cross_d)
                C[:, :k, k:] = cross
                C[:, k:, :k] = np.transpose(cross, (0, 2, 1))
                # 模拟节点间真实互协方差（同 reference 的 W5 修复——对角
                # 近似在序贯区制产生非正定系统）
                d_ss = np.sqrt(
                    ((sim_xy[:, :, None, :] - sim_xy[:, None, :, :]) ** 2)
                    .sum(-1))                     # (B, k_sim, k_sim)
                C[:, k:, k:] = cov(d_ss)
                sdiag = np.arange(k_sim)
                C[:, k + sdiag, k + sdiag] = total_sill - ridge
                rhs[:, k:] = cov(d_sim)
            try:
                sol = np.linalg.solve(C, rhs[:, :, None])[:, :, 0]
                if not np.isfinite(sol).all():
                    raise np.linalg.LinAlgError("non-finite")
                var = np.maximum(total_sill - np.sum(sol * rhs, axis=1), 0.0)
                sol_ok = np.isfinite(sol).all(axis=1) & np.isfinite(var)
                if not sol_ok.all():
                    raise np.linalg.LinAlgError("row-non-finite")
                w = sol                            # (B, m)
            except np.linalg.LinAlgError:
                # 整批异常 → 逐节点隔离重解（counted 回退同 reference 语义）
                w = np.empty((b, m), dtype=float)
                var = np.empty(b, dtype=float)
                for j in range(b):
                    try:
                        cand = np.linalg.solve(C[j], rhs[j])
                        if not np.isfinite(cand).all():
                            raise np.linalg.LinAlgError("non-finite")
                        w[j] = cand
                        var[j] = max(
                            total_sill - float(cand @ rhs[j]), 0.0)
                    except np.linalg.LinAlgError:
                        w[j] = 0.0
                        var[j] = -1.0              # 哨兵 → 经验回退
                # 行级回退在下方逐实现处理（var<0 标记）

            # ── 逐实现：条件值不同，权重共享 ─────────────────────────
            nb_z = z_scores[i0[chunk]]             # (B, k)
            if k_sim:
                # i_sim 是条件树位置索引（path 位置）→ 经 path 映射回目标
                # 序号再取已模拟值（同 reference 路径的 W5 修复语义）
                sim_vals_nb = sim_values[:, path[i_sim]]  # (r_g, B, k_sim)
                cond = np.concatenate(
                    (np.broadcast_to(nb_z, (r_g, b, k)), sim_vals_nb),
                    axis=2)                        # (r_g, B, m)
            else:
                cond = np.broadcast_to(nb_z, (r_g, b, k))
            preds = np.einsum("bm,rbm->rb", w, cond)   # (r_g, B)
            noise = rng.standard_normal((r_g, b))
            draws = preds + np.sqrt(np.maximum(var, 0.0))[None, :] * noise
            bad = np.nonzero(var < 0)[0]
            if bad.size:
                # 病态节点：条件值经验分布近似抽样（counted，同 reference）
                n_degenerate_nodes += int(bad.size * r_g)
                for j in bad:
                    emp_std = float(np.std(cond[:, j, :])
                                    + 1e-9) if cond.shape[2] else 1.0
                    draws[:, j] = (np.mean(cond[:, j, :], axis=1)
                                   + emp_std * noise[:, j])
            sim_values[:, chunk] = draws
            sim_coords[start:start + b] = chunk_xy
            n_sim = start + b

        realizations[rows] = ns_state.backward(sim_values)

    disclosures = [
        "SGS batched k-neighbourhood approximation: conditioning set = k "
        "nearest data + k nearest simulated (tree rebuilt every "
        f"{chunk_size} nodes) — ensemble statistics are Monte Carlo "
        "estimates, not exact distributions",
        f"path sharing: {n_groups} group(s) share paths within group "
        f"({n_realizations} realizations total); between-path variance "
        "re-enters the ensemble via groups",
        "RNG consumption differs from the reference path — same-seed "
        "results are bitwise-reproducible per backend and statistically "
        "equivalent (differential oracle), not bitwise-identical across "
        "backends",
    ]
    if n_degenerate_nodes:
        disclosures.append(
            f"{n_degenerate_nodes} node draws fell back to empirical "
            "sampling of the conditioning values (ill-conditioned systems, "
            "counted)")

    mean = realizations.mean(axis=0)
    std = (realizations.std(axis=0, ddof=1) if n_realizations > 1
           else np.zeros(n_t))
    p10, p50, p90 = np.percentile(realizations, [10, 50, 90], axis=0)
    return SGSEnsemble(
        targets=target_pts,
        mean=mean, std=std, p10=p10, p50=p50, p90=p90,
        n_realizations=n_realizations,
        seed=int(seed),
        variogram=variogram,
        transform_info={
            **ns_state.to_dict(),
            "domain": "normal_score (rank-gaussian)",
        },
        disclosures=disclosures,
        n_degenerate_nodes=int(n_degenerate_nodes),
        realizations=(realizations if return_realizations else None),
        backend="numpy_batched",
    )


# ── H3 表面驱动（共享 preamble 见 interpolation._metric_samples_and_target_grid）

def sgs_simulation_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    n_realizations: int = 100,
    seed: int = 42,
    neighbors: int = 16,
    backend: str = "auto",
) -> dict:
    """SGS 多实现表面：H3 网格逐格 P10/P50/P90/std（E-type 中值为主值）。

    ``records`` 主值 = P50（后向变换后）；每条另带 ``sgs_std``/``p10``/
    ``p90``。``metadata`` 携带 ensemble 摘要、近似语义披露、backend 变体
    证据与 uncertainty artifact 摘要（W6/W7）。

    ``backend``：``"auto"``（默认——按 descriptor 变体窗口经
    plan_execution 纯函数解析，决策证据写入 metadata.execution_plan）|
    ``"numpy_reference"``（逐节点路径）| ``"numpy_batched"``（W5 批量）。
    Raises 与 :func:`sequential_gaussian_simulation` 相同 + IDW 契约的
    资源守卫。
    """
    from app.lib.geo_analysis.interpolation import _metric_samples_and_target_grid
    from app.lib.geo_analysis.uncertainty import (
        data_quality_summary,
        from_sgs as _artifact_from_sgs,
    )

    if backend not in ("numpy_reference", "numpy_batched", "auto"):
        raise ValueError(
            f"backend 必须是 numpy_reference|numpy_batched|auto，got {backend!r}")
    (
        lonlat, values, pts_metric, cell_metric, target_cells,
        working_crs, bbox,
    ) = _metric_samples_and_target_grid(
        points_geojson, value_field, resolution,
        purpose="SGS 模拟", label="SGS 模拟", log_prefix="sgs_simulation",
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.sgs",
        "value_field": value_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}

    if backend == "auto":
        # 变体窗口单位 = 目标格点（挑战 R0-#6）：n_t 已知处做纯函数规划
        from app.lib.gis.backend_selection import ScaleProfile, plan_execution

        plan = plan_execution(
            "interpolation.sgs", ScaleProfile(raster_cells=len(target_cells)))
        backend = (plan.variant_id
                   if plan.variant_id in ("numpy_batched", "numpy_sequential")
                   else "numpy_reference")
        metadata["execution_plan"] = plan.to_dict()
    sgs_fn = (sequential_gaussian_simulation_batched
              if backend == "numpy_batched"
              else sequential_gaussian_simulation)
    ens = sgs_fn(
        pts_metric, values, cell_metric,
        n_realizations=n_realizations, seed=seed, k=neighbors,
    )
    metadata["value_semantics"] = "P50（逐格 ensemble 中值，后向变换后）"
    metadata.update(ens.to_dict())
    # W6：uncertainty artifact 摘要（estimator=sgs_ensemble；模型不确定
    # 性与数据质量分离；渲染断点建议）
    artifact = _artifact_from_sgs(
        ens,
        data_quality=data_quality_summary(
            n_samples=int(len(values)), n_targets=len(target_cells),
            value_field=value_field, working_crs=working_crs),
    )
    metadata["uncertainty"] = artifact.to_dict()
    metadata["renderer"] = artifact.to_renderer_metadata()
    records = [
        {
            "h3_index": cell,
            "value": float(p50),
            "sgs_std": float(sd),
            "p10": float(lo),
            "p90": float(hi),
        }
        for cell, p50, sd, lo, hi in zip(
            target_cells, ens.p50, ens.std, ens.p10, ens.p90)
    ]
    return {"records": records, "metadata": metadata}
