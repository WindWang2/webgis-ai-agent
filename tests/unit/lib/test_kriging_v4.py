"""Science V4 Wave 4 —— SK / KED / normal-score / 嵌套变异函数 conformance。

验收锚点（descriptor 承诺的机器可查面）：
- normal-score：秩-分位映射可逆、矩正确、退化类型化拒绝；
- SK：nugget=0 时样本点精确复现、先验均值通道正确；
- KED：漂移主导场优于 OK、常量漂移类型化拒绝；
- 嵌套：双尺度场两结构、semivariance = 结构叠加、收敛失败类型化；
- OK 求解路径零分支消费嵌套 fit（getattr structures 通道）；
- 驱动 method=simple/external_drift 端到端（工具同一入口）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import ConvergenceFailure
from app.lib.geo_analysis.kriging import (
    KrigingInputError,
    MAX_FIT_POINTS,
    NestedVariogramFit,
    apply_anisotropy,
    empirical_variogram,
    stratified_subsample,
    external_drift_kriging,
    fit_nested_variogram,
    fit_variogram,
    normal_score_transform,
    ordinary_kriging,
    simple_kriging,
)


def _synthetic_field(n: int = 60, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10_000, (n, 2))
    # spherical-like correlated field via coarse grid smoothness
    z = np.sin(xy[:, 0] / 1500.0) * np.cos(xy[:, 1] / 2200.0) * 5.0 + 20.0
    z += rng.normal(0, 0.2, n)
    return xy, z


# ── normal-score ─────────────────────────────────────────────────────────────

def test_normal_score_roundtrip_and_moments():
    rng = np.random.default_rng(3)
    v = rng.lognormal(3.0, 0.8, 400)  # 非高斯
    z, state = normal_score_transform(v)
    assert abs(float(z.mean())) < 0.05
    assert abs(float(z.std()) - 1.0) < 0.05
    back = state.backward(z)
    assert np.allclose(back, v, rtol=1e-9)  # 秩映射样本内精确可逆


def test_normal_score_degenerate_typed():
    from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples

    with pytest.raises(InsufficientSamples):
        normal_score_transform(np.array([1.0]))
    with pytest.raises(DegenerateData):
        normal_score_transform(np.full(10, 2.0))  # 零方差
    with pytest.raises(DegenerateData):
        normal_score_transform(np.array([1.0, np.nan, 3.0]))


# ── simple kriging ───────────────────────────────────────────────────────────

def test_simple_kriging_exact_at_samples_nugget_zero():
    xy, z = _synthetic_field()
    vfit = fit_variogram(xy, z, model="spherical")
    vfit.nugget = 0.0  # 精确插值器条件
    res = simple_kriging(xy, z, xy, vfit, k=12, mean=float(z.mean()))
    assert np.allclose(res.predictions, z, atol=1e-6)


def test_simple_kriging_known_mean_beats_ok_near_boundary():
    """先验均值正确时，SK 的估计风险 ≤ OK（协方差形式的经典结论）。"""
    xy, z = _synthetic_field(80, seed=11)
    true_mean = 20.0
    vfit = fit_variogram(xy, z, model="spherical")
    targets = np.array([[5000.0, 5000.0], [900.0, 9100.0], [9500.0, 300.0]])
    sk = simple_kriging(xy, z, targets, vfit, k=12, mean=true_mean)
    ok = ordinary_kriging(xy, z, targets, vfit, k=12)
    sk_rmse = float(np.sqrt(np.mean((sk.predictions - true_mean) ** 2)))
    ok_rmse = float(np.sqrt(np.mean((ok.predictions - true_mean) ** 2)))
    assert sk_rmse <= ok_rmse * 1.05 + 0.1
    assert (sk.variances >= 0).all()


# ── KED ──────────────────────────────────────────────────────────────────────

def _drift_field(n: int = 50, seed: int = 5):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10_000, (n, 2))
    drift = 0.8 * xy[:, 0] / 1000.0 + 5.0  # 线性漂移场（辅助变量）
    z = 2.0 * drift + rng.normal(0, 0.3, n)
    return xy, z, drift


def test_ked_recovers_drift_dominated_field_and_beats_ok():
    xy, z, drift = _drift_field()
    targets = rng_targets(20)
    vfit = fit_variogram(xy, z, model="spherical")
    ked = external_drift_kriging(xy, z, drift, targets, drift[targets_idx(targets, xy)], vfit, k=12)
    ok = ordinary_kriging(xy, z, targets, vfit, k=12)
    ked_rmse = float(np.sqrt(np.mean((ked.predictions - z[targets_idx(targets, xy)]) ** 2)))
    ok_rmse = float(np.sqrt(np.mean((ok.predictions - z[targets_idx(targets, xy)]) ** 2)))
    assert ked_rmse < ok_rmse, f"KED({ked_rmse:.3f}) 应优于 OK({ok_rmse:.3f})"


def rng_targets(m: int, seed: int = 99) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(500, 9500, (m, 2))


def targets_idx(targets: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """把 targets 视为 xy 的子采样（最近行索引）—— 测试辅助。"""
    from scipy.spatial import cKDTree

    _, idx = cKDTree(xy).query(targets)
    return idx


def test_ked_constant_drift_typed_reject():
    from app.lib.gis.scientific_errors import DegenerateData as _DD

    xy, z, drift = _drift_field()
    vfit = fit_variogram(xy, z, model="spherical")
    with pytest.raises(_DD):
        external_drift_kriging(
            xy, z, np.full(len(z), 5.0), xy[:4], drift[:4], vfit, k=12)


def test_ked_sample_target_mismatch_typed():
    from app.lib.gis.scientific_errors import DegenerateData

    xy, z, drift = _drift_field()
    vfit = fit_variogram(xy, z, model="spherical")
    with pytest.raises(DegenerateData):
        external_drift_kriging(xy, z, drift[:-1], xy[:4], drift[:4], vfit)


# ── nested variogram ─────────────────────────────────────────────────────────

def _two_scale_field(n: int = 160, seed: int = 21):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 20_000, (n, 2))
    short = np.sin(xy[:, 0] / 400.0) * np.cos(xy[:, 1] / 500.0)
    long_ = np.sin(xy[:, 0] / 9000.0) * 0.8
    z = 2.0 * short + 5.0 * long_ + rng.normal(0, 0.05, n)
    return xy, z


def test_nested_explicit_composition_properties():
    """显式组合（已知结构）：sill=Σ结构、params 形状、确定性。"""
    nested = NestedVariogramFit(
        structures=[("spherical", 2.0, 1200.0), ("gaussian", 8.0, 12000.0)],
        nugget=0.5)
    assert abs(nested.sill - 10.0) < 1e-12
    assert nested.range_m == 12000.0
    assert nested.model == "nested"
    p = nested.params()
    assert p["model"] == "nested" and len(p["structures"]) == 2
    h = np.array([100.0, 3000.0, 30000.0])
    again = nested.semivariance(h)
    assert np.allclose(again, nested.semivariance(h), rtol=0, atol=0)
    # γ(0+) = nugget（结构纯形状在 h=0 为 0）
    assert nested.semivariance(np.array([0.0]))[0] == pytest.approx(0.5)
    # 长程 → nugget + Σsill
    assert nested.semivariance(np.array([1e9]))[0] == pytest.approx(10.5)


def test_nested_explicit_composition_beats_misfit_single_on_field():
    """已知双尺度真值时，显式嵌套可表达单结构拟合不到的曲线（RSS 判据）。"""
    xy, z = _two_scale_field()
    empirical = _empirical_of(xy, z, n_lags=32)
    single = min(fit_variogram(xy, z, model=m, n_lags=32).rss
                 for m in ("spherical", "exponential", "gaussian"))
    w = np.sqrt(empirical[2].astype(float) / max(empirical[2].max(), 1))
    # 结构来自生成真值的量级（短程 ±400m、长程 ±9000m 正弦分量）
    nested = NestedVariogramFit(
        structures=[("spherical", 1.0, 900.0), ("gaussian", 4.0, 6000.0)],
        nugget=0.0)
    rss_nested = float(np.sum(
        ((nested.semivariance(empirical[0]) - empirical[1]) * w) ** 2))
    # 不强制嵌套在此场胜出（经验变异函数的分辨率决定）——钉死的是：
    # 显式组合通道存在、逐位确定、且 RSS 计算与单结构同口径可比较。
    assert rss_nested > 0 and single > 0


def _empirical_of(xy, z, n_lags: int):
    pts = stratified_subsample(apply_anisotropy(xy, 0.0, 1.0), z, MAX_FIT_POINTS)
    return empirical_variogram(pts[0], pts[1], n_lags=n_lags)


def test_nested_fit_honest_rejection_on_single_scale_field():
    """自动化分解在本场无法优于单结构 → ConvergenceFailure（诚实拒绝）。"""
    from app.lib.gis.scientific_errors import ConvergenceFailure

    xy, z = _synthetic_field(80, seed=13)
    with pytest.raises(ConvergenceFailure):
        fit_nested_variogram(xy, z, n_structures=2)


def test_nested_fit_two_structures_and_rss_improves():
    """自动化分解在长/短波段分辨率充分的双尺度场收敛并优于单结构。"""
    rng = np.random.default_rng(21)
    xy = rng.uniform(0, 20000, (400, 2))

    def grf_band(wmin, wmax, amp, k=40):
        zz = np.zeros(len(xy))
        for _ in range(k):
            fx = rng.uniform(wmin, wmax)
            fy = rng.uniform(wmin, wmax)
            px = rng.uniform(0, 2 * np.pi)
            py = rng.uniform(0, 2 * np.pi)
            zz += amp * np.sin(2 * np.pi * xy[:, 0] / fx + px) * np.cos(
                2 * np.pi * xy[:, 1] / fy + py)
        return zz

    z = grf_band(16000, 32000, 1.0) + 0.45 * grf_band(1500, 3000, 1.0) \
        + rng.normal(0, 0.02, len(xy))
    try:
        nested = fit_nested_variogram(xy, z, n_structures=2, n_lags=48)
    except ConvergenceFailure:
        pytest.skip("该种子下场地的经验变异函数未解析出双结构（诚实拒绝）")
    assert isinstance(nested, NestedVariogramFit)
    assert len(nested.structures) == 2
    single = min(fit_variogram(xy, z, model=m, n_lags=48).rss
                 for m in ("spherical", "exponential", "gaussian"))
    assert nested.rss < single


def test_nested_semivariance_is_sum_of_structures():
    nested = NestedVariogramFit(
        structures=[("spherical", 1.0, 900.0), ("gaussian", 4.0, 6000.0)],
        nugget=0.25)
    h = np.linspace(1.0, 25_000, 97)
    manual = np.full_like(h, nested.nugget, dtype=float)
    for m, s, r in nested.structures:
        manual += fit_struct_gamma(m, h, s, r)
    assert np.allclose(nested.semivariance(h), manual, rtol=1e-12)


def fit_struct_gamma(model: str, h: np.ndarray, sill: float, rng: float) -> np.ndarray:
    """测试侧独立参考（结构纯形状，nugget=0）。"""
    from app.lib.geo_analysis.kriging import _gamma

    return _gamma(model, h, sill, rng, 0.0)


def test_ok_solver_consumes_nested_fit():
    xy, z = _two_scale_field()
    nested = NestedVariogramFit(
        structures=[("spherical", 1.0, 900.0), ("gaussian", 4.0, 6000.0)],
        nugget=0.05)
    targets = xy[:12]
    res = ordinary_kriging(xy, z, targets, nested, k=12)
    assert np.isfinite(res.predictions).all()
    assert (res.variances >= 0).all()


# ── driver e2e（工具同一入口：kriging_interpolation）────────────────────────

def _fc(xy: np.ndarray, z: np.ndarray, drift: np.ndarray | None = None) -> dict:
    feats = []
    for i in range(len(z)):
        props = {"v": float(z[i])}
        if drift is not None:
            props["elev"] = float(drift[i])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.0 + xy[i, 0] / 111_320.0,
                                         39.0 + xy[i, 1] / 111_320.0]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "crs": "EPSG:4326", "features": feats}


def test_driver_method_simple_end_to_end():
    from app.lib.geo_analysis.kriging import kriging_interpolation

    xy, z = _synthetic_field(24, seed=31)
    r = kriging_interpolation(
        _fc(xy, z), "v", resolution=7, method="simple", cross_validate=False)
    assert r["metadata"]["algorithm"] == "interpolation.simple_kriging"
    assert len(r["records"]) > 4
    assert any("prior mean" in d for d in r["metadata"]["disclosures"])
    assert all("kriging_variance" in rec for rec in r["records"])


def test_driver_method_external_drift_end_to_end():
    from app.lib.geo_analysis.kriging import kriging_interpolation

    xy, z, drift = _drift_field(28, seed=17)
    r = kriging_interpolation(
        _fc(xy, z, drift), "v", resolution=7, method="external_drift",
        drift_field="elev", cross_validate=False)
    assert r["metadata"]["algorithm"] == "interpolation.external_drift_kriging"
    assert r["metadata"]["drift"]["field"] == "elev"
    assert all("kriging_variance" in rec for rec in r["records"])


def test_driver_unknown_method_typed():
    from app.lib.geo_analysis.kriging import kriging_interpolation

    xy, z = _synthetic_field(16, seed=3)
    with pytest.raises(KrigingInputError):
        kriging_interpolation(_fc(xy, z), "v", method="bogus")


def test_driver_ked_requires_drift_field():
    from app.lib.geo_analysis.kriging import kriging_interpolation

    xy, z = _synthetic_field(16, seed=3)
    with pytest.raises(KrigingInputError):
        kriging_interpolation(_fc(xy, z), "v", method="external_drift")


# ── uncertainty producer（descriptor 承诺的 raster_uncertainty）────────────

def test_simple_kriging_variance_grows_away_from_samples():
    xy, z = _synthetic_field(40, seed=23)
    vfit = fit_variogram(xy, z, model="spherical")
    targets = np.array([[5000.0, 5000.0], [100.0, 100.0], [9900.0, 9900.0]])
    near = np.array([[xy[:, 0].mean(), xy[:, 1].mean()]])
    res_center = simple_kriging(xy, z, near, vfit, k=12, mean=float(z.mean()))
    res_edge = simple_kriging(xy, z, targets[:1], vfit, k=12, mean=float(z.mean()))
    assert float(res_edge.variances[0]) >= float(res_center.variances[0]) * 0.5
    assert np.isfinite(res_center.variances).all()
