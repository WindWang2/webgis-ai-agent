"""多源指标求值器（WP2）单元测试 —— 全部独立手算 oracle。

覆盖：算子语义（count/sum/mean/weighted_mean/median/distinct_count）、
分母治理（零/空/全非数值 → unavailable + 原因码）、跨源 rate（每万人
oracle、键匹配/缺失/不可比披露）、typed 分组、组帽溢出、面积权重
shoelace oracle、有界帽（median/distinct 超帽 unavailable 不近似）。
"""
from __future__ import annotations

import pytest

from app.services.linked_exploration import contract, metrics


def _feat(props, geometry=None):
    return {"type": "Feature", "properties": props, "geometry": geometry}


def _pop(sid="pop"):
    return {"source_id": sid, "role": "primary",
            "anchor": {"ref_id": f"ref:{sid}", "content_revision": 1}}


def _src(sid, role="context"):
    return {"source_id": sid, "role": role,
            "anchor": {"ref_id": f"ref:{sid}", "content_revision": 1}}


def _metric(**kw):
    base = {"metric_id": "m1", "op": "count"}
    base.update(kw)
    return contract.MetricSpec.model_validate(base)


def _plan(sources, links=(), metrics_=()):
    raw = {
        "schema_version": 3,
        "sources": sources,
        "links": list(links),
        "metrics": list(metrics_),
        "charts": [],
        "conditions": [],
    }
    return contract.ExplorationPlan.model_validate(raw)


def _attr_link(lid, left, right, lf, rf):
    return {"link_id": lid, "kind": "attribute", "left_source_id": left,
            "right_source_id": right,
            "keys": [{"left_field": lf, "right_field": rf}],
            "cardinality": "one_to_many", "propagate": "none"}


class TestMetricSpecValidation:
    def test_value_ops_require_value_field(self):
        for op in ("sum", "mean", "weighted_mean", "distinct_count", "median"):
            with pytest.raises(ValueError):
                _metric(op=op)

    def test_weighted_mean_requires_weight_or_area(self):
        with pytest.raises(ValueError):
            _metric(op="weighted_mean", source_id="pop",
                    value_field="gdp")
        ok = _metric(op="weighted_mean", value_field="gdp",
                     weight_field="pop")
        assert ok.weight_field == "pop"

    def test_weight_field_xor_area(self):
        with pytest.raises(ValueError):
            _metric(op="weighted_mean", value_field="gdp",
                    weight_field="pop", weight_by_area=True)

    def test_weight_only_on_weighted_mean(self):
        with pytest.raises(ValueError):
            _metric(op="mean", value_field="v", weight_field="w")

    def test_cross_source_only_for_rate(self):
        with pytest.raises(ValueError):
            _metric(op="sum", value_field="v", source_id="a",
                    denominator_source_id="b", link_id="l1")

    def test_cross_rate_needs_both_denominator_and_link(self):
        with pytest.raises(ValueError):
            _metric(op="rate", source_id="a", link_id="l1")
        with pytest.raises(ValueError):
            _metric(op="rate", source_id="a", denominator_source_id="b")

    def test_scale_must_be_finite_positive(self):
        with pytest.raises(ValueError):
            _metric(op="count", scale=0)
        with pytest.raises(ValueError):
            _metric(op="count", scale=-1)
        with pytest.raises(ValueError):
            _metric(op="count", scale=float("inf"))
        assert _metric(op="count", scale=10000).scale == 10000

    def test_plan_rejects_unknown_metric_refs(self):
        with pytest.raises(ValueError, match="link_id"):
            _plan(
                [_pop(), _src("school")],
                links=[_attr_link("l1", "school", "pop", "district", "district")],
                metrics_=[{"metric_id": "m", "op": "rate", "source_id": "school",
                           "link_id": "ghost",
                           "denominator_source_id": "pop"}],
            )

    def test_plan_rejects_duplicate_metric_ids(self):
        with pytest.raises(ValueError, match="metric_id 重复"):
            _plan([_pop()], metrics_=[
                {"metric_id": "m", "op": "count"},
                {"metric_id": "m", "op": "count"},
            ])


class TestWithinSourceOps:
    def test_count_and_sum_mean_hand_computed(self):
        rows = [
            _feat({"district": "武侯区", "v": 2}),
            _feat({"district": "武侯区", "v": 4}),
            _feat({"district": "锦江区", "v": 10}),
            _feat({"district": "锦江区", "v": None}),      # null 计数
            _feat({"district": "锦江区", "v": "x"}),        # 非数值计数
            _feat({"district": "锦江区", "v": float("nan")}),  # 非有限
        ]
        m = _metric(op="sum", source_id="pop", value_field="v")
        out = metrics.compute_metric(m, {"pop": rows})
        # oracle：sum 全组 = 2+4+10 = 16（NaN 非有限不计入）
        assert out["groups"][0]["value"] == 16.0
        assert out["groups"][0]["numeric_count"] == 3
        assert out["groups"][0]["null_count"] == 1
        assert out["groups"][0]["non_numeric_count"] == 2

        m2 = _metric(op="mean", source_id="pop", value_field="v",
                     group_field="district")
        out2 = metrics.compute_metric(m2, {"pop": rows})
        by_key = {g["key"]: g for g in out2["groups"]}
        assert by_key["武侯区"]["value"] == 3.0   # (2+4)/2
        assert by_key["锦江区"]["value"] == 10.0  # 只有 10 有效

    def test_count_with_group_and_empty_source(self):
        m = _metric(op="count", source_id="pop", group_field="district")
        out = metrics.compute_metric(m, {"pop": []})
        # 空行集 = 无组（不是 0 组的假值）；单组视图由服务层披露
        assert out["groups"] == []

    def test_mean_no_numeric_is_unavailable_not_zero(self):
        rows = [_feat({"v": None}), _feat({"v": "x"})]
        m = _metric(op="mean", source_id="pop", value_field="v")
        out = metrics.compute_metric(m, {"pop": rows})
        assert out["groups"][0]["available"] is False
        assert out["groups"][0]["unavailable_reason"] == "no_numeric_values"
        assert out["groups"][0]["value"] is None

    def test_median_odd_even_and_cap(self):
        rows = [_feat({"v": v}) for v in [5, 1, 3]]
        m = _metric(op="median", source_id="pop", value_field="v")
        out = metrics.compute_metric(m, {"pop": rows})
        assert out["groups"][0]["value"] == 3.0  # oracle: [1,3,5]

        rows_even = [_feat({"v": v}) for v in [4, 1, 2, 3]]
        out2 = metrics.compute_metric(m, {"pop": rows_even})
        assert out2["groups"][0]["value"] == 2.5  # (2+3)/2

        big = [_feat({"v": i}) for i in range(metrics.QUANTILE_MAX_VALUES + 1)]
        out3 = metrics.compute_metric(m, {"pop": big})
        assert out3["groups"][0]["available"] is False
        assert out3["groups"][0]["unavailable_reason"] == "unsupported_at_scale"

    def test_distinct_count_typed_and_cap(self):
        rows = [
            _feat({"v": 1}), _feat({"v": 1.0}),   # 数值族归并 → 1 个
            _feat({"v": "1"}),                     # 字符串族独立 → +1
            _feat({"v": True}),                    # bool 族独立 → +1
            _feat({"v": None}),                    # null 不参与
            _feat({"v": ["x"]}),                   # 不可比披露
        ]
        m = _metric(op="distinct_count", source_id="pop", value_field="v")
        out = metrics.compute_metric(m, {"pop": rows})
        assert out["groups"][0]["value"] == 3.0
        assert out["groups"][0]["invalid_weight_count"] == 1

        big = [_feat({"v": f"k{i:06d}"})
               for i in range(metrics.DISTINCT_VALUE_CAP + 1)]
        out2 = metrics.compute_metric(m, {"pop": big})
        assert out2["groups"][0]["unavailable_reason"] == "unsupported_at_scale"

    def test_weighted_mean_hand_computed(self):
        rows = [
            _feat({"v": 10, "w": 1}),
            _feat({"v": 20, "w": 3}),
            _feat({"v": 999, "w": -1}),   # 负权重排除
            _feat({"v": 999, "w": None}),  # 权重缺失排除
        ]
        m = _metric(op="weighted_mean", source_id="pop", value_field="v",
                    weight_field="w")
        out = metrics.compute_metric(m, {"pop": rows})
        # oracle：(10*1 + 20*3) / (1+3) = 70/4 = 17.5
        assert out["groups"][0]["value"] == 17.5
        assert out["groups"][0]["invalid_weight_count"] == 2

    def test_weighted_mean_zero_total_weight(self):
        rows = [_feat({"v": 10, "w": 0}), _feat({"v": 20, "w": 0})]
        m = _metric(op="weighted_mean", source_id="pop", value_field="v",
                    weight_field="w")
        out = metrics.compute_metric(m, {"pop": rows})
        assert out["groups"][0]["unavailable_reason"] == "zero_total_weight"

    def test_weight_by_area_shoelace_oracle(self):
        # 矩形 (0,0)-(4,0)-(4,3)-(0,3) 面积 = 12；(0,0)-(1,0)-(1,1)-(0,1) = 1
        rect = {"type": "Polygon", "coordinates": [
            [[0, 0], [4, 0], [4, 3], [0, 3], [0, 0]]]}
        square = {"type": "Polygon", "coordinates": [
            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        rows = [
            _feat({"v": 10}, geometry=rect),
            _feat({"v": 20}, geometry=square),
            _feat({"v": 30}, geometry=None),      # 无几何排除
        ]
        m = _metric(op="weighted_mean", source_id="pop", value_field="v",
                    weight_by_area=True)
        out = metrics.compute_metric(m, {"pop": rows})
        # oracle：(10*12 + 20*1) / (12+1) = 140/13 ≈ 10.769230...
        assert abs(out["groups"][0]["value"] - 140 / 13) < 1e-9
        assert out["groups"][0]["invalid_weight_count"] == 1
        assert "area_note" in out["disclosure"]


class TestRateDenominatorGovernance:
    def test_within_rate_zero_denominator_unavailable(self):
        rows = [
            _feat({"district": "武侯区", "births": 100, "pop": 50000}),
            _feat({"district": "武侯区", "births": 100, "pop": 50000}),
            _feat({"district": "空区", "births": 10, "pop": 0}),
            _feat({"district": "缺区", "births": 5, "pop": None}),
        ]
        m = _metric(op="rate", source_id="pop", value_field="births",
                    denominator_field="pop", group_field="district")
        out = metrics.compute_metric(m, {"pop": rows})
        by_key = {g["key"]: g for g in out["groups"]}
        # oracle：200 / 100000 = 0.002
        assert by_key["武侯区"]["available"] is True
        assert by_key["武侯区"]["value"] == pytest.approx(0.002)
        assert by_key["空区"]["unavailable_reason"] == "denominator_zero"
        assert by_key["缺区"]["unavailable_reason"] == "denominator_missing"
        assert "denominator_note" in out["disclosure"]

    def test_rate_scale_per_10k(self):
        rows = [_feat({"district": "武侯区", "births": 100, "pop": 50000})]
        m = _metric(op="rate", source_id="pop", value_field="births",
                    denominator_field="pop", group_field="district",
                    scale=10000)
        out = metrics.compute_metric(m, {"pop": rows})
        # oracle：100/50000 * 10000 = 20
        assert out["groups"][0]["value"] == pytest.approx(20.0)


class TestCrossSourceRate:
    def _fixtures(self):
        schools = [
            _feat({"district": "武侯区", "type": "public"}),
            _feat({"district": "武侯区", "type": "private"}),
            _feat({"district": "武侯区", "type": "public"}),
            _feat({"district": "锦江区", "type": "public"}),
            _feat({"district": "青羊区", "type": "public"}),  # 人口表没有青羊区
            _feat({"type": "public"}),                        # 缺键
            _feat({"district": ["x"], "type": "public"}),     # 不可比键
        ]
        population = [
            _feat({"district": "武侯区", "pop": 1000000}),
            _feat({"district": "锦江区", "pop": 500000}),
            _feat({"district": "零人口区", "pop": 0}),
            _feat({"district": "缺人口区", "pop": None}),
        ]
        return schools, population

    def _link_and_metric(self):
        link = _attr_link("l1", "school", "pop", "district", "district")
        metric = _metric(op="rate", source_id="school",
                         denominator_source_id="pop", denominator_field="pop",
                         link_id="l1", scale=10000, unit="所/万人")
        return link, metric

    def test_schools_per_10k_hand_computed(self):
        schools, population = self._fixtures()
        link, metric = self._link_and_metric()
        plan = _plan(
            [_src("pop", role="primary"), _src("school")],
            links=[link], metrics_=[metric.model_dump()])
        m = plan.metrics[0]
        out = metrics.compute_metric(
            m, {"school": schools, "pop": population},
            links_by_id={lk.link_id: lk for lk in plan.links})
        by_key = {g["key"]: g for g in out["groups"]}
        # oracle：武侯区 3 所 / 1,000,000 人 × 10000 = 0.03 所/万人
        assert by_key["武侯区"]["value"] == pytest.approx(0.03)
        assert by_key["武侯区"]["numerator"] == 3.0
        assert by_key["武侯区"]["denominator"] == 1000000.0
        # 锦江区 1 / 500000 × 10000 = 0.02
        assert by_key["锦江区"]["value"] == pytest.approx(0.02)
        # 分母治理：零人口 / 缺人口 → unavailable，绝不返回 0 冒充率
        assert by_key["零人口区"]["unavailable_reason"] == "denominator_zero"
        assert by_key["缺人口区"]["unavailable_reason"] == "denominator_missing"
        # 诚实披露：青羊区学校无分母匹配、1 缺键、1 不可比
        assert out["disclosure"]["numerator_rows_unmatched_key"] == 1
        assert out["disclosure"]["numerator_rows_missing_key"] == 1
        assert out["disclosure"]["numerator_rows_invalid_key"] == 1
        assert out["groups"] and by_key["武侯区"]["available"] is True

    def test_cross_rate_with_value_field_numerator(self):
        link = _attr_link("l1", "school", "pop", "district", "district")
        metric = _metric(op="rate", source_id="school", value_field="students",
                         denominator_source_id="pop", denominator_field="pop",
                         link_id="l1")
        plan = _plan(
            [_src("pop", role="primary"), _src("school")],
            links=[link], metrics_=[metric.model_dump()])
        m = plan.metrics[0]
        schools = [
            _feat({"district": "武侯区", "students": 800}),
            _feat({"district": "武侯区", "students": 1200}),
            _feat({"district": "武侯区", "students": None}),  # 非数值不计入
        ]
        population = [_feat({"district": "武侯区", "pop": 2000})]
        out = metrics.compute_metric(
            m, {"school": schools, "pop": population},
            links_by_id={lk.link_id: lk for lk in plan.links})
        # oracle：分子 = 800+1200 = 2000；分母 = 2000；rate = 1.0
        g = out["groups"][0]
        assert g["value"] == pytest.approx(1.0)
        assert g["numerator"] == 2000.0
        assert g["denominator"] == 2000.0

    def test_spatial_cross_metric_typed_rejected(self):
        link = {"link_id": "l1", "kind": "spatial_within",
                "left_source_id": "school", "right_source_id": "pop",
                "predicate": "within", "cardinality": "one_to_many"}
        metric = _metric(op="rate", source_id="school",
                         denominator_source_id="pop", link_id="l1")
        plan = _plan([_src("pop", role="primary"), _src("school")],
                     links=[link], metrics_=[metric.model_dump()])
        with pytest.raises(Exception) as ei:
            metrics.compute_metric(
                plan.metrics[0], {"school": [], "pop": []},
                links_by_id={lk.link_id: lk for lk in plan.links})
        assert "SPATIAL_METRIC_UNSUPPORTED" in str(ei.value) or \
            getattr(ei.value, "code", "") == "SPATIAL_METRIC_UNSUPPORTED"

    def test_transform_keys_match(self):
        """变换键跨源匹配：左 "武侯区 "（尾空格）与右 "武侯区" trim 后匹配。"""
        link = {"link_id": "l1", "kind": "attribute",
                "left_source_id": "school", "right_source_id": "pop",
                "keys": [{"left_field": "district", "right_field": "district",
                          "transform": "trim"}],
                "cardinality": "one_to_many"}
        metric = _metric(op="rate", source_id="school",
                         denominator_source_id="pop", denominator_field="pop",
                         link_id="l1")
        plan = _plan([_src("pop", role="primary"), _src("school")],
                     links=[link], metrics_=[metric.model_dump()])
        schools = [_feat({"district": "武侯区 "})]   # 尾随空格
        population = [_feat({"district": "武侯区", "pop": 1000})]
        out = metrics.compute_metric(
            plan.metrics[0], {"school": schools, "pop": population},
            links_by_id={lk.link_id: lk for lk in plan.links})
        assert out["groups"][0]["value"] == pytest.approx(
            1 / 1000 * 1)  # scale None → rate 原值 0.001
        assert out["disclosure"]["numerator_rows_unmatched_key"] == 0


class TestGroupCaps:
    def test_group_overflow_other_truncated(self):
        n = metrics.MAX_GROUPS + 5
        rows = [_feat({"district": f"d{i:04d}", "v": 1}) for i in range(n)]
        m = _metric(op="count", source_id="pop", group_field="district")
        out = metrics.compute_metric(m, {"pop": rows})
        assert out["truncated"] is True
        assert len(out["groups"]) == metrics.MAX_GROUPS
        other = [g for g in out["groups"] if g["key"] == "__other__"]
        assert len(other) == 1
        assert other[0]["count"] == n - (metrics.MAX_GROUPS - 1)

    def test_scan_limit_guard(self):
        n = metrics.METRIC_SCAN_LIMIT + 1
        rows = [_feat({"v": 1}) for _ in range(n)]
        m = _metric(op="count", source_id="pop")
        with pytest.raises(Exception) as ei:
            metrics.compute_metric(m, {"pop": rows})
        assert getattr(ei.value, "code", "") == "METRIC_OVER_LIMIT"


class TestReviewRegressionAnchors:
    """审查修复回归锚（A-P3-5/6/11：防回退）。"""

    def test_fsum_overflow_group_unavailable_not_500(self):
        """A-P3-5：1e308 双行 fsum 中间溢出 → 该组 unavailable_scale。"""
        rows = [_feat({"v": 1e308}), _feat({"v": 1e308})]
        m = _metric(op="sum", source_id="pop", value_field="v")
        out = metrics.compute_metric(m, {"pop": rows})
        g = out["groups"][0]
        assert g["available"] is False
        assert g["unavailable_reason"] == "unsupported_at_scale"

    def test_negative_denominator_unavailable(self):
        """A-P3-6：负分母（脏数据）→ denominator_non_positive。"""
        rows = [_feat({"district": "d", "births": 10, "pop": -100})]
        m = _metric(op="rate", source_id="pop", value_field="births",
                    denominator_field="pop", group_field="district")
        out = metrics.compute_metric(m, {"pop": rows})
        g = out["groups"][0]
        assert g["available"] is False
        assert g["unavailable_reason"] == "denominator_non_positive"

    def test_cross_rate_grain_is_denominator_side(self):
        """A-P3-11：跨源 rate 的 grain_source_id = 分母侧（键即分组）。"""
        link = _attr_link("l1", "school", "pop", "district", "district")
        metric = _metric(op="rate", source_id="school",
                         denominator_source_id="pop", link_id="l1")
        plan = _plan([_src("pop", role="primary"), _src("school")],
                     links=[link], metrics_=[metric.model_dump()])
        m = plan.metrics[0]
        out = metrics.compute_metric(
            m, {"school": [], "pop": []},
            links_by_id={lk.link_id: lk for lk in plan.links})
        assert out["grain_source_id"] == "pop"
