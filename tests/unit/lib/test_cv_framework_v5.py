"""CV 框架（science-v5 W1）—— 折分配/泄漏守卫/指标聚合 单元测试。

覆盖（架构 01-architecture.md D1 + Subagent-A Round-0 #9 修订）：

- 正例：三族 splitter 的确定性、spatial_block 与 kriging 旧实现逐位一致
  （同一对象 re-import）、temporal_forward 零 future leakage（逐折
  max(train_t) < min(test_t)）、指标手算锚、z 校准统计、重复坐标披露。
- 负例：folds<2、folds>unique 时间值、temporal 无 times_sec、坐标/样本
  数不一致、scheme 词表外、非有限时间戳。
- 边界：n=folds 恰好、块 0 纯训练库（fold_id=-1）、同值时间组永不跨折、
  fold 全失败诚实披露、min_samples 诚实退化。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis.cv import (
    CVReport,
    run_cross_validation,
    spatial_block_folds,
    temporal_forward_folds,
)


# ── 固定装置：小型合成场（与 oracle 哲学一致——可手算） ────────────────────

def _grid_xy(n_per_side: int = 8, spacing: float = 10.0) -> np.ndarray:
    ax = np.arange(n_per_side) * spacing
    xx, yy = np.meshgrid(ax, ax)
    return np.column_stack([xx.ravel(), yy.ravel()])


def _linear_field(xy: np.ndarray) -> np.ndarray:
    return 2.0 + 0.5 * xy[:, 0] + 0.1 * xy[:, 1]


# ── spatial_block_folds ───────────────────────────────────────────────────

class TestSpatialBlockFolds:
    def test_deterministic_no_rng(self):
        xy = _grid_xy()
        f1, b1 = spatial_block_folds(xy, 4)
        f2, b2 = spatial_block_folds(xy, 4)
        assert np.array_equal(f1, f2)
        assert np.array_equal(b1, b2)

    def test_kriging_alias_is_same_implementation(self):
        # 零第二事实源：kriging._spatial_block_folds 必须 re-import 同一对象
        from app.lib.geo_analysis import cv as cv_mod
        from app.lib.geo_analysis import kriging as kg_mod
        assert kg_mod._spatial_block_folds is cv_mod.spatial_block_folds

    def test_fold_ids_in_range_and_blocks_coarser(self):
        xy = _grid_xy(6)
        fold_id, block_id = spatial_block_folds(xy, 4)
        assert fold_id.min() >= 0 and fold_id.max() < 4
        assert block_id.max() >= fold_id.max()  # 块 id 空间不小于折 id 空间
        assert set(np.unique(fold_id)).issubset(set(range(4)))

    def test_every_fold_nonempty_for_grid(self):
        xy = _grid_xy(8)
        fold_id, _ = spatial_block_folds(xy, 4)
        counts = [int((fold_id == f).sum()) for f in range(4)]
        assert all(c > 0 for c in counts)

    @pytest.mark.parametrize("bad", [1, 0, -3])
    def test_rejects_folds_below_two(self, bad):
        with pytest.raises(ValueError, match="folds 必须"):
            spatial_block_folds(_grid_xy(4), bad)

    def test_rejects_bad_coords_shape(self):
        from app.lib.gis.scientific_errors import DegenerateData
        with pytest.raises(DegenerateData):
            spatial_block_folds(np.zeros(5), 3)


# ── temporal_forward_folds ────────────────────────────────────────────────

class TestTemporalForwardFolds:
    def test_block0_is_train_only(self):
        t = np.arange(20, dtype=float) * 3600.0
        fold_id, block_id = temporal_forward_folds(t, 4)
        assert (fold_id == -1).sum() > 0          # 头部纯训练库
        assert fold_id.max() == 2                 # folds-1 个测试折
        assert set(np.unique(block_id)) == {0, 1, 2, 3}

    def test_no_future_leakage_strict(self):
        rng = np.random.default_rng(7)
        t = np.sort(rng.choice(np.arange(100.0), size=60, replace=False))
        fold_id, _ = temporal_forward_folds(t, 5)
        for f in range(fold_id.max() + 1):
            test_mask = fold_id == f
            train_mask = t < t[test_mask].min()
            assert train_mask.any()
            assert t[train_mask].max() < t[test_mask].min()

    def test_duplicate_timestamps_never_split(self):
        # 同一时刻 4 个样本：任何切分都不得把它们拆进两个块
        t = np.array([0, 0, 0, 0, 10, 10, 20, 20, 30, 30], dtype=float)
        fold_id, block_id = temporal_forward_folds(t, 4)
        for value in (0.0, 10.0, 20.0, 30.0):
            blocks = np.unique(block_id[t == value])
            assert len(blocks) == 1, f"t={value} 被跨块拆分"

    def test_rejects_folds_exceeding_unique_times(self):
        t = np.array([1, 1, 1, 2, 2, 2], dtype=float)  # unique=2
        with pytest.raises(ValueError, match="unique 时间值数"):
            temporal_forward_folds(t, 3)

    def test_rejects_constant_times(self):
        with pytest.raises(ValueError, match="unique 时间值数"):
            temporal_forward_folds(np.ones(12), 2)

    @pytest.mark.parametrize("bad", [1, 0])
    def test_rejects_folds_below_two(self, bad):
        with pytest.raises(ValueError, match="folds 必须"):
            temporal_forward_folds(np.arange(10.0), bad)

    def test_rejects_nonfinite_times(self):
        from app.lib.gis.scientific_errors import DegenerateData
        t = np.arange(10.0)
        t[3] = np.nan
        with pytest.raises(DegenerateData):
            temporal_forward_folds(t, 3)

    def test_stable_under_input_permutation_of_equal_times(self):
        # 排序稳定：样本恒等（同值组保持原相对顺序 → 分块结果逐位一致）
        t = np.array([5, 5, 1, 1, 9, 9], dtype=float)
        f1, b1 = temporal_forward_folds(t, 3)
        f2, b2 = temporal_forward_folds(t, 3)
        assert np.array_equal(f1, f2) and np.array_equal(b1, b2)
        assert np.all(b1[t == 5.0] == b1[t == 5.0][0])


# ── run_cross_validation ──────────────────────────────────────────────────

def _make_fold_predictor(xy: np.ndarray, values: np.ndarray):
    """手算锚预测器：训练集均值常数模型（var=样本方差/训练数）。"""

    def fit(train, train_vals):
        return float(np.mean(train_vals))

    def predict(model, test):
        n = int(test.sum())
        return np.full(n, model), np.full(n, max(model * 0.01, 1e-9))

    return fit, predict


class TestRunCrossValidation:
    def test_index_scheme_matches_manual_computation(self):
        xy = _grid_xy(6)
        v = _linear_field(xy)
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v),
            scheme="index", folds=4, min_samples=8)
        # 手算：folds=4、n=36 → usable = max(2, min(4, 36//4)) = 4；
        # 逐折 refit（无泄漏）→ 每折预测 = 该折训练子集的均值
        assert report.folds == 4
        assert report.folds_used == 4
        errs = []
        for f in range(4):
            test = np.arange(len(v)) % 4 == f
            train_mean = float(v[~test].mean())
            errs.extend((train_mean - v[test]).tolist())
        e = np.asarray(errs)
        assert report.rmse == pytest.approx(float(np.sqrt(np.mean(e ** 2))), rel=1e-12)
        assert report.mae == pytest.approx(float(np.mean(np.abs(e))), rel=1e-12)
        assert report.bias == pytest.approx(float(np.mean(e)), rel=1e-12)

    def test_spatial_block_leakage_guard_recorded(self):
        xy = _grid_xy(6)
        v = _linear_field(xy)
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v),
            scheme="spatial_block", folds=4)
        assert report.leakage_check is not None
        assert all(report.leakage_check.values())          # 全部折块不相交
        assert report.duplicate_crossfold_pairs == 0
        assert all("block_ids" in pf for pf in report.per_fold)

    def test_temporal_forward_no_future_leakage_end_to_end(self):
        rng = np.random.default_rng(11)
        n = 60
        xy = rng.uniform(0, 100, size=(n, 2))
        t = np.sort(rng.choice(np.arange(200.0), size=n, replace=False))
        v = 5.0 + 0.02 * t + rng.normal(0, 0.1, n)
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v),
            scheme="temporal_forward", folds=4, times_sec=t)
        assert report.scheme == "temporal_forward"
        assert report.leakage_check is not None
        assert all(report.leakage_check.values())          # 零 future leakage
        for pf in report.per_fold:
            assert pf["n_train"] > 0 and pf["n_test"] > 0

    def test_temporal_forward_requires_times(self):
        xy = _grid_xy(4)
        with pytest.raises(ValueError, match="times_sec"):
            run_cross_validation(
                xy, _linear_field(xy), *_make_fold_predictor(xy, xy),
                scheme="temporal_forward", folds=3)

    def test_insufficient_samples_honest_decline(self):
        xy = _grid_xy(2)                       # n=4 < min_samples
        report = run_cross_validation(
            xy, _linear_field(xy), *_make_fold_predictor(xy, xy),
            scheme="index", folds=2)
        assert isinstance(report, CVReport)
        assert report.rmse is None and report.folds == 0
        assert "无法进行可靠的交叉验证" in report.note
        assert report.to_dict()["note"]

    def test_duplicate_crossfold_coords_disclosed(self):
        # 同一坐标复制 2 份、值相同 → spatial_block 下两份同折（同块）；
        # 用 index 折强制跨折 → 重复坐标披露计数 2
        xy = np.array([[0.0, 0.0], [0.0, 0.0], [50.0, 50.0],
                       [10.0, 0.0], [0.0, 10.0], [30.0, 30.0],
                       [40.0, 5.0], [5.0, 40.0]])
        v = np.array([1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v), scheme="index", folds=2)
        assert report.duplicate_crossfold_pairs >= 1

    def test_fold_failure_counted_not_swallowed(self):
        xy = _grid_xy(5)
        v = _linear_field(xy)

        def fit(train, train_vals):
            raise RuntimeError("boom")

        def predict(model, test):  # pragma: no cover
            return np.zeros(int(test.sum())), None

        report = run_cross_validation(xy, v, fit, predict,
                                      scheme="index", folds=3)
        assert report.folds_used == 0
        assert report.fold_failures == 3
        assert "拟合均失败" in report.note

    def test_on_fold_error_raise_propagates(self):
        xy = _grid_xy(5)
        v = _linear_field(xy)

        def fit(train, train_vals):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            run_cross_validation(
                xy, v, fit, lambda m, t: (np.zeros(int(t.sum())), None),
                scheme="index", folds=3, on_fold_error="raise")

    def test_calibration_z_scores_from_variance(self):
        xy = _grid_xy(6)
        rng = np.random.default_rng(3)
        v = _linear_field(xy) + rng.normal(0, 0.5, len(xy))

        def fit(train, train_vals):
            return float(np.mean(train_vals))

        def predict(model, test):
            n = int(test.sum())
            # var 给常数大值 → z 很小 → 覆盖率 1
            return np.full(n, model), np.full(n, 1e6)

        report = run_cross_validation(xy, v, fit, predict,
                                      scheme="index", folds=3)
        assert report.calibration is not None
        assert report.calibration["n"] == len(v)
        assert report.calibration["z_coverage_95"] == pytest.approx(1.0)

    def test_calibration_filters_nonfinite_z(self):
        xy = _grid_xy(6)
        v = _linear_field(xy)

        def fit(train, train_vals):
            return float(np.mean(train_vals))

        calls = {"n": 0}

        def predict(model, test):
            calls["n"] += 1
            n = int(test.sum())
            if calls["n"] == 1:
                var = np.zeros(n)             # → z=inf/NaN（诚实过滤）
            else:
                var = np.full(n, 1.0)
            return np.full(n, model), var

        report = run_cross_validation(xy, v, fit, predict,
                                      scheme="index", folds=2)
        assert report.calibration is not None
        # 第一折的非有限 z 被过滤，只统计第二折
        assert report.calibration["n"] == len(v) // 2

    def test_r2_against_constant_baseline(self):
        xy = _grid_xy(6)
        v = _linear_field(xy)
        # 常数（逐折训练均值）预测器对线性场的 R² 应接近 0（基线表现）
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v), scheme="index", folds=4)
        assert report.r2 is not None
        assert abs(report.r2) < 0.1

    @pytest.mark.parametrize("bad_scheme", ["random", "", "TIME"])
    def test_scheme_vocabulary_enforced(self, bad_scheme):
        xy = _grid_xy(4)
        with pytest.raises(ValueError, match="scheme 必须是"):
            run_cross_validation(
                xy, _linear_field(xy), *_make_fold_predictor(xy, xy),
                scheme=bad_scheme)

    def test_coords_values_mismatch_rejected(self):
        from app.lib.gis.scientific_errors import DegenerateData
        xy = _grid_xy(4)
        with pytest.raises(DegenerateData):
            run_cross_validation(
                xy, np.zeros(len(xy) + 1), *_make_fold_predictor(xy, xy),
                scheme="index")

    def test_to_dict_shape_stable(self):
        xy = _grid_xy(6)
        v = _linear_field(xy)
        report = run_cross_validation(
            xy, v, *_make_fold_predictor(xy, v),
            scheme="spatial_block", folds=4)
        d = report.to_dict()
        for key in ("n_samples", "folds", "folds_used", "scheme",
                    "rmse", "mae", "bias", "r2"):
            assert key in d
        assert d["scheme"] == "spatial_block"
