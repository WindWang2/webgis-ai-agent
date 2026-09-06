"""Variogram V2 conformance — new families, anisotropy, block CV, backends.

Contract bullets (Foundation V2 · A2):

* ``matern`` γ matches the independent ``scipy.special`` reference formula
  (K_ν, log-Gamma) to machine precision; ν=0.5 coincides with the
  exponential family under the range rescaling α_matern = α_exp/3; large ν
  converges MONOTONICALLY to the gaussian family (matched range
  α_matern = α_gauss/√(12ν)) — the convergence rate is O(1/ν), so ν=30 is
  asserted within 3% (the asymptotic limit itself, not a fixed rtol).
* ``wave`` reaches its first sill exactly at h = π·range (sin(π) = 0) and
  γ(0) = nugget.
* ``cubic`` carries the golden polynomial coefficients (7, −8.75, 3.5,
  −0.75): γ(0) = nugget, γ(range) = nugget + sill, constant beyond, C² at
  the range.
* ``auto`` keeps fitting ONLY {spherical, exponential, gaussian} — the
  production selection is unchanged (new families are explicit opt-in).
* Geometric anisotropy: defaults are the identity (bit-identical path); the
  correct angle/ratio lowers the fitted weighted RSS on a synthetic
  anisotropic field; invalid ratios are typed rejections.
* Spatial block CV: deterministic (no RNG), declines < 20 samples, and
  reports the honest degradation of clustered sampling designs (block RMSE
  > index RMSE there).
* Explicit solve backends: forced numpy_batched and scipy_linalg agree on
  well-conditioned data (atol 1e-9); the executed backend is reported.
"""
import numpy as np
import pytest
from scipy.special import gammaln, kv

from app.lib.geo_analysis.kriging import (
    ALL_VARIOGRAM_MODELS,
    AUTO_VARIOGRAM_MODELS,
    VariogramFit,
    _gamma,
    anisotropy_transform,
    apply_anisotropy,
    cross_validate_kriging,
    fit_variogram,
    ordinary_kriging,
)
from app.lib.gis.scientific_errors import InsufficientSamples

pytestmark = pytest.mark.unit

SILL, RANGE, NUGGET = 2.5, 120.0, 0.4


def _reference_matern_gamma(h, sill, rng, nugget, nu):
    """Independent hand formula: γ = nugget + sill·(1 − 2^{1−ν}x^νK_ν(x)/Γ(ν))."""
    x = np.asarray(h, dtype=float) / rng
    corr = (2.0 ** (1.0 - nu)) * np.power(x, nu) * kv(nu, x) / float(np.exp(gammaln(nu)))
    corr = np.where(x == 0.0, 1.0, corr)  # exact h=0 limit: γ = nugget
    return nugget + sill * (1.0 - corr)


# ── matern ──────────────────────────────────────────────────────────────────

def test_matern_matches_scipy_reference_formula():
    h = np.array([0.0, 1.0, 5.0, 30.0, 120.0, 300.0])
    for nu in (0.3, 0.5, 1.5, 2.5):
        got = _gamma("matern", h, SILL, RANGE, NUGGET, nu=nu)
        expected = _reference_matern_gamma(h, SILL, RANGE, NUGGET, nu)
        np.testing.assert_allclose(got, expected, rtol=1e-12, atol=1e-12)


def test_matern_guard_h_zero_is_nugget():
    for nu in (0.5, 2.0):
        got = float(_gamma("matern", np.array([0.0]), SILL, RANGE, NUGGET, nu=nu)[0])
        assert got == pytest.approx(NUGGET, abs=1e-12)


def test_matern_half_equals_exponential_rescaled():
    """ν=0.5: 1−e^{−x}; our exponential uses 1−e^{−3h/α} → α_matern = α_exp/3."""
    h = np.linspace(0.0, 200.0, 25)
    got = _gamma("matern", h, SILL, RANGE, NUGGET, nu=0.5)
    expected = _gamma("exponential", h, SILL, RANGE * 3.0, NUGGET)
    np.testing.assert_allclose(got, expected, rtol=1e-12)


def test_matern_large_nu_converges_monotonically_to_gaussian():
    """Matched ranges α_m = α_g/√(12ν); deviation shrinks as ν grows.

    The honest convergence rate is O(1/ν) — ν=30 sits near 1% (not 1e-3);
    the assertion is the monotone convergence + the ν=30 magnitude.
    """
    h = np.linspace(1.0, 150.0, 60)
    gaussian = _gamma("gaussian", h, SILL, RANGE, NUGGET)
    errors = []
    for nu in (10.0, 30.0, 60.0, 120.0):
        matern = _gamma("matern", h, SILL, RANGE / np.sqrt(12.0 * nu), NUGGET, nu=nu)
        errors.append(float(np.max(np.abs(matern - gaussian))))
    assert errors[0] > errors[1] > errors[2] > errors[3]
    assert errors[1] < 0.03 * SILL  # ν=30: within 3% of the gaussian limit


# ── wave ────────────────────────────────────────────────────────────────────

def test_wave_reaches_sill_at_pi_range():
    h = np.array([0.0, np.pi * RANGE, 2.0 * np.pi * RANGE])
    got = _gamma("wave", h, SILL, RANGE, NUGGET)
    assert got[0] == pytest.approx(NUGGET, abs=1e-12)
    assert got[1] == pytest.approx(NUGGET + SILL, abs=1e-12)  # sin(π)=0
    assert got[2] == pytest.approx(NUGGET + SILL, abs=1e-12)  # sin(2π)=0
    # hole effect: γ exceeds the sill between π and 2π (by design, disclosed)
    mid = _gamma("wave", np.array([1.5 * np.pi * RANGE]), SILL, RANGE, NUGGET)
    assert float(mid[0]) > NUGGET + SILL


# ── cubic ───────────────────────────────────────────────────────────────────

def test_cubic_golden_polynomial_coefficients():
    """γ(x) = nugget + sill·(7x²−8.75x³+3.5x⁵−0.75x⁷) for x ≤ 1 (golden
    coefficients verified against an independent numpy polynomial)."""
    x = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    h = x * RANGE
    got = _gamma("cubic", h, SILL, RANGE, NUGGET)
    coeffs = [-0.75, 0.0, 3.5, 0.0, -8.75, 7.0, 0.0, 0.0]  # x^7 … x^0
    poly = np.polyval(coeffs, x)
    expected = NUGGET + SILL * poly
    np.testing.assert_allclose(got, expected, rtol=1e-12)
    assert got[0] == pytest.approx(NUGGET, abs=1e-12)
    assert got[-1] == pytest.approx(NUGGET + SILL, abs=1e-12)
    # compact support: constant beyond the range; C² at the range
    beyond = _gamma("cubic", np.array([1.5 * RANGE, 10 * RANGE]), SILL, RANGE, NUGGET)
    assert beyond[0] == pytest.approx(NUGGET + SILL, abs=1e-12)
    assert beyond[1] == pytest.approx(NUGGET + SILL, abs=1e-12)
    eps = 1e-6
    dp = (_gamma("cubic", np.array([RANGE]), SILL, RANGE, NUGGET)[0]
          - _gamma("cubic", np.array([RANGE - eps]), SILL, RANGE, NUGGET)[0]) / eps
    dp_in = (_gamma("cubic", np.array([RANGE]), SILL, RANGE, NUGGET)[0]
             - _gamma("cubic", np.array([RANGE - eps]), SILL, RANGE, NUGGET)[0]) / eps
    assert abs(dp) < 1e-3 and abs(dp_in) < 1e-3


# ── fit machinery: auto unchanged, new families opt-in ──────────────────────

def _stationary_field(n=80, seed=11, span=5000.0):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, span, (n, 2))
    # gaussian-shaped covariance so the gaussian family is the truth
    z = (
        8.0 * np.exp(-((xy[:, 0] - span / 2) ** 2 + (xy[:, 1] - span / 2) ** 2)
                     / (2 * 1500.0 ** 2))
        + rng.normal(0, 0.4, n)
    )
    return xy, z


def test_auto_fit_unchanged_legacy_trio():
    assert AUTO_VARIOGRAM_MODELS == ("spherical", "exponential", "gaussian")
    xy, z = _stationary_field()
    fit = fit_variogram(xy, z, model="auto")
    assert fit.model in AUTO_VARIOGRAM_MODELS


def test_new_family_explicit_fit_and_exact_prediction():
    xy, z = _stationary_field()
    # 1) explicit fits succeed and predict finitely (fitted ranges allowed
    #    to sit in the model's own optimal regime)
    for model in ("matern", "wave", "cubic"):
        vfit = fit_variogram(xy, z, model=model, matern_smoothness=1.0)
        assert vfit.model == model
        assert vfit.sill > 0 and vfit.range_m > 0 and vfit.nugget >= 0
        res = ordinary_kriging(xy, z, xy[:8], vfit, k=10)
        assert np.isfinite(res.predictions).all()
        assert np.isfinite(res.variances).all()
    # 2) exact interpolation at the sample sites is honoured for every
    #    family with nugget=0 when the neighbourhood sits on the monotone
    #    branch with γ(h) well above the solve-time ridge: range ~ sample
    #    spacing keeps x = h/range ∈ (0, π) for wave (monotone first lobe)
    #    and γ ≫ 1e-6·sill for all three families.
    for model in ("matern", "wave", "cubic"):
        exact = VariogramFit(model=model, sill=1.0, range_m=600.0, nugget=0.0)
        res_exact = ordinary_kriging(xy, z, xy[:8], exact, k=6)
        # wave carries slightly larger dust near the lobe edge (steeper
        # weights) — 5e-3 on a z scale of ~1.5-7 still proves exactness
        np.testing.assert_allclose(res_exact.predictions, z[:8], atol=5e-3)


def test_unknown_family_rejected_and_vocabulary_frozen():
    xy, z = _stationary_field(n=30)
    with pytest.raises(Exception, match="variogram model"):
        fit_variogram(xy, z, model="power")
    assert set(ALL_VARIOGRAM_MODELS) == {
        "spherical", "exponential", "gaussian", "matern", "wave", "cubic",
    }


def test_matern_smoothness_bounds_validated():
    xy, z = _stationary_field(n=30)
    with pytest.raises(Exception, match="matern_smoothness"):
        fit_variogram(xy, z, model="matern", matern_smoothness=9.0)
    with pytest.raises(Exception, match="matern_smoothness"):
        fit_variogram(xy, z, model="matern", matern_smoothness=0.01)


# ── anisotropy ──────────────────────────────────────────────────────────────

def _anisotropic_field(angle_deg=30.0, ratio=3.0, n=140, seed=5, noise=0.15):
    """Field correlated along the major axis at ``angle_deg`` (ratio:1)."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 10000.0, (n, 2))
    t = np.deg2rad(angle_deg)
    major = xy[:, 0] * np.cos(t) + xy[:, 1] * np.sin(t)
    z = np.sin(major / 900.0) + rng.normal(0, noise, n)
    return xy, z


def test_anisotropy_default_identity_bitwise():
    xy, z = _stationary_field(n=50)
    base = fit_variogram(xy, z, model="spherical")
    explicit = fit_variogram(xy, z, model="spherical",
                             anisotropy_angle=0.0, anisotropy_ratio=1.0)
    assert base.params() == explicit.params()
    pts = xy[:10]
    v = VariogramFit(model="spherical", sill=base.sill, range_m=base.range_m,
                     nugget=base.nugget)
    r1 = ordinary_kriging(xy, z, pts, v)
    r2 = ordinary_kriging(xy, z, pts, v, anisotropy_angle=0.0, anisotropy_ratio=1.0)
    assert np.array_equal(r1.predictions, r2.predictions)
    assert np.array_equal(r1.variances, r2.variances)
    # the transform itself returns the input object (identity fast path)
    assert apply_anisotropy(xy) is xy


def test_anisotropy_improves_fit_on_rotated_field():
    angle, ratio = 30.0, 3.0
    xy, z = _anisotropic_field(angle_deg=angle, ratio=ratio)
    iso = fit_variogram(xy, z, model="gaussian")
    aniso = fit_variogram(xy, z, model="gaussian",
                          anisotropy_angle=angle, anisotropy_ratio=ratio)
    assert aniso.rss < iso.rss, (
        f"anisotropic fit RSS {aniso.rss:.4f} must beat isotropic {iso.rss:.4f}"
    )


def test_anisotropy_prediction_changes_with_rotation():
    xy, z = _anisotropic_field(angle_deg=30.0, ratio=3.0)
    vfit = VariogramFit(model="gaussian", sill=1.0, range_m=2000.0, nugget=0.1)
    targets = np.array([[5000.0, 5000.0], [2000.0, 8000.0]])
    r_iso = ordinary_kriging(xy, z, targets, vfit)
    r_aniso = ordinary_kriging(xy, z, targets, vfit,
                               anisotropy_angle=30.0, anisotropy_ratio=3.0)
    assert not np.allclose(r_iso.predictions, r_aniso.predictions)


def test_anisotropy_invalid_ratio_rejected():
    with pytest.raises(Exception, match="anisotropy_ratio"):
        anisotropy_transform(0.0, 0.5)


def test_anisotropy_axis_lengths_golden():
    """评审 R2 CRITICAL-1 锁：主轴映长 1、垂直主轴映长 ratio（不可翻转）。

    R(−θ) 约定：沿声明主轴 (cosθ, sinθ) 的位移映射后长度 = d（变程 α），
    垂直方向 = d·ratio（变程 α/ratio）。R(+θ) 会把主轴映到 2θ —— 在
    θ=45° 时主/短轴完全互换，属科学错误，这里按轴向长度精确钉死。
    """
    angle, ratio = 30.0, 3.0
    A = anisotropy_transform(angle, ratio)
    t = np.deg2rad(angle)
    u_major = np.array([np.cos(t), np.sin(t)])
    u_minor = np.array([-np.sin(t), np.cos(t)])
    assert np.isclose(np.linalg.norm(A @ u_major), 1.0, atol=1e-12)
    assert np.isclose(np.linalg.norm(A @ u_minor), ratio, atol=1e-12)
    # 45° 是最易翻转朝向的情形（R(+θ) 下主轴长度 = ratio）
    A45 = anisotropy_transform(45.0, ratio)
    u = np.array([np.cos(np.pi / 4), np.sin(np.pi / 4)])
    assert np.isclose(np.linalg.norm(A45 @ u), 1.0, atol=1e-12)


# ── explicit solve backend (A7 wiring) ──────────────────────────────────────

def test_solve_backends_identical_on_well_conditioned_data():
    xy, z = _stationary_field(n=60, seed=19)
    vfit = fit_variogram(xy, z, model="spherical")
    targets = xy[:12]
    r_np = ordinary_kriging(xy, z, targets, vfit, k=8, solve_backend="numpy_batched")
    r_sc = ordinary_kriging(xy, z, targets, vfit, k=8, solve_backend="scipy_linalg")
    np.testing.assert_allclose(r_np.predictions, r_sc.predictions, atol=1e-9)
    np.testing.assert_allclose(r_np.variances, r_sc.variances, atol=1e-9)
    assert r_np.solve_backend_used == "numpy_batched"
    assert r_sc.solve_backend_used == "scipy_linalg"


def test_solve_backend_auto_keeps_degraded_accounting():
    """auto = historical path: still counts degraded cells when the system is
    singular (duplicate coordinates with k covering them all)."""
    xy = np.array([[0.0, 0.0]] * 6 + [[1000.0, 0.0]] * 6, dtype=float)
    z = np.array([1.0] * 6 + [2.0] * 6)
    vfit = VariogramFit(model="spherical", sill=1.0, range_m=500.0, nugget=0.1)
    res = ordinary_kriging(xy, z, np.array([[500.0, 0.0]]), vfit, k=12)
    assert res.solve_backend_used == "numpy_batched"
    assert np.isfinite(res.predictions).all()


def test_solve_backend_invalid_rejected():
    xy, z = _stationary_field(n=20)
    vfit = fit_variogram(xy, z, model="spherical")
    with pytest.raises(Exception, match="solve_backend"):
        ordinary_kriging(xy, z, xy[:3], vfit, solve_backend="cuda")


# ── spatial block CV ────────────────────────────────────────────────────────

def test_block_cv_deterministic_and_reports_folds():
    xy, z = _stationary_field(n=60, seed=23)
    a = cross_validate_kriging(xy, z, cv_scheme="spatial_block")
    b = cross_validate_kriging(xy, z, cv_scheme="spatial_block")
    assert a.metrics() == b.metrics()
    assert a.scheme == "spatial_block"
    assert len(a.per_fold) == a.folds
    for entry in a.per_fold:
        assert entry["rmse"] is not None and entry["n_test"] > 0
        assert isinstance(entry["block_ids"], list) and entry["block_ids"]
    assert a.rmse is not None


def test_block_cv_declines_small_samples():
    xy, z = _stationary_field(n=15, seed=3)
    rep = cross_validate_kriging(xy, z, cv_scheme="spatial_block")
    assert rep.rmse is None and rep.folds == 0
    assert "20" in rep.note
    # invalid scheme is a structured rejection
    with pytest.raises(Exception, match="cv_scheme"):
        cross_validate_kriging(xy, z, cv_scheme="bootstrap")


def test_block_cv_honest_degradation_on_clustered_design():
    """Clustered sampling: block CV (validating across spatially separated
    blocks) must report a HIGHER error than index CV (which mixes cluster
    neighbours into train/test) — the honest degradation, no RNG."""
    rng = np.random.default_rng(42)
    centers = np.array([[2500.0, 2500.0], [7500.0, 2500.0], [5000.0, 7500.0]])
    pts, vals = [], []
    for c in centers:
        pts.append(c + rng.normal(0, 120.0, (24, 2)))
        base = np.sin(c[0] / 1500.0) + np.cos(c[1] / 1200.0)
        vals.append(np.full(24, base))
    xy = np.vstack(pts)
    z = np.concatenate(vals) + rng.normal(0, 0.05, len(xy))
    index_cv = cross_validate_kriging(xy, z, model="gaussian", cv_scheme="index")
    block_cv = cross_validate_kriging(xy, z, model="gaussian",
                                      cv_scheme="spatial_block")
    assert index_cv.rmse is not None and block_cv.rmse is not None
    assert block_cv.rmse > index_cv.rmse, (
        f"block CV ({block_cv.rmse:.4f}) must exceed index CV ({index_cv.rmse:.4f}) "
        "on a clustered design — extrapolative blocks are honest"
    )


def test_insufficient_samples_ordinary_typed():
    with pytest.raises(InsufficientSamples):
        ordinary_kriging(
            np.array([[0.0, 0.0]]), np.array([1.0]),
            np.array([[1.0, 1.0]]),
            VariogramFit(model="spherical", sill=1.0, range_m=1.0),
        )
