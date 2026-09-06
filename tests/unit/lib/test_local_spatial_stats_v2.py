"""Conformance tests for the V2 local-spatial-statistics family (A1).

Contract bullets under test (descriptor ids in app/lib/gis/algorithms/
statistics.py):

- ``stats.local_geary``: 2×2 rook checkerboard golden C_i; esda.Geary_Local
  conformance to 1e-8 (identical Queen weights); clustered-data
  similar_high/similar_low classification with BH correction; typed-error
  adversarial inputs;
- ``stats.join_count``: 2×2 checkerboard golden (n_BB=n_WW=0, n_BW=J);
  non-binary field → UnsupportedMethod; 8×8 analytic + permutation
  significance;
- ``stats.bivariate_moran``: x=y equals univariate Moran's I (property);
  conformance vs esda.Moran_BV;
- ``stats.geodetector``: q=1 on perfectly stratified data (property);
  Wang-2010 interaction classification (all five classes, exact); typed
  adversarial inputs;
- ``stats.weights_sensitivity``: rook/queen skipped-with-disclosure for
  points; stable clustering verdict across schemes on clustered data;
  SensitivityEnvelope block;
- determinism: fixed-seed (42) permutation payloads identical across runs,
  p ∈ [0,1];
- tool layer: registry parity for the new contracts, scientific evidence
  attached, apply_contract defaults applied, moran_i backend diagnostic.
"""
import json

import numpy as np
import pytest

from app.lib.geo_analysis.statistics import (
    _classify_interaction,
    bivariate_moran_narrated,
    geodetector_narrated,
    join_count_narrated,
    local_geary_narrated,
    moran_i_narrated,
    weights_sensitivity_narrated,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit

esda = pytest.importorskip("esda")
libpysal = pytest.importorskip("libpysal")


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
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]], [xs[c], ys[r]],
                ]]},
                "properties": {field: float(val_fn(r, c))},
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


def _clustered_points():
    """Two tight high-value clusters + two tight low-value clusters."""
    rng = np.random.default_rng(5)
    pts = []
    for cx, cy, val in [(116.39, 39.90, 100.0), (116.42, 39.92, 100.0),
                        (116.39, 39.95, 1.0), (116.42, 39.95, 1.0)]:
        for _ in range(12):
            pts.append(((cx + rng.normal(0, 3e-4), cy + rng.normal(0, 3e-4)),
                        float(val)))
    return pts


def _utm_gdf_and_values(fc, field="val"):
    from app.lib.geo_processor.core import to_utm_gdf

    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    return gdf, gdf[field].to_numpy(dtype=float)


# ── stats.local_geary ────────────────────────────────────────────────

def test_local_geary_checkerboard_golden():
    """2×2 rook 棋盘：每格 2 个 rook 邻居全为异色 → C_i = (4+4)/2 = 4 精确。

    小样本诚实性：±1 格子上置换分布退化（n=4 只有 6 种排布），p 无法
    显著 —— 实现如实报告 p>0.05、全部 neutral，而不是编造显著性。
    """
    fc = _grid_fc(2, 2, lambda r, c: float((r + c) % 2))
    res = local_geary_narrated(fc, "val", weights_scheme="rook",
                               permutations=99)
    assert res.success, res.summary
    c_values = [f["properties"]["local_geary_c"] for f in res.data["features"]]
    assert c_values == pytest.approx([4.0, 4.0, 4.0, 4.0])
    assert res.data["weights"]["scheme"] == "rook"
    assert all(f["properties"]["p_value"] > 0.05
               for f in res.data["features"])
    assert res.data["local_geary_counts"]["neutral"] == 4
    # BH 校正字段随要素输出
    assert all("p_bh" in f["properties"] for f in res.data["features"])


def test_local_geary_matches_esda():
    """与 esda.Geary_Local（同 Queen 行标准化权重）一致到 1e-8。"""
    fc = _grid_fc(5, 4, lambda r, c: r * 4 + c)
    gdf, values = _utm_gdf_and_values(fc)
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False)
    ref = esda.Geary_Local(connectivity=w)
    ref.fit(values)

    res = local_geary_narrated(fc, "val", weights_scheme="queen",
                               permutations=99)
    assert res.success, res.summary
    mine = np.array([f["properties"]["local_geary_c"]
                     for f in res.data["features"]])
    assert mine == pytest.approx(np.asarray(ref.localG), abs=1e-8)


def test_local_geary_clustered_classification_and_correction():
    pts = _clustered_points()
    res = local_geary_narrated(_points_fc(pts), "val", permutations=499)
    assert res.success, res.summary
    counts = res.data["local_geary_counts"]
    # 48 个点聚成两高两低团 → 相似聚集显著（BH 校正后）
    assert counts["similar_high"] == 24
    assert counts["similar_low"] == 24
    assert res.data["significant_count"] == 48
    assert res.data["correction"] == "bh"  # 契约默认
    assert res.data["expected_false_positives"] == round(0.05 * 48, 1)
    labels = [f["properties"]["local_geary_cluster"]
              for f in res.data["features"]]
    assert set(labels) <= {"similar_high", "similar_low", "dissimilar",
                           "neutral"}
    unc = res.data["uncertainty"]
    assert unc["uncertainty_type"] == "statistical_significance"
    assert unc["multiple_testing"] == "BH-FDR"


def test_local_geary_adversarial_inputs():
    pts = _clustered_points()
    with pytest.raises(NoValidObservations):
        local_geary_narrated({"type": "FeatureCollection", "features": []},
                             "val")
    with pytest.raises(MissingRequiredField):
        local_geary_narrated(_points_fc(pts), "wrong_field")
    const = [((116.0 + i * 0.001, 39.0), 7.0) for i in range(8)]
    with pytest.raises(DegenerateData):
        local_geary_narrated(_points_fc(const), "val")
    with pytest.raises(InsufficientSamples):
        local_geary_narrated(_points_fc(pts[:2]), "val")
    with pytest.raises(ValueError, match="correction"):
        local_geary_narrated(_points_fc(pts), "val", correction="sidak")


# ── stats.join_count ─────────────────────────────────────────────────

def test_join_count_checkerboard_golden():
    """2×2 rook 棋盘：4 个连接全是异类 → n_BB=n_WW=0、n_BW=4（golden）。"""
    fc = _grid_fc(2, 2, lambda r, c: float((r + c) % 2))
    res = join_count_narrated(fc, "val", weights_scheme="rook")
    assert res.success, res.summary
    assert res.data["join_counts"] == {"n_bb": 0.0, "n_bw": 4.0, "n_ww": 0.0}
    # free-sampling 期望：E[n_BW] = J·2ml/(n(n-1)) = 4·16/12 = 8/3
    assert res.data["expected"]["n_bw"] == pytest.approx(8.0 / 3.0)
    assert res.data["joins"] == 4.0
    assert res.data["n_black"] == 2.0 and res.data["n_white"] == 2.0

    # 8×8 棋盘：112 个 rook 连接全为 BW → 解析 z 极显著 + 置换复核
    fc8 = _grid_fc(8, 8, lambda r, c: float((r + c) % 2))
    res8 = join_count_narrated(fc8, "val", weights_scheme="rook",
                               permutations=99)
    assert res8.success
    assert res8.data["join_counts"]["n_bb"] == 0.0
    assert res8.data["join_counts"]["n_bw"] == 112.0
    assert res8.data["z"]["n_bw"] > 5.0
    assert res8.data["p_value_analytic"]["n_bw"] < 1e-6
    assert res8.data["p_value_permutation"]["n_bw"] == pytest.approx(0.01)
    assert res8.data["pattern"] == "negative_spatial_autocorrelation"


def test_join_count_rejects_non_binary():
    pts = [((116.0 + i * 0.001, 39.0), float(i)) for i in range(8)]
    # 连续值字段 → UnsupportedMethod（带 correction_hint），不是静默计算
    with pytest.raises(UnsupportedMethod) as excinfo:
        join_count_narrated(_points_fc(pts), "val")
    assert excinfo.value.correction_hint
    # 单一取值（全 0）→ DegenerateData
    same = [((116.0 + i * 0.001, 39.0), 0.0) for i in range(8)]
    with pytest.raises(DegenerateData):
        join_count_narrated(_points_fc(same), "val")
    # n < 4 → InsufficientSamples（free-sampling 方差需要 n-3）
    with pytest.raises(InsufficientSamples):
        join_count_narrated(_points_fc(_clustered_points()[:3]), "val")


# ── stats.bivariate_moran ────────────────────────────────────────────

def test_bivariate_moran_equals_univariate_when_x_is_y():
    """属性：x=y 时双变量 Moran 与单变量 Moran 严格一致。"""
    fc = _grid_fc(6, 6, lambda r, c: r * 6 + c)
    uni = moran_i_narrated(fc, "val", weights_scheme="queen")
    bi = bivariate_moran_narrated(fc, "val", "val", weights_scheme="queen")
    assert uni.success and bi.success
    assert bi.data["bivariate_morans_i"] == \
        pytest.approx(uni.data["moran_i"], abs=1e-12)
    # 共位相关 ≠ 因果：叙事必须披露该限制
    assert "因果" in bi.summary


def test_bivariate_moran_matches_esda():
    fc = _grid_fc(5, 4, lambda r, c: r * 4 + c)
    # 第二字段与 val 不同（确定性变换）
    for i, feat in enumerate(fc["features"]):
        feat["properties"]["val2"] = (feat["properties"]["val"] * 0.5
                                      + (i % 3))
    gdf, _ = _utm_gdf_and_values(fc)
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False)
    w.transform = "r"
    ref = esda.Moran_BV(gdf["val"].to_numpy(float),
                        gdf["val2"].to_numpy(float), w)
    res = bivariate_moran_narrated(fc, "val", "val2", weights_scheme="queen",
                                   permutations=99)
    assert res.success, res.summary
    assert res.data["bivariate_morans_i"] == \
        pytest.approx(ref.I, abs=1e-9)
    assert 0.0 <= res.data["p_value"] <= 1.0


# ── stats.geodetector ────────────────────────────────────────────────

def _stratified_fc(rows=6, cols=6):
    """每行一个分层、行内取值恒定 → q=1 的完美分层。"""
    feats = []
    for r in range(rows):
        for c in range(cols):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [116.0 + 0.001 * c,
                                             39.0 + 0.001 * r]},
                "properties": {"y": float(r), "strata": float(r)},
            })
    return {"type": "FeatureCollection", "features": feats}


def _crossed_strata_fc(means, sizes, noise, seed):
    """2×2 交叉分层设计的点要素集（可控制 q1/q2/q12 的相对位置）。"""
    rng = np.random.default_rng(seed)
    feats = []
    for (a, b), mean in means.items():
        for _ in range(sizes[(a, b)]):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [116.0 + 0.001 * (len(feats) % 20),
                                             39.0 + 0.001 * (len(feats) // 20)]},
                "properties": {"y": float(mean + rng.normal(0, noise)),
                               "s1": a, "s2": b},
            })
    return {"type": "FeatureCollection", "features": feats}


def test_geodetector_q_and_interaction_classes():
    # 性质：完美分层 → q=1 精确（F=∞，解析 p=0；置换 p 落在分辨率下限）
    res = geodetector_narrated(_stratified_fc(), "y", "strata",
                               permutations=99)
    assert res.success, res.summary
    assert res.data["factor"]["q"] == 1.0
    assert res.data["factor"]["p_value_f"] == 0.0
    assert res.data["factor"]["p_value_permutation"] == pytest.approx(0.01)
    assert res.data["factor"]["n_strata"] == 6
    # MC 摘要块（置换分位）随置换输出
    mc = [u for u in res.data["uncertainty"]
          if u["uncertainty_type"] == "monte_carlo_summary"]
    assert mc and mc[0]["draws"] == 99 and mc[0]["seed"] == 42

    # Wang 2010 交互分类逻辑（穷举五类，精确）
    assert _classify_interaction(0.2, 0.3, 0.5) == "independent"
    assert _classify_interaction(0.2, 0.3, 0.6) == "nonlinear_enhanced"
    assert _classify_interaction(0.2, 0.3, 0.45) == "bilinear_enhanced"
    assert _classify_interaction(0.2, 0.3, 0.1) == "nonlinear_weakened"
    assert _classify_interaction(0.2, 0.3, 0.25) == "uni_nonlinear"

    # 积分（⑤ crossover：边际无解释力、交集近完美 → nonlinear_enhanced）
    fc_x = _crossed_strata_fc(
        {(0, 0): 0.0, (0, 1): 5.0, (1, 0): 5.0, (1, 1): 0.0},
        {(0, 0): 20, (0, 1): 20, (1, 0): 20, (1, 1): 20}, 0.5, seed=11)
    res_x = geodetector_narrated(fc_x, "y", "s1", interaction_field="s2",
                                 permutations=0)
    inter = res_x.data["interaction"]
    assert inter["interaction_class"] == "nonlinear_enhanced"
    assert inter["q_1_and_2"] > inter["q_1"] + inter["q_2"]

    # 积分（④ s2 是 s1 四层两两合并的粗化 → bilinear_enhanced）
    fc_b = _crossed_strata_fc(
        {(0, 0): 0.0, (0, 1): 0.5, (1, 0): 2.0, (1, 1): 2.3},
        {(0, 0): 20, (0, 1): 6, (1, 0): 6, (1, 1): 20}, 0.3, seed=13)
    res_b = geodetector_narrated(fc_b, "y", "s1", interaction_field="s2",
                                 permutations=0)
    inter_b = res_b.data["interaction"]
    assert inter_b["interaction_class"] == "bilinear_enhanced"
    assert max(inter_b["q_1"], inter_b["q_2"]) < inter_b["q_1_and_2"] \
        < inter_b["q_1"] + inter_b["q_2"]


def test_geodetector_adversarial_inputs():
    with pytest.raises(MissingRequiredField):
        geodetector_narrated(_stratified_fc(), "y", "nope")
    with pytest.raises(MissingRequiredField):
        geodetector_narrated(_stratified_fc(), "nope", "strata")
    # 单一分层 → DegenerateData
    fc_one = _stratified_fc()
    for feat in fc_one["features"]:
        feat["properties"]["strata"] = 1.0
    with pytest.raises(DegenerateData):
        geodetector_narrated(fc_one, "y", "strata")
    # 高基数数值分层且未给 bins → UnsupportedMethod（防逐观测分层）
    fc_fine = _stratified_fc()
    for i, feat in enumerate(fc_fine["features"]):
        feat["properties"]["strata"] = float(i)  # 36 唯一值
    with pytest.raises(UnsupportedMethod):
        geodetector_narrated(fc_fine, "y", "strata")
    # 但给 bins 后可用
    res = geodetector_narrated(fc_fine, "y", "strata", bins=4, permutations=0)
    assert res.success
    assert res.data["factor"]["n_strata"] == 4


# ── stats.weights_sensitivity ────────────────────────────────────────

def test_weights_sensitivity_stability_and_disclosure():
    # 点输入：queen/rook 无面邻接 → 如实跳过并披露；knn/distance_band 稳定
    res = weights_sensitivity_narrated(_points_fc(_clustered_points()),
                                       "val", permutations=99)
    assert res.success, res.summary
    by_scheme = {s["scheme"]: s for s in res.data["schemes"]}
    assert by_scheme["queen"]["status"] == "skipped"
    assert by_scheme["queen"]["reason"]
    assert by_scheme["rook"]["status"] == "skipped"
    for scheme in ("knn", "distance_band"):
        assert by_scheme[scheme]["status"] == "ok"
        assert by_scheme[scheme]["verdict"] == "clustering"
        assert by_scheme[scheme]["p_value"] < 0.05
    assert res.data["stable"] is True
    assert res.data["rank_stability"] == 1.0
    assert res.data["moran_i_range"][0] <= res.data["moran_i_range"][1]

    # 面输入：四方案全部可用且对强梯度一致判 clustering
    res_poly = weights_sensitivity_narrated(
        _grid_fc(6, 6, lambda r, c: r * 6 + c), "val", permutations=99)
    assert res_poly.success
    assert len(res_poly.data["schemes"]) == 4
    assert all(s["verdict"] == "clustering" for s in res_poly.data["schemes"])
    assert res_poly.data["rank_by_abs_i"]

    # 不确定性块：sensitivity_envelope + statistical_significance
    unc_types = {u["uncertainty_type"] for u in res.data["uncertainty"]}
    assert unc_types == {"sensitivity_envelope", "statistical_significance"}


# ── 确定性 / p 值界限 ────────────────────────────────────────────────

def test_local_family_permutation_determinism():
    """固定种子 42：同一载荷逐位可复现；p ∈ [0,1]。"""
    fc = _points_fc(_clustered_points())
    r1 = local_geary_narrated(fc, "val", permutations=99)
    r2 = local_geary_narrated(fc, "val", permutations=99)
    assert json.dumps(r1.data, sort_keys=True) == \
        json.dumps(r2.data, sort_keys=True)
    assert all(0.0 <= f["properties"]["p_value"] <= 1.0
               for f in r1.data["features"])

    grid = _grid_fc(6, 6, lambda r, c: float((r + c) % 2))
    j1 = join_count_narrated(grid, "val", weights_scheme="rook",
                             permutations=99)
    j2 = join_count_narrated(grid, "val", weights_scheme="rook",
                             permutations=99)
    assert json.dumps(j1.data, sort_keys=True) == \
        json.dumps(j2.data, sort_keys=True)

    bi1 = bivariate_moran_narrated(grid, "val", "val", permutations=99)
    bi2 = bivariate_moran_narrated(grid, "val", "val", permutations=99)
    assert json.dumps(bi1.data, sort_keys=True) == \
        json.dumps(bi2.data, sort_keys=True)
    assert 0.0 <= bi1.data["p_value"] <= 1.0


# ── 工具层（registry parity / evidence / 契约默认值）────────────────

async def test_tool_registry_parity_and_evidence():
    """契约必填参数 ⊆ 工具 schema；载荷挂 scientific_evidence；默认值生效。"""
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    assert validate_algorithm_tool_parameter_parity() == []

    registry = ToolRegistry()
    init_tools(registry)
    tool_names = {
        "local_geary", "join_count", "bivariate_moran", "geodetector",
        "ols_regression", "sar_ml_regression", "sem_ml_regression",
        "slx_regression", "gwr_regression", "weights_sensitivity",
    }
    schemas = {s["function"]["name"]: s["function"].get("parameters") or {}
               for s in registry.get_schemas()}
    assert tool_names <= set(schemas)
    for name in tool_names:
        assert "geojson" in schemas[name]["properties"]

    fc = _points_fc(_clustered_points())

    # local_geary：evidence 挂载 + apply_contract 默认值（correction=bh）
    payload = await registry.dispatch(
        "local_geary", {"geojson": fc, "value_field": "val"})
    assert payload["success"] is True, payload.get("summary")
    ev = payload["scientific_evidence"]
    assert ev["algorithm"] == "stats.local_geary"
    assert ev["parameters_applied"]["correction"] == "bh"
    assert ev["parameters_applied"]["permutations"] == 99
    assert ev["reproducibility"]["random_seed_policy"] == "fixed_seed"

    # ols_regression：evidence + 置换元数据透传
    grid_feats = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [116.0 + 0.001 * c, 39.0 + 0.001 * r]},
         "properties": {"val": float(r * 6 + c), "x1": float(c)}}
        for r in range(6) for c in range(6)
    ]
    grid = {"type": "FeatureCollection", "features": grid_feats}
    payload_ols = await registry.dispatch(
        "ols_regression", {"geojson": grid, "target_field": "val",
                           "explanatory_fields": "x1"})
    assert payload_ols["success"] is True, payload_ols.get("summary")
    ev_ols = payload_ols["scientific_evidence"]
    assert ev_ols["algorithm"] == "spatial.ols_regression"
    assert ev_ols["parameters_applied"]["explanatory_fields"] == "x1"

    # moran_i（既有工具，additive）：backend_selection 诊断进证据块
    payload_moran = await registry.dispatch(
        "moran_i", {"geojson": fc, "value_field": "val"})
    assert payload_moran["success"] is True, payload_moran.get("summary")
    diags = payload_moran["scientific_evidence"]["diagnostics"]
    assert any(d["name"] == "backend_selection" for d in diags)
