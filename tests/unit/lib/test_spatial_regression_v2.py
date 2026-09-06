"""Conformance tests for the spatial-regression family (Foundation V2 · A1).

Contract bullets under test (descriptor ids in app/lib/gis/algorithms/
statistics.py):

- ``spatial.ols_regression``: exact-plane coefficient recovery to 1e-10;
  LM-error vs hand-computed formula on a hand-built 3×3 rook adjacency;
  JB/BP diagnostics; typed-error guards;
- ``spatial.sar_ml``: recovers ρ=0.6 on a rook grid (LR significant);
  ResourceScaleMismatch BEFORE eigen allocation (guard monkeypatched small);
  determinism (same payload twice);
- ``spatial.sem_ml``: recovers λ on the same SAR-generated data;
- ``spatial.slx``: exact plane + WX lagged terms present;
- ``spatial.gwr``: local coefficients match a hand-computed bisquare WLS at
  one location (golden anchor); bandwidth CV bounded & deterministic; guards;
- ``multiple_testing_correction``: bonferroni/holm/bh semantics, Holm ≤
  Bonferroni, BH monotonicity;
- adversarial: NaN/inf rows dropped, constant/missing fields typed errors,
  empty FeatureCollection.
"""
import copy
import json

import numpy as np
import pytest

from app.lib.geo_analysis.spatial_regression import (
    SAR_EIGEN_MAX_N,
    gwr_regression_narrated,
    multiple_testing_correction,
    ols_regression_narrated,
    sar_ml_regression_narrated,
    sem_ml_regression_narrated,
    slx_regression_narrated,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
)

pytestmark = pytest.mark.unit


# ── fixtures ─────────────────────────────────────────────────────────

def _grid_fc(nrows, ncols, props_fn, cell=0.01, lon0=116.0, lat0=39.0):
    """Polygon-grid FeatureCollection with shared edge coordinates."""
    xs = [lon0 + i * cell for i in range(ncols + 1)]
    ys = [lat0 + j * cell for j in range(nrows + 1)]
    feats = []
    for r in range(nrows):
        for c in range(ncols):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [xs[c], ys[r]], [xs[c + 1], ys[r]],
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]], [xs[c], ys[r]],
                ]]},
                "properties": props_fn(r, c),
            })
    return {"type": "FeatureCollection", "features": feats}


def _points_fc(pts, fields=("y", "x1")):
    feats = []
    for xy, values in pts:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
            "properties": dict(zip(fields, values)),
        })
    return {"type": "FeatureCollection", "features": feats}


def _sar_generated_grid(seed=1, rho=0.6, n_rows=10):
    """Rook grid with y = (I-ρW)⁻¹(1 + 2x1 + ε), deterministically seeded."""
    from app.lib.geo_analysis.spatial_weights import build_contiguity_weights
    from app.lib.geo_processor.core import to_utm_gdf

    rng = np.random.default_rng(seed)
    fc0 = _grid_fc(n_rows, n_rows,
                   lambda r, c: {"x1": float(rng.normal()), "y": 0.0})
    gdf, _ = to_utm_gdf(fc0)
    wm = build_contiguity_weights(gdf, "rook", row_standardized=True)
    w_dense = wm.matrix.toarray()
    n = len(gdf)
    x1 = gdf["x1"].to_numpy(float)
    eps = rng.normal(0.0, 1.0, n)
    y = np.linalg.solve(np.eye(n) - rho * w_dense, 1.0 + 2.0 * x1 + eps)
    fc = copy.deepcopy(fc0)
    for feat, yi in zip(fc["features"], y):
        feat["properties"]["y"] = float(yi)
    return fc


# ── OLS ──────────────────────────────────────────────────────────────

def test_ols_recovers_exact_plane():
    rng = np.random.default_rng(0)

    def plane(r, c):
        x1 = float(r)
        x2 = float(c) + rng.normal(0, 0.1)
        return {"x1": x1, "x2": x2, "y": 2.0 + 3.0 * x1 - 5.0 * x2}

    fc = _grid_fc(8, 8, plane)
    res = ols_regression_narrated(fc, "y", ["x1", "x2"], weights_scheme="rook")
    assert res.success, res.summary
    coefs = {c["name"]: c["coef"] for c in res.data["coefficients"]}
    assert coefs["intercept"] == pytest.approx(2.0, abs=1e-10)
    assert coefs["x1"] == pytest.approx(3.0, abs=1e-10)
    assert coefs["x2"] == pytest.approx(-5.0, abs=1e-10)
    assert res.data["r_squared"] == pytest.approx(1.0, abs=1e-10)
    # contract surface: weights metadata + n_features + uncertainty blocks
    assert res.data["n_features"] == 64
    assert res.data["weights"]["scheme"] == "rook"
    unc_types = {u["uncertainty_type"] for u in res.data["uncertainty"]}
    assert "validation_metrics" in unc_types
    assert "statistical_significance" in unc_types


def test_ols_lm_error_matches_hand_formula():
    """LM_err = (e'We/σ̃²)² / tr(W'W + W²)，与 spreg LMtests 同式（手算）。"""
    from app.lib.geo_processor.core import to_utm_gdf

    rng = np.random.default_rng(7)
    fc = _grid_fc(3, 3, lambda r, c: {
        "x1": float(rng.normal()), "y": float(rng.normal(5, 2))})
    res = ols_regression_narrated(fc, "y", ["x1"], weights_scheme="rook",
                                  permutations=99)
    assert res.success, res.summary

    # 手算路径：UTM gdf + 手工 rook 邻接 + 显式 OLS
    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    y = gdf["y"].to_numpy(float)
    x1 = gdf["x1"].to_numpy(float)
    n = 9
    # 手工 3×3 rook 邻接（行标准化）
    idx = lambda r, c: r * 3 + c  # noqa: E731
    w_bin = np.zeros((n, n))
    for r in range(3):
        for c in range(3):
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 3 and 0 <= cc < 3:
                    w_bin[idx(r, c), idx(rr, cc)] = 1.0
    row_sums = w_bin.sum(axis=1, keepdims=True)
    w = w_bin / row_sums
    x_mat = np.column_stack([np.ones(n), x1])
    beta = np.linalg.lstsq(x_mat, y, rcond=None)[0]
    e = y - x_mat @ beta
    sigma2n = float(e @ e) / n
    utwu = float(e @ (w @ e)) / sigma2n
    t_term = float(((w.T + w) @ w).trace())
    lm_err_hand = utwu ** 2 / t_term

    got = res.data["diagnostics"]["lm_error"]
    assert got["statistic"] is not None
    assert got["statistic"] == pytest.approx(lm_err_hand, rel=1e-10)
    assert 0.0 <= got["p_value"] <= 1.0


def test_ols_jb_bp_diagnostics_and_guards():
    rng = np.random.default_rng(3)
    fc = _grid_fc(6, 6, lambda r, c: {
        "x1": float(rng.normal()),
        "y": 1.0 + 0.5 * float(rng.normal())})
    res = ols_regression_narrated(fc, "y", ["x1"], weights_scheme="knn")
    assert res.success, res.summary
    diag = res.data["diagnostics"]
    # JB on (near-)normal residuals should not reject at n=36
    assert 0.0 <= diag["jarque_bera"]["p_value"] <= 1.0
    assert 0.0 <= diag["breusch_pagan"]["p_value"] <= 1.0
    # VIF present for one explanatory variable? p_x=1 → empty table (honest)
    assert diag["vif"] == []
    # residual Moran block exists with the contracted metadata
    rm = diag["residual_morans_i"]
    assert rm["permutations"] == 99
    if rm["p_value"] is not None:
        assert 0.0 <= rm["p_value"] <= 1.0

    # skewed residuals → JB rejects (constructed, not sampled)
    fc_skew = _grid_fc(8, 8, lambda r, c: {
        "x1": float(rng.normal()),
        "y": float(np.exp(rng.normal(0, 1)))})
    res_skew = ols_regression_narrated(fc_skew, "y", ["x1"])
    assert res_skew.data["diagnostics"]["jarque_bera"]["p_value"] < 0.05

    # constant target → DegenerateData
    fc_const = _grid_fc(5, 5, lambda r, c: {"x1": float(r), "y": 3.0})
    with pytest.raises(DegenerateData):
        ols_regression_narrated(fc_const, "y", ["x1"])
    # constant explanatory → DegenerateData
    fc_constx = _grid_fc(5, 5, lambda r, c: {"x1": 7.0, "y": float(r)})
    with pytest.raises(DegenerateData):
        ols_regression_narrated(fc_constx, "y", ["x1"])
    # missing field → MissingRequiredField
    with pytest.raises(MissingRequiredField):
        ols_regression_narrated(fc, "y", ["nope"])
    # tiny n → InsufficientSamples (n=5 < 2p+2=6 for p=2)
    fc_small = _points_fc(
        [((116.0 + i * 0.001, 39.0), (1.0, float(i))) for i in range(5)])
    with pytest.raises(InsufficientSamples):
        ols_regression_narrated(fc_small, "y", ["x1"])
    # empty FC → NoValidObservations
    with pytest.raises(NoValidObservations):
        ols_regression_narrated({"type": "FeatureCollection", "features": []},
                                "y", ["x1"])


def test_ols_adversarial_nan_inf_dropped():
    pts = [((116.0 + i * 0.001, 39.0), (float(i), 1.0 + float(i)))
           for i in range(10)]
    pts.insert(4, ((116.005, 39.0), (float("nan"), 2.0)))
    pts.insert(7, ((116.008, 39.0), (float("inf"), 2.0)))
    fc = _points_fc(pts)
    res = ols_regression_narrated(fc, "y", ["x1"], permutations=99)
    assert res.success, res.summary
    assert res.data["n_features"] == 10  # NaN/inf rows dropped before weights


# ── SAR / SEM ────────────────────────────────────────────────────────

def test_sar_ml_recovers_rho_on_grid():
    fc = _sar_generated_grid(seed=1, rho=0.6)
    res = sar_ml_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")
    assert res.success, res.summary
    assert 0.4 < res.data["rho"] < 0.75
    assert res.data["lr_test"]["p_value"] < 0.05
    coef_x1 = next(c for c in res.data["coefficients"] if c["name"] == "x1")
    assert coef_x1["coef"] == pytest.approx(2.0, abs=0.5)
    # feasible bounds honoured
    lo, hi = res.data["rho_bounds"]
    assert lo < res.data["rho"] < hi

    # weak dependence (ρ=0.05): SAR ≈ OLS
    fc_weak = _sar_generated_grid(seed=2, rho=0.05)
    res_weak = sar_ml_regression_narrated(fc_weak, "y", ["x1"],
                                          weights_scheme="rook")
    assert abs(res_weak.data["rho"]) < 0.2
    coef_weak = next(c for c in res_weak.data["coefficients"]
                     if c["name"] == "x1")["coef"]
    assert coef_weak == pytest.approx(2.0, abs=0.3)

    # determinism: identical payloads on repeat (bounded Brent, no RNG)
    res2 = sar_ml_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")
    assert json.dumps(res.data, sort_keys=True) == \
        json.dumps(res2.data, sort_keys=True)


def test_sem_ml_recovers_lambda():
    fc = _sar_generated_grid(seed=1, rho=0.6)
    res = sem_ml_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")
    assert res.success, res.summary
    assert 0.3 < res.data["lambda"] < 0.8
    assert res.data["lr_test"]["p_value"] < 0.05
    assert 0.0 <= res.data["pseudo_r_squared"] <= 1.0
    unc_types = {u["uncertainty_type"] for u in res.data["uncertainty"]}
    assert unc_types == {"validation_metrics", "statistical_significance"}


def test_sar_ml_scale_guard_and_degenerate_inputs(monkeypatch):
    fc = _sar_generated_grid(seed=3, rho=0.5)
    # guard BEFORE eigen allocation: shrink the cap under a small n
    import app.lib.geo_analysis.spatial_regression as sr

    assert len(fc["features"]) > 12
    monkeypatch.setattr(sr, "SAR_EIGEN_MAX_N", 12)
    with pytest.raises(ResourceScaleMismatch) as excinfo:
        sr.sar_ml_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")
    assert excinfo.value.estimated and excinfo.value.limit
    with pytest.raises(ResourceScaleMismatch):
        sr.sem_ml_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")

    # constant target → DegenerateData (before any ML work)
    fc_const = _grid_fc(5, 5, lambda r, c: {"x1": float(r), "y": 1.0})
    with pytest.raises(DegenerateData):
        sar_ml_regression_narrated(fc_const, "y", ["x1"])
    # zero-variance explanatory → DegenerateData
    fc_constx = _grid_fc(5, 5, lambda r, c: {"x1": 2.0, "y": float(r)})
    with pytest.raises(DegenerateData):
        sar_ml_regression_narrated(fc_constx, "y", ["x1"])
    # module-level default intact for production paths
    assert SAR_EIGEN_MAX_N == 4000


# ── SLX ──────────────────────────────────────────────────────────────

def test_slx_recovers_plane_with_lagged_terms():
    def plane(r, c):
        return {"x1": float(r), "y": 2.0 + 3.0 * float(r)}

    fc = _grid_fc(6, 6, plane)
    res = slx_regression_narrated(fc, "y", ["x1"], weights_scheme="rook")
    assert res.success, res.summary
    names = [c["name"] for c in res.data["coefficients"]]
    assert "intercept" in names and "x1" in names and "WX:x1" in names
    # exact plane: y = 2 + 3x and W·x also linear in x → collinear-free solve
    coefs = {c["name"]: c["coef"] for c in res.data["coefficients"]}
    assert coefs["intercept"] == pytest.approx(2.0, abs=1e-9)
    assert coefs["x1"] == pytest.approx(3.0, abs=1e-9)
    assert coefs["WX:x1"] == pytest.approx(0.0, abs=1e-9)
    assert res.data["r_squared"] == pytest.approx(1.0, abs=1e-12)


# ── GWR ──────────────────────────────────────────────────────────────

def _hand_bisquare_wls(coords, y, x_mat, i, k):
    """独立手算：观测 i 的 bisquare-kNN 局地 WLS（golden 锚参考实现）。"""
    from scipy.spatial import cKDTree

    tree = cKDTree(coords)
    dist, nbr = tree.query(coords[i], k=min(k, len(coords)))
    dist = np.atleast_1d(dist)
    nbr = np.atleast_1d(nbr).astype(int)
    keep = nbr != i
    nbr, dist = nbr[keep], dist[keep]
    order = np.argsort(dist, kind="stable")[: k - 1]
    nbr, dist = nbr[order], dist[order]
    d_max = dist[-1]
    wts = np.where(dist / d_max < 1.0, (1.0 - (dist / d_max) ** 2) ** 2, 0.0)
    nbr = np.append(nbr, i)
    wts = np.append(wts, 1.0)
    xn = x_mat[nbr]
    beta = np.linalg.solve(xn.T @ (xn * wts[:, None]),
                           xn.T @ (wts * y[nbr]))
    return beta


def test_gwr_matches_hand_wls_and_bandwidth_cv():
    from app.lib.geo_analysis.spatial_regression import _design_matrix
    from app.lib.geo_processor.core import to_utm_gdf

    rng = np.random.default_rng(5)
    fc = _grid_fc(4, 5, lambda r, c: {
        "x1": float(rng.uniform(0, 10)),
        "y": 1.0 + 0.8 * float(rng.uniform(0, 10)) + float(rng.normal(0, 0.2))})
    bandwidth = 6
    res = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=bandwidth)
    assert res.success, res.summary
    assert res.data["full_coefficient_surfaces"] is True
    assert res.data["bandwidth"]["selected"] == bandwidth

    # golden anchor：观测 0 的局地系数 == 手算 bisquare WLS
    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    y = gdf["y"].to_numpy(float)
    x_mat, _names = _design_matrix(gdf[["x1"]].to_numpy(float), ["x1"])
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    beta_hand = _hand_bisquare_wls(coords, y, x_mat, 0, bandwidth)
    # 载荷里系数面统一 round(6) —— 一致性下限即舍入精度（最差 5e-7）。
    assert res.data["surfaces"]["intercept"][0] == \
        pytest.approx(float(beta_hand[0]), abs=5e-7)
    assert res.data["surfaces"]["x1"][0] == \
        pytest.approx(float(beta_hand[1]), abs=5e-7)
    # summary blocks + uncertainty types
    assert 0.0 <= res.data["local_r2"]["min"] <= res.data["local_r2"]["max"] <= 1.0
    assert "intercept" in res.data["coefficient_variation"]
    unc_types = {u["uncertainty_type"] for u in res.data["uncertainty"]}
    assert "validation_metrics" in unc_types
    assert "field_uncertainty" in unc_types

    # bandwidth CV：有界网格、确定性、结果来自候选集
    res_cv = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=10,
                                     bandwidth_selection="cv")
    assert res_cv.success
    assert res_cv.data["bandwidth"]["selection"] == "cv"
    assert res_cv.data["bandwidth"]["selected"] in (5, 10, 15, 20, 30)
    assert res_cv.data["bandwidth_cv"] == res_cv.data["bandwidth_cv"]
    res_cv2 = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=10,
                                      bandwidth_selection="cv")
    assert json.dumps(res_cv.data, sort_keys=True) == \
        json.dumps(res_cv2.data, sort_keys=True)


def test_gwr_guards_typed_errors():
    # n < 2p+2 → InsufficientSamples（p=2 → n≥6；这里 n=5）
    fc_small = _points_fc(
        [((116.0 + i * 0.001, 39.0), (float(i), float(i))) for i in range(5)])
    with pytest.raises(InsufficientSamples):
        gwr_regression_narrated(fc_small, "y", ["x1"])
    # 零方差解释变量 → DegenerateData
    fc_constx = _grid_fc(4, 4, lambda r, c: {"x1": 1.0, "y": float(r + c)})
    with pytest.raises(DegenerateData):
        gwr_regression_narrated(fc_constx, "y", ["x1"])
    # 非法 bandwidth_selection → UnsupportedMethod（ValueError 子类）
    fc_ok = _grid_fc(4, 4, lambda r, c: {"x1": float(r), "y": float(r + c)})
    with pytest.raises(ValueError, match="bandwidth_selection"):
        gwr_regression_narrated(fc_ok, "y", ["x1"], bandwidth_selection="magic")


# ── multiple_testing_correction ──────────────────────────────────────

def test_multiple_testing_correction_semantics():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.2, 0.7, 1.0])
    n = p.size
    bonf = multiple_testing_correction(p, "bonferroni")
    assert bonf == pytest.approx(np.clip(p * n, 0, 1))
    holm = multiple_testing_correction(p, "holm")
    bh = multiple_testing_correction(p, "bh")
    # Holm 调整 p ≤ Bonferroni → 同 α 下 Holm 的拒绝数 ≥ Bonferroni
    assert np.all(holm <= bonf + 1e-12)
    assert np.sum(holm < 0.05) >= np.sum(bonf < 0.05)
    # BH 单调：按 p 排序的 q 非降，且 q ≥ p
    order = np.argsort(p)
    assert np.all(np.diff(bh[order]) >= -1e-12)
    assert np.all(bh >= p - 1e-12)
    # bh 与既有 _bh_qvalues 语义一致
    from app.lib.geo_analysis.statistics import _bh_qvalues

    assert bh == pytest.approx(_bh_qvalues(p))
    # none 原样、非法方法抛 UnsupportedMethod、NaN → 1.0
    assert multiple_testing_correction(p, "none") == pytest.approx(p)
    with pytest.raises(ValueError):
        multiple_testing_correction(p, "sidak")
    p_nan = np.array([0.01, np.nan, 0.5])
    assert np.isnan(multiple_testing_correction(p_nan, "bh")).sum() == 0
    assert multiple_testing_correction(p_nan, "bh")[1] == 1.0
