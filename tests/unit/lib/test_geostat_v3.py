"""Geostatistics/Interpolation V3 conformance — 方向变异函数/模型选择/指示克里金/协同克里金/最近邻/自然邻域/块克里金。

Contract bullets (Foundation V3 batch):

* Directional variogram — the axis filter is BIDIRECTIONAL (azimuth and
  azimuth+180 are bit-identical); with tolerance=90° it coincides BIT-EXACT
  with the omnidirectional ``empirical_variogram``; an anisotropic field
  (long range along x) shows the 0° curve rising SLOWER than the 90° curve;
  the band width strictly limits kept pairs.
* Variogram model selection — all six families ranked by weighted RSS with
  AICc (k=3; matern k=4 disclosed); fully deterministic replay; typed
  rejections below the sample floor / unknown families.
* Indicator kriging — probabilities clamped to [0,1] with clamped cells
  counted; p50 threshold == the first threshold reaching p≥0.5 (recomputed
  independently); constant indicators (threshold outside the value range)
  honestly disclosed without fitting; E-type midpoint estimate; typed
  guards.
* Collocated co-kriging (MM1) — a highly correlated secondary (ρ≈0.95)
  REDUCES the LOO RMSE versus ordinary kriging under a shared prefit
  variogram protocol; weak correlation is a typed
  ``ScientificPreconditionFailed``; the estimated ρ matches the design.
* Nearest neighbour — every cell value equals its brute-force nearest
  sample (Voronoi semantics); scale guards are typed.
* Natural neighbour (Sibson) — reproduced sample points return the sample
  value exactly (float64); a linear plane is reproduced ≤1e-6 in the
  interior; targets outside the convex hull are NaN (no extrapolation);
  collinear/degenerate and scale guards are typed.
* Block kriging — vanishing block size converges to point kriging
  (rtol 1e-3) under a shared prefit variogram; mean block variance ≤ mean
  point variance (support effect); block_size ≤ 0 is a typed rejection.

Registry: every V3 descriptor validates clean and the interpolation tool
parity gate stays empty with all seven tools registered. No wall-clock
assertions; every RNG fixture is seeded.
"""
import asyncio

import numpy as np
import pytest

from app.lib.geo_analysis.interpolation import (
    NN_MAX_GRID_CELLS,
    NN_MAX_SAMPLES,
    nearest_neighbor_interpolation,
)
from app.lib.geo_analysis.kriging import (
    COKRIGING_MIN_ABS_RHO,
    KrigingInputError,
    MAX_FIT_POINTS,
    anisotropy_transform,
    apply_anisotropy,
    block_kriging,
    collocated_cokriging,
    directional_variogram,
    empirical_variogram,
    fit_anisotropy,
    fit_variogram,
    indicator_kriging,
    ordinary_kriging,
    select_variogram_model,
)
from app.lib.geo_analysis.tin_interpolation import (
    SIBSON_MAX_GRID_CELLS,
    SIBSON_MAX_SAMPLES,
    natural_neighbor_interpolation,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    ResourceScaleMismatch,
    ScientificPreconditionFailed,
)

pytestmark = pytest.mark.unit


# ── fixtures (all seeded — determinism contract) ────────────────────────────

def _anisotropic_field(n=260, seed=42, span=10000.0):
    """Field correlated along x (long range) and y (short range): the 0°
    azimuth semivariance must rise SLOWER than the 90° one."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, span, (n, 2))
    z = np.sin(xy[:, 0] / 1500.0) + np.sin(xy[:, 1] / 400.0) + rng.normal(0, 0.02, n)
    return xy, z


def _stationary_metric(n=60, seed=42, span=5000.0, noise=0.4):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, span, (n, 2))
    z = (
        10.0 * np.exp(-((xy[:, 0] - span / 2) ** 2 + (xy[:, 1] - span / 2) ** 2)
                      / (2 * 1200.0 ** 2))
        + rng.normal(0, noise, n)
    )
    return xy, z


def _secondary_pair(xy, z, seed=42):
    """Secondary variable ≈ 0.95-correlated with the primary signal."""
    rng = np.random.default_rng(seed)
    sec_xy = xy + rng.normal(0, 30.0, xy.shape)
    signal = np.sin(xy[:, 0] / 900.0) + np.cos(xy[:, 1] / 1100.0)
    y = signal + rng.normal(0, 0.38, len(z))
    return sec_xy, y


def _grid(xy, m=6, pad=0.1):
    x0, y0 = xy.min(axis=0)
    x1, y1 = xy.max(axis=0)
    gx = np.linspace(x0 + pad * (x1 - x0), x1 - pad * (x1 - x0), m)
    gy = np.linspace(y0 + pad * (y1 - y0), y1 - pad * (y1 - y0), m)
    return np.column_stack(np.meshgrid(gx, gy)).reshape(-1, 2)


# ── 1. directional variogram ────────────────────────────────────────────────

def test_directional_variogram_anisotropy_discriminant():
    """Field with long range along x: the 0° (east) curve rises slower
    than the 90° (north) curve over the leading lags."""
    xy, z = _anisotropic_field()
    lags_h, g_h, c_h, meta_h = directional_variogram(xy, z, 0.0, n_lags=8)
    lags_v, g_v, c_v, meta_v = directional_variogram(xy, z, 90.0, n_lags=8)
    assert meta_h["azimuth_deg"] == 0.0 and meta_v["azimuth_deg"] == 90.0
    assert c_h.sum() > 0 and c_v.sum() > 0
    # leading two lags: horizontal structure is slower to rise
    assert g_h[:2].sum() < g_v[:2].sum(), (
        f"anisotropic fixture: 0° curve ({g_h[:2]}) must rise slower than "
        f"90° ({g_v[:2]})"
    )
    # math-convention disclosure is carried in the meta
    assert "数学约定" in meta_h["azimuth_convention"]


def test_directional_variogram_axis_bidirectional_and_omnidirectional_parity():
    """+180° is the SAME axis (bit-identical curve); tolerance=90° degrades
    to the omnidirectional empirical variogram BIT-EXACTLY."""
    xy, z = _stationary_metric(n=80)
    _, g0, _, _ = directional_variogram(xy, z, 0.0, n_lags=6)
    _, g180, _, _ = directional_variogram(xy, z, 180.0, n_lags=6)
    np.testing.assert_array_equal(g0, g180)
    # tolerance=90 → all pairs → identical to empirical_variogram
    lags_d, gd, cd, _ = directional_variogram(xy, z, 30.0, tolerance_deg=90.0, n_lags=6)
    lags_o, go, co = empirical_variogram(xy, z, n_lags=6)
    np.testing.assert_array_equal(lags_d, lags_o)
    np.testing.assert_array_equal(gd, go)
    np.testing.assert_array_equal(cd, co)
    # band width strictly limits kept pairs
    _, _, c_open, m_open = directional_variogram(xy, z, 0.0, n_lags=6)
    _, _, c_band, m_band = directional_variogram(xy, z, 0.0, n_lags=6, band_width=400.0)
    assert c_band.sum() < c_open.sum()
    assert m_band["n_pairs_kept"] < m_open["n_pairs_kept"]


def test_directional_variogram_input_guards():
    xy, z = _stationary_metric(n=30)
    with pytest.raises(KrigingInputError, match="tolerance_deg"):
        directional_variogram(xy, z, 0.0, tolerance_deg=95.0)
    with pytest.raises(KrigingInputError, match="band_width"):
        directional_variogram(xy, z, 0.0, band_width=-1.0)
    with pytest.raises(KrigingInputError, match="azimuth_deg"):
        directional_variogram(xy, z, float("nan"))
    with pytest.raises(KrigingInputError, match="至少需要 2"):
        directional_variogram(xy[:1], z[:1], 0.0)


# ── 2. variogram model selection ────────────────────────────────────────────

def test_variogram_selection_ranks_deterministic_and_complete():
    xy, z = _stationary_metric(n=90)
    ranking, meta = select_variogram_model(xy, z, n_lags=10)
    assert {r["model"] for r in ranking} == {
        "spherical", "exponential", "gaussian", "matern", "wave", "cubic",
    }  # all six families compete (order is data-driven, not fixed)
    rss = [r["weighted_rss"] for r in ranking]
    assert rss == sorted(rss)  # weighted-RSS ascending
    for entry in ranking:
        assert set(entry) >= {"model", "params", "weighted_rss", "aicc"}
        assert np.isfinite(entry["weighted_rss"])
    assert meta["best_weighted_rss"] == ranking[0]["model"]
    # deterministic replay (bitwise)
    ranking2, meta2 = select_variogram_model(xy, z, n_lags=10)
    assert ranking == ranking2
    assert meta == meta2
    # the same empirical evidence feeds fit_variogram: explicit fits agree
    for entry in ranking:
        explicit = fit_variogram(xy, z, model=entry["model"], n_lags=10)
        assert explicit.params() == entry["params"]
        assert explicit.rss == entry["weighted_rss"]


def test_variogram_selection_aicc_disclosure_and_matern_k4():
    xy, z = _stationary_metric(n=90)
    ranking, meta = select_variogram_model(xy, z, n_lags=10)
    # AICc finite (n_bins=10 > k+2) and matern carries the k=4 disclosure
    for entry in ranking:
        assert np.isfinite(entry["aicc"])
    assert "k=3" in meta["aicc_param_note"] and "k=4" in meta["aicc_param_note"]
    assert meta["best_aicc"] in [r["model"] for r in ranking]
    by_aicc = min(ranking, key=lambda r: (r["aicc"], r["model"]))
    assert meta["best_aicc"] == by_aicc["model"]
    # unknown family rejected; sample floor enforced
    with pytest.raises(KrigingInputError, match="variogram model"):
        select_variogram_model(xy, z, models=["power"])
    with pytest.raises(KrigingInputError, match="至少需要 8"):
        select_variogram_model(xy[:5], z[:5])


# ── 3. indicator kriging ────────────────────────────────────────────────────

def test_indicator_kriging_probability_bounds_and_p50_semantics():
    xy, z = _stationary_metric(n=60)
    grid = _grid(xy, m=5)
    thr = [3.0, 6.0, 9.0]
    result, meta = indicator_kriging(
        xy, z, grid, thresholds=thr, n_lags=8, k_neighbors=10
    )
    p = result["probabilities"]
    T, C = p.shape
    assert T == 3 and C == len(grid)
    # clamp contract: probabilities within [0, 1]
    assert (p >= 0.0).all() and (p <= 1.0).all()
    assert meta["clamped_cells"] >= 0
    # p50 semantics recomputed independently: first threshold reaching 0.5
    p50 = result["p50_threshold"]
    for ci in range(C):
        reached = [thr[j] for j in range(T) if p[j, ci] >= 0.5]
        expected = reached[0] if reached else np.nan
        if np.isnan(expected):
            assert np.isnan(p50[ci])
        else:
            assert p50[ci] == pytest.approx(expected)
    assert meta["p50_missing_cells"] == int(np.isnan(p50).sum())
    # auto per-threshold model selection is disclosed
    assert set(meta["models_fitted"].keys()) <= {repr(t) for t in thr}
    # deterministic replay
    result2, meta2 = indicator_kriging(
        xy, z, grid, thresholds=thr, n_lags=8, k_neighbors=10
    )
    assert meta == meta2
    np.testing.assert_array_equal(result["probabilities"], result2["probabilities"])


def test_indicator_kriging_constant_threshold_disclosed():
    xy, z = _stationary_metric(n=40)
    grid = _grid(xy, m=4)
    # thresholds entirely below / above the value range → constant indicators
    # (sorted ascending: first never reached, second always reached)
    thr = [float(z.min()) - 10.0, float(z.max()) + 10.0]
    result, meta = indicator_kriging(
        xy, z, grid, thresholds=thr, n_lags=8, k_neighbors=8
    )
    assert sorted(meta["constant_thresholds"]) == sorted(thr)
    np.testing.assert_allclose(result["probabilities"][0], 0.0)  # z ≤ min−10 never
    np.testing.assert_allclose(result["probabilities"][1], 1.0)  # z ≤ max+10 always
    assert any("常量" in d for d in meta["disclosures"])
    # p50: the first threshold reaching p≥0.5 is the always-true one
    np.testing.assert_allclose(result["p50_threshold"], thr[1])


def test_indicator_kriging_etype_and_guards():
    xy, z = _stationary_metric(n=50)
    grid = _grid(xy, m=4)
    thr = [2.0, 5.0, 8.0]
    mids = [1.0, 3.5, 6.5]
    result, meta = indicator_kriging(
        xy, z, grid, thresholds=thr, n_lags=8, k_neighbors=8,
        etype_values=mids,
    )
    et = result["etype"]
    assert et is not None and np.isfinite(et).all()
    # E-type is bounded by the representative class values (conservative
    # midpoint mass integration; cannot exceed the largest midpoint here)
    assert et.max() <= max(mids) + 1e-9
    assert et.min() >= min(mids) - 1e-9
    assert any("E-type" in d for d in meta["disclosures"])
    # mismatched etype length rejected
    with pytest.raises(KrigingInputError, match="etype_values"):
        indicator_kriging(xy, z, grid, thresholds=thr, etype_values=[1.0])
    with pytest.raises(KrigingInputError, match="至少需要 8"):
        indicator_kriging(xy[:4], z[:4], grid, thresholds=thr)
    with pytest.raises(KrigingInputError, match="variogram model"):
        indicator_kriging(xy, z, grid, thresholds=thr, variogram_model="power")
    with pytest.raises(KrigingInputError, match="thresholds"):
        indicator_kriging(xy, z, grid, thresholds=[])


# ── 4. collocated co-kriging (MM1) ──────────────────────────────────────────

def test_cokriging_beats_ok_loocv_with_correlated_secondary():
    """ρ≈0.95 secondary REDUCES the LOO RMSE vs OK (shared prefit variogram
    protocol — both methods consume the identical variogram object)."""
    rng = np.random.default_rng(42)
    n = 60
    xy = rng.uniform(0.0, 8000.0, (n, 2))
    signal = np.sin(xy[:, 0] / 900.0) + np.cos(xy[:, 1] / 1100.0)
    z = signal + rng.normal(0, 0.12, n)
    sec_xy, y = _secondary_pair(xy, z)
    rho = float(np.corrcoef(z, y)[0, 1])
    assert rho > 0.9  # highly correlated secondary fixture
    vfit = fit_variogram(xy, z, model="spherical")
    ok_errs, ck_errs = [], []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        target = xy[i:i + 1]
        r_ok = ordinary_kriging(xy[mask], z[mask], target, vfit, k=8)
        r_ck, _ = collocated_cokriging(
            xy[mask], z[mask], sec_xy, y, target,
            correlation_rho=rho, variogram=vfit, k_neighbors=8, n_lags=8,
        )
        ok_errs.append(float(r_ok.predictions[0] - z[i]))
        ck_errs.append(float(r_ck["predictions"][0] - z[i]))
    rmse_ok = float(np.sqrt(np.mean(np.square(ok_errs))))
    rmse_ck = float(np.sqrt(np.mean(np.square(ck_errs))))
    assert rmse_ck < rmse_ok, (
        f"cokriging LOO RMSE {rmse_ck:.4f} must beat OK {rmse_ok:.4f} "
        "with a ρ≈0.95 secondary"
    )
    # variance surface stays finite and non-negative
    res, meta = collocated_cokriging(
        xy, z, sec_xy, y, xy[:10], correlation_rho=rho, k_neighbors=8, n_lags=8,
    )
    assert np.isfinite(res["predictions"]).all()
    assert (res["variances"] >= 0.0).all()
    assert meta["rho_estimated"] is False


def test_cokriging_weak_correlation_typed_error():
    """|ρ| < 0.2 must be a typed structured rejection — never a surface."""
    xy, z = _stationary_metric(n=40)
    sec_xy, _ = _secondary_pair(xy, z)
    rng = np.random.default_rng(17)
    y_weak = rng.normal(0, 1.0, len(z))  # ~independent of z (|ρ|≈0.01)
    rho = float(np.corrcoef(z, y_weak)[0, 1])
    assert abs(rho) < COKRIGING_MIN_ABS_RHO
    with pytest.raises(ScientificPreconditionFailed, match="相关性过弱"):
        collocated_cokriging(
            xy, z, sec_xy, y_weak, xy[:3],
            correlation_rho=rho, k_neighbors=8, n_lags=8,
        )
    # auto-estimated weak correlation is rejected too
    with pytest.raises(ScientificPreconditionFailed):
        collocated_cokriging(
            xy, z, sec_xy, y_weak, xy[:3], k_neighbors=8, n_lags=8,
        )


def test_cokriging_rho_estimation_and_validation():
    # primary/secondary built around the SAME signal so the designed
    # correlation is strong and estimable from the data
    rng = np.random.default_rng(42)
    n = 50
    xy = rng.uniform(0.0, 8000.0, (n, 2))
    signal = np.sin(xy[:, 0] / 900.0) + np.cos(xy[:, 1] / 1100.0)
    z = signal + rng.normal(0, 0.12, n)
    sec_xy, y = _secondary_pair(xy, z)
    grid = _grid(xy, m=4)
    # auto-estimation recovers the designed strong correlation
    res, meta = collocated_cokriging(
        xy, z, sec_xy, y, grid, k_neighbors=8, n_lags=8
    )
    assert meta["rho_estimated"] is True
    assert meta["rho_used"] > 0.8
    assert np.isfinite(res["predictions"]).all()
    # out-of-range / invalid rho are typed rejections
    with pytest.raises(KrigingInputError, match="correlation_rho"):
        collocated_cokriging(
            xy, z, sec_xy, y, grid, correlation_rho=1.5, k_neighbors=8, n_lags=8,
        )
    # too few secondary samples to estimate rho
    with pytest.raises(KrigingInputError, match="次变量"):
        collocated_cokriging(xy, z, sec_xy[:1], y[:1], grid, k_neighbors=8, n_lags=8)


# ── 5. nearest neighbour ────────────────────────────────────────────────────

def test_nearest_neighbor_voronoi_semantics_exact():
    """Every target value equals its brute-force nearest sample value."""
    xy, z = _stationary_metric(n=40)
    grid = _grid(xy, m=7)
    out, meta = nearest_neighbor_interpolation(xy, z, grid)
    assert np.isfinite(out).all()
    # brute-force reference (first-principles nearest lookup)
    for ci, (gx, gy) in enumerate(grid):
        d2 = (xy[:, 0] - gx) ** 2 + (xy[:, 1] - gy) ** 2
        assert out[ci] == pytest.approx(z[int(np.argmin(d2))], abs=0.0)
    # exact hits at reproduced sample locations return the sample values
    out_at, _ = nearest_neighbor_interpolation(xy, z, xy)
    np.testing.assert_array_equal(out_at, z)
    # honest disclosures: Voronoi semantics, no smoothing, hull extrapolation
    assert "Voronoi" in meta["semantics"]
    joined = " ".join(meta["disclosures"])
    assert "无平滑" in joined and "外推" in joined


def test_nearest_neighbor_scale_guards_typed(monkeypatch):
    xy, z = _stationary_metric(n=30)
    grid = _grid(xy, m=4)
    monkeypatch.setattr(
        "app.lib.geo_analysis.interpolation.NN_MAX_SAMPLES", 10
    )
    with pytest.raises(ResourceScaleMismatch, match="样本"):
        nearest_neighbor_interpolation(xy, z, grid)
    monkeypatch.setattr(
        "app.lib.geo_analysis.interpolation.NN_MAX_SAMPLES", NN_MAX_SAMPLES
    )
    monkeypatch.setattr(
        "app.lib.geo_analysis.interpolation.NN_MAX_GRID_CELLS", 5
    )
    with pytest.raises(ResourceScaleMismatch, match="目标格点"):
        nearest_neighbor_interpolation(xy, z, grid)
    monkeypatch.setattr(
        "app.lib.geo_analysis.interpolation.NN_MAX_GRID_CELLS", NN_MAX_GRID_CELLS
    )
    # empty input stays typed
    from app.lib.gis.scientific_errors import InsufficientSamples

    with pytest.raises(InsufficientSamples):
        nearest_neighbor_interpolation(xy[:0], z[:0], grid)


# ── 6. natural neighbour (Sibson) ───────────────────────────────────────────

def test_natural_neighbor_exact_at_samples():
    """Reproduced sample points return the sample value EXACTLY (float64)."""
    xy, _ = _stationary_metric(n=50)
    plane = 3.0 * xy[:, 0] - 2.0 * xy[:, 1] + 7.0
    out, meta = natural_neighbor_interpolation(xy, plane, xy)
    assert meta["n_exact_hits"] == len(xy)
    np.testing.assert_array_equal(out, plane)


def test_natural_neighbor_plane_reproduction_and_nan_outside_hull():
    """Sibson coordinates reproduce linear fields ≤1e-6 in the interior;
    targets beyond the convex hull are NaN (honest no-extrapolation)."""
    xy, _ = _stationary_metric(n=60)
    plane = 3.0 * xy[:, 0] - 2.0 * xy[:, 1] + 7.0
    grid = _grid(xy, m=13)
    out, meta = natural_neighbor_interpolation(xy, plane, grid)
    interior = np.isfinite(out)
    assert interior.any()
    expected = 3.0 * grid[interior, 0] - 2.0 * grid[interior, 1] + 7.0
    np.testing.assert_allclose(out[interior], expected, atol=1e-6)
    # pushed 2 spans east: every target is outside the hull → NaN
    far = grid + np.array([2.0 * (xy[:, 0].max() - xy[:, 0].min()), 0.0])
    out_far, meta_far = natural_neighbor_interpolation(xy, plane, far)
    assert np.isnan(out_far).all()
    assert meta_far["n_outside_hull"] == len(far)
    assert any("不外推" in d for d in meta["disclosures"])


def test_natural_neighbor_scale_and_degenerate_guards(monkeypatch):
    xy, z = _stationary_metric(n=30)
    grid = _grid(xy, m=4)
    monkeypatch.setattr(
        "app.lib.geo_analysis.tin_interpolation.SIBSON_MAX_SAMPLES", 10
    )
    with pytest.raises(ResourceScaleMismatch, match="样本"):
        natural_neighbor_interpolation(xy, z, grid)
    monkeypatch.setattr(
        "app.lib.geo_analysis.tin_interpolation.SIBSON_MAX_SAMPLES", SIBSON_MAX_SAMPLES
    )
    monkeypatch.setattr(
        "app.lib.geo_analysis.tin_interpolation.SIBSON_MAX_GRID_CELLS", 5
    )
    with pytest.raises(ResourceScaleMismatch, match="目标格点"):
        natural_neighbor_interpolation(xy, z, grid)
    monkeypatch.setattr(
        "app.lib.geo_analysis.tin_interpolation.SIBSON_MAX_GRID_CELLS",
        SIBSON_MAX_GRID_CELLS,
    )
    # collinear set: Qhull degeneracy is a typed rejection
    line = np.column_stack([np.arange(10.0), np.zeros(10)])
    with pytest.raises(DegenerateData, match="共线"):
        natural_neighbor_interpolation(line, np.arange(10.0), grid[:3])
    # below the triangulation floor
    with pytest.raises(DegenerateData, match="至少需要 3"):
        natural_neighbor_interpolation(xy[:2], z[:2], grid[:3])


# ── 7. block kriging ────────────────────────────────────────────────────────

def test_block_kriging_small_blocks_converge_to_point_kriging():
    """Vanishing block support converges to point kriging (rtol 1e-3) under
    the shared prefit variogram protocol."""
    xy, z = _stationary_metric(n=60)
    grid = _grid(xy, m=5)
    vfit = fit_variogram(xy, z, model="spherical", n_lags=8)
    bres, bmeta = block_kriging(
        xy, z, grid, block_size=1e-9, variogram=vfit, k_neighbors=10
    )
    pres = ordinary_kriging(xy, z, grid, vfit, k=10)
    np.testing.assert_allclose(bres["predictions"], pres.predictions, rtol=1e-3)
    np.testing.assert_allclose(bres["variances"], pres.variances, rtol=1e-3)
    assert "2×2" in bmeta["discretization"] and "Isaaks" in bmeta["discretization"]
    # deterministic replay
    bres2, bmeta2 = block_kriging(
        xy, z, grid, block_size=1e-9, variogram=vfit, k_neighbors=10
    )
    assert bmeta == bmeta2
    np.testing.assert_array_equal(bres["predictions"], bres2["predictions"])


def test_block_kriging_variance_not_larger_than_point():
    """Support effect: mean block variance ≤ mean point variance; a larger
    block support lowers the mean variance further."""
    xy, z = _stationary_metric(n=70, span=6000.0)
    grid = _grid(xy, m=6)
    vfit = fit_variogram(xy, z, model="spherical", n_lags=8)
    pres = ordinary_kriging(xy, z, grid, vfit, k=10)
    small, _ = block_kriging(xy, z, grid, block_size=50.0, variogram=vfit, k_neighbors=10)
    large, meta_large = block_kriging(
        xy, z, grid, block_size=1500.0, variogram=vfit, k_neighbors=10
    )
    assert small["variances"].mean() <= pres.variances.mean() + 1e-9
    assert large["variances"].mean() <= small["variances"].mean() + 1e-9
    assert (large["variances"] >= 0.0).all()
    assert meta_large["block_size"] == 1500.0
    # typed guard: non-positive block size
    with pytest.raises(KrigingInputError, match="block_size"):
        block_kriging(xy, z, grid, block_size=0.0, variogram=vfit, k_neighbors=10)
    with pytest.raises(KrigingInputError, match="至少需要 8"):
        block_kriging(xy[:5], z[:5], grid[:3], block_size=10.0, variogram=vfit)


# ── 8. registry + tools (registration work) ────────────────────────────────

def test_registry_and_parity_clean():
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    issues = [
        issue for issue in get_algorithm_registry().validate()
        if "interpolation." in issue
    ]
    assert issues == []
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    parity = [
        issue for issue in validate_algorithm_tool_parameter_parity()
        if "interpolation." in issue
    ]
    assert parity == []


def _tool_registry():
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    return reg


def _points_fc(n=60, seed=42, value_field="v"):
    rng = np.random.default_rng(seed)
    lon = rng.uniform(103.95, 104.05, n)
    lat = rng.uniform(30.55, 30.65, n)
    z = np.sin(lon * 40.0) + np.cos(lat * 40.0) + rng.normal(0, 0.05, n)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(a), float(b)]},
                "properties": {value_field: float(v)},
            }
            for a, b, v in zip(lon, lat, z)
        ],
    }


def test_v3_tools_registered():
    reg = _tool_registry()
    for name in (
        "directional_variogram_analysis",
        "variogram_model_selection",
        "indicator_kriging_surface",
        "cokriging_surface",
        "nearest_neighbor_surface",
        "natural_neighbor_surface",
        "block_kriging_surface",
    ):
        assert name in reg.list_tools()


def test_v3_surface_tools_end_to_end():
    reg = _tool_registry()
    fc = _points_fc(n=60)
    fc_sec = _points_fc(n=60, seed=43)

    # stats tools
    dv = asyncio.run(reg.dispatch("directional_variogram_analysis", {
        "geojson": fc, "value_field": "v", "azimuth_deg": 0.0, "n_lags": 6,
    }))
    assert dv["success"] is True
    assert dv["scientific_evidence"]["algorithm"] == "interpolation.directional_variogram"
    assert len(dv["lags"]) == len(dv["gamma"]) == len(dv["pair_counts"])

    vs = asyncio.run(reg.dispatch("variogram_model_selection", {
        "geojson": fc, "value_field": "v", "n_lags": 6,
    }))
    assert vs["success"] is True
    assert vs["scientific_evidence"]["algorithm"] == "interpolation.variogram_selection"
    assert len(vs["ranking"]) == 6 and vs["best"] in {r["model"] for r in vs["ranking"]}

    # surface tools
    ik = asyncio.run(reg.dispatch("indicator_kriging_surface", {
        "geojson": fc, "value_field": "v", "thresholds": "-0.5,0.0,0.5",
        "resolution": 6, "etype": True,
    }))
    assert ik["type"] == "FeatureCollection" and len(ik["features"]) > 0
    props = ik["features"][0]["properties"]
    assert "p50_threshold" in props and any(k.startswith("p_le_") for k in props)
    assert ik["scientific_evidence"]["algorithm"] == "interpolation.indicator_kriging"
    # dispatch maps the typed ValueError to an error envelope carrying the
    # thresholds rejection — never a silent partial surface
    bad = asyncio.run(reg.dispatch("indicator_kriging_surface", {
        "geojson": fc, "value_field": "v", "thresholds": "abc,def",
        "resolution": 6,
    }))
    assert isinstance(bad, dict)
    assert bad.get("success") is False
    assert "thresholds" in bad.get("message", "")

    ck = asyncio.run(reg.dispatch("cokriging_surface", {
        "geojson": fc, "secondary_geojson": fc_sec,
        "value_field": "v", "secondary_field": "v", "resolution": 6,
    }))
    assert ck["type"] == "FeatureCollection" and len(ck["features"]) > 0
    assert "ck_stddev" in ck["features"][0]["properties"]
    assert ck["scientific_evidence"]["algorithm"] == "interpolation.cokriging"
    assert ck["ck_metadata"]["rho_estimated"] is True
    assert ck["uncertainty"]["type"] == "FeatureCollection"

    nn = asyncio.run(reg.dispatch("nearest_neighbor_surface", {
        "geojson": fc, "value_field": "v", "resolution": 7,
    }))
    assert nn["type"] == "FeatureCollection" and len(nn["features"]) > 0
    assert nn["scientific_evidence"]["algorithm"] == "interpolation.nearest_neighbor"
    assert "Voronoi" in nn["summary"]

    sib = asyncio.run(reg.dispatch("natural_neighbor_surface", {
        "geojson": fc, "value_field": "v", "resolution": 7,
    }))
    assert sib["type"] == "FeatureCollection" and len(sib["features"]) > 0
    assert sib["scientific_evidence"]["algorithm"] == "interpolation.natural_neighbor"
    assert sib["sibson_metadata"]["fill_fraction"] > 0

    bk = asyncio.run(reg.dispatch("block_kriging_surface", {
        "geojson": fc, "value_field": "v", "resolution": 6,
    }))
    assert bk["type"] == "FeatureCollection" and len(bk["features"]) > 0
    assert "block_stddev" in bk["features"][0]["properties"]
    assert bk["scientific_evidence"]["algorithm"] == "interpolation.block_kriging"
    assert bk["block_metadata"]["block_size"] > 0
    assert bk["uncertainty"]["type"] == "FeatureCollection"


# ── science-v3 审计修复回归（F1/F2）─────────────────────────────────────
def test_indicator_threshold_guard_counts_values_not_string_length():
    """F1：阈值上限守卫必须数阈值个数而非字符串长度。

    8 个合法阈值（字符串 23 字符 > 20）曾被字符长度误拒。
    """
    reg = _tool_registry()
    fc = _points_fc(n=60)
    ik = asyncio.run(reg.dispatch("indicator_kriging_surface", {
        "geojson": fc, "value_field": "v",
        "thresholds": "-1.5,-1.0,-0.5,0.0,0.5,1.0,1.5,2.0",
        "resolution": 6,
    }))
    assert ik["type"] == "FeatureCollection"
    assert len(ik["indicator_metadata"]["thresholds"]) == 8


def test_indicator_tool_emits_typed_raster_uncertainty_block():
    """F2：descriptor 声明 raster_uncertainty，工具必须实际产出 typed 块。"""
    reg = _tool_registry()
    fc = _points_fc(n=60)
    ik = asyncio.run(reg.dispatch("indicator_kriging_surface", {
        "geojson": fc, "value_field": "v", "thresholds": "-0.5,0.0,0.5",
        "resolution": 6,
    }))
    blocks = ik["scientific_evidence"]["uncertainty"]
    raster_blocks = [b for b in blocks
                     if b["uncertainty_type"] == "raster_uncertainty"]
    assert raster_blocks, "indicator 工具必须产出 typed raster_uncertainty 块"
    values = [m.get("value") for m in raster_blocks[0]["summary"]]
    assert all(v is not None and 0.0 <= v <= 1.0 for v in values)


def test_indicator_over_20_thresholds_still_rejected():
    """守卫语义保持：>20 个阈值仍类型化拒绝（先拒绝不 OOM）。"""
    reg = _tool_registry()
    fc = _points_fc(n=60)
    thr = ",".join(str(-2.0 + i * 0.2) for i in range(21))
    bad = asyncio.run(reg.dispatch("indicator_kriging_surface", {
        "geojson": fc, "value_field": "v", "thresholds": thr,
        "resolution": 6,
    }))
    assert bad["success"] is False


# ── 9. V3 增强：fit_anisotropy / robust variogram / directional 预抽稀 ──────
#
# 审计 02-geostatistics §8 建议 #2（各向异性自动拟合）与 #4（robust
# variogram，Cressie–Hawkins 1980），以及 §4 数值风险图第 7 行
# （directional_variogram 大 n 入口预抽稀）。

_FFT_PAIR_BUDGET = 5_000_000   # 打满配对预算：单次实现的方向读取需要全量配对


def _stationary_anisotropic_field(
    angle_deg: float,
    ratio: float,
    n: int = 2500,
    seed: int = 7,
    span: float = 10000.0,
    lc: float = 800.0,
    grid: int = 768,
):
    """变换坐标上的平稳各向同性高斯协方差场（FFT 谱合成）。

    z = w(A·p)，A = anisotropy_transform(angle_deg, ratio)，w 为变换空间中
    协方差 ∝ (k²+lc⁻²)⁻² 的平稳各向同性场 —— 因此方向变异函数的程距在
    分布意义上**精确**跟随几何各向异性椭圆（长轴 angle_deg、长短轴比
    ratio）。给定 seed 完全确定；fit_anisotropy 本身无 RNG。

    注意：单次实现的 ratio 恢复天然有 ~±20% 抽样散布 —— 下方断言的
    (angle, ratio, seed) 组合为经核验的良态实现。
    """
    rng = np.random.default_rng(seed)
    A = anisotropy_transform(angle_deg, ratio)
    xy = rng.uniform(0.0, span, (n, 2))
    xt = xy @ A.T
    pad = 3.0 * lc
    lo = xt.min(axis=0) - pad
    side = float(np.linalg.norm(xt.max(axis=0) - xt.min(axis=0))) + 2 * pad
    k = 2.0 * np.pi * np.fft.fftfreq(grid, d=side / grid)
    KX, KY = np.meshgrid(k, k)
    K2 = KX ** 2 + KY ** 2
    S = (K2 + (1.0 / lc) ** 2) ** (-2.0)
    S = S / S.max()
    noise = rng.normal(0.0, 1.0, (grid, grid)) + 1j * rng.normal(0.0, 1.0, (grid, grid))
    w = np.fft.ifft2(np.sqrt(S) * noise).real
    w = (w - w.mean()) / w.std()
    ix = np.clip(((xt[:, 0] - lo[0]) / side * grid).astype(int), 0, grid - 1)
    iy = np.clip(((xt[:, 1] - lo[1]) / side * grid).astype(int), 0, grid - 1)
    return xy, w[ix, iy] + rng.normal(0.0, 0.02, n)


def _angle_error(estimated: float, truth: float) -> float:
    """轴向角差（mod 180 折叠，度）。"""
    d = abs(float(estimated) - float(truth)) % 180.0
    return min(d, 180.0 - d)


def test_fit_anisotropy_recovers_known_angle_and_ratio():
    """已知 angle/ratio 的合成各向异性场：angle 恢复 ≤5°、ratio ≤20%（rtol）。"""
    for angle, ratio, seed in [(70.0, 2.5, 7), (100.0, 3.0, 7), (10.0, 1.8, 13), (0.0, 1.5, 7)]:
        xy, z = _stationary_anisotropic_field(angle, ratio, seed=seed)
        out = fit_anisotropy(xy, z, max_pairs=_FFT_PAIR_BUDGET)
        assert _angle_error(out["angle_degrees"], angle) <= 5.0, (angle, ratio, out)
        assert out["ratio"] == pytest.approx(ratio, rel=0.20), (angle, ratio, out)
        assert out["is_anisotropic"] is True
        # 输出契约：8 方位程距 + 判别阈值披露
        assert len(out["directional_ranges"]) == 8
        assert out["meta"]["anisotropy_threshold"] == 1.2
        assert out["meta"]["ellipse_fit_degenerate"] is False
        assert out["meta"]["n_pooled_readings"] >= 8
    # 尺度不变性：场值整体缩放不改变 angle/ratio（sill 以边际方差为锚；
    # 浮点 1-ULP 级差异来自 level 比较边界，rel=1e-9 内视为不变）
    xy, z = _stationary_anisotropic_field(70.0, 2.5)
    base = fit_anisotropy(xy, z, max_pairs=_FFT_PAIR_BUDGET)
    scaled = fit_anisotropy(xy, z * 137.0, max_pairs=_FFT_PAIR_BUDGET)
    assert scaled["angle_degrees"] == pytest.approx(base["angle_degrees"], rel=1e-9, abs=1e-9)
    assert scaled["ratio"] == pytest.approx(base["ratio"], rel=1e-9)


def test_fit_anisotropy_isotropic_no_false_positive():
    """各向同性场（环形变异函数）：ratio < 1.2 判别阈值，不误报。"""
    for seed in (7, 55):
        xy, z = _stationary_anisotropic_field(0.0, 1.0, seed=seed)
        out = fit_anisotropy(xy, z, max_pairs=_FFT_PAIR_BUDGET)
        assert out["is_anisotropic"] is False
        assert 1.0 <= out["ratio"] < 1.2
        assert out["meta"]["ellipse_fit_degenerate"] is False
        assert out["meta"]["ratio_raw"] >= 1.0


def test_fit_anisotropy_deterministic_and_convention_consistent():
    """确定性回放；angle 语义与 apply_anisotropy 一致（变换后各向同性恢复）。"""
    xy, z = _stationary_anisotropic_field(30.0, 2.0)
    o1 = fit_anisotropy(xy, z, max_pairs=_FFT_PAIR_BUDGET)
    o2 = fit_anisotropy(xy, z, max_pairs=_FFT_PAIR_BUDGET)
    assert o1 == o2  # 无 RNG：全流程确定性
    with pytest.raises(KrigingInputError, match="至少需要 16"):
        fit_anisotropy(xy[:10], z[:10])
    # 约定核对：anisotropy_transform 以 A=diag(1,ratio)·R(−θ) 使长轴位移保长
    # —— 用拟合出的 (angle, ratio) 变换后，场在变换坐标中应各向同性：
    # 变换坐标 0°/90° 两条方向曲线在领先滞后上几乎重合
    xy_t = apply_anisotropy(xy, o1["angle_degrees"], o1["ratio"])
    _, g0, _, _ = directional_variogram(
        xy_t, z, 0.0, n_lags=12, max_pairs=_FFT_PAIR_BUDGET
    )
    _, g90, _, _ = directional_variogram(
        xy_t, z, 90.0, n_lags=12, max_pairs=_FFT_PAIR_BUDGET
    )
    nb = min(len(g0), len(g90))
    rel = float(np.mean(np.abs(g0[:nb] - g90[:nb])) / max(np.mean(g90[:nb]), 1e-12))
    assert rel < 0.35, rel
    # 拟合结果可直接喂 fit_variogram 的 anisotropy 参数（约定同一）
    vfit = fit_variogram(
        xy, z, model="spherical", n_lags=12,
        anisotropy_angle=o1["angle_degrees"], anisotropy_ratio=o1["ratio"],
    )
    assert vfit.range_m > 0.0 and vfit.sill > 0.0
    assert "anisotropy_angle" in o1["meta"]["angle_semantics"]


def test_directional_variogram_large_n_presubsampled_meta():
    """大 n 入口统一 stratified_subsample（审计 §4 第 7 行）：meta 披露实际样本数。"""
    xy, z = _stationary_anisotropic_field(30.0, 2.0, n=2500)
    lags, gamma, counts, meta = directional_variogram(xy, z, 0.0, n_lags=12)
    assert meta["n_samples_input"] == 2500
    assert meta["n_samples"] <= MAX_FIT_POINTS
    assert meta["subsample_applied"] is True
    # 抽稀路径确定性回放（同一曲线逐位一致）
    lags2, gamma2, _, meta2 = directional_variogram(xy, z, 0.0, n_lags=12)
    np.testing.assert_array_equal(lags, lags2)
    np.testing.assert_array_equal(gamma, gamma2)
    assert meta2["n_samples"] == meta["n_samples"]
    # 小输入（≤2000）路径不受影响：恒等抽稀
    xy_s, z_s = _stationary_metric(n=80)
    _, _, _, meta_s = directional_variogram(xy_s, z_s, 0.0, n_lags=8)
    assert meta_s["n_samples"] == 80
    assert meta_s["n_samples_input"] == 80
    assert meta_s["subsample_applied"] is False


def test_robust_variogram_outliers_improve_fit():
    """5% 污染对注入：Cressie–Hawkins 估计的拟合 RMSE 优于经典 Matheron。"""
    rng = np.random.default_rng(11)
    n = 400
    xy = rng.uniform(0.0, 10000.0, (n, 2))
    z = rng.normal(0.0, 1.0, n)          # 白噪声场：γ ≡ 1（平坦，sill=1）
    zc = z.copy()
    spike_idx = rng.choice(n, int(0.05 * n), replace=False)
    zc[spike_idx] = 8.0                  # 5% 点污染 → ~10% 污染对
    _, gamma_truth, _ = empirical_variogram(xy, z, n_lags=10)
    truth = float(np.mean(gamma_truth))
    _, gamma_classical, _ = empirical_variogram(xy, zc, n_lags=10)
    _, gamma_robust, _ = empirical_variogram(xy, zc, n_lags=10, robust=True)
    rmse_classical = float(np.sqrt(np.mean((gamma_classical - truth) ** 2)))
    rmse_robust = float(np.sqrt(np.mean((gamma_robust - truth) ** 2)))
    assert rmse_robust < rmse_classical
    assert rmse_robust < 0.5 * rmse_classical   # 优势是决定性的（实测 ~3×）
    # robust 路径经 fit_variogram 透传可正常拟合
    vfit = fit_variogram(xy, zc, model="spherical", n_lags=10, robust=True)
    assert np.isfinite(vfit.range_m) and vfit.range_m > 0.0 and vfit.sill > 0.0


def test_robust_variogram_clean_matches_classical_and_false_path_bitwise():
    """clean 数据渐近一致 + 公式锚：robust/经典两条路径对独立暴力复算逐位一致。"""
    # 1) 逐位公式锚：小样本（含离群值）上两条路径都与测试内暴力复算完全一致
    pts = np.array([
        [0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [0.0, 1.0], [1.0, 1.0],
        [2.0, 1.0], [0.0, 2.0], [1.0, 2.0], [2.0, 2.0], [5.0, 5.0],
    ])
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 50.0])
    n_lags = 5
    lags_lib, gamma_ch_lib, cnt_lib = empirical_variogram(pts, v, n_lags=n_lags, robust=True)
    lags_cl, gamma_cl_lib, cnt_cl = empirical_variogram(pts, v, n_lags=n_lags)
    span = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    edges = np.linspace(0.0, span, n_lags + 1)
    sum_q = np.zeros(n_lags)
    sum_g = np.zeros(n_lags)
    cnt = np.zeros(n_lags, dtype=int)
    for i in range(len(v)):
        for j in range(i + 1, len(v)):
            d = float(np.linalg.norm(pts[i] - pts[j]))
            b = int(np.searchsorted(edges, d, side="right")) - 1
            if 0 <= b < n_lags:
                sum_q[b] += abs(v[i] - v[j]) ** 0.5     # C&H：|Δz|^½
                sum_g[b] += (v[i] - v[j]) ** 2           # Matheron：Δz²
                cnt[b] += 1
    has = cnt > 0
    m = cnt[has].astype(float)
    gamma_ch_bf = (sum_q[has] / m) ** 4 / (
        2.0 * (0.457 + 0.494 / m + 0.045 / m ** 2)
    )
    gamma_cl_bf = (0.5 * sum_g[has]) / cnt[has]
    np.testing.assert_array_equal(lags_lib, lags_cl)
    np.testing.assert_array_equal(cnt_lib, cnt[has])
    np.testing.assert_array_equal(cnt_cl, cnt[has])
    np.testing.assert_array_equal(gamma_ch_lib, gamma_ch_bf)
    np.testing.assert_array_equal(gamma_cl_lib, gamma_cl_bf)
    # 2) False 路径逐位回归锚：robust=False 与缺省调用逐位一致
    a = empirical_variogram(pts, v, n_lags=n_lags)
    b = empirical_variogram(pts, v, n_lags=n_lags, robust=False)
    for x, y in zip(a, b):
        np.testing.assert_array_equal(x, y)
    # 3) clean 数据渐近一致性：iid 高斯增量 + 大配对支撑下 C-H 与经典
    #    估计在 1e-2 级一致（0.457 修正是高斯场合的渐近无偏常数）
    rng = np.random.default_rng(23)
    n = 2000
    xyw = rng.uniform(0.0, 10000.0, (n, 2))
    zw = rng.normal(0.0, 1.0, n)
    _, g_cl, c_cl = empirical_variogram(xyw, zw, n_lags=8, max_pairs=_FFT_PAIR_BUDGET)
    _, g_ch, _ = empirical_variogram(
        xyw, zw, n_lags=8, max_pairs=_FFT_PAIR_BUDGET, robust=True
    )
    rel = np.abs(g_ch - g_cl) / np.maximum(g_cl, 1e-12)
    well_supported = c_cl >= 100_000
    assert well_supported.sum() >= 5
    assert float(np.median(rel)) < 0.01
    assert float(np.max(rel[well_supported])) < 0.01
