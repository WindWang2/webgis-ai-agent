"""ADR-0153 P4：离群值与值域剖析 —— outlier_policy 契约（与 03 线的对账）。

本线只剖析不裁剪：字段级断言钉死 {field_policy, outlier_ratio, skew,
zero_ratio, p99, suggested_clip} 契约，03 线按 field_policy 执行裁剪。
"""
from __future__ import annotations

import math

import pytest

from app.services.spatial_quality_gate import (
    profile_numeric_fields,
    profile_outlier_policy,
)

CONTRACT_KEYS = {
    "field_policy", "outlier_ratio", "skew", "zero_ratio",
    "p50", "p90", "p99", "min", "max", "suggested_clip", "reason",
}
POLICY_VOCABULARY = {"none", "clip_p99", "head_tail", "log"}


class TestPolicyContract:
    def test_contract_keys_closed(self):
        prof = profile_outlier_policy([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        assert set(prof) == CONTRACT_KEYS

    def test_policy_vocabulary_closed(self):
        datasets = [
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            [10.0, 12.0, 11.0, 13.0, 12.0, 11.5, 12.5, 5000.0],
            [1.0, 1.1, 1.2, 1.0, 1.3, 1.1, 1.4, 1.2, 5000.0],
            [1.0, 2.0, 5.0, 50.0, 500.0, 5000.0, 50000.0, 500000.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        ]
        for vals in datasets:
            assert profile_outlier_policy(vals)["field_policy"] in POLICY_VOCABULARY


class TestPolicies:
    def test_symmetric_no_outliers_none(self):
        prof = profile_outlier_policy([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        assert prof["field_policy"] == "none"

    def test_positive_heavy_tail_log(self):
        # n=9：经典 3σ 在小样本被掩蔽（max z = (n-1)/√n ≈ 2.67 < 3），
        # 无硬离群点的平滑重尾 → log 变换建议。
        vals = [1.0, 1.1, 0.9, 1.2, 1.0, 1.1, 1.3, 0.8, 5000.0]
        prof = profile_outlier_policy(vals)
        assert prof["field_policy"] == "log"
        assert prof["skew"] > 2.0
        assert prof["min"] >= 0.0

    def test_heavy_tail_clip_p99_with_concrete_value(self):
        # >3σ 拉爆色带场景：15 个正常值 + 1 极值 → clip_p99 + 明确建议值。
        vals = [float(10 + i) for i in range(15)] + [100000.0]
        prof = profile_outlier_policy(vals)
        assert prof["field_policy"] == "clip_p99"
        # 建议值 = inlier 质量的 p99（稳健分位）—— 不是被极值吞掉的原始 p99。
        assert prof["suggested_clip"] is not None
        assert prof["suggested_clip"] != prof["p99"]
        # 建议值必须落在主体区间内（把色带断点拉回主体，而不是被极值吞掉）。
        assert prof["suggested_clip"] < 1000.0
        assert prof["suggested_clip"] > min(vals)

    def test_low_cardinality_long_tail_head_tail(self):
        vals = [1.0, 1.0, 2.0, 2.0, 5.0, 5.0, 10.0, 10.0, 50.0, 100.0, 200.0, 500.0]
        prof = profile_outlier_policy(vals)
        assert prof["field_policy"] == "head_tail"

    def test_zero_heavy_column_none(self):
        prof = profile_outlier_policy([0.0] * 6 + [1.0, 2.0])
        assert prof["field_policy"] == "none"
        assert prof["zero_ratio"] > 0.5

    def test_insufficient_samples_none(self):
        prof = profile_outlier_policy([1.0, 2.0, 100000.0])
        assert prof["field_policy"] == "none"
        assert "insufficient" in prof["reason"]

    def test_skew_and_zero_ratio_are_finite(self):
        prof = profile_outlier_policy([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 1000.0])
        assert math.isfinite(prof["skew"])
        assert math.isfinite(prof["zero_ratio"])
        assert 0.0 <= prof["outlier_ratio"] <= 1.0


class TestDatasetProfiling:
    def test_profile_numeric_fields_bounded_and_flagged(self):
        features = [
            {
                "type": "Feature",
                "properties": {"pop": float(100 + i), "area": 1.0, "name": f"f{i}"},
                "geometry": None,
            }
            for i in range(12)
        ]
        features.append(
            {"type": "Feature", "properties": {"pop": 5_000_000.0, "area": 1.0, "name": "x"},
             "geometry": None}
        )
        prof = profile_numeric_fields(features)
        # string 字段与常数字段不进入数值剖析；pop 被剖析。
        assert "pop" in prof
        assert "name" not in prof
        assert prof["pop"]["field_policy"] in POLICY_VOCABULARY

    def test_field_cap(self):
        features = [
            {"type": "Feature", "properties": {f"f{i}": float(i) for i in range(20)}, "geometry": None}
        ]
        prof = profile_numeric_fields(features, field_cap=8)
        assert len(prof) <= 8


@pytest.mark.cartography
def test_outlier_policy_contract_fields_for_line03():
    """03 线消费契约的字段级断言：任何字段剖析结果都必须携带裁剪所需的
    全部信息（policy + 建议值），本线绝不产出裁剪后的数据。"""
    vals = [float(10 + i) for i in range(15)] + [100000.0]
    prof = profile_outlier_policy(vals)
    if prof["field_policy"] == "clip_p99":
        assert prof["suggested_clip"] is not None
    # 契约里没有"裁剪执行"字段 —— 本线只剖析。
    assert "clipped_values" not in prof
    assert "trimmed_indices" not in prof
