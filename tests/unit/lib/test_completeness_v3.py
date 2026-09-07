"""Completeness V3 batch conformance tests.

Contract bullets under test (descriptor ids in app/lib/gis/algorithms/):

- ``stats.bivariate_join_count``: two-color join count on arbitrary
  two-category fields (hand fixture + free-sampling expectations); seeded
  permutation determinism; typed rejections (3/1 categories, missing field,
  n<4);
- ``spatial.ols_regression`` cov_type extension: HC0/HC1/HC3 robust
  standard errors vs closed-form numpy sandwich references on a
  heteroskedastic fixture; default ("classic") output byte-identical;
  invalid cov_type typed rejection;
- ``stats.rate_smoothing``: Marshall (1991) MOM empirical-Bayes rate
  smoothing — constant rates recover raw rates exactly (zero-variance
  prior), heavy population shrinks less, zero-population features typed +
  disclosed;
- ``temporal.seasonal_decompose``: exact recovery on a linear-ramp +
  fixed-season fixture (trend = centered MA of the ramp, seasonal indices
  = the constructed season, remainder ≈ 0); additive reconstruction;
  typed rejections (even period, period<3, <2 full periods);
- ``spatial.kde.surface`` bandwidth_method extension: adaptive evaluator
  integrates to ≈1 on a uniform fixture; fixed mode byte-identical to the
  legacy path; adaptive determinism + disclosure envelope;
- tool layer: registry parity, new tool schemas, evidence attachment.
"""
import json

import numpy as np
import pytest

from app.lib.geo_analysis.statistics import (
    bivariate_join_count_narrated,
    empirical_bayes_rate_smooth,
)
from app.lib.geo_analysis.spatial_regression import ols_regression_narrated
from app.lib.geo_analysis.density import (
    _adaptive_bandwidths,
    _evaluate_adaptive_kde,
    kde_surface,
)
from app.services.temporal.trend import seasonal_decompose_narrated
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit


# ── fixtures ─────────────────────────────────────────────────────────

def _grid_fc(nrows, ncols, val_fn, cell=0.01, lon0=116.0, lat0=39.0,
             field="val"):
    """Polygon-grid FeatureCollection with identical shared vertices."""
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
                "properties": {field: float(val_fn(r, c))},
            })
    return {"type": "FeatureCollection", "features": feats}


def _strip_fc(props, cell=0.01):
    """1×len(props) rook-adjacent square strip with per-cell properties."""
    feats = []
    for i, p in enumerate(props):
        x0 = 116.0 + i * cell
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, 39.0], [x0 + cell, 39.0],
                [x0 + cell, 39.0 + cell], [x0, 39.0 + cell], [x0, 39.0],
            ]]},
            "properties": dict(p),
        })
    return {"type": "FeatureCollection", "features": feats}


def _points_fc(pts):
    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [116.0 + 0.001 * p[0],
                                      39.0 + 0.001 * p[1]]},
         "properties": {}}
        for p in pts
    ]
    return {"type": "FeatureCollection", "features": feats}


# ── stats.bivariate_join_count ───────────────────────────────────────

def test_bivariate_join_count_hand_fixture():
    """2×2 rook 棋盘（类别 {1, 7}）：4 个无序连接全为异类。

    手算黄金值：n_BB=n_WW=0、n_BW=J=4；free-sampling 期望
    E[n_BB]=J·b(b−1)/(n(n−1))=4·2/12=2/3、E[n_BW]=4·8/12=8/3。
    颜色映射：排序后较小值 1 → B、较大值 7 → W。
    """
    fc = _grid_fc(2, 2, lambda r, c: 1.0 if (r + c) % 2 == 0 else 7.0)
    res = bivariate_join_count_narrated(fc, "val", weights_scheme="rook")
    assert res.success, res.summary
    d = res.data
    assert d["joins"] == 4.0
    assert d["join_counts"] == {"n_bb": 0.0, "n_bw": 4.0, "n_ww": 0.0}
    assert d["category_black"] == 1.0
    assert d["category_white"] == 7.0
    assert d["n_black"] == 2.0 and d["n_white"] == 2.0
    assert d["expected"]["n_bb"] == pytest.approx(4.0 * 2.0 / 12.0)
    assert d["expected"]["n_bw"] == pytest.approx(4.0 * 8.0 / 12.0)
    assert d["expected"]["n_ww"] == pytest.approx(4.0 * 2.0 / 12.0)
    # free-sampling 假设显式披露（spec 要求）
    assert any("free sampling" in a for a in d["assumptions_disclosed"])
    assert "free-sampling" in res.summary


def test_bivariate_join_count_expectation_and_permutation_determinism():
    """4×4 棋盘：期望手算核对 + BW 解析 z/p 可用 + 置换固定种子 42 确定性。"""
    fc = _grid_fc(4, 4, lambda r, c: 1.0 if (r + c) % 2 == 0 else 3.0)
    res = bivariate_join_count_narrated(fc, "val", weights_scheme="rook",
                                        permutations=99)
    assert res.success, res.summary
    d = res.data
    # J = 2·4·3 = 24 个 rook 无序连接；棋盘 → 全异类。
    assert d["joins"] == 24.0
    assert d["join_counts"] == {"n_bb": 0.0, "n_bw": 24.0, "n_ww": 0.0}
    # free-sampling 期望：b=w=8、n=16。
    assert d["expected"]["n_bb"] == pytest.approx(24.0 * 56.0 / 240.0)
    assert d["expected"]["n_bw"] == pytest.approx(24.0 * 128.0 / 240.0)
    # BW 的解析二阶矩在此构型下为正 → z/p 可用且 z 方向正确（异类偏高）。
    assert d["z"]["n_bw"] is not None and d["z"]["n_bw"] > 0
    assert 0.0 < d["p_value_analytic"]["n_bw"] <= 1.0
    # 置换复核：固定种子 → 逐位可复现；p ∈ (0, 1]。
    res2 = bivariate_join_count_narrated(fc, "val", weights_scheme="rook",
                                         permutations=99)
    assert json.dumps(d["p_value_permutation"], sort_keys=True) == \
        json.dumps(res2.data["p_value_permutation"], sort_keys=True)
    pp = d["p_value_permutation"]
    assert all(0.0 < pp[k] <= 1.0 for k in ("n_bb", "n_bw", "n_ww"))
    assert pp["permutations"] == 99


def test_bivariate_join_count_typed_rejections():
    """3 类 / 1 类 / 缺字段 / n<4 → 类型化科学错误（不伪造结果）。"""
    fc3 = _grid_fc(2, 2, lambda r, c: float(r * 2 + c))  # 4 个不同值
    with pytest.raises(UnsupportedMethod):
        bivariate_join_count_narrated(fc3, "val", weights_scheme="rook")

    fc1 = _grid_fc(2, 2, lambda r, c: 5.0)
    with pytest.raises(DegenerateData):
        bivariate_join_count_narrated(fc1, "val", weights_scheme="rook")

    with pytest.raises(MissingRequiredField):
        bivariate_join_count_narrated(_grid_fc(2, 2, lambda r, c: 1.0),
                                      "nope", weights_scheme="rook")

    small = _grid_fc(1, 3, lambda r, c: float(c % 2))  # n=3 < 4
    with pytest.raises(InsufficientSamples):
        bivariate_join_count_narrated(small, "val", weights_scheme="rook")


# ── spatial.ols_regression cov_type（MacKinnon-White HC0/HC1/HC3）────

def _heteroskedastic_ols_fixture():
    """异方差构造：e_i ~ N(0,1)·x_i/5 —— 经典 se 与稳健 se 必然分化。"""
    rng = np.random.default_rng(7)
    n = 40
    x = np.linspace(1.0, 10.0, n)
    y = 2.0 + 3.0 * x + rng.normal(0.0, 1.0, n) * x / 5.0
    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [116.0 + 0.001 * i,
                                      39.0 + 0.001 * ((i * 7) % 5)]},
         "properties": {"x1": float(x[i]), "y1": float(y[i])}}
        for i in range(n)
    ]
    fc = {"type": "FeatureCollection", "features": feats}
    return fc, x, y


def _ols_reference(x, y):
    """闭式 OLS：β、残差、(X'X)⁻¹ 与杠杆 —— 测试内独立重算。"""
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ beta
    XtXi = np.linalg.pinv(X.T @ X)
    h = np.einsum("ij,jk,ik->i", X, XtXi, X)
    return X, beta, e, XtXi, h


def test_ols_hc0_matches_closed_form():
    """HC0 与手算夹心公式 (X'X)⁻¹(Σx_i x_i'e_i²)(X'X)⁻¹ 差 <1e-10。"""
    fc, x, y = _heteroskedastic_ols_fixture()
    res = ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC0")
    assert res.success, res.summary
    assert res.data["cov_type"] == "HC0"
    X, beta, e, XtXi, _ = _ols_reference(x, y)
    meat = (X * (e ** 2)[:, None]).T @ X
    se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
    for i, row in enumerate(res.data["coefficients"]):
        assert row["robust_std_error"] == pytest.approx(se[i], abs=1e-10)
    # 系数点估计不受协方差选择影响。
    assert res.data["coefficients"][1]["coef"] == pytest.approx(beta[1],
                                                                abs=1e-10)


def test_ols_hc3_matches_closed_form():
    """HC3 与手算杠杆校正夹心 e_i²/(1−h_i)² 差 <1e-10（比 HC0 更保守）。"""
    fc, x, y = _heteroskedastic_ols_fixture()
    res = ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC3")
    X, beta, e, XtXi, h = _ols_reference(x, y)
    adj = e ** 2 / (1.0 - h) ** 2
    meat = (X * adj[:, None]).T @ X
    se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
    for i, row in enumerate(res.data["coefficients"]):
        assert row["robust_std_error"] == pytest.approx(se[i], abs=1e-10)
    hc0 = ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC0")
    assert row["robust_std_error"] > \
        hc0.data["coefficients"][1]["robust_std_error"] * 0.999  # 杠杆校正方向
    assert "cov_type_disclosure" in res.data


def test_ols_hc1_scales_hc0():
    """HC1 = sqrt(n/(n−p)) × HC0（自由度校正的解析关系）。"""
    fc, x, y = _heteroskedastic_ols_fixture()
    n = len(y)
    p = 2
    hc0 = ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC0")
    hc1 = ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC1")
    scale = np.sqrt(n / (n - p))
    for r0, r1 in zip(hc0.data["coefficients"], hc1.data["coefficients"]):
        assert r1["robust_std_error"] == pytest.approx(
            r0["robust_std_error"] * scale, rel=1e-12)


def test_ols_default_cov_type_unchanged():
    """缺省 = 显式 classic 输出逐位一致，且不带任何稳健协方差新键。"""
    fc, _, _ = _heteroskedastic_ols_fixture()
    default = ols_regression_narrated(fc, "y1", ["x1"])
    classic = ols_regression_narrated(fc, "y1", ["x1"], cov_type="classic")
    assert json.dumps(default.data, sort_keys=True) == \
        json.dumps(classic.data, sort_keys=True)
    assert "cov_type" not in default.data
    assert all("robust_std_error" not in row
               for row in default.data["coefficients"])


def test_ols_invalid_cov_type_typed_error():
    fc, _, _ = _heteroskedastic_ols_fixture()
    with pytest.raises(UnsupportedMethod):
        ols_regression_narrated(fc, "y1", ["x1"], cov_type="HC9")


# ── stats.rate_smoothing（Marshall 1991 MOM）─────────────────────────

def test_eb_constant_rates_recover_raw_rates():
    """零方差先验不变量：常数率下先验均值=原始率 → 平滑率逐位还原。

    （全局与局部邻居先验两路都核；MOM/Poisson 披露在 meta。）
    """
    fc = _strip_fc([{"cnt": 6, "pop": 100}] * 4)
    for scheme in ("none", "knn"):
        res = empirical_bayes_rate_smooth(fc, "cnt", "pop",
                                          weights_scheme=scheme, k=1)
        assert res.success, res.summary
        raw = [f["properties"]["raw_rate"] for f in res.data["features"]]
        sm = [f["properties"]["smoothed_rate"] for f in res.data["features"]]
        assert all(r == pytest.approx(0.06) for r in raw)
        for a, b in zip(raw, sm):
            assert a == pytest.approx(b, abs=1e-12)
        assert res.data["prior_parameters"]["estimator"] == \
            "method_of_moments (Marshall 1991)"
        assert "Poisson" in \
            res.data["prior_parameters"]["poisson_assumption_disclosure"]


def test_eb_heavy_population_shrinks_less():
    """人口重的区收缩权重更大（先验抽样噪声 μ/P 更小）→ 更贴近自身原始率。"""
    props = [
        {"cnt": 5, "pop": 100},         # 0.05
        {"cnt": 50, "pop": 100},        # 0.50 小人口离群（邻居率 {0.05, 0.15}）
        {"cnt": 15, "pop": 100},        # 0.15
        {"cnt": 5000, "pop": 10000},    # 0.50 重人口离群（邻居率 {0.15, 0.05}）
        {"cnt": 5, "pop": 100},         # 0.05
        {"cnt": 15, "pop": 100},        # 0.15
    ]
    res = empirical_bayes_rate_smooth(_strip_fc(props), "cnt", "pop",
                                      weights_scheme="rook")
    assert res.success, res.summary
    feats = res.data["features"]
    w_small = feats[1]["properties"]["shrinkage_weight"]
    w_heavy = feats[3]["properties"]["shrinkage_weight"]
    # 两者邻居构型对称（σ² 相同），唯一差别是人口 → w_重 > w_小 > 0。
    assert 0.0 < w_small < w_heavy <= 1.0
    raw_small = feats[1]["properties"]["raw_rate"]
    raw_heavy = feats[3]["properties"]["raw_rate"]
    pull_small = abs(feats[1]["properties"]["smoothed_rate"] - raw_small)
    pull_heavy = abs(feats[3]["properties"]["smoothed_rate"] - raw_heavy)
    assert pull_heavy < pull_small
    # 先验参数与平滑率/原始率同时输出（spec 要求）。
    assert all(f["properties"]["prior_mean"] is not None for f in feats)
    assert res.data["method"] == "marshall1991_mom_local"


def test_eb_zero_population_typed_and_disclosed():
    """零人口区类型化排除（rate=None）+ 计数披露；全零人口 → DegenerateData。"""
    fc = _strip_fc([{"cnt": 5, "pop": 0}, {"cnt": 5, "pop": 100},
                    {"cnt": 5, "pop": 100}, {"cnt": 5, "pop": 100}])
    res = empirical_bayes_rate_smooth(fc, "cnt", "pop")
    assert res.success, res.summary
    assert res.data["zero_population_excluded"] == 1
    smoothed = [f["properties"]["smoothed_rate"] for f in res.data["features"]]
    assert smoothed[0] is None                     # 不产率值（JSON null）
    assert all(s is not None for s in smoothed[1:])
    assert "零人口" in res.summary or "排除" in res.summary

    all_zero = _strip_fc([{"cnt": 5, "pop": 0}] * 4)
    with pytest.raises(DegenerateData):
        empirical_bayes_rate_smooth(all_zero, "cnt", "pop")


# ── temporal.seasonal_decompose（经典 MA；非 STL）────────────────────

def _seasonal_fixture():
    """y_t = 5 + 0.5t + s_{t mod 5}，s=[0,3,−1,−2,0]（Σ=0）。

    奇数窗口 5 的中心 MA 对斜坡逐位还原、对每窗恰好含各相位一次的
    季节序列 MA≡0 → 趋势=斜坡精确、季节指数=构造季节、余项=0。
    """
    s = np.array([0.0, 3.0, -1.0, -2.0, 0.0])
    n = 25
    t = np.arange(n)
    y = 5.0 + 0.5 * t + s[t % 5]
    return y.tolist(), s


def test_seasonal_decompose_sine_plus_linear_exact():
    """趋势 = 斜坡的中心 MA（内点精确）；季节指数=构造季节（Σ=0）；余项≈0。"""
    y, s = _seasonal_fixture()
    res = seasonal_decompose_narrated(y, 5)
    assert res["n"] == 25 and res["period"] == 5
    trend = res["trend"]
    # 首尾 (period−1)/2=2 个位置无定义（诚实 None）。
    assert trend[0] is None and trend[1] is None
    assert trend[23] is None and trend[24] is None
    for i in range(2, 23):
        assert trend[i] == pytest.approx(5.0 + 0.5 * i, abs=1e-9)
    assert res["seasonal_indices"] == pytest.approx(s.tolist(), abs=1e-9)
    assert sum(res["seasonal_indices"]) == pytest.approx(0.0, abs=1e-9)
    interior_rem = [abs(r) for r in res["remainder"][2:23]]
    assert max(interior_rem) < 1e-9
    # 经典 MA 而非 STL 的诚实披露。
    assert any("STL" in d for d in res["disclosures"])


def test_seasonal_decompose_additive_reconstruction():
    """加法可分解性：y = trend + seasonal + remainder（内点 1e-9）。"""
    y, _ = _seasonal_fixture()
    res = seasonal_decompose_narrated(y, 5)
    for i in range(2, 23):
        recombined = res["trend"][i] + res["seasonal"][i] + res["remainder"][i]
        assert recombined == pytest.approx(y[i], abs=1e-9)
    # 乘法模型：季节指数均值归一化到 1。
    ym = [30.0, 60.0, 15.0, 24.0, 30.0] * 6
    rm = seasonal_decompose_narrated(ym, 5, model="multiplicative")
    assert float(np.mean(rm["seasonal_indices"])) == pytest.approx(1.0)


def test_seasonal_decompose_typed_rejections():
    """偶数周期 / period<3 / 不足 2 个完整周期 / 非法模型 → 类型化错误。"""
    y, _ = _seasonal_fixture()
    with pytest.raises(UnsupportedMethod):
        seasonal_decompose_narrated(y, 4)          # 偶数周期
    with pytest.raises(UnsupportedMethod):
        seasonal_decompose_narrated(y, 2)          # period < 3
    with pytest.raises(UnsupportedMethod):
        seasonal_decompose_narrated(y, 5, model="stl")  # 非法模型
    with pytest.raises(InsufficientSamples):
        seasonal_decompose_narrated(y[:9], 5)      # n=9 < 2×5
    with pytest.raises(UnsupportedMethod):
        seasonal_decompose_narrated([-1.0, 2.0] * 10, 5,
                                    model="multiplicative")  # 非正序列


# ── spatial.kde.surface bandwidth_method（Abramson 1982）─────────────

def test_adaptive_kde_evaluator_integrates_to_one():
    """逐点带宽核求和器在均匀 fixture 上数值积分 ≈1（密度归一化正确）。"""
    rng = np.random.default_rng(42)
    pts = rng.uniform(0.0, 1000.0, (60, 2))
    data = pts.T
    h0 = 80.0
    from scipy.stats import gaussian_kde

    kde = gaussian_kde(data, bw_method="scott")
    kde.cho_cov = np.eye(2) * h0  # 与 _fit_kde 的各向同性语义一致
    h_is, lam_min, lam_max = _adaptive_bandwidths(kde, data, h0, None)
    assert 0.0 < lam_min <= 1.0 <= lam_max  # 几何均值锚定 λ=1
    assert np.all(h_is > 0)

    g = np.linspace(-400.0, 1400.0, 181)
    gx, gy = np.meshgrid(g, g)
    grid = np.vstack([gx.ravel(), gy.ravel()])
    dens = _evaluate_adaptive_kde(data, h_is, None, grid)
    integral = float(dens.sum()) * (g[1] - g[0]) ** 2
    assert integral == pytest.approx(1.0, abs=0.01)
    # 加权路径同样归一（权重和归一在求和器内完成）。
    w = rng.uniform(0.5, 2.0, len(pts))
    dens_w = _evaluate_adaptive_kde(data, h_is, w, grid)
    assert float(dens_w.sum()) * (g[1] - g[0]) ** 2 == pytest.approx(1.0,
                                                                     abs=0.01)


def test_kde_surface_fixed_mode_byte_identical():
    """bandwidth_method 缺省与显式 "fixed" 输出逐位一致（无新增信封键）。"""
    rng = np.random.default_rng(3)
    pts = [(i % 8 + rng.normal(0, 0.2), (i // 8) % 8 + rng.normal(0, 0.2))
           for i in range(40)]
    fc = _points_fc(pts)
    a = kde_surface(fc, bandwidth=300, cell_size=100)
    b = kde_surface(fc, bandwidth=300, cell_size=100, bandwidth_method="fixed")
    assert a.success and b.success
    assert json.dumps(a.data, sort_keys=True) == \
        json.dumps(b.data, sort_keys=True)
    assert "bandwidth_mode" not in a.data
    assert "adaptive_bandwidth" not in a.data


def test_kde_surface_adaptive_determinism_and_disclosure():
    """自适应路径确定性重放 + 先导带宽/λ 范围披露（信封键）。"""
    rng = np.random.default_rng(3)
    pts = [(i % 8 + rng.normal(0, 0.2), (i // 8) % 8 + rng.normal(0, 0.2))
           for i in range(40)]
    fc = _points_fc(pts)
    a = kde_surface(fc, bandwidth=300, cell_size=100, bandwidth_method="adaptive")
    b = kde_surface(fc, bandwidth=300, cell_size=100, bandwidth_method="adaptive")
    assert a.success and b.success
    assert json.dumps(a.data, sort_keys=True) == \
        json.dumps(b.data, sort_keys=True)
    assert a.data["bandwidth_mode"] == "adaptive"
    info = a.data["adaptive_bandwidth"]
    assert info["pilot_bandwidth_m"] == pytest.approx(300.0)
    assert 0.0 < info["lambda_min"] <= info["lambda_max"]
    assert info["method"] == "abramson1982_sqrt_pilot"
    assert "Abramson" in info["disclosure"]
    # 自适应面与固定带当面不同（非恒等改名）。
    fixed = kde_surface(fc, bandwidth=300, cell_size=100)
    assert json.dumps(fixed.data, sort_keys=True) != \
        json.dumps(a.data, sort_keys=True)


# ── 工具层（parity / schema / evidence）──────────────────────────────

async def test_tool_registry_parity_and_new_tools():
    """parity 门 == []；新工具 schema 注册；evidence/契约默认值透传。"""
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    assert validate_algorithm_tool_parameter_parity() == []

    registry = ToolRegistry()
    init_tools(registry)
    schemas = {s["function"]["name"]: s["function"].get("parameters") or {}
               for s in registry.get_schemas()}
    # 新工具 + additive 参数进 schema。
    for name in ("bivariate_join_count", "rate_smoothing",
                 "temporal_seasonal_decompose"):
        assert name in schemas, name
    assert "cov_type" in schemas["ols_regression"]["properties"]
    assert "bandwidth_method" in schemas["kde_surface"]["properties"]

    # bivariate_join_count：dispatch → success + 证据块 + 类别映射透传。
    fc = _grid_fc(4, 4, lambda r, c: 1.0 if (r + c) % 2 == 0 else 3.0)
    payload = await registry.dispatch(
        "bivariate_join_count",
        {"geojson": fc, "binary_field": "val", "weights_scheme": "rook",
         "permutations": 99})
    assert payload["success"] is True, payload.get("summary")
    ev = payload["scientific_evidence"]
    assert ev["algorithm"] == "stats.bivariate_join_count"
    assert ev["parameters_applied"]["permutations"] == 99
    assert payload["data"]["join_counts"]["n_bw"] == 24.0

    # rate_smoothing：dispatch → success + 证据块。
    strip = _strip_fc([{"cnt": 6, "pop": 100}] * 4)
    payload_rs = await registry.dispatch(
        "rate_smoothing",
        {"geojson": strip, "count_field": "cnt", "population_field": "pop"})
    assert payload_rs["success"] is True, payload_rs.get("summary")
    assert payload_rs["scientific_evidence"]["algorithm"] == "stats.rate_smoothing"

    # ols_regression：cov_type additive 参数透传 evidence。
    fc_ols = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + 0.001 * c,
                                          39.0 + 0.001 * r]},
             "properties": {"val": float(r * 6 + c) + (c % 2),
                            "x1": float(c)}}
            for r in range(6) for c in range(6)
        ],
    }
    payload_ols = await registry.dispatch(
        "ols_regression",
        {"geojson": fc_ols, "target_field": "val",
         "explanatory_fields": "x1", "cov_type": "HC3"})
    assert payload_ols["success"] is True, payload_ols.get("summary")
    ev_ols = payload_ols["scientific_evidence"]
    assert ev_ols["parameters_applied"]["cov_type"] == "HC3"

    # temporal_seasonal_decompose：dispatch → success + 非 STL 披露。
    payload_sd = await registry.dispatch(
        "temporal_seasonal_decompose",
        {"values": [float(5.0 + 0.5 * t +
                         [0.0, 3.0, -1.0, -2.0, 0.0][t % 5])
                    for t in range(25)],
         "period": 5})
    assert payload_sd["success"] is True, payload_sd
    assert payload_sd["seasonal_indices"] == pytest.approx(
        [0.0, 3.0, -1.0, -2.0, 0.0], abs=1e-6)
    assert any("STL" in w for w in payload_sd["disclosures"])


def test_rate_smoothing_rejects_negative_counts():
    """负计数 → 类型化拒绝（V3 review MINOR-2）：MOM 先验均值为负会把
    收缩因子推出率支撑 [0,1]——静默外推是伪造。"""
    import numpy as np
    import pytest
    from app.lib.gis.scientific_errors import UnsupportedMethod
    from app.lib.geo_analysis.statistics import empirical_bayes_rate_smooth

    feats = []
    for i, (count, pop) in enumerate([(5, 100), (3, 80), (-2, 60)]):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"count": float(count), "pop": float(pop)},
        })
    fc = {"type": "FeatureCollection", "features": feats}
    with pytest.raises(UnsupportedMethod):
        empirical_bayes_rate_smooth(fc, "count", "pop", weights_scheme="")
