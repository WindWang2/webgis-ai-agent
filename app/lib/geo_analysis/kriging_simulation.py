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
SGS_MAX_TARGETS = 4_000_000             # 目标格点上限（与 NN/Sibson 同级）
SGS_MAX_ENSEMBLE_CELLS = 20_000_000     # n_realizations × n_targets 硬顶
SGS_SIM_CHUNK = 1_024                   # 模拟节点分块（条件树重建节奏）


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

    def to_dict(self) -> dict:
        def rng(a: np.ndarray) -> list[float]:
            return [round(float(a.min()), 6), round(float(a.max()), 6)]

        return {
            "n_realizations": int(self.n_realizations),
            "seed": int(self.seed),
            "n_targets": int(len(self.mean)),
            "ensemble_mean_range": rng(self.mean),
            "ensemble_std_range": rng(self.std),
            "p10_range": rng(self.p10),
            "p50_range": rng(self.p50),
            "p90_range": rng(self.p90),
            "variogram": self.variogram.params(),
            "transform": dict(self.transform_info),
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
                    C[k:, k:] = total_sill - ridge
                    cond_vals = np.concatenate([nb_z, sim_values[sim_k_i]])
                    rhs = np.concatenate([c0_data, cov(sim_k_d)])
                try:
                    sol = np.linalg.solve(C, rhs)
                    if not np.isfinite(sol).all():
                        raise np.linalg.LinAlgError("non-finite")
                    pred = float(sol @ cond_vals)
                    var = max(total_sill - float(sol @ rhs), 0.0)
                    sim_values[t_idx] = pred + np.sqrt(var) * rng.standard_normal()
                except np.linalg.LinAlgError:
                    # 病态邻域：条件值经验分布近似抽样（不静默——实现级
                    # 抖动即其披露，同 OK 的邻域均值回退口径）
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
        realizations=(realizations if return_realizations else None),
    )


# ── H3 表面驱动（共享 preamble 见 interpolation._metric_samples_and_target_grid）

def sgs_simulation_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    n_realizations: int = 100,
    seed: int = 42,
    neighbors: int = 16,
) -> dict:
    """SGS 多实现表面：H3 网格逐格 P10/P50/P90/std（E-type 中值为主值）。

    ``records`` 主值 = P50（后向变换后）；每条另带 ``sgs_std``/``p10``/
    ``p90``。``metadata`` 携带 ensemble 摘要与近似语义披露。
    Raises 与 :func:`sequential_gaussian_simulation` 相同 + IDW 契约的
    资源守卫。
    """
    from app.lib.geo_analysis.interpolation import _metric_samples_and_target_grid

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

    ens = sequential_gaussian_simulation(
        pts_metric, values, cell_metric,
        n_realizations=n_realizations, seed=seed, k=neighbors,
    )
    metadata["value_semantics"] = "P50（逐格 ensemble 中值，后向变换后）"
    metadata.update(ens.to_dict())
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
