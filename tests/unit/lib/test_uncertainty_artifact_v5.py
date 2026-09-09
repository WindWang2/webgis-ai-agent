"""Uncertainty Artifact（science-v5 W6）单元测试。

契约（01-architecture.md D5 + 挑战 R0-#13）：

- estimator 必填无默认、封闭词表（跨估计器混读是渲染事故源）；
- R<2 ensemble → std 诚实 None + 披露（不是 0 伪精确）；
- model_uncertainty vs data_quality 显式分离（data_quality 只来自真实
  元数据——本文件不给它任何合成值）；
- 方差路径：p10/p90 = pred ± 1.2816σ、95% 区间 = pred ± 1.96σ（手算锚）；
- to_dict 有界（6 位收敛 + 披露截断）；renderer 元数据确定性。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import DegenerateData
from app.lib.geo_analysis import uncertainty as ua


class TestEstimatorContract:
    def test_estimator_required_and_closed_vocabulary(self):
        preds = np.array([1.0, 2.0])
        with pytest.raises(DegenerateData, match="estimator 必须是"):
            ua.UncertaintyArtifact(
                estimator="",
                n_targets=2, mean=preds, std=np.array([0.1, 0.1]))
        with pytest.raises(DegenerateData, match="estimator 必须是"):
            ua.UncertaintyArtifact(
                estimator="kriging std",   # 非词表
                n_targets=2, mean=preds, std=np.array([0.1, 0.1]))

    def test_length_mismatch_rejected(self):
        with pytest.raises(DegenerateData, match="同形一维"):
            ua.from_kriging(np.zeros(3), np.zeros(4))

    def test_nonfinite_variance_rejected(self):
        with pytest.raises(DegenerateData, match="非有限"):
            ua.from_kriging(np.zeros(3), np.array([1.0, np.nan, 1.0]))


class TestVariancePath:
    def test_gaussian_interval_hand_anchor(self):
        preds = np.array([10.0, 20.0])
        var = np.array([4.0, 1.0])            # σ = 2, 1
        art = ua.from_kriging(preds, var)
        np.testing.assert_allclose(art.std, [2.0, 1.0])
        np.testing.assert_allclose(
            art.q10, preds - 1.2815515655446004 * art.std, rtol=1e-12)
        np.testing.assert_allclose(
            art.q90, preds + 1.2815515655446004 * art.std, rtol=1e-12)
        np.testing.assert_allclose(
            art.interval_low, preds - 1.959963984540054 * art.std,
            rtol=1e-12)
        np.testing.assert_allclose(
            art.interval_high, preds + 1.959963984540054 * art.std,
            rtol=1e-12)
        assert art.interval_level == 0.95
        assert art.model_uncertainty["interval_semantics"] == \
            "gaussian_predictive"
        assert art.model_uncertainty["definition"]

    def test_negative_variance_clamped_not_failed(self):
        # 负方差已由实现层钳 0 的输入在此同样安全（std=0 诚实）
        art = ua.from_kriging(np.array([1.0]), np.array([-0.5]))
        assert art.std[0] == 0.0

    def test_estimator_vocabulary_served(self):
        art = ua.from_variance(
            "st_kriging_variance", np.array([1.0]), np.array([1.0]))
        assert art.estimator == "st_kriging_variance"


class TestSGSPath:
    def _ensemble(self, r: int):
        from app.lib.geo_analysis.kriging_simulation import (
            sequential_gaussian_simulation,
        )

        rng = np.random.default_rng(7)
        xy = rng.uniform(0, 10_000, (24, 2))
        z = 20 + 5 * np.sin(xy[:, 0] / 2000.0) + rng.normal(0, 0.3, 24)
        targets = rng.uniform(0, 10_000, (80, 2))
        return sequential_gaussian_simulation(
            xy, z, targets, n_realizations=r, seed=42, k=8)

    def test_from_sgs_quantiles_and_provenance(self):
        ens = self._ensemble(12)
        art = ua.from_sgs(ens)
        assert art.estimator == "sgs_ensemble"
        assert art.interval_level == 0.8
        np.testing.assert_array_equal(art.q10, ens.p10)
        np.testing.assert_array_equal(art.q50, ens.p50)
        np.testing.assert_array_equal(art.q90, ens.p90)
        np.testing.assert_array_equal(art.interval_low, ens.p10)
        assert art.provenance["seed"] == 42
        assert art.provenance["n_realizations"] == 12
        assert art.provenance["backend"] == "numpy_reference"
        assert "variogram" in art.provenance
        assert art.model_uncertainty["interval_semantics"] == \
            "ensemble_quantiles"

    def test_single_realization_std_honest_absence(self):
        ens = self._ensemble(1)
        art = ua.from_sgs(ens)
        assert art.std is None                        # 不是 0（伪精确）
        assert art.interval_low is None
        assert any("honest absence" in s for s in art.disclosures)
        d = art.to_dict()
        assert d["std_available"] is False
        assert "std_range" not in d


class TestSeparationAndBounds:
    def test_model_vs_data_quality_separation(self):
        art = ua.from_kriging(
            np.array([1.0, 2.0]), np.array([1.0, 4.0]),
            data_quality=ua.data_quality_summary(
                n_samples=48, n_targets=100, nodata_fraction=0.02,
                value_field="pm25", working_crs="EPSG:32650"))
        d = art.to_dict()
        assert d["data_quality"]["n_samples"] == 48
        assert d["data_quality"]["samples_per_target"] == 0.48
        assert d["data_quality"]["nodata_fraction"] == 0.02
        assert d["data_quality"]["value_field"] == "pm25"
        # 模型不确定性块绝不携带 data_quality 字段
        assert "n_samples" not in d["model_uncertainty"]
        assert "renderer" in art.to_renderer_metadata()["separation"] or \
            "不可混合" in art.to_renderer_metadata()["separation"]

    def test_data_quality_defaults_absent_not_synthesized(self):
        dq = ua.data_quality_summary(n_samples=10, n_targets=20)
        assert "nodata_fraction" not in dq        # 无真实值 → 不合成
        assert "value_field" not in dq

    def test_to_dict_bounded_disclosures(self):
        preds = np.zeros(4)
        art = ua.from_variance(
            "kriging_variance", preds, np.ones(4),
            disclosures=[f"d{i}" for i in range(30)])
        d = art.to_dict()
        assert len(d["disclosures"]) <= 16

    def test_renderer_breaks_deterministic(self):
        art = ua.from_kriging(
            np.arange(10.0), np.linspace(0.1, 2.0, 10))
        r1 = art.to_renderer_metadata()
        r2 = art.to_renderer_metadata()
        assert r1 == r2
        assert "p50" in r1["suggested_breaks"]
        assert len(r1["suggested_breaks"]["p50"]) == 3

    def test_provenance_keys_sorted_in_dict(self):
        art = ua.from_kriging(
            np.zeros(2), np.ones(2),
            provenance={"zeta": 1, "alpha": 2, "mid": 3})
        keys = list(art.to_dict()["provenance"].keys())
        assert keys == sorted(keys)
