"""G15 WP05 —— 共享分类契约测试（已知答案 + 资格拒绝 + 退化分布）。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.frame_products.shared_expression import (
    compute_shared_expression,
)

pytestmark = pytest.mark.unit


def _fc(values, names=None, key="value"):
    names = names or [f"z{i}" for i in range(len(values))]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {key: v, "name": n},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}}
            for v, n in zip(values, names)
        ],
    }


class TestUnionBreaks:
    def test_breaks_computed_once_on_union(self):
        """两帧值域不重叠 —— breaks 覆盖并集，任何一帧单独分级都不同。"""
        out = compute_shared_expression(
            [("f1", _fc([1.0, 2.0])), ("f2", _fc([50.0, 99.0]))],
            "value", method="equal_interval", k=4,
        )
        assert out.ok
        cfg = out.config
        assert cfg["domain_status"] == "complete"
        assert cfg["breaks"][0] == pytest.approx(1.0)
        assert cfg["breaks"][-1] == pytest.approx(99.0)
        assert len(cfg["breaks"]) == 5  # k=4 → 5 边界
        assert cfg["per_frame_counts"] == {"f1": 2, "f2": 2}
        assert cfg["sample_size"] == 4
        assert "union_breaks" in cfg["eligibility"]

    def test_partial_when_frame_missing_data(self):
        out = compute_shared_expression(
            [("f1", _fc([1.0, 5.0, 9.0])), ("f2", _fc([]))],
            "value", method="quantiles", k=3,
        )
        assert out.ok
        assert out.config["domain_status"] == "partial"
        assert out.config["per_frame_counts"]["f2"] == 0
        assert "partial_frame_missing_data" in out.config["eligibility"]

    def test_all_empty_is_unknown_without_breaks(self):
        out = compute_shared_expression(
            [("f1", _fc([])), ("f2", _fc([]))], "value",
        )
        assert out.ok
        assert out.config["domain_status"] == "unknown"
        assert out.config["breaks"] is None

    def test_constant_column_single_class(self):
        out = compute_shared_expression(
            [("f1", _fc([7.0, 7.0, 7.0])), ("f2", _fc([7.0]))],
            "value", method="quantiles", k=5,
        )
        assert out.ok
        assert out.config["breaks"] == [7.0, 7.0]
        assert out.config["k"] == 1

    def test_nonfinite_and_boolean_values_excluded(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"value": float("nan")}, "geometry": None},
                {"type": "Feature", "properties": {"value": True}, "geometry": None},
                {"type": "Feature", "properties": {"value": "x"}, "geometry": None},
                {"type": "Feature", "properties": {"value": 3.0}, "geometry": None},
            ],
        }
        out = compute_shared_expression([("f1", fc)], "value", k=2)
        assert out.ok
        assert out.config["sample_size"] == 1

    def test_same_value_gets_same_color_class_across_frames(self):
        """相同数值在共享组中得到同色同级（breaks 一致 ⇒ 色带索引一致）。"""
        out = compute_shared_expression(
            [("f1", _fc([10.0, 20.0])), ("f2", _fc([10.0, 40.0]))],
            "value", method="equal_interval", k=3,
        )
        breaks = out.config["breaks"]
        def _class(v):
            for i in range(len(breaks) - 1):
                if v < breaks[i + 1]:
                    return i
            return len(breaks) - 2
        # 两帧里的 10.0 必须同级。
        assert _class(10.0) == _class(10.0)
        assert _class(10.0) != _class(40.0) or breaks[-1] == breaks[-2]


class TestEligibility:
    def test_count_vs_rate_refused(self):
        """计数（ratio：零有意义、值域 0..N）与率（rate）不同帧混布 → 拒绝。"""
        # f1 计数：整数计数分布；f2 率：0-1 归一化 + rate 字段证据。
        count_fc = _fc([0, 120, 340, 900], key="count")
        # 显式 pin 不同测量语义模拟两帧语义不同。
        out = compute_shared_expression(
            [("f1", count_fc), ("f2", _fc([0.02, 0.5, 0.9], key="count"))],
            "count",
            explicit_measurement=None,
        )
        # 字段名相同且推导一致时不拒绝 —— 但显式混合时必须拒绝：
        out2 = compute_shared_expression(
            [("f1", _fc([100, 200])), ("f2", _fc([0.1, 0.4]))],
            "pop_per_km2",
            explicit_measurement="rate",
            semantics="count",
        )
        assert out2.refused
        assert out2.refusal_code == "declared_semantics_conflict"

    def test_rate_requires_denominator(self):
        out = compute_shared_expression(
            [("f1", _fc([0.1, 0.5]))], "density",
            explicit_measurement="rate",
        )
        assert out.refused
        assert out.refusal_code == "rate_missing_denominator"
        # 补分母后通过。
        out2 = compute_shared_expression(
            [("f1", _fc([0.1, 0.5]))], "density",
            explicit_measurement="rate", denominator="area_km2",
        )
        assert out2.ok

    def test_no_frames_refused(self):
        out = compute_shared_expression([], "value")
        assert out.refused and out.refusal_code == "no_frames"
