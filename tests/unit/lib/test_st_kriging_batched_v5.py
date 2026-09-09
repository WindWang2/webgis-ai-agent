"""ST 克里金批量求解 differential + ST CV（science-v5 W4）。

契约（01-architecture.md D3 + Subagent-A Round-0 #1/#2 修订）：

- 邻域定长 k 填充批量系统与 V4 逐目标路径**数值一致**：窗口内候选 ≥2
  取窗口内前 k（relax 语义 = 仍解系统，非均值回退）；无填充的行矩阵与
  V4 构造逐位同值 → 解逐位一致；有填充的行只受有效槽位影响（哨兵行
  整行列清零含约束行列 → w_pad 恰 0，容差一致）；
- ST CV：temporal_forward 零 future leakage（逐折 max(train) < min(test)）；
  空间块方案块不相交；样本不足诚实退化。
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial import cKDTree

from app.lib.geo_analysis.kriging_st import (
    fit_st_model,
    st_cross_validate,
    st_kriging,
)


def _synthetic_spacetime(rng, n_stations=10, n_times=8, span=200.0):
    """可分离时空场：C[(i,t),(j,t')] = 9·ρ_s(i,j)·ρ_t(t,t')（PSD 按构造）。"""
    xy = rng.uniform(0, span, size=(n_stations, 2))
    times = np.arange(n_times, dtype=float) * 86400.0   # 逐日
    d2 = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    rho_s = np.exp(-3.0 * d2 / (0.6 * span))
    rho_t = np.exp(-3.0 * np.abs(times[:, None] - times[None, :]) / (5 * 86400.0))
    # 全量 (S·T, S·T) 可分离协方差
    cov = 9.0 * np.kron(rho_t, rho_s)
    cov = cov + 1e-6 * np.eye(n_stations * n_times)
    draws = rng.standard_normal((n_stations * n_times, 1))
    z_flat = np.linalg.cholesky(cov) @ draws            # (S·T, 1)
    # 平铺顺序与 kron 一致：t 外层、station 内层（同 flat_xy/flat_t）
    flat_xy = np.repeat(xy[None], n_times, axis=0).reshape(-1, 2)
    flat_t = np.repeat(times[:, None], n_stations, axis=1).ravel()
    flat_z = z_flat.ravel()
    return flat_xy, flat_z, flat_t


def _reference_per_target_st(
    pts, values, times, targets, target_times, st, k, time_window,
):
    """V4 逐目标路径的测试内参考实现（oracle —— 不复用生产代码）。"""
    from app.lib.geo_analysis.kriging import apply_anisotropy

    pts_t = apply_anisotropy(pts, 0.0, 1.0)
    targets_t = apply_anisotropy(targets, 0.0, 1.0)
    tree = cKDTree(pts_t)
    k_query = min(96, len(values))
    d_all, i_all = tree.query(targets_t, k=k_query)
    i_all = np.asarray(i_all, int).reshape(len(targets_t), k_query)
    C00 = float(st_cov(st, 0.0, 0.0)[0])
    preds = np.empty(len(targets_t))
    varis = np.empty(len(targets_t))
    degraded = 0
    n_used = np.empty(len(targets_t), dtype=int)
    for b in range(len(targets_t)):
        tau = times[i_all[b]] - target_times[b]
        in_window = np.abs(tau) <= (
            time_window if time_window is not None else np.inf)
        idx = i_all[b][in_window][:k]
        if len(idx) < 2:
            idx = i_all[b][:k]
            degraded += 1
        h = np.sqrt(((pts_t[idx] - targets_t[b]) ** 2).sum(axis=1))
        nb_tau = times[idx] - target_times[b]
        m = len(idx) + 1
        C = np.empty((m, m))
        diff = pts_t[idx][:, None, :] - pts_t[idx][None, :, :]
        h_ss = np.sqrt((diff ** 2).sum(-1))
        tau_ss = times[idx][:, None] - times[idx][None, :]
        C[:m - 1, :m - 1] = st_cov(st, h_ss, tau_ss)
        np.fill_diagonal(C[:m - 1, :m - 1], C00)
        C[:m - 1, m - 1] = 1.0
        C[m - 1, :m - 1] = 1.0
        C[m - 1, m - 1] = 0.0
        rhs = np.empty(m)
        rhs[:m - 1] = st_cov(st, h, nb_tau)
        rhs[m - 1] = 1.0
        sol = np.linalg.solve(C, rhs)
        w = sol[:m - 1]
        preds[b] = float(w @ values[idx])
        var = max(C00 - float(w @ rhs[:m - 1]) - float(sol[m - 1]), 0.0)
        if var <= 0:
            degraded += 1
        varis[b] = var
        n_used[b] = len(idx)
    return preds, varis, degraded, n_used


def st_cov(st, h, tau):
    from app.lib.geo_analysis.kriging_st import st_covariance

    h = np.atleast_1d(np.asarray(h, dtype=float))
    tau = np.atleast_1d(np.asarray(tau, dtype=float))
    return st_covariance(st, h, tau)


class TestSTBatchedDifferential:
    def test_bitwise_equal_no_padding(self):
        # 无时间窗限制 → 每目标邻居数恰 k（无填充）→ 逐位一致
        rng = np.random.default_rng(42)
        xy, z, t = _synthetic_spacetime(rng)
        st = fit_st_model(pts_metric=xy, values=z, model="separable")
        targets = rng.uniform(0, 200, size=(40, 2))
        target_times = np.full(40, 3 * 86400.0)
        got = st_kriging(xy, z, t, targets, target_times, st,
                         k=8, time_window_sec=None)
        ref_p, ref_v, ref_deg, ref_n = _reference_per_target_st(
            xy, z, t, targets, target_times, st, 8, None)
        assert (got["n_neighbors"] == ref_n).all()
        assert got["degraded_cells"] == ref_deg
        np.testing.assert_array_equal(got["predictions"], ref_p)
        np.testing.assert_array_equal(got["variances"], ref_v)

    def test_window_relax_matches_v4_semantics(self):
        # 目标时刻远离全部样本 → 窗口空 → relax 仍解系统（非均值回退）
        rng = np.random.default_rng(11)
        xy, z, t = _synthetic_spacetime(rng)
        st = fit_st_model(pts_metric=xy, values=z, model="separable")
        far_time = 86400.0 * 365.0        # 一年后 → 全部出窗
        targets = rng.uniform(0, 200, size=(15, 2))
        target_times = np.full(15, far_time)
        got = st_kriging(xy, z, t, targets, target_times, st,
                         k=8, time_window_sec=30 * 86400.0)
        ref_p, ref_v, ref_deg, ref_n = _reference_per_target_st(
            xy, z, t, targets, target_times, st, 8, 30 * 86400.0)
        assert got["degraded_cells"] == ref_deg
        assert ref_deg >= 15               # 全部 relax（counted）
        np.testing.assert_array_equal(got["predictions"], ref_p)
        np.testing.assert_array_equal(got["variances"], ref_v)

    def test_padding_row_weights_are_exactly_zero(self):
        # 2 ≤ 窗口内候选数 < k → 哨兵填充（relax 不触发、纯窗口过滤）；
        # 有效预测与"手动缩减系统"参考逐位一致
        rng = np.random.default_rng(19)
        xy, z, t = _synthetic_spacetime(rng, n_stations=3, n_times=6)
        # 3 站的空间对不足以拟合变异函数 → 空间模型从大 fixture 预拟合
        xy_big, z_big, _ = _synthetic_spacetime(rng, n_stations=12, n_times=6)
        st = fit_st_model(pts_metric=xy_big, values=z_big, model="separable")
        targets = rng.uniform(0, 200, size=(10, 2))
        # 目标对齐 t=1 天、窗口半日 → 窗口内恰 1 时相 × 3 站 = 3 候选
        # （≥2 非 relax、<8 填充）
        target_times = np.full(10, 1.0 * 86400.0)
        got = st_kriging(xy, z, t, targets, target_times, st,
                         k=8, time_window_sec=0.5 * 86400.0)
        ref_p, ref_v, ref_deg, ref_n = _reference_per_target_st(
            xy, z, t, targets, target_times, st, 8, 0.5 * 86400.0)
        np.testing.assert_array_equal(got["predictions"], ref_p)
        assert (got["n_neighbors"] == ref_n).all()
        assert (ref_n == 3).all()          # 恰 3 有效邻居（5 槽位填充）
        assert (ref_n < 8).all()           # 全部为填充行

    def test_batch_chunked_512_spanned(self):
        rng = np.random.default_rng(23)
        xy, z, t = _synthetic_spacetime(rng)
        st = fit_st_model(pts_metric=xy, values=z, model="separable")
        targets = rng.uniform(0, 200, size=(600, 2))     # 跨 512 chunk 边界
        target_times = np.full(600, 86400.0)
        got = st_kriging(xy, z, t, targets, target_times, st, k=8,
                         time_window_sec=None)
        ref_p, ref_v, ref_deg, _ = _reference_per_target_st(
            xy, z, t, targets, target_times, st, 8, None)
        np.testing.assert_array_equal(got["predictions"], ref_p)
        np.testing.assert_array_equal(got["variances"], ref_v)

    def test_cancellation_on_chunk_boundary(self):
        from app.lib.cancellation import CancellationToken, use_token

        rng = np.random.default_rng(29)
        xy, z, t = _synthetic_spacetime(rng)
        st = fit_st_model(pts_metric=xy, values=z, model="separable")
        targets = rng.uniform(0, 200, size=(600, 2))
        token = CancellationToken()
        token.cancel()
        with pytest.raises(Exception) as ei:
            with use_token(token):
                st_kriging(xy, z, t, targets, np.full(600, 86400.0), st, k=8)
        assert "cancel" in type(ei.value).__name__.lower() or \
            "取消" in str(ei.value)


class TestSTCrossValidation:
    def _fixture(self, seed=5):
        rng = np.random.default_rng(seed)
        return _synthetic_spacetime(rng, n_stations=12, n_times=10)

    def test_temporal_forward_no_future_leakage(self):
        xy, z, t = self._fixture()
        report = st_cross_validate(xy, z, t, scheme="temporal_forward",
                                   folds=3, k=8)
        assert report.scheme == "temporal_forward"
        assert report.leakage_check is not None
        assert report.leakage_check and all(report.leakage_check.values())
        assert report.folds_used >= 1
        assert report.rmse is not None

    def test_spatial_block_scheme_works(self):
        xy, z, t = self._fixture()
        report = st_cross_validate(xy, z, t, scheme="spatial_block",
                                   folds=3, k=8)
        assert report.scheme == "spatial_block"
        assert report.leakage_check is not None
        assert all(report.leakage_check.values())   # 块不相交
        assert report.rmse is not None

    def test_insufficient_samples_honest_decline(self):
        xy, z, t = self._fixture()
        n = 6                                        # < ST_MIN_SAMPLES
        report = st_cross_validate(xy[:n], z[:n], t[:n], folds=2)
        assert report.rmse is None
        assert "无法进行可靠的时空交叉验证" in report.note

    def test_unknown_scheme_rejected(self):
        xy, z, t = self._fixture()
        with pytest.raises(ValueError, match="scheme 必须是"):
            st_cross_validate(xy, z, t, scheme="random")

    def test_report_json_shape(self):
        xy, z, t = self._fixture()
        report = st_cross_validate(xy, z, t, folds=3, k=8)
        d = report.to_dict()
        for key in ("n_samples", "folds", "folds_used", "scheme",
                    "rmse", "mae", "bias", "r2", "uncertainty_calibration"):
            assert key in d
