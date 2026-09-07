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
    block_kriging,
    collocated_cokriging,
    directional_variogram,
    empirical_variogram,
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
