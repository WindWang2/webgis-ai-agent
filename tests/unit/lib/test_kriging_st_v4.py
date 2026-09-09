"""Science V4 Wave 7 —— 时空克里金 conformance。

验收锚点（descriptor ``interpolation.st_kriging`` 承诺）：
- product_sum 按构造 PSD（解析协方差正定采样核验）+ C(0,0) 一致；
- separable 在 τ=0 退化为纯空间协方差（缩放恒等，oracle 级解析）；
- 求解：确定性、方差 ≥0、时间窗语义（远处时相不进入邻域）；
- 退化类型化拒绝（样本不足 / 全同时刻 / 非法模型 / 时间数组失配）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples
from app.lib.geo_analysis.kriging import fit_variogram
from app.lib.geo_analysis.kriging_st import (
    ST_MIN_SAMPLES,
    fit_st_model,
    st_covariance,
    st_kriging,
)


def _fixture(seed: int = 3, n: int = 72):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10_000, (n, 2))
    t = rng.uniform(0, 30 * 86400.0, n)
    z = np.array([
        10.0 + 0.3 * np.sin(tt / 86400.0 / 5.0 * 2 * np.pi)
        + 0.0004 * np.linalg.norm(p - [5000.0, 5000.0])
        for p, tt in zip(xy, t)
    ]) + rng.normal(0, 0.2, n)
    return xy, z, t


def test_product_sum_covariance_psd_and_origin():
    xy, z, t = _fixture()
    st = fit_st_model(
        spatial_variogram=fit_variogram(xy, z, model="spherical"),
        temporal_range_sec=5 * 86400.0, model="product_sum")
    # C(0,0) = sill（双尺度可分离混合解析；ρ(0)=1 定义要求）
    c00 = float(st_covariance(st, np.array([0.0]), np.array([0.0]))[0])
    assert c00 == pytest.approx(st.sill, rel=1e-12)
    assert c00 > 0
    # product_sum 在 τ=0 精确退化为空间协方差 s·ρ_s(h)（oracle 锚）
    h_line = np.linspace(1.0, 20000, 33)
    from app.lib.geo_analysis.kriging_st import _spatial_corr

    assert np.allclose(
        st_covariance(st, h_line, np.zeros_like(h_line)),
        st.sill * _spatial_corr(st.spatial, h_line), rtol=1e-12)
    # 网格采样的协方差矩阵特征值非负（PSD 数值核验）
    pts = np.column_stack([xy[:40], t[:40] / 1e6])  # 空间+时间联合点
    k = 3.0 / st.temporal_range_sec  # 时间距离→空间尺度换算（采样核验用）
    h_mat = np.sqrt(((pts[:, None, :2] - pts[None, :, :2]) ** 2).sum(-1))
    tau_mat = (pts[:, None, 2] - pts[None, :, 2]) / k
    C = st_covariance(st, h_mat, tau_mat)
    eig = np.linalg.eigvalsh(C)
    assert eig.min() >= -1e-9


def test_separable_tau_zero_reduces_to_spatial():
    """oracle 锚：separable τ=0 ⇒ C(h,0) = sill·ρ_s(h)（解析恒等）。"""
    xy, z, t = _fixture()
    g = fit_variogram(xy, z, model="spherical")
    st = fit_st_model(spatial_variogram=g, temporal_range_sec=86400.0,
                      model="separable")
    h = np.linspace(1.0, 20_000, 50)
    expected = st.sill * (1.0 - _gamma_pure(g, h) / st.sill)
    assert np.allclose(st_covariance(st, h, np.zeros_like(h)), expected,
                       rtol=1e-12)


def _gamma_pure(g, h):
    from app.lib.geo_analysis.kriging import _gamma

    return _gamma(g.model, h, g.sill, g.range_m, g.nugget, nu=g.nu)


def test_st_kriging_deterministic_and_valid():
    xy, z, t = _fixture()
    st = fit_st_model(spatial_variogram=fit_variogram(xy, z, model="auto"),
                      temporal_range_sec=5 * 86400.0, model="product_sum")
    targets = np.array([[5000.0, 5000.0], [2500.0, 7500.0]])
    tt = np.array([15 * 86400.0, 20 * 86400.0])
    a = st_kriging(xy, z, t, targets, tt, st, k=12)
    b = st_kriging(xy, z, t, targets, tt, st, k=12)
    assert np.array_equal(a["predictions"], b["predictions"])
    assert np.isfinite(a["predictions"]).all()
    assert (a["variances"] >= 0).all()


def test_st_time_window_excludes_far_epochs():
    """时间窗外样本不进入邻域（远处时相不硬凑）——窄窗邻域数 < 全量 k。"""
    xy, z, t = _fixture()
    # 构造两簇时相：0 天与 60 天
    t = np.where(np.arange(len(t)) % 2 == 0, t, t + 60 * 86400.0)
    st = fit_st_model(spatial_variogram=fit_variogram(xy, z, model="auto"),
                      temporal_range_sec=5 * 86400.0, model="product_sum")
    targets = np.array([[5000.0, 5000.0]])
    tt = np.array([0.0])
    r_wide = st_kriging(xy, z, t, targets, tt, st, k=16, time_window_sec=None)
    r_narrow = st_kriging(xy, z, t, targets, tt, st, k=16,
                          time_window_sec=10 * 86400.0)
    assert r_narrow["n_neighbors"][0] < r_wide["n_neighbors"][0]


def test_st_degenerate_time_typed_reject():
    xy, z, t = _fixture()
    st = fit_st_model(spatial_variogram=fit_variogram(xy, z, model="auto"),
                      temporal_range_sec=86400.0)
    targets = np.array([[5000.0, 5000.0]])
    with pytest.raises(InsufficientSamples):
        st_kriging(xy[:ST_MIN_SAMPLES - 1], z[:ST_MIN_SAMPLES - 1],
                   t[:ST_MIN_SAMPLES - 1], targets, np.array([0.0]), st)
    with pytest.raises(DegenerateData):
        st_kriging(xy, z, np.full(len(z), 5.0), targets,
                   np.array([0.0]), st)  # 全同时刻
    with pytest.raises(DegenerateData):
        st_kriging(xy, z, t[:-1], targets, np.array([0.0]), st)  # 数组失配
    with pytest.raises(DegenerateData):
        fit_st_model(temporal_range_sec=0.0, model="product_sum")
    with pytest.raises(DegenerateData):
        fit_st_model(temporal_range_sec=86400.0, model="bogus")


def test_st_driver_end_to_end():
    from app.lib.geo_analysis.kriging_st import st_kriging_surface

    fc = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + (i % 5) * 0.01,
                                          39.0 + (i // 5) * 0.01]},
             "properties": {"v": float(10 + (i % 5) * 0.5),
                            "t": float((i // 5) * 86400)}}
            for i in range(20)
        ],
    }
    r = st_kriging_surface(fc, "v", "t", target_time_sec=86400.0,
                           resolution=7, neighbors=10)
    assert r["metadata"]["algorithm"] == "interpolation.st_kriging"
    assert len(r["records"]) > 0
    rec = r["records"][0]
    assert rec["st_variance"] >= 0
    assert r["metadata"]["st_model"]["model"] == "product_sum"
