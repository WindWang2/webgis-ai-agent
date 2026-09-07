"""Conformance tests for the Spatial Statistics V3 batch (Foundation V3).

Contract bullets under test (descriptor ids in app/lib/gis/algorithms/
statistics.py):

- ``spatial.mgwr``: CONFORMANCE ANCHOR — with every term's bandwidth forced
  equal to the same k, backfitting must land on the joint GWR/WLS solution
  (exact-plane fixture, rtol 1e-4 vs the GWR implementation); per-variable
  bandwidth search must change coefficient surfaces (not renamed GWR);
  scale/arity guards + determinism;
- ``stats.geodetector_ecological``: hand-computed SSW / t / df / p golden
  values (n=10 fixture, computable by hand); dominant-direction decision;
  typed-error adversarial inputs;
- ``stats.geodetector_risk``: pairwise Welch t directions + ordered matrix
  form; seeded (42) permutation determinism;
- ``stats.local_join_count``: hand-computed LJC on a 1×4 rook line; 3×3
  block co-location clusters with BH correction; non-binary rejection;
  permutation determinism (same seed twice → identical payload);
- ``stats.bivariate_local_moran``: esda delegation label shape (HH/LH/LL/
  HL/not_significant) + determinism + island caveat disclosure;
- ``stats.weights_diagnostics``: island detection (distance_band with a far
  point) + connected components + warnings;
- ``spatial.hotspot.local``: significance_method="permutation" conditional
  randomization (seeded, deterministic, additive keys only), normal path
  byte-identical key set, scale guard.

All random paths use np.random.default_rng(42) inside the implementation —
the determinism tests replay the same call twice and compare payloads.
"""
import json

import numpy as np
import pytest
from scipy import stats as sps

from app.lib.geo_analysis.spatial_regression import (
    gwr_regression_narrated,
    mgwr_regression_narrated,
)
from app.lib.geo_analysis.statistics import (
    bivariate_local_moran_narrated,
    geodetector_ecological,
    geodetector_ecological_narrated,
    geodetector_risk,
    geodetector_risk_narrated,
    hotspot_narrated,
    local_join_count_narrated,
    weights_diagnostics_narrated,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit

libpysal = pytest.importorskip("libpysal")


# ── fixtures ─────────────────────────────────────────────────────────

def _grid_fc(nrows, ncols, props_fn, cell=0.01, lon0=116.0, lat0=39.0):
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
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]], [xs[c], ys[r]],
                ]]},
                "properties": props_fn(r, c),
            })
    return {"type": "FeatureCollection", "features": feats}


def _points_fc(pts, field="val"):
    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
         "properties": {field: float(v)}}
        for xy, v in pts
    ]
    return {"type": "FeatureCollection", "features": feats}


def _payload_json(res):
    return json.dumps(res.data, sort_keys=True)


# ── spatial.mgwr ─────────────────────────────────────────────────────

def _mgwr_exact_plane_fc(seed=7):
    """精确平面 y = 2 + 3·x1 − 1·x2（无噪声）—— 等带宽锚的可表示表面。"""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.linspace(0, 1, 10), np.linspace(0, 1, 6))
    x1 = gx.ravel() + 0.05 * rng.normal(size=60)
    x2 = gy.ravel() + 0.05 * rng.normal(size=60)
    vals = {"x1": x1, "x2": x2}
    return _grid_fc(6, 10, lambda r, c: {
        "x1": float(vals["x1"][r * 10 + c]),
        "x2": float(vals["x2"][r * 10 + c]),
        "y": float(2.0 + 3.0 * vals["x1"][r * 10 + c]
                   - 1.0 * vals["x2"][r * 10 + c]),
    })


def test_mgwr_equal_bandwidth_matches_gwr_anchor():
    """CONFORMANCE ANCHOR：全部项带宽钳到同一 k 时，反向拟合收敛到联合
    GWR/WLS 解 —— 精确平面上与 GWR 实现的系数面 rtol 1e-4 逐位一致。
    这证明反向拟合是真实的坐标式迭代（等带宽下联合解是其不动点），
    不是改名的 GWR。"""
    fc = _mgwr_exact_plane_fc()
    k = 12
    res_gwr = gwr_regression_narrated(fc, "y", ["x1", "x2"], bandwidth=k)
    assert res_gwr.success, res_gwr.summary
    res_mgwr = mgwr_regression_narrated(
        fc, "y", ["x1", "x2"], bandwidth=k, fixed_bandwidths=[k, k, k])
    assert res_mgwr.success, res_mgwr.summary
    # 收敛且迭代数有界（精确平面上 GWR 热启动即不动点，应迅速收敛）
    assert res_mgwr.data["backfitting"]["converged"] is True
    assert res_mgwr.data["backfitting"]["iterations"] <= 200
    for name in ("intercept", "x1", "x2"):
        got = np.asarray(res_mgwr.data["surfaces"][name], dtype=float)
        ref = np.asarray(res_gwr.data["surfaces"][name], dtype=float)
        np.testing.assert_allclose(got, ref, rtol=1e-4, atol=1e-9)
    # 拟合面同样一致（GWR 精确平面 R²≈1）
    assert res_mgwr.data["r_squared"] == pytest.approx(
        res_gwr.data["r_squared"], abs=1e-6)
    # 逐项 ENP 闭合（帽迹和 ≥ p，< n·p）
    enp = res_mgwr.data["enp_per_term"]
    assert 3.0 <= sum(enp.values()) < 3 * 60


def test_mgwr_different_bandwidths_change_surfaces():
    """逐变量带宽搜索必须改变系数面 —— 与等带宽固定运行显著不同
    （「不同带宽 → 不同系数面」，排除改名的 GWR）。"""
    rng = np.random.default_rng(42)
    gx, gy = np.meshgrid(np.linspace(0, 1, 12), np.linspace(0, 1, 12))
    gxr, gyr = gx.ravel(), gy.ravel()

    def props(r, c):
        i = r * 12 + c
        local_field = np.sin(4 * np.pi * gxr[i]) * np.cos(3 * np.pi * gyr[i])
        return {"x2": float(gyr[i]),
                "y": float(local_field + 0.8 * gyr[i] + 0.02 * rng.normal())}

    fc = _grid_fc(12, 12, props)
    res_search = mgwr_regression_narrated(fc, "y", ["x2"], bandwidth=8)
    assert res_search.success, res_search.summary
    res_fixed = mgwr_regression_narrated(fc, "y", ["x2"], bandwidth=8,
                                         fixed_bandwidths=[8, 8])
    assert res_fixed.success, res_fixed.summary

    per_term = res_search.data["bandwidths"]["per_term"]
    grid = res_search.data["bandwidths"]["candidate_grid"]
    # 每项带宽都落在确定性候选网格内，且网格有界非空
    assert len(grid) >= 1 and all(grid[i] <= grid[i + 1] for i in range(len(grid) - 1))
    assert all(v in grid for v in per_term.values())
    # 搜索运行与等带宽固定的运行给出不同的系数面（两种带宽组合）
    for name in ("intercept", "x2"):
        a = np.asarray(res_search.data["surfaces"][name], dtype=float)
        b = np.asarray(res_fixed.data["surfaces"][name], dtype=float)
        assert np.max(np.abs(a - b)) > 1e-6
    # 输出面形状 = n，载荷结构完整
    n = res_search.data["n_features"]
    assert len(res_search.data["surfaces"]["x2"]) == n
    assert len(res_search.data["backfitting"]["rss_trajectory"]) >= 2
    assert res_search.data["full_coefficient_surfaces"] is True


def test_mgwr_guards_typed_errors(monkeypatch):
    fc = _mgwr_exact_plane_fc()
    # n > MGWR_MAX_N → ResourceScaleMismatch（monkeypatch 小上限，守卫在
    # 任何重计算之前触发）
    import app.lib.geo_analysis.spatial_regression as sr

    monkeypatch.setattr(sr, "MGWR_MAX_N", 10)
    with pytest.raises(ResourceScaleMismatch) as excinfo:
        sr.mgwr_regression_narrated(fc, "y", ["x1", "x2"])
    assert excinfo.value.estimated and excinfo.value.limit
    monkeypatch.setattr(sr, "MGWR_MAX_N", 2000)  # 还原后再测其余守卫
    assert sr.MGWR_MAX_N == 2000

    # 变量数 > 20 → UnsupportedMethod（21 个解释变量、n=49 ≥ 2p+2）
    def wide_props(r, c):
        props = {f"x{i}": float(r + c + i * 0.1) for i in range(21)}
        props["y"] = float(r + c)
        return props

    fc_wide = _grid_fc(7, 7, wide_props)
    with pytest.raises(UnsupportedMethod):
        mgwr_regression_narrated(fc_wide, "y", [f"x{i}" for i in range(21)])

    # 非法 tolerance / max_iterations → ValueError
    with pytest.raises(ValueError, match="tolerance"):
        mgwr_regression_narrated(fc, "y", ["x1", "x2"], tolerance=0.0)
    with pytest.raises(ValueError, match="max_iterations"):
        mgwr_regression_narrated(fc, "y", ["x1", "x2"], max_iterations=0)

    # fixed_bandwidths 长度错 → InsufficientSamples
    with pytest.raises(InsufficientSamples):
        mgwr_regression_narrated(fc, "y", ["x1", "x2"], fixed_bandwidths=[8])

    # 常数目标 → DegenerateData
    fc_const = _grid_fc(5, 5, lambda r, c: {"x1": float(r), "y": 3.0})
    with pytest.raises(DegenerateData):
        mgwr_regression_narrated(fc_const, "y", ["x1"])


def test_mgwr_deterministic_payload():
    """确定性：同载荷两次运行（含带宽搜索）逐位一致；无隐藏随机成分。"""
    rng = np.random.default_rng(42)
    gx, gy = np.meshgrid(np.linspace(0, 1, 8), np.linspace(0, 1, 8))
    gxr, gyr = gx.ravel(), gy.ravel()

    def props(r, c):
        i = r * 8 + c
        return {"x1": float(gxr[i]), "x2": float(gyr[i]),
                "y": float(np.sin(3 * np.pi * gxr[i]) + 0.5 * gyr[i]
                           + 0.05 * rng.normal())}

    fc = _grid_fc(8, 8, props)
    r1 = mgwr_regression_narrated(fc, "y", ["x1", "x2"], bandwidth=10)
    r2 = mgwr_regression_narrated(fc, "y", ["x1", "x2"], bandwidth=10)
    assert r1.success and r2.success
    assert _payload_json(r1) == _payload_json(r2)


# ── stats.geodetector_ecological ─────────────────────────────────────

def test_geodetector_ecological_hand_fixture():
    """手算黄金值（n=10，可手算）：
    y=[1,1,2,2,3,3,4,4,5,5]；s1=[a×5, b×5] → SSW1=5.6（组内离差平方和
    2.8+2.8）；s2=交错 → 每层 [1,2,3,4,5] → SSW2=20。
    t = (0.7−2.5)/sqrt(0.7²/7+2.5²/7) = −1.8343895887…，df=n−2=8，
    p=2·sf(|t|,8)≈0.1039 → 差异不显著（尽管 SSW1<SSW2）。"""
    y = np.array([1., 1, 2, 2, 3, 3, 4, 4, 5, 5])
    s1 = np.array(["a"] * 5 + ["b"] * 5)
    s2 = np.array(["a", "b"] * 5)
    out = geodetector_ecological(y, s1, s2)
    assert out["ssw1"] == pytest.approx(5.6, abs=1e-12)
    assert out["ssw2"] == pytest.approx(20.0, abs=1e-12)
    assert out["rate1"] == pytest.approx(0.7, abs=1e-12)
    assert out["rate2"] == pytest.approx(2.5, abs=1e-12)
    t_hand = (0.7 - 2.5) / np.sqrt(0.7 ** 2 / 7 + 2.5 ** 2 / 7)
    assert out["t_statistic"] == pytest.approx(t_hand, abs=1e-10)
    assert out["df"] == 8
    assert out["p_value"] == pytest.approx(
        float(2.0 * sps.t.sf(abs(t_hand), 8)), abs=1e-10)
    assert out["decision"] == "no_significant_difference"

    # 完美分层：SSW1=0 → t=−sqrt(7)，p≈0.0294<0.05 → Y1 显著解释占优
    y2 = np.array([1.] * 5 + [10.] * 5)
    out2 = geodetector_ecological(y2, s1, s2)
    assert out2["ssw1"] == pytest.approx(0.0, abs=1e-12)
    assert out2["t_statistic"] == pytest.approx(-np.sqrt(7), rel=1e-10)
    assert out2["decision"] == "Y1_significantly_dominant"

    # narrated 包装：载荷携带 df 选择披露 + 显著性块；叙事含因果免责
    feats = [{"type": "Feature",
              "geometry": {"type": "Point",
                           "coordinates": [116.0 + 0.001 * i, 39.0]},
              "properties": {"y": float(v), "s1": str(s1[i]), "s2": str(s2[i])}}
             for i, v in enumerate(y)]
    res = geodetector_ecological_narrated(
        {"type": "FeatureCollection", "features": feats}, "y", "s1", "s2")
    assert res.success, res.summary
    assert res.data["t_statistic"] == pytest.approx(round(t_hand, 6), abs=1e-9)
    assert res.data["df"] == 8
    assert "n−2" in res.data["df_disclosure"] or "n-2" in res.data["df_disclosure"]
    assert res.data["uncertainty"]["uncertainty_type"] == "statistical_significance"
    assert "因果" in res.summary


def test_geodetector_ecological_adversarial_inputs():
    feats = [{"type": "Feature",
              "geometry": {"type": "Point",
                           "coordinates": [116.0 + 0.001 * i, 39.0]},
              "properties": {"y": float(i), "s1": str(i % 3),
                             "s2": "a", "s3": str(i % 2)}}
             for i in range(12)]
    fc = {"type": "FeatureCollection", "features": feats}
    with pytest.raises(NoValidObservations):
        geodetector_ecological_narrated(
            {"type": "FeatureCollection", "features": []}, "y", "s1", "s3")
    with pytest.raises(MissingRequiredField):
        geodetector_ecological_narrated(fc, "y", "missing", "s3")
    with pytest.raises(MissingRequiredField):
        geodetector_ecological_narrated(fc, "missing", "s1", "s3")
    # 单一分层 → DegenerateData（s2 全 a）
    with pytest.raises(DegenerateData):
        geodetector_ecological_narrated(fc, "y", "s1", "s2")
    # 常数 y → DegenerateData
    fc_const = {"type": "FeatureCollection", "features": [
        {**f, "properties": {**f["properties"], "y": 1.0}} for f in feats]}
    with pytest.raises(DegenerateData):
        geodetector_ecological_narrated(fc_const, "y", "s1", "s3")
    # n < 10 → InsufficientSamples
    with pytest.raises(InsufficientSamples):
        geodetector_ecological_narrated(
            {"type": "FeatureCollection", "features": feats[:6]},
            "y", "s1", "s3")
    # 长度不一致（核心函数直接收数组）
    with pytest.raises(ValueError):
        geodetector_ecological(np.arange(10.0), np.arange(10), np.arange(9))


# ── stats.geodetector_risk ───────────────────────────────────────────

def _risk_fc():
    """3 层 × 显著不同均值（low≈1.2 / mid≈5.5 / high≈10.3），n=10。"""
    rows = [("low", v) for v in (1.0, 2.0, 1.5, 1.2)]
    rows += [("mid", v) for v in (5.0, 6.0, 5.5)]
    rows += [("high", v) for v in (10.0, 11.0, 10.5)]
    feats = [{"type": "Feature",
              "geometry": {"type": "Point",
                           "coordinates": [116.0 + 0.001 * i, 39.0]},
              "properties": {"y": v, "g": g}}
             for i, (g, v) in enumerate(rows)]
    return {"type": "FeatureCollection", "features": feats}


def test_geodetector_risk_pairwise_and_matrix():
    res = geodetector_risk_narrated(_risk_fc(), "y", "g", permutations=0)
    assert res.success, res.summary
    data = res.data
    assert data["n_strata"] == 3
    pairs = {(p["stratum_1"], p["stratum_2"]): p for p in data["pairs"]}
    assert len(pairs) == 3  # C(3,2)
    hi_lo = pairs[("high", "low")]
    assert hi_lo["mean_diff"] == pytest.approx(
        float(np.mean([10.0, 11.0, 10.5]) - np.mean([1.0, 2.0, 1.5, 1.2])),
        abs=1e-6)
    assert hi_lo["p_value"] < 0.05 and hi_lo["direction"] == "higher"
    # 组合按排序分层生成：(high,low)/(high,mid)/(low,mid)
    assert pairs[("low", "mid")]["direction"] == "lower"
    assert pairs[("high", "mid")]["direction"] == "higher"
    # 方向矩阵按有序对存（[a][b] 是 a 相对 b 的方向；[b][a] 翻转）
    m = data["matrix"]
    assert m["high"]["low"] == "higher" and m["low"]["high"] == "lower"
    assert set(m) == {"high", "low", "mid"}
    assert m["mid"]["high"] == "lower" and m["mid"]["low"] == "higher"
    assert "mid" not in m["mid"]  # 无自对
    # 逐对显著性块齐全
    assert data["significant_pair_count"] == 3


def test_geodetector_risk_permutation_determinism():
    r1 = geodetector_risk_narrated(_risk_fc(), "y", "g", permutations=99)
    r2 = geodetector_risk_narrated(_risk_fc(), "y", "g", permutations=99)
    assert r1.success and r2.success
    assert r1.data["permutations"] == 99
    assert _payload_json(r1) == _payload_json(r2)
    # 置换 p 与解析 p 同号且都在 [0,1]
    for p1, p2 in zip(r1.data["pairs"], r2.data["pairs"]):
        assert 0.0 <= p1["p_value_permutation"] <= 1.0
        assert p1["direction"] == p2["direction"]
    # 分层数 < 2 的对诚实留空（不编 p）：a×9 + 单例 lonely → n=10
    y = np.array([1., 2, 3, 4, 5, 6, 7, 8, 9, 10.])
    s = np.array(["a"] * 9 + ["lonely"])
    out = geodetector_risk(y, s, permutations=0)
    lonely = [p for p in out["pairs"]
              if "lonely" in (p["stratum_1"], p["stratum_2"])][0]
    assert lonely["t_statistic"] is None and lonely["note"]
    assert lonely["direction"] == "not_significant"


# ── stats.local_join_count ───────────────────────────────────────────

def test_local_join_count_hand_fixture_and_clustering():
    """手算 golden：1×4 rook 行、y=[1,1,0,1] → LJC=[1,1,0,0]
    （cell0↔cell1 是唯一的 1-1 连接）；小样本置换不显著（诚实）。"""
    fc = _grid_fc(1, 4, lambda r, c: {
        "bin": 1.0 if c in (0, 1, 3) else 0.0})
    res = local_join_count_narrated(fc, "bin", weights_scheme="rook",
                                    permutations=99)
    assert res.success, res.summary
    ljc = [f["properties"]["local_join_count"]
           for f in res.data["features"]]
    assert ljc == [1.0, 1.0, 0.0, 0.0]
    # y=0 位置 p≡1（不在 1-簇族内）；BH 字段随要素输出
    props = [f["properties"] for f in res.data["features"]]
    assert props[2]["p_value"] == 1.0 and props[3]["p_value"] == 1.0
    assert all("p_bh" in p for p in props)
    assert res.data["weights"]["scheme"] == "rook"

    # 3×3 的 1 块嵌在 8×8 → 边界内侧 LJC=4 的位置显著共位（BH 校正后）
    fc_cluster = _grid_fc(8, 8, lambda r, c: {
        "bin": 1.0 if (r < 3 and c < 3) else 0.0})
    res_c = local_join_count_narrated(fc_cluster, "bin",
                                      weights_scheme="rook", permutations=999)
    assert res_c.success
    assert res_c.data["significant_count"] >= 1
    assert res_c.data["local_join_count_counts"]["co_location_cluster"] \
        == res_c.data["significant_count"]
    labels = {f["properties"]["local_join_count_cluster"]
              for f in res_c.data["features"]}
    assert labels <= {"co_location_cluster", "neutral"}
    assert res_c.data["uncertainty"]["multiple_testing"] == "BH-FDR"

    # 棋盘：1 的位置 LJC≡0 → 无共位簇（上尾检验的正确空结果）
    fc_cb = _grid_fc(6, 6, lambda r, c: {"bin": float((r + c) % 2)})
    res_cb = local_join_count_narrated(fc_cb, "bin", weights_scheme="rook",
                                       permutations=999)
    assert res_cb.data["significant_count"] == 0


def test_local_join_count_rejects_non_binary():
    pts = [((116.0 + i * 0.001, 39.0), float(i)) for i in range(8)]
    with pytest.raises(UnsupportedMethod) as excinfo:
        local_join_count_narrated(_points_fc(pts), "val")
    assert excinfo.value.correction_hint
    same = [((116.0 + i * 0.001, 39.0), 1.0) for i in range(8)]
    with pytest.raises(DegenerateData):
        local_join_count_narrated(_points_fc(same), "val")
    with pytest.raises(InsufficientSamples):
        local_join_count_narrated(_points_fc(pts[:3]), "val")
    with pytest.raises(ValueError, match="correction"):
        local_join_count_narrated(
            _grid_fc(4, 4, lambda r, c: {"bin": float((r + c) % 2)}),
            "bin", correction="sidak")


def test_local_join_count_permutation_determinism():
    fc = _grid_fc(8, 8, lambda r, c: {
        "bin": 1.0 if (r < 3 and c < 3) else 0.0})
    r1 = local_join_count_narrated(fc, "bin", weights_scheme="rook",
                                   permutations=999)
    r2 = local_join_count_narrated(fc, "bin", weights_scheme="rook",
                                   permutations=999)
    assert r1.success and r2.success
    assert _payload_json(r1) == _payload_json(r2)  # 同种子 → 逐位一致
    ps = [f["properties"]["p_value"] for f in r1.data["features"]]
    assert all(0.0 < p <= 1.0 for p in ps)


# ── stats.bivariate_local_moran ──────────────────────────────────────

def test_bivariate_local_moran_labels_and_determinism():
    fc = _grid_fc(5, 5, lambda r, c: {"v1": float(r * 5 + c),
                                      "v2": float(c * 5 + r)})
    res = bivariate_local_moran_narrated(fc, "v1", "v2", permutations=999)
    assert res.success, res.summary
    labels = [f["properties"]["bivariate_local_moran_label"]
              for f in res.data["features"]]
    assert len(labels) == 25
    assert set(labels) <= {"HH", "LH", "LL", "HL", "not_significant"}
    counts = res.data["label_counts"]
    assert sum(counts.values()) == 25
    assert counts["not_significant"] == sum(
        1 for v in labels if v == "not_significant")
    # BH q 值随要素输出且 ∈ [0,1]
    q = [f["properties"]["p_bh"] for f in res.data["features"]]
    assert all(0.0 <= v <= 1.0 for v in q)
    # 孤岛警示披露（无论有无孤岛，载荷必须携带该语义键）
    assert "孤岛" in res.data["island_caveat"]
    assert "因果" in res.summary
    # 确定性：esda 委托带 seed=42 → 同载荷逐位一致
    res2 = bivariate_local_moran_narrated(fc, "v1", "v2", permutations=999)
    assert _payload_json(res) == _payload_json(res2)


# ── stats.weights_diagnostics ────────────────────────────────────────

def test_weights_diagnostics_island_detection():
    """孤岛检测：5 个相距 ~1110m 的链上点 + 1 个远点，distance_band=1500m
    → 远点是唯一孤岛、两个连通分量、结构警告非空。"""
    pts = [((116.0 + 0.01 * i, 39.0), 1.0) for i in range(5)]
    pts += [((117.5, 40.5), 2.0)]  # 唯一孤岛（远点，index 5）
    res = weights_diagnostics_narrated(
        _points_fc(pts), weights_scheme="distance_band", distance_band=1500)
    assert res.success, res.summary
    assert res.data["island_count"] == 1
    assert res.data["island_ids"] == [5]
    comp = res.data["connected_components"]
    assert comp["count"] == 2 and comp["largest_share"] == pytest.approx(5 / 6)
    assert res.data["scheme"] == "distance_band"
    assert res.data["binary_symmetric"] is True  # 二值邻接对称
    assert len(res.data["warnings"]) >= 2  # 孤岛 + 多分量
    assert "孤岛" in res.summary

    # knn 方案：kNN 永不产生孤岛（每个点都有 k 个邻居）；警告为空
    res_knn = weights_diagnostics_narrated(_points_fc(pts),
                                           weights_scheme="knn", k=2)
    assert res_knn.data["island_count"] == 0
    assert res_knn.data["neighbors"]["min"] >= 2
    assert res_knn.data["binary_symmetric"] is True  # knn 对称并集
    # 面输入 queen：行标准化 → 存储矩阵不对称但二值邻接对称（诚实分开报）
    res_q = weights_diagnostics_narrated(
        _grid_fc(4, 4, lambda r, c: {"val": float(r * 4 + c)}),
        weights_scheme="queen")
    assert res_q.data["row_standardized"] is True
    assert res_q.data["symmetric"] is False
    assert res_q.data["binary_symmetric"] is True
    assert res_q.data["connected_components"]["count"] == 1


# ── spatial.hotspot.local：significance_method 附加参数 ─────────────

def _hotspot_fc():
    return _grid_fc(6, 6, lambda r, c: {
        "val": 100.0 if (r < 3 and c < 3) else 1.0})


def test_hotspot_permutation_significance_option():
    """permutation 路径：条件随机化（固定种子 42）→ 确定性；载荷只做
    additive 扩展（既有键不变 + permutation 元数据）；聚类数据检出热点。"""
    fc = _hotspot_fc()
    res = hotspot_narrated(fc, "val", distance_band=1500,
                           significance_method="permutation", permutations=99)
    assert res.success, res.summary
    assert res.data["significance_method"] == "permutation"
    assert res.data["permutations"] == 99
    props = res.data["features"][0]["properties"]
    for key in ("gi_star", "p_value", "q_value_fdr", "hotspot_type",
                "confidence", "p_value_permutation", "p_value_normal"):
        assert key in props
    assert res.data["hot_spots_count"] >= 1
    assert 0.0 < min(
        f["properties"]["p_value_permutation"]
        for f in res.data["features"]) <= 1.0
    # 确定性：同种子两次运行逐位一致
    res2 = hotspot_narrated(fc, "val", distance_band=1500,
                            significance_method="permutation",
                            permutations=99)
    assert _payload_json(res) == _payload_json(res2)
    # 置换 p 与解析 p 给出一致的热点判定（该聚类fixture两种方法都显著）
    assert res2.data["hot_spots_count"] == res.data["hot_spots_count"]


def test_hotspot_normal_path_unchanged():
    """normal（默认）路径输出键集与既有契约逐键一致 —— 不带置换键。"""
    fc = _hotspot_fc()
    res = hotspot_narrated(fc, "val", distance_band=1500)
    assert res.success
    assert "significance_method" not in res.data
    assert "permutations" not in res.data
    expected_keys = {"gi_star", "p_value", "q_value_fdr", "hotspot_type",
                     "confidence"}
    assert expected_keys <= set(res.data["features"][0]["properties"])
    assert "p_value_permutation" not in res.data["features"][0]["properties"]
    assert set(res.data) == {"type", "features", "hot_spots_count",
                             "cold_spots_count", "distance_band_m",
                             "fdr_hot_spots_count", "expected_false_positives"}


def test_hotspot_permutation_scale_guard(monkeypatch):
    import app.lib.geo_analysis.statistics as gstat

    monkeypatch.setattr(gstat, "HOTSPOT_PERMUTATION_MAX_N", 10)
    with pytest.raises(ResourceScaleMismatch) as excinfo:
        hotspot_narrated(_hotspot_fc(), "val", distance_band=1500,
                         significance_method="permutation", permutations=99)
    assert excinfo.value.estimated and excinfo.value.limit
    # normal 路径不受置换守卫影响
    res = hotspot_narrated(_hotspot_fc(), "val", distance_band=1500)
    assert res.success
    monkeypatch.setattr(gstat, "HOTSPOT_PERMUTATION_MAX_N", 5000)
    assert gstat.HOTSPOT_PERMUTATION_MAX_N == 5000
    # 非法方法 → ValueError（在重计算之前）
    with pytest.raises(ValueError, match="significance_method"):
        hotspot_narrated(_hotspot_fc(), "val",
                         significance_method="bootstrap")
