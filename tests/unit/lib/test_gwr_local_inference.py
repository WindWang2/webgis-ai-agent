"""Conformance tests for GWR/MGWR per-coefficient local inference (审计 A1)
and the fixed-bandwidth sensitivity envelope (审计 F-4).

Contract bullets under test (descriptor ids in app/lib/gis/algorithms/
statistics.py):

- ``spatial.gwr``: per-coefficient local SE matches an independently
  hand-computed bisquare WLS sandwich (Var(b_i) = σ̂²·A⁻¹BA⁻¹ with
  A = X'WX, B = X'W²X, σ̂² = RSS/(n − tr(S))); t = b/SE identity;
  spatially varying design → spatially varying SE; fixed path produces a
  real SensitivityEnvelope over a deterministic 3-point bandwidth grid
  (F-4); field_uncertainty typed block targets the local SE;
- ``spatial.mgwr``: per-term through-origin sandwich factor
  Σw²x²/(Σwx²)² matches a hand computation; field_uncertainty block.

All numeric anchors are hand loops (no reuse of implementation helpers)
except the documented σ̂² recovery from the payload scalars
(rmse·√n = √RSS, effective_params − 1 = tr(S) for GWR / ENP for MGWR).
"""
import numpy as np
import pytest
from scipy.spatial import cKDTree

from app.lib.geo_analysis.spatial_regression import (
    gwr_regression_narrated,
    mgwr_regression_narrated,
)

pytestmark = pytest.mark.unit


def _grid_fc(nrows, ncols, props_fn, cell=0.01, lon0=116.0, lat0=39.0):
    """Polygon-grid FeatureCollection（与 test_spatial_regression_v2 同构）。"""
    xs = [lon0 + i * cell for i in range(ncols + 1)]
    ys = [lat0 + j * cell for j in range(nrows + 1)]
    feats = []
    for r in range(nrows):
        for c in range(ncols):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [xs[c], ys[r]], [xs[c + 1], ys[r]],
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]],
                    [xs[c], ys[r]],
                ]]},
                "properties": props_fn(r, c),
            })
    return {"type": "FeatureCollection", "features": feats}


def _noisy_plane_fc(seed=11, nrows=4, ncols=5, x_scale=False):
    """y = 1 + 0.8·x1 + 同方差噪声；x_scale=True 时 x1 幅度随列线性放大
    （局地设计结构变化 → 局地 SE 空间变异的机制）。"""
    rng = np.random.default_rng(seed)

    def props(r, c):
        x1 = float(rng.uniform(0, 10)) * (1.0 + 3.0 * c / ncols
                                          if x_scale else 1.0)
        return {"x1": x1,
                "y": 1.0 + 0.8 * x1 + float(rng.normal(0, 0.3))}

    return _grid_fc(nrows, ncols, props)


def _hand_sandwich_se(coords, y, x_mat, i, k, sigma2):
    """观测 i 的局地 sandwich SE（显式循环手算，不用实现层 helper）。"""
    tree = cKDTree(coords)
    dist, nbr = tree.query(coords[i], k=min(k + 1, len(coords)))
    dist, nbr = np.atleast_1d(dist), np.atleast_1d(nbr).astype(int)
    pairs = [(float(d), int(j)) for d, j in zip(dist, nbr) if int(j) != i]
    pairs.sort(key=lambda t: t[0])
    pairs = pairs[: k - 1]
    rows, wts = [], []
    for d, j in pairs:
        d_max = pairs[-1][0]
        u = d / d_max
        rows.append(j)
        wts.append((1.0 - u * u) ** 2 if u < 1.0 else 0.0)
    rows.append(i)
    wts.append(1.0)
    p = x_mat.shape[1]
    a = np.zeros((p, p))
    b = np.zeros((p, p))
    for w, j in zip(wts, rows):
        xj = x_mat[j]
        a += w * np.outer(xj, xj)
        b += w * w * np.outer(xj, xj)
    a_inv = np.linalg.inv(a)
    cov = sigma2 * (a_inv @ b @ a_inv)
    return np.sqrt(np.diag(cov))


def _coords_y_x(fc, fields):
    from app.lib.geo_analysis.spatial_regression import _design_matrix
    from app.lib.geo_processor.core import to_utm_gdf

    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    y = gdf["y"].to_numpy(float)
    x_mat, _ = _design_matrix(gdf[list(fields)].to_numpy(float),
                              list(fields))
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    return coords, y, x_mat


# ── spatial.gwr：局地 SE / t（审计 A1 P0）────────────────────────────

def test_gwr_local_se_matches_hand_sandwich():
    """GOLDEN ANCHOR：观测 0 的局地 SE == 手算 bisquare sandwich 对角
    （σ̂² 从载荷标量还原：RSS=(rmse·√n)²，tr(S)=effective_params−1）。"""
    fc = _noisy_plane_fc()
    bandwidth = 6
    res = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=bandwidth)
    assert res.success, res.summary
    n = res.data["n_features"]
    sigma2 = (res.data["rmse"] ** 2) * n / (
        n - (res.data["effective_params"] - 1.0))
    coords, y, x_mat = _coords_y_x(fc, ["x1"])
    se_hand = _hand_sandwich_se(coords, y, x_mat, 0, bandwidth, sigma2)
    # 载荷 6 位舍入 → 一致性下限 5e-7（与系数面 golden 锚同一口径）
    assert res.data["surfaces"]["se_intercept"][0] == \
        pytest.approx(float(se_hand[0]), abs=5e-7)
    assert res.data["surfaces"]["se_x1"][0] == \
        pytest.approx(float(se_hand[1]), abs=5e-7)
    # SE 恒正、有限（该 fixture 无奇异局地系统）
    se_arr = np.asarray(res.data["surfaces"]["se_x1"], dtype=float)
    assert np.all(np.isfinite(se_arr)) and np.all(se_arr > 0)
    # t = b / SE 恒等式（全观测逐位；容差 = 面载荷 6 位舍入经除法放大）
    betas = np.asarray(res.data["surfaces"]["x1"], dtype=float)
    t_vals = np.asarray(res.data["surfaces"]["t_x1"], dtype=float)
    np.testing.assert_allclose(t_vals, betas / se_arr,
                               rtol=1e-3, atol=1e-3)
    # 摘要块与面一致
    summary = res.data["local_inference"]["x1"]
    assert summary["se"]["min"] == pytest.approx(
        float(se_arr.min()), abs=1e-6)


def test_gwr_local_se_spatial_variation_and_null_signal():
    """（a）x1 幅度空间放大 → 局地 SE 空间变异（IQR>0）；（b）强信号系数
    的 |t|>1.96 比例高于纯噪声系数 —— 方向性守卫。"""
    fc = _noisy_plane_fc(seed=13, x_scale=True)
    res = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=6)
    assert res.success, res.summary
    se_arr = np.asarray(res.data["surfaces"]["se_x1"], dtype=float)
    iqr = float(np.percentile(se_arr, 75) - np.percentile(se_arr, 25))
    assert iqr > 0  # 空间变异被检出
    strong_share = res.data["local_inference"]["x1"]["share_abs_t_gt_1p96"]
    # 纯噪声对照：把 y 换成与 x 无关的同方差噪声
    rng = np.random.default_rng(21)
    fc_null = _noisy_plane_fc(seed=13, x_scale=True)
    feats = fc_null["features"]
    for f in feats:
        f["properties"]["y"] = float(rng.normal(0, 1.0))
    res_null = gwr_regression_narrated(fc_null, "y", ["x1"], bandwidth=6)
    assert res_null.success
    noise_share = res_null.data["local_inference"]["x1"][
        "share_abs_t_gt_1p96"]
    assert strong_share > noise_share
    assert 0.0 <= noise_share <= 1.0


# ── spatial.gwr：fixed 路径带宽敏感性 envelope（审计 F-4 P0）────────

def test_gwr_fixed_bandwidth_sensitivity_envelope():
    """fixed（默认）路径必须真实产出 sensitivity_envelope —— 确定性
    3 点带宽网格 {k/2, k, 2k}（裁剪去重）上的 ENP/R² 摘要。"""
    fc = _noisy_plane_fc()
    res = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=10)
    assert res.success
    assert res.data["bandwidth"]["selection"] == "fixed"
    sens = res.data["bandwidth_sensitivity"]
    assert 1 <= len(sens) <= 3
    k_used = res.data["bandwidth"]["selected"]
    assert any(pt["bandwidth"] == k_used for pt in sens)
    for pt in sens:
        assert pt["effective_params"] >= 2.0  # p=2 的 sandwich 下界
        assert 0.0 <= pt["r_squared"] <= 1.0
    # envelope 证据块真实挂在 uncertainty 列表
    env_blocks = [u for u in res.data["uncertainty"]
                  if u["uncertainty_type"] == "sensitivity_envelope"]
    assert len(env_blocks) == 1
    env = env_blocks[0]
    assert "coarse grid" in env["perturbation_scheme"]
    assert env["tipping_points"]  # 真实填充
    # 确定性：两次运行逐位一致
    res2 = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=10)
    assert res.data["bandwidth_sensitivity"] == res2.data["bandwidth_sensitivity"]
    # cv 路径仍走既有 CV envelope（不回归）
    res_cv = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=10,
                                     bandwidth_selection="cv")
    assert res_cv.success
    assert "bandwidth_cv" in res_cv.data
    assert "bandwidth_sensitivity" not in res_cv.data


def test_gwr_local_se_field_uncertainty_block():
    """uncertainty_producer_tests（field_uncertainty）：typed 块 target=
    gwr_local_coefficient_se、measure=standard_error，数值与摘要一致。"""
    fc = _noisy_plane_fc()
    res = gwr_regression_narrated(fc, "y", ["x1"], bandwidth=6)
    assert res.success
    blocks = [u for u in res.data["uncertainty"]
              if u.get("target") == "gwr_local_coefficient_se"]
    assert len(blocks) == 1
    block = blocks[0]
    assert block["uncertainty_type"] == "field_uncertainty"
    measures = block["summary"]
    assert len(measures) == 2  # intercept + x1（≤8 有界）
    assert all(m["measure"] == "standard_error" for m in measures)
    for m in measures:
        name = "intercept" if "'intercept'" in m["method"] else "x1"
        assert m["value"] == pytest.approx(
            res.data["local_inference"][name]["se"]["median"], abs=1e-9)


# ── spatial.mgwr：逐项局地 SE / t（审计 A1 P0）──────────────────────

def _mgwr_fc(seed=7):
    """带噪声平面（σ̂²>0 使 SE 可观测）。"""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.linspace(0, 1, 10), np.linspace(0, 1, 6))
    x1 = gx.ravel() + 0.05 * rng.normal(size=60)
    x2 = gy.ravel() + 0.05 * rng.normal(size=60)
    return _grid_fc(6, 10, lambda r, c: {
        "x1": float(x1[r * 10 + c]),
        "x2": float(x2[r * 10 + c]),
        "y": float(2.0 + 3.0 * x1[r * 10 + c] - 1.0 * x2[r * 10 + c]
                   + rng.normal(0, 0.4)),
    })


def test_mgwr_local_se_matches_hand_factor():
    """MGWR：截距项（全 1 列）的过原点 sandwich 因子 s2/den² 手算锚。
    σ̂² 从载荷标量还原：RSS=(rmse·√n)²，ENP=effective_params。"""
    fc = _mgwr_fc()
    res = mgwr_regression_narrated(fc, "y", ["x1", "x2"], bandwidth=12,
                                   fixed_bandwidths=[12, 12, 12])
    assert res.success, res.summary
    n = res.data["n_features"]
    sigma2 = (res.data["rmse"] ** 2) * n / (
        n - res.data["effective_params"])
    k_int = res.data["bandwidths"]["per_term"]["intercept"]
    coords, _y, _x = _coords_y_x(fc, ["x1", "x2"])
    tree = cKDTree(coords)
    dist, nbr = tree.query(coords[0], k=min(k_int + 1, n))
    dist, nbr = np.atleast_1d(dist), np.atleast_1d(nbr).astype(int)
    pairs = sorted((float(d), int(j)) for d, j in zip(dist, nbr)
                   if int(j) != 0)[: k_int - 1]
    w = [(1.0 - (d / pairs[-1][0]) ** 2) ** 2 if d / pairs[-1][0] < 1.0
         else 0.0 for d, _ in pairs]
    den = sum(w) + 1.0          # 全 1 列：Σwx² = Σw + 自身 1
    s2 = sum(v * v for v in w) + 1.0
    se_hand = float(np.sqrt(sigma2 * (s2 / (den * den))))
    got = res.data["surfaces_local_inference"]["se_intercept"][0]
    assert got == pytest.approx(se_hand, abs=2e-6)
    # t = b / SE 恒等式（截距项全观测）
    betas = np.asarray(res.data["surfaces"]["intercept"], dtype=float)
    se_arr = np.asarray(
        res.data["surfaces_local_inference"]["se_intercept"], dtype=float)
    t_arr = np.asarray(
        res.data["surfaces_local_inference"]["t_intercept"], dtype=float)
    np.testing.assert_allclose(t_arr, betas / se_arr, rtol=1e-3, atol=1e-3)
    assert res.data["local_inference"]["intercept"]["se"]["median"] > 0


def test_mgwr_local_se_field_uncertainty_block():
    """uncertainty_producer_tests（field_uncertainty）：MGWR typed 块。"""
    fc = _mgwr_fc()
    res = mgwr_regression_narrated(fc, "y", ["x1", "x2"], bandwidth=12,
                                   fixed_bandwidths=[12, 12, 12])
    assert res.success
    blocks = [u for u in res.data["uncertainty"]
              if u.get("target") == "mgwr_local_coefficient_se"]
    assert len(blocks) == 1
    block = blocks[0]
    assert block["uncertainty_type"] == "field_uncertainty"
    measures = {m["method"]: m["value"] for m in block["summary"]}
    assert len(measures) == 3  # intercept + x1 + x2（≤8 有界）
    for name in ("intercept", "x1", "x2"):
        assert any(f"'{name}'" in key for key in measures)
        assert res.data["local_inference"][name]["se"]["median"] > 0
