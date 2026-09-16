"""时序特征编排 v1 契约测试：季节/年度合成 + percentile/slope/changepoint。

红线：特征只在有效观测上计算（NaN 传播）；不做静默插值填充（物候的
有界填充是下游显式 opt-in）；Theil-Sen 有 T 上界（诚实跳过并披露）；
变点只给 CUSUM 指数与量级，不伪造逐像元 bootstrap 显著性。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis import rs_features as rf
from app.lib.gis.scientific_errors import ResourceScaleMismatch


def _monthly_times(years=2, months=12):
    import datetime as dt

    out = []
    for y in range(2023, 2023 + years):
        for m in range(1, months + 1):
            out.append(dt.datetime(
                y, m, 15, tzinfo=dt.timezone.utc).timestamp())
    return np.array(out, dtype=float)


class TestSeasonalComposites:
    def test_four_season_groups_median_anchor(self):
        t = _monthly_times(years=2)
        # 值 = 月序号（2023-01=0 ... 2024-12=23）
        stack = np.broadcast_to(
            np.arange(24, dtype=float)[:, None, None], (24, 2, 2)).copy()
        out = rf.period_composites(stack, t, periods_per_year=4)
        keys = [g["key"] for g in out["groups"]]
        # 2023-12 + 2024-01/02 → 2024-S1；2024-12 → 2025-S1（DJF 惯例）
        assert len(keys) == 9
        assert "2023-S1" in keys and "2024-S4" in keys and "2025-S1" in keys
        g = next(x for x in out["groups"] if x["key"] == "2023-S2")
        # 春季 = 3,4,5 月 → 值 2,3,4 → median 3
        assert g["median"][0, 0] == pytest.approx(3.0)
        assert g["n_slices"] == 3

    def test_december_belongs_to_next_year_winter(self):
        t = _monthly_times(years=1)
        stack = np.broadcast_to(
            np.arange(12, dtype=float)[:, None, None], (12, 1, 1)).copy()
        out = rf.period_composites(stack, t, periods_per_year=4)
        winter = [g for g in out["groups"] if g["key"].endswith("S1")]
        # 2023-12 归入 2024-S1（DJF 惯例；披露在 meta）
        assert "2024-S1" in [g["key"] for g in winter]
        w = next(g for g in winter if g["key"] == "2024-S1")
        assert w["n_slices"] == 1      # 只有 12 月（1-2 月不在数据里）

    def test_yearly_period_one(self):
        t = _monthly_times(years=2)
        stack = np.ones((24, 2, 2))
        out = rf.period_composites(stack, t, periods_per_year=1)
        assert [g["key"] for g in out["groups"]] == ["2023-P1", "2024-P1"]

    def test_insufficient_group_validity_is_nan_and_counted(self):
        t = _monthly_times(years=1)
        stack = np.ones((12, 1, 1))
        stack[2] = np.nan              # 3 月无效 → 春季只剩 2 个有效
        out = rf.period_composites(stack, t, periods_per_year=4, min_valid=3)
        spring = next(g for g in out["groups"] if g["key"] == "2023-S2")
        assert np.isnan(spring["median"]).all()
        assert spring["n_valid"] == 2
        assert spring["excluded_insufficient"] is True

    def test_group_bound_typed_rejection(self, monkeypatch):
        t = _monthly_times(years=2)
        stack = np.ones((24, 1, 1))
        monkeypatch.setattr(rf, "COMPOSITE_MAX_GROUPS", 4)
        with pytest.raises(ResourceScaleMismatch):
            rf.period_composites(stack, t, periods_per_year=4)


class TestFeaturePack:
    def test_percentile_hand_anchor(self):
        t = np.arange(10, dtype=float) * 86400.0
        stack = np.broadcast_to(
            np.arange(10, dtype=float)[:, None, None], (10, 1, 1)).copy()
        pack = rf.temporal_feature_pack(stack, t)
        f = pack["features"]
        assert f["p10"][0, 0] == pytest.approx(np.percentile(np.arange(10), 10))
        assert f["p50"][0, 0] == pytest.approx(4.5)
        assert f["p90"][0, 0] == pytest.approx(np.percentile(np.arange(10), 90))

    def test_sen_slope_exact_on_linear_series(self):
        t = np.arange(12, dtype=float) * 86400.0 * 30   # 月距
        series = 0.5 * np.arange(12, dtype=float) + 3.0
        stack = np.broadcast_to(series[:, None, None], (12, 2, 2)).copy()
        pack = rf.temporal_feature_pack(stack, t)
        slope = pack["features"]["sen_slope"]
        # 斜率单位：每 30 天 0.5 → 每 天 = 0.5/30（斜率按天归一）
        assert slope[0, 0] == pytest.approx(0.5 / 30.0, rel=1e-9)

    def test_sen_slope_skipped_beyond_cap_with_disclosure(self, monkeypatch):
        monkeypatch.setattr(rf, "THEIL_SEN_MAX_T", 6)
        t = np.arange(10, dtype=float) * 86400.0
        stack = np.ones((10, 1, 1))
        pack = rf.temporal_feature_pack(stack, t)
        assert np.isnan(pack["features"]["sen_slope"]).all()
        assert any("THEIL_SEN_MAX_T" in s or "斜率" in s
                   for s in pack["meta"]["disclosures"])

    def test_nan_slice_propagates_validity_not_zero(self):
        t = np.arange(12, dtype=float) * 86400.0 * 30
        series = np.linspace(0, 5.5, 12)
        stack = np.broadcast_to(series[:, None, None], (12, 1, 1)).copy()
        stack[5] = np.nan
        pack = rf.temporal_feature_pack(stack, t)
        # Sen 斜率仍由其余 11 个有效切片估计（不填 0）；单位 = 值/天
        assert pack["features"]["sen_slope"][0, 0] == pytest.approx(
            0.5 / 30.0, rel=0.2)
        # p50 只用有效切片
        valid = np.delete(series, 5)
        assert pack["features"]["p50"][0, 0] == pytest.approx(
            np.median(valid))

    def test_changepoint_detects_step(self):
        t = np.arange(12, dtype=float) * 86400.0
        series = np.concatenate([np.zeros(6), np.ones(6) * 4.0])
        stack = np.broadcast_to(series[:, None, None], (12, 2, 2)).copy()
        pack = rf.temporal_feature_pack(stack, t)
        idx = pack["features"]["change_index"]
        assert idx[0, 0] == pytest.approx(6.0, abs=1.0)
        assert pack["features"]["cusum_magnitude"][0, 0] > 0

    def test_changepoint_constant_series_degenerate_nan(self):
        t = np.arange(8, dtype=float) * 86400.0
        stack = np.ones((8, 1, 1))
        pack = rf.temporal_feature_pack(stack, t)
        mag = pack["features"]["cusum_magnitude"]
        # 零方差序列：CUSUM 幅值为 0（无变化），change_index 无意义
        assert mag[0, 0] == pytest.approx(0.0)

    def test_pack_meta_bounded_and_deterministic(self):
        t = np.arange(9, dtype=float) * 86400.0
        rng = np.random.default_rng(7)
        stack = rng.random((9, 3, 3))
        p1 = rf.temporal_feature_pack(stack, t)
        p2 = rf.temporal_feature_pack(stack, t)
        assert (p1["features"]["sen_slope"] == p2["features"]["sen_slope"]).all()
        import json
        blob = json.dumps(p1["meta"])
        assert len(blob) < 4000
        assert p1["meta"]["n_time_slices"] == 9

    def test_scale_guard(self):
        t = np.arange(2, dtype=float)
        big = np.empty((2, 2049, 2049))          # 2×2049² ≈ 8.4M > 8M
        with pytest.raises(ResourceScaleMismatch):
            rf.temporal_feature_pack(big, t)
