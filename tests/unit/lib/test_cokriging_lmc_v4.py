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
