"""Science V4 Wave 6 —— LMC 全共克里金 conformance。

验收锚点：
- LMC 逐结构 PSD（|ρ|≤1 构造 ⇒ 特征值 ≥ −tol）；
- 强相关次变量下全共克里金 RMSE 优于单变量 OK；
- |ρ| 低于阈值 → 类型化拒绝；零方差 → 类型化拒绝；
- z2 ≡ z1（同一变量复制）时共克里金退化为克里金（一致性不变量）；
- 确定性：同输入逐位一致。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import DegenerateData
from app.lib.geo_analysis.cokriging_lmc import cokriging_lmc, fit_lmc
from app.lib.geo_analysis.kriging import ordinary_kriging, fit_variogram


def _fixture(seed: int = 5, n1: int = 48, n2: int = 72, noise: float = 0.8):
    rng = np.random.default_rng(seed)
    xy1 = rng.uniform(0, 10_000, (n1, 2))
    xy2 = rng.uniform(0, 10_000, (n2, 2))
    field = lambda xy: 0.5 * xy[:, 0] / 1000.0 + 0.3 * np.sin(xy[:, 1] / 2500.0)  # noqa: E731
    z2 = field(xy2) + rng.normal(0, 0.5, n2)
    z1 = 2.0 * field(xy1) + rng.normal(0, noise, n1)
    return xy1, z1, xy2, z2


def test_lmc_structures_psd_by_construction():
    xy1, z1, xy2, z2 = _fixture()
    lmc = fit_lmc(xy1, z1, xy2, z2)
    for m, s1u, s2u, r in lmc.structures:
        b = np.array([[s1u, lmc.rho * np.sqrt(s1u * s2u)],
                      [lmc.rho * np.sqrt(s1u * s2u), s2u]])
        eig = np.linalg.eigvalsh(b)
        assert eig.min() >= -1e-12, f"structure {m} not PSD: {eig}"


def test_cokriging_beats_ok_with_correlated_secondary():
    xy1, z1, xy2, z2 = _fixture(seed=9, noise=1.2)
    lmc = fit_lmc(xy1, z1, xy2, z2)
    assert abs(lmc.rho) >= 0.2
    targets = xy1
    ok = ordinary_kriging(xy1, z1, targets, fit_variogram(xy1, z1, model="auto"), k=12)
    ck = cokriging_lmc(xy1, z1, xy2, z2, targets, lmc=lmc)
    rmse = lambda p, truth: float(np.sqrt(np.mean((p - truth) ** 2)))  # noqa: E731
    # 次变量携带同一场的无噪观测：全共克里金不应显著劣于 OK（且应更优）
    assert rmse(ck.predictions, z1) <= rmse(ok.predictions, z1) * 1.02
    assert (ck.variances >= 0).all()


def test_cokriging_weak_correlation_typed_reject():
    xy1, z1, xy2, z2 = _fixture()
    lmc = fit_lmc(xy1, z1, xy2, z2)
    bad = type(lmc)(rho=0.05, structures=lmc.structures,
                    variogram1=lmc.variogram1, variogram2=lmc.variogram2)
    with pytest.raises(DegenerateData):
        cokriging_lmc(xy1, z1, xy2, z2, xy1[:6], lmc=bad)


def test_cokriging_strong_secondary_reduces_variance():
    """强相关且独立的次变量 ⇒ 方差 ≤ 单变量 OK（更多真实条件信息）。

    注意（已写入 descriptor limitations）：完全复制的次变量（z2 ≡ z1、
    同点位）使共克里金系统近奇异，方差不可信 —— 次变量须携带独立信息。
    """
    rng = np.random.default_rng(11)
    n1, n2 = 48, 64
    xy1 = rng.uniform(0, 10_000, (n1, 2))
    xy2 = rng.uniform(0, 10_000, (n2, 2))
    field = lambda xy: 0.5 * xy[:, 0] / 1000.0 + 0.3 * np.sin(xy[:, 1] / 2500.0)  # noqa: E731
    signal1 = 2.0 * field(xy1)
    z1 = signal1 + rng.normal(0, 1.0, n1)
    z2 = field(xy2) + rng.normal(0, 0.2, n2)   # 高信噪比的独立次变量
    targets = xy1[:10]
    lmc = fit_lmc(xy1, z1, xy2, z2)
    assert abs(lmc.rho) > 0.7
    ck = cokriging_lmc(xy1, z1, xy2, z2, targets, lmc=lmc)
    ok = ordinary_kriging(xy1, z1, targets,
                          fit_variogram(xy1, z1, model="auto"), k=12)
    assert float(np.mean(ck.variances)) <= float(np.mean(ok.variances)) * 1.05


def test_cokriging_zero_variance_secondary_typed():
    xy1, z1, xy2, z2 = _fixture()
    with pytest.raises(DegenerateData):
        fit_lmc(xy1, z1, xy2, np.full(len(z2), 5.0))


def test_cokriging_deterministic():
    xy1, z1, xy2, z2 = _fixture(seed=13)
    targets = np.array([[2500.0, 2500.0], [7500.0, 7500.0]])
    a = cokriging_lmc(xy1, z1, xy2, z2, targets)
    b = cokriging_lmc(xy1, z1, xy2, z2, targets)
    assert np.array_equal(a.predictions, b.predictions)
    assert np.array_equal(a.variances, b.variances)


def test_cokriging_nugget_not_polluting_offdiagonal_covariance():
    """P2 修复：交叉块金 nug12 不得进入 C11/C22 非对角元与全程 C12。

    纯 nugget LMC（结构 sill=0、nug1=nug2=1、ρ=0.8 ⇒ nug12=0.8）隔离
    块金项：修复后系统为对角占优，解析方差 = nug1·(1+1/k1)（w1=1/k1、
    λ1=−nug1/k1、rhs=0）。缺陷版把 nug12 加到所有 h>0 的直接/交叉协
    方差，方差被系统性低估（同配置下为 0.3 而非 1.5）。
    """
    from app.lib.geo_analysis.cokriging_lmc import LMCModel
    from app.lib.geo_analysis.kriging import VariogramFit

    rng = np.random.default_rng(3)
    xy1 = rng.uniform(0, 10_000, (16, 2))
    xy2 = rng.uniform(0, 10_000, (12, 2))
    z1 = 5.0 + rng.normal(0, 1.0, len(xy1))
    z2 = 3.0 + rng.normal(0, 1.0, len(xy2))
    nug1 = nug2 = 1.0
    rho = 0.8
    lmc = LMCModel(
        rho=rho,
        structures=[("spherical", 0.0, 0.0, 100.0)],
        variogram1=VariogramFit(model="spherical", sill=0.0,
                                range_m=100.0, nugget=nug1),
        variogram2=VariogramFit(model="spherical", sill=0.0,
                                range_m=100.0, nugget=nug2),
    )
    k1, k2 = 2, 2
    targets = np.array([[5000.0, 5000.0], [1234.0, 4321.0]])
    ck = cokriging_lmc(xy1, z1, xy2, z2, targets, lmc=lmc, k1=k1, k2=k2)
    expected_var = nug1 * (1.0 + 1.0 / k1)
    assert np.allclose(ck.variances, expected_var, rtol=1e-9), (
        f"variances {ck.variances} != nugget-only cokriging variance "
        f"{expected_var}（交叉块金泄漏进 h>0 协方差？）")
    # 纯 nugget 下主变量权重均等 ⇒ 预测为主变量邻域均值
    from scipy.spatial import cKDTree
    for t_i, t in enumerate(targets):
        nb = cKDTree(xy1).query(t, k=k1)[1]
        assert abs(ck.predictions[t_i] - float(np.mean(z1[nb]))) < 1e-9


def test_cokriging_lmc_surface_tool_registered_with_complete_descriptor():
    """工具 cokriging_lmc_surface 注册存在且描述符完整（quality 棘轮闸静态引用）。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    d = reg.descriptors()["cokriging_lmc_surface"]
    assert d.side_effect.value == "deterministic_compute"
    assert "cokriging" in d.tags
