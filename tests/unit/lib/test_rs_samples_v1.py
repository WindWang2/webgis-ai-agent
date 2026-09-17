"""polygon/patch 样本挂接 + 地理/时间 split v1 契约测试。

Oracle 核心：**时间/空间 split 有 leakage 测试**——空间侧钉住
"同一 block 永远同一 fold"（块不相交 = fold↔block 是函数）；时间侧
钉住"逐折 max(train_t) < min(test_t)"（严格前向链，零 future leakage）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis import rs_samples as rsp


def _grid():
    return {"crs": "EPSG:32650", "width": 4, "height": 4,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}


def _rect(x0, y0, x1, y1):
    """投影坐标矩形（transform 原点 (500000, 4000000)，像元 10m）。"""
    xs = [500000 + x * 10 for x in (x0, x1, x1, x0, x0)]
    ys = [4000000 - y * 10 for y in (y0, y0, y1, y1, y0)]
    return {"type": "Polygon", "coordinates": [list(zip(xs, ys))]}


def _features():
    ndvi = np.array([
        [0.1, 0.2, 0.3, 0.4],
        [0.5, 0.6, 0.7, 0.8],
        [0.9, 1.0, np.nan, 0.2],
        [0.3, 0.4, 0.5, 0.6],
    ])
    vv = np.full((4, 4), -12.0)
    return {"ndvi_p50": ndvi, "sar::vv_p50": vv}


class TestPolygonAttach:
    def test_cell_counts_and_nan_aware_mean(self):
        out = rsp.attach_polygon_samples(
            _features(), _grid(), [_rect(0, 0, 2, 2)])
        rec = out["records"][0]
        assert rec["cell_count"] == 4      # 2×2 像元（pixel-center 语义）
        assert rec["features"]["ndvi_p50"] == pytest.approx(
            (0.1 + 0.2 + 0.5 + 0.6) / 4)
        assert rec["features"]["sar::vv_p50"] == pytest.approx(-12.0)
        assert out["meta"]["n_polygons"] == 1
        assert out["records"][0]["centroid_xy"] is not None

    def test_nan_cells_excluded_and_counted(self):
        feats = _features()
        feats["ndvi_p50"][0, 0] = np.nan
        out = rsp.attach_polygon_samples(
            feats, _grid(), [_rect(0, 0, 2, 2)])
        rec = out["records"][0]
        assert rec["features"]["ndvi_p50"] == pytest.approx(
            (0.2 + 0.5 + 0.6) / 3)
        assert rec["nan_cells"]["ndvi_p50"] == 1

    def test_label_passthrough(self):
        poly = _rect(0, 0, 2, 2)
        poly["properties"] = {"label": "water"}
        out = rsp.attach_polygon_samples(_features(), _grid(), [poly])
        assert out["records"][0]["label"] == "water"

    def test_offgrid_polygon_excluded_not_fabricated(self):
        poly = {
            "type": "Polygon",
            "coordinates": [[(999900, 999900), (999950, 999900),
                             (999950, 999950), (999900, 999950),
                             (999900, 999900)]],
        }
        out = rsp.attach_polygon_samples(_features(), _grid(), [poly])
        assert out["records"][0]["cell_count"] == 0
        assert out["records"][0]["excluded_offgrid"] is True
        assert out["meta"]["n_excluded_offgrid"] == 1
        assert out["records"][0]["features"] == {}

    def test_empty_feature_values_are_nan_not_zero(self):
        feats = {"f": np.full((4, 4), np.nan)}
        out = rsp.attach_polygon_samples(feats, _grid(), [_rect(0, 0, 3, 3)])
        rec = out["records"][0]
        assert np.isnan(rec["features"]["f"])


class TestGeographicSplit:
    def _spread_points(self, n=40, seed=1):
        rng = np.random.default_rng(seed)
        return rng.uniform(0, 1000, size=(n, 2))

    def test_block_disjoint_folds_no_leakage(self):
        xy = self._spread_points()
        out = rsp.geographic_block_split(xy, folds=4)
        fold, block = out["fold"], out["block"]
        # 核心不变量：同一 block 永远同一 fold（fold↔block 是函数）
        for b in np.unique(block):
            assert np.unique(fold[block == b]).size == 1
        # 全部 fold 非空（充分散布下）
        assert np.unique(fold).size == 4
        assert out["meta"]["disclosure"].startswith("同 block 必同 fold")

    def test_clustered_samples_share_fold_with_neighbors(self):
        # 两个紧致簇 + 噪声：簇内样本互为近邻，同块必同折保证簇内不进
        # 训练/测试两侧（泄漏测试的可检验形式）
        cluster_a = np.random.default_rng(2).normal(200, 5, size=(12, 2))
        cluster_b = np.random.default_rng(3).normal(800, 5, size=(12, 2))
        noise = np.random.default_rng(4).uniform(0, 1000, size=(8, 2))
        xy = np.vstack([cluster_a, cluster_b, noise])
        out = rsp.geographic_block_split(xy, folds=3)
        fold, block = out["fold"], out["block"]
        for b in np.unique(block):
            assert np.unique(fold[block == b]).size == 1

    def test_deterministic(self):
        xy = self._spread_points()
        o1 = rsp.geographic_block_split(xy, folds=4)
        o2 = rsp.geographic_block_split(xy, folds=4)
        assert (o1["fold"] == o2["fold"]).all()
        assert (o1["block"] == o2["block"]).all()

    def test_folds_lower_bound(self):
        with pytest.raises(ValueError):
            rsp.geographic_block_split(self._spread_points(10), folds=1)


class TestTemporalSplit:
    def test_strict_forward_chain_no_future_leakage(self):
        times = np.arange(30, dtype=float) * 86400.0
        out = rsp.temporal_forward_split(times, folds=3)
        fold = out["fold"]
        t = times
        for k in range(3):
            test_mask = fold == k
            train_mask = (fold == -1) | (fold < k)
            if test_mask.any():
                assert t[train_mask].max() < t[test_mask].min()

    def test_same_timestamp_never_split(self):
        times = np.array([1.0, 1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        out = rsp.temporal_forward_split(times, folds=4)
        fold, block = out["fold"], out["block"]
        for b in np.unique(block):
            assert np.unique(fold[block == b]).size == 1

    def test_insufficient_unique_times_rejected(self):
        times = np.array([1.0, 1.0, 1.0])
        with pytest.raises(ValueError):
            rsp.temporal_forward_split(times, folds=3)


class TestSampleMatrix:
    def _records(self):
        return [
            {"polygon_id": "a", "features": {"ndvi": 0.2, "vv": -10.0},
             "label": "crop", "centroid_xy": (0.0, 0.0)},
            {"polygon_id": "b", "features": {"ndvi": np.nan, "vv": -12.0},
             "label": "water", "centroid_xy": (100.0, 100.0)},
            {"polygon_id": "c", "features": {"ndvi": np.nan, "vv": np.nan},
             "label": "bare", "centroid_xy": (200.0, 200.0)},
        ]

    def test_matrix_shapes_and_nan_policy(self):
        out = rsp.build_sample_matrix(self._records(), ["ndvi", "vv"],
                                      min_valid_features=1)
        assert out["X"].shape == (2, 2)       # c 全 NaN 被排除
        assert out["y"] == ["crop", "water"]
        assert out["excluded_ids"] == ["c"]
        assert np.isnan(out["X"][1, 0])        # b 的 ndvi 保持 NaN

    def test_min_valid_features_two_excludes_b(self):
        out = rsp.build_sample_matrix(self._records(), ["ndvi", "vv"],
                                      min_valid_features=2)
        assert out["X"].shape == (1, 2)
        assert out["y"] == ["crop"]

    def test_leakage_disclosure_in_matrix_meta(self):
        out = rsp.build_sample_matrix(self._records(), ["ndvi", "vv"])
        assert any("split" in s or "泄漏" in s
                   for s in out["meta"]["disclosures"])
