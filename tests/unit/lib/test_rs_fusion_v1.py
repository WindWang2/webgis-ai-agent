"""SAR×optical fusion v1 契约测试：联合特征栈 + 晚期证据融合。

红线：逐像元覆盖是类型化语义（none/optical-only/sar-only/both）——单模态
缺失不是 0；NaN 永不充当有效值；晚期融合是描述性加权（非概率模型，
诚实披露）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis import rs_fusion as rfu


def _f(name, arr):
    return (name, np.asarray(arr, dtype=float))


class TestJointFeatureStack:
    def test_naming_and_coverage_anchor(self):
        H = W = 2
        ndvi_p50 = np.ones((H, W))
        ndvi_amp = np.ones((H, W))
        vv_p50 = np.ones((H, W))
        ndvi_p50[1, :] = np.nan                    # 行 1：光学无效
        ndvi_amp[1, :] = np.nan
        vv_p50[:, 1] = np.nan                      # 列 1：SAR 无效
        out = rfu.build_joint_feature_stack(
            {"ndvi_p50": ndvi_p50, "ndvi_amp": ndvi_amp},
            {"vv_p50": vv_p50},
        )
        assert out["feature_names"] == [
            "optical::ndvi_p50", "optical::ndvi_amp", "sar::vv_p50"]
        cov = out["coverage"]
        assert cov[0, 0] == rfu.COVERAGE_BOTH
        assert cov[0, 1] == rfu.COVERAGE_OPTICAL_ONLY
        assert cov[1, 0] == rfu.COVERAGE_SAR_ONLY
        assert cov[1, 1] == rfu.COVERAGE_NONE
        meta = out["meta"]
        assert meta["coverage_fractions"]["both"] == pytest.approx(0.25)
        assert meta["coverage_fractions"]["sar_only"] == pytest.approx(0.25)
        assert meta["coverage_fractions"]["optical_only"] == pytest.approx(0.25)
        assert meta["coverage_fractions"]["none"] == pytest.approx(0.25)

    def test_no_pixels_valid_anywhere(self):
        out = rfu.build_joint_feature_stack(
            {"a": np.full((2, 2), np.nan)}, {"b": np.full((2, 2), np.nan)})
        assert (out["coverage"] == rfu.COVERAGE_NONE).all()
        assert out["meta"]["coverage_fractions"]["none"] == 1.0

    def test_shape_mismatch_typed_rejection(self):
        with pytest.raises(ValueError, match="形状|shape"):
            rfu.build_joint_feature_stack(
                {"a": np.ones((2, 2))}, {"b": np.ones((3, 3))})

    def test_feature_validity_counts_disclosed(self):
        a = np.ones((2, 2))
        a[0, 0] = np.nan
        out = rfu.build_joint_feature_stack({"ndvi": a}, {"vv": a})
        assert out["meta"]["feature_valid_counts"]["optical::ndvi"] == 3
        assert out["meta"]["feature_valid_counts"]["sar::vv"] == 3

    def test_empty_features_rejected(self):
        with pytest.raises(ValueError):
            rfu.build_joint_feature_stack({}, {"b": np.ones((2, 2))})


class TestLateEvidenceFusion:
    def test_agreement_anchor(self):
        a = np.array([[2.0, 2.0], [-2.0, np.nan]])
        b = np.array([[1.0, -1.0], [-1.0, 3.0]])
        out = rfu.late_evidence_fusion(a, b, names=("optical", "sar"))
        agr = out["agreement"]
        assert agr[0, 0] == rfu.AGREEMENT_CONSENSUS_POS
        assert agr[0, 1] == rfu.AGREEMENT_CONFLICT
        assert agr[1, 0] == rfu.AGREEMENT_CONSENSUS_NEG
        assert agr[1, 1] == rfu.AGREEMENT_SAR_ONLY    # a=NaN，b=3 有效
        # 融合值：可用源按权重归一加权；单源像元保留自己的证据
        assert out["fused"][1, 1] == pytest.approx(3.0)   # 仅 SAR
        assert out["fused"][0, 0] == pytest.approx(1.5)   # 等权默认

    def test_single_source_pixels_keep_own_evidence(self):
        a = np.array([[np.nan]])
        b = np.array([[5.0]])
        out = rfu.late_evidence_fusion(a, b)
        assert out["fused"][0, 0] == pytest.approx(5.0)
        assert out["agreement"][0, 0] == rfu.AGREEMENT_SAR_ONLY

    def test_neither_source_nan_not_zero(self):
        out = rfu.late_evidence_fusion(
            np.array([[np.nan]]), np.array([[np.nan]]))
        assert np.isnan(out["fused"][0, 0])
        assert out["agreement"][0, 0] == rfu.AGREEMENT_NONE

    def test_custom_weights(self):
        a = np.array([[2.0]])
        b = np.array([[4.0]])
        out = rfu.late_evidence_fusion(
            a, b, weights={"optical": 3.0, "sar": 1.0})
        assert out["fused"][0, 0] == pytest.approx(2.5)

    def test_invalid_weights_rejected(self):
        with pytest.raises(ValueError, match="weight"):
            rfu.late_evidence_fusion(
                np.ones((1, 1)), np.ones((1, 1)),
                weights={"optical": -1.0, "sar": 1.0})

    def test_agreement_counts_bounded_json_safe(self):
        rng = np.random.default_rng(3)
        a = rng.normal(size=(4, 4))
        b = rng.normal(size=(4, 4))
        out = rfu.late_evidence_fusion(a, b)
        import json
        blob = json.dumps(out["meta"])
        assert len(blob) < 2000
        assert sum(out["meta"]["agreement_counts"].values()) == 16

    def test_deterministic(self):
        rng = np.random.default_rng(9)
        a = rng.normal(size=(3, 3))
        b = rng.normal(size=(3, 3))
        o1 = rfu.late_evidence_fusion(a, b)
        o2 = rfu.late_evidence_fusion(a, b)
        assert (o1["fused"] == o2["fused"]).all()
        assert (o1["agreement"] == o2["agreement"]).all()
