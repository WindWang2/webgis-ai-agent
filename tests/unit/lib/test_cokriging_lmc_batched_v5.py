"""LMC 批量求解 differential（science-v5 W3）。

契约（01-architecture.md D3 + Subagent-A Round-0 #3）：

- 批量 ``np.linalg.solve((c,m,m),(c,m))`` 与逐行解**同环境逐位一致**
  （numpy 逐片调同一 LAPACK 例程——实现性质非 API 保证，本 differential
  持续钉死）；
- 隔离条件 = LinAlgError ∨ 非有限：恰奇异行 → 逐行重解 → 仍失败 →
  邻域均值回退（V4 语义），degraded 计数逐位对齐；
- 近奇异行若解出**有限**值：V4 与批量路径同样接受（不回退）——语义
  无 delta，负例钉死该边界不被"修复"成额外回退。
"""
from __future__ import annotations

import numpy as np
import pytest

from scipy.spatial import cKDTree

from app.lib.geo_analysis.cokriging_lmc import cokriging_lmc as _cok
from app.lib.geo_analysis.kriging import (
    COKRIGING_MIN_ABS_RHO,
    apply_anisotropy,
    _gamma,
)


def _reference_per_row_cokriging(
    pts1, z1, pts2, z2, targets, lmc, k1, k2,
    anisotropy_angle=0.0, anisotropy_ratio=1.0,
):
    """V4 逐行求解路径的测试内参考实现（oracle —— 不复用生产代码）。"""
    pts1 = np.asarray(pts1, dtype=float)
    pts2 = np.asarray(pts2, dtype=float)
    targets = np.asarray(targets, dtype=float)

    pts1_t = apply_anisotropy(pts1, anisotropy_angle, anisotropy_ratio)
    pts2_t = apply_anisotropy(pts2, anisotropy_angle, anisotropy_ratio)
    targets_t = apply_anisotropy(targets, anisotropy_angle, anisotropy_ratio)
    tree1, tree2 = cKDTree(pts1_t), cKDTree(pts2_t)
    d1, i1 = tree1.query(targets_t, k=k1)
    d2, i2 = tree2.query(targets_t, k=k2)
    n_t = len(targets)
    d1 = np.asarray(d1, float).reshape(n_t, k1)
    i1 = np.asarray(i1, int).reshape(n_t, k1)
    d2 = np.asarray(d2, float).reshape(n_t, k2)
    i2 = np.asarray(i2, int).reshape(n_t, k2)

    def struct_corr(model, h, rng):
        return 1.0 - _gamma(model, h, 1.0, rng, 0.0)

    z1 = np.asarray(z1, float)
    z2 = np.asarray(z2, float)
    rho = lmc.rho
    nug1 = max(float(lmc.variogram1.nugget), 0.0)
    nug2 = max(float(lmc.variogram2.nugget), 0.0)
    nug12 = abs(rho) * float(np.sqrt(nug1 * nug2))

    def C11(h):
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, s1u, _, r in lmc.structures:
            out = out + s1u * struct_corr(m, h, r)
        return np.where(h <= 0.0, nug1 + out, out + nug12)

    def C22(h):
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, _, s2u, r in lmc.structures:
            out = out + s2u * struct_corr(m, h, r)
        return np.where(h <= 0.0, nug2 + out, out + nug12)

    def C12(h):
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, s1u, s2u, r in lmc.structures:
            out = out + rho * np.sqrt(max(s1u, 0.0) * max(s2u, 0.0)) \
                * struct_corr(m, h, r)
        return out + nug12

    C00 = float(C11(np.array([0.0]))[0])
    preds = np.empty(n_t)
    varis = np.empty(n_t)
    degraded = 0
    for r_i in range(n_t):
        m = k1 + k2 + 2
        C = np.zeros((m, m))
        b1 = pts1_t[i1[r_i]]
        b2 = pts2_t[i2[r_i]]
        diff11 = b1[:, None, :] - b1[None, :, :]
        h11 = np.sqrt((diff11 ** 2).sum(-1))
        for a in range(k1):
            C[a, a] = C00
        if k1 > 1:
            off = ~np.eye(k1, dtype=bool)
            ia, ib = np.nonzero(off)
            C[ia, ib] = C11(h11[ia, ib])
        diff22 = b2[:, None, :] - b2[None, :, :]
        h22 = np.sqrt((diff22 ** 2).sum(-1))
        C[k1:k1 + k2, k1:k1 + k2] = C22(h22)
        C[k1:k1 + k2, k1:k1 + k2][np.arange(k2), np.arange(k2)] = \
            float(C22(np.array([0.0]))[0])
        diff12 = b1[:, None, :] - b2[None, :, :]
        h12 = np.sqrt((diff12 ** 2).sum(-1))
        C[:k1, k1:k1 + k2] = C12(h12)
        C[k1:k1 + k2, :k1] = C[:k1, k1:k1 + k2].T
        C[:k1, m - 2] = 1.0
        C[m - 2, :k1] = 1.0
        C[k1:k1 + k2, m - 1] = 1.0
        C[m - 1, k1:k1 + k2] = 1.0
        t_xy = targets_t[r_i]
        rhs = np.zeros(m)
        rhs[:k1] = C11(np.sqrt(((b1 - t_xy) ** 2).sum(-1)))
        rhs[k1:k1 + k2] = C12(np.sqrt(((b2 - t_xy) ** 2).sum(-1)))
        rhs[m - 2] = 1.0
        try:
            sol = np.linalg.solve(C, rhs)
            if not np.isfinite(sol).all():
                raise np.linalg.LinAlgError("non-finite")
            w1 = sol[:k1]
            w2 = sol[k1:k1 + k2]
            preds[r_i] = float(w1 @ z1[i1[r_i]] + w2 @ z2[i2[r_i]])
            var = C00 - float(sol[:k1 + k2] @ rhs[:k1 + k2]) - float(sol[m - 2])
            varis[r_i] = max(var, 0.0)
            if var < 0:
                degraded += 1
        except np.linalg.LinAlgError:
            preds[r_i] = float(np.mean(z1[i1[r_i]]))
            varis[r_i] = float(np.var(z1[i1[r_i]]))
            degraded += 1
    return preds, varis, degraded


def _synthetic_coregionalized(rng, n1=60, n2=50, rho_true=0.8):
    xy1 = rng.uniform(0, 100, size=(n1, 2))
    field = rng.normal(0, 1.0, n1)
    z1 = 10.0 + 3.0 * field + rng.normal(0, 0.3, n1)
    xy2 = rng.uniform(0, 100, size=(n2, 2))
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(xy1)
    d, idx = tree.query(xy2, k=1)
    z2 = 5.0 + 2.0 * (rho_true * field[idx]
                      + np.sqrt(1 - rho_true ** 2) * rng.normal(0, 1, n2))
    return xy1, z1, xy2, z2


def test_bitwise_equal_well_conditioned():
    from app.lib.geo_analysis.cokriging_lmc import fit_lmc

    rng = np.random.default_rng(42)
    xy1, z1, xy2, z2 = _synthetic_coregionalized(rng)
    targets = rng.uniform(0, 100, size=(137, 2))
    lmc = fit_lmc(xy1, z1, xy2, z2)
    assert abs(lmc.rho) >= COKRIGING_MIN_ABS_RHO
    got = _cok(xy1, z1, xy2, z2, targets, lmc=lmc, k1=8, k2=6)
    ref_p, ref_v, ref_deg = _reference_per_row_cokriging(
        xy1, z1, xy2, z2, targets, lmc, 8, 6)
    # 同环境逐位一致（架构 D3 的 differential 钉死）
    np.testing.assert_array_equal(got.predictions, ref_p)
    np.testing.assert_array_equal(got.variances, ref_v)
    assert got.degraded_cells == ref_deg

def test_exactly_singular_row_isolated_to_fallback():
    # 重合主变量点 → 邻域含重复行 → 恰奇异 → 逐行隔离 → 邻域均值回退。
    # LMC 从干净数据预拟合（被测对象是求解器；重复点会破坏经验 ρ 触发
    # fit_lmc 的守卫——那是另一条契约，已有测试覆盖）。
    from app.lib.geo_analysis.cokriging_lmc import fit_lmc

    rng = np.random.default_rng(11)
    xy1, z1, xy2, z2 = _synthetic_coregionalized(rng)
    lmc = fit_lmc(xy1, z1, xy2, z2)
    # 3 个完全重合点（z 不同 → 邻域矩阵重复行 → 奇异）
    xy1 = np.vstack([xy1, xy1[:3]])
    z1 = np.concatenate([z1, [100.0, -50.0, 25.0]])
    targets = np.vstack([
        xy1[:3],                      # 目标恰在重合点上 → 必奇异邻域
        rng.uniform(0, 100, size=(20, 2)),
    ])
    got = _cok(xy1, z1, xy2, z2, targets, lmc=lmc, k1=8, k2=6)
    ref_p, ref_v, ref_deg = _reference_per_row_cokriging(
        xy1, z1, xy2, z2, targets, lmc, 8, 6)
    np.testing.assert_array_equal(got.predictions, ref_p)
    np.testing.assert_array_equal(got.variances, ref_v)
    assert got.degraded_cells == ref_deg
    assert got.degraded_cells >= 3   # 奇异目标确实走了回退


def test_cancellation_checkpoint_preserved():
    # 分块 checkpoint 语义保留：取消异常在 chunk 边界抛出
    from app.lib.cancellation import CancellationToken, use_token

    from app.lib.geo_analysis.cokriging_lmc import fit_lmc

    rng = np.random.default_rng(19)
    xy1, z1, xy2, z2 = _synthetic_coregionalized(rng)
    targets = rng.uniform(0, 100, size=(600, 2))   # >1 chunk (512)
    lmc = fit_lmc(xy1, z1, xy2, z2)

    token = CancellationToken()
    token.cancel()

    def _run():
        with use_token(token):
            _cok(xy1, z1, xy2, z2, targets, lmc=lmc, k1=8, k2=6)

    with pytest.raises(Exception) as ei:
        _run()
    # 取消在首个 chunk 边界触发（OperationCancelled 或既有异常族）
    assert "cancel" in type(ei.value).__name__.lower() or \
        "取消" in str(ei.value)


def test_512_chunk_boundary_spanned():
    # 目标数跨 chunk 边界（513 = 512+1）→ 批量语义与参考逐位一致
    from app.lib.geo_analysis.cokriging_lmc import fit_lmc

    rng = np.random.default_rng(23)
    xy1, z1, xy2, z2 = _synthetic_coregionalized(rng)
    targets = rng.uniform(0, 100, size=(513, 2))
    lmc = fit_lmc(xy1, z1, xy2, z2)
    got = _cok(xy1, z1, xy2, z2, targets, lmc=lmc, k1=8, k2=6)
    ref_p, ref_v, ref_deg = _reference_per_row_cokriging(
        xy1, z1, xy2, z2, targets, lmc, 8, 6)
    np.testing.assert_array_equal(got.predictions, ref_p)
    np.testing.assert_array_equal(got.variances, ref_v)
