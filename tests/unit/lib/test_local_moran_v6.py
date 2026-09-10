"""Conformance tests for ``stats.local_moran`` (univariate LISA, V6).

Contract bullets under test (descriptor in app/lib/gis/algorithms/
statistics.py). Native numerics — textbook closed-form anchors first,
esda reference conformance where the dependency is installed:

- ``stats.local_moran``: 3×3 rook checkerboard golden I_i = −(n−1)/n for
  every cell; esda.Moran_Local statistic conformance to 1e-8 (identical
  Queen row-standardized weights; skipped when esda is absent);
  global decomposition Σ I_i·n/(S₀(n−1)) == global Moran's I;
  clustered fixture HH/LL classification with BH correction;
  islands disclosed (I_i=0, p=1, q=0, island_count);
- typed scientific errors for degenerate/empty/missing-field inputs;
- determinism: fixed-seed (42) payloads identical across runs.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.statistics import (
    local_moran_narrated,
    moran_i_narrated,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
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
                        (116.35, 39.86, 1.0), (116.45, 39.87, 1.0)]:
        for _ in range(12):
            pts.append(((cx + rng.normal(0, 0.0015),
                         cy + rng.normal(0, 0.0015)), val))
    return pts


# ── golden anchors（无 esda 依赖）─────────────────────────────────────

def test_local_moran_rook_checkerboard_golden():
    """3×3 rook 棋盘：每个格子的 rook 邻居全为异色 → lag_i=−z_i →
    I_i = −(n−1)/n = −8/9（golden，闭合式）。"""
    fc = _grid_fc(3, 3, lambda r, c: (r + c) % 2)
    res = local_moran_narrated(fc, "val", weights_scheme="rook",
                               permutations=199)
    assert res.success, res.summary
    i_vals = [f["properties"]["local_moran_i"] for f in res.data["features"]]
    assert i_vals == pytest.approx([-8.0 / 9.0] * 9, abs=1e-9)
    # 棋盘的全局负自相关极值：global decomposition 有限且为负
    assert res.data["global_moran_from_local"] < 0


def test_local_moran_global_decomposition():
    """Σ I_i·n/(S₀(n−1)) == 全局 Moran's I（跨实现一致性锚）。"""
    fc = _grid_fc(6, 5, lambda r, c: 3.0 + np.sin(r * 0.9) + np.cos(c * 0.7))
    local = local_moran_narrated(fc, "val", weights_scheme="queen",
                                 permutations=99)
    glob = moran_i_narrated(fc, "val", weights_scheme="queen")
    # 10 位小数载荷舍入 → 1e-8 容差
    assert local.data["global_moran_from_local"] == pytest.approx(
        glob.data["moran_i"], abs=1e-8)


def test_local_moran_clustered_classification():
    """两高两低点团 → HH/LL 对称聚集、BH 校正后显著；不确定性块。"""
    res = local_moran_narrated(_points_fc(_clustered_points()), "val",
                               permutations=499)
    assert res.success, res.summary
    counts = res.data["lisa_counts"]
    assert counts["high_high"] == counts["low_low"]
    assert counts["high_high"] >= 12
    assert res.data["significant_count"] >= 24
    assert res.data["correction"] == "bh"  # 契约默认
    assert res.data["expected_false_positives"] == pytest.approx(
        round(0.05 * res.data["n_features"], 1))
    labels = {f["properties"]["lisa_cluster"]
              for f in res.data["features"]}
    assert labels <= {"high_high", "low_high", "low_low", "high_low",
                      "neutral"}
    q_vals = [f["properties"]["lisa_q"] for f in res.data["features"]]
    assert set(q_vals) <= {0, 1, 2, 3, 4}
    unc = res.data["uncertainty"]
    assert unc["uncertainty_type"] == "statistical_significance"
    assert unc["multiple_testing"] == "BH-FDR"
    assert 0.0 <= unc["statistic_value"] <= 1.0


def test_local_moran_islands_disclosed():
    """distance_band 阈值隔离孤点 → I_i=0、p=1、q=0、island_count 披露。"""
    pts = [
        ((116.0000, 39.0000), 1.0), ((116.0010, 39.0000), 2.0),
        ((116.0020, 39.0000), 3.0),   # 紧密三连（~111m 间距）
        ((116.1000, 39.0000), 9.0),   # ~10km 外的孤点
    ]
    res = local_moran_narrated(_points_fc(pts), "val",
                               weights_scheme="distance_band",
                               distance_band=500, permutations=99)
    assert res.success, res.summary
    assert res.data["island_count"] == 1
    feats = res.data["features"]
    islands = [f for f in feats
               if f["properties"]["local_moran_i"] == 0.0]
    assert len(islands) == 1
    assert islands[0]["properties"]["p_value"] == 1.0
    assert islands[0]["properties"]["lisa_q"] == 0
    assert islands[0]["properties"]["lisa_cluster"] == "neutral"


def test_local_moran_determinism():
    fc = _grid_fc(5, 5, lambda r, c: float(np.sin(r * 1.1) * np.cos(c * 0.6)))
    a = local_moran_narrated(fc, "val", permutations=99)
    b = local_moran_narrated(fc, "val", permutations=99)
    assert a.data == b.data
    p_vals = [f["properties"]["p_value"] for f in a.data["features"]]
    assert all(0.0 <= p <= 1.0 for p in p_vals)


def test_local_moran_adversarial_inputs():
    pts = _clustered_points()
    with pytest.raises(NoValidObservations):
        local_moran_narrated({"type": "FeatureCollection", "features": []},
                             "val")
    with pytest.raises(MissingRequiredField):
        local_moran_narrated(_points_fc(pts), "wrong_field")
    const = [((116.0 + i * 0.001, 39.0), 7.0) for i in range(8)]
    with pytest.raises(DegenerateData):
        local_moran_narrated(_points_fc(const), "val")
    with pytest.raises(InsufficientSamples):
        local_moran_narrated(_points_fc(pts[:2]), "val")
    with pytest.raises(ValueError, match="correction"):
        local_moran_narrated(_points_fc(pts), "val", correction="sidak")
    with pytest.raises(DegenerateData, match="island"):
        # 全部孤岛：距离阈值小于最小点间距 → s0=0
        local_moran_narrated(_points_fc(pts), "val",
                             weights_scheme="distance_band",
                             distance_band=1.0)


# ── esda 参照一致性（依赖存在时）──────────────────────────────────────

def test_local_moran_matches_esda():
    """统计量与 esda.Moran_Local（同 Queen 行标准化权重）一致到 1e-8；
    象限 q 非零滞后逐位一致；双侧 p 向量与 esda two-sided 高度相关
    （esdd 的百分位对称区间与本实现的零点对称计数是同一零假设的两种
    双侧约定，MC 流不同 → 相关性对账而非逐位对账）。"""
    esda = pytest.importorskip("esda")
    libpysal = pytest.importorskip("libpysal")

    from app.lib.geo_processor.core import to_utm_gdf

    fc = _grid_fc(6, 6, lambda r, c: float(r * 6 + c))
    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    values = gdf["val"].to_numpy(float)
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False,
                                              silence_warnings=True)
    w.transform = "r"
    ref = esda.Moran_Local(values, w, permutations=199, seed=42,
                           alternative="two-sided")

    res = local_moran_narrated(fc, "val", weights_scheme="queen",
                               permutations=199)
    assert res.success, res.summary
    mine_i = np.array([f["properties"]["local_moran_i"]
                       for f in res.data["features"]])
    assert mine_i == pytest.approx(np.asarray(ref.Is), abs=1e-8)
    mine_q = np.array([f["properties"]["lisa_q"]
                       for f in res.data["features"]])
    nonzero = mine_q != 0
    assert np.array_equal(mine_q[nonzero], np.asarray(ref.q)[nonzero])
    mine_p = np.array([f["properties"]["p_value"]
                       for f in res.data["features"]])
    corr = float(np.corrcoef(mine_p, np.asarray(ref.p_sim))[0, 1])
    assert corr >= 0.9, f"p-vector correlation {corr:.3f} < 0.9"
    assert np.abs(mine_p - np.asarray(ref.p_sim)).max() <= 0.2


def test_local_moran_payload_stable_copy():
    """kNN 权重（默认方案、点要素）路径：强聚集团全部显著。"""
    res = local_moran_narrated(_points_fc(_clustered_points()), "val",
                               weights_scheme="knn", k=6, permutations=199)
    assert res.success, res.summary
    labels = [f["properties"]["lisa_cluster"]
              for f in res.data["features"]]
    # 每团 12 点、k=6 → 团内互为近邻 → HH/LL 检出且占总数多数
    assert labels.count("high_high") >= 12
    assert labels.count("low_low") >= 12
    assert res.data["significant_count"] >= 24
    assert res.data["island_count"] == 0  # knn 恒连通（k≥1）
