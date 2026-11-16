"""G46 contract 单元测试：组合语义、typed identity、MapLibre 编译、digest。

oracle：期望值独立手算/枚举，不调用生产 evaluator 生成 expected。
"""
from __future__ import annotations

import pytest

from app.services.linked_exploration import contract
from app.services.linked_exploration.errors import LinkedExplorationError


def _cat(cid, chart, field, values, include_null=False):
    return contract.parse_condition({
        "kind": "category", "condition_id": cid, "chart_id": chart,
        "field": field, "values": values, "include_null": include_null,
    })


def _rng(cid, chart, field, **bounds):
    return contract.parse_condition({
        "kind": "range", "condition_id": cid, "chart_id": chart,
        "field": field, **bounds,
    })


class TestCombinedWhere:
    def test_cross_dimension_and_same_dimension_or(self):
        """E02/E03：跨维度 AND；同分类条件内多值 OR。"""
        c1 = _cat("c1", "ch1", "type", ["a", "b"])
        c2 = _rng("c2", "ch2", "pop", gte=100, lte=500)
        w = contract.compile_combined_where([c1, c2])
        assert w["op"] == "and"
        # oracle：手算期望结构
        assert w["terms"][0] == {"op": "in", "field": "type", "values": ["a", "b"]}
        assert w["terms"][1] == {
            "op": "and",
            "terms": [
                {"op": "gte", "field": "pop", "value": 100.0},
                {"op": "lte", "field": "pop", "value": 500.0},
            ],
        }

    def test_single_condition_no_wrapper(self):
        c = _cat("c1", "ch1", "type", ["a"])
        w = contract.compile_combined_where([c])
        assert w == {"op": "in", "field": "type", "values": ["a"]}

    def test_empty_conditions_is_none(self):
        """无条件 = None（不过滤），不是永假。"""
        assert contract.compile_combined_where([]) is None

    def test_include_null_or_leaf(self):
        """E07：null 追加 eq-null 叶（命中缺席或 null）。"""
        c = _cat("c1", "ch1", "type", ["a"], include_null=True)
        w = contract.compile_combined_where([c])
        assert w == {
            "op": "or",
            "terms": [
                {"op": "in", "field": "type", "values": ["a"]},
                {"op": "eq", "field": "type", "value": None},
            ],
        }

    def test_only_null(self):
        c = _cat("c1", "ch1", "type", [], include_null=True)
        w = contract.compile_combined_where([c])
        assert w == {"op": "eq", "field": "type", "value": None}

    def test_facet_excludes_only_own_chart(self):
        """E11：facet 只排除自身条件，其余保留。"""
        c1 = _cat("c1", "ch1", "type", ["a"])
        c2 = _rng("c2", "ch2", "pop", gte=100)
        c3 = _cat("c3", "ch3", "zone", ["x"])
        w = contract.compile_combined_where([c1, c2, c3], exclude_chart_id="ch2")
        # oracle：剩 c1 和 c3 的 and
        assert w == {
            "op": "and",
            "terms": [
                {"op": "in", "field": "type", "values": ["a"]},
                {"op": "in", "field": "zone", "values": ["x"]},
            ],
        }

    def test_same_field_two_controls_is_and(self):
        """两个控件同字段：条件间一律 AND（口径可解释，不隐式合并）。"""
        c1 = _cat("c1", "ch1", "type", ["a"])
        c2 = _cat("c2", "ch2", "type", ["b"])
        w = contract.compile_combined_where([c1, c2])
        assert w["op"] == "and" and len(w["terms"]) == 2


class TestTypedIdentity:
    def test_distinct_scalars_not_merged(self):
        """E06：1 / "1" / true 三个 typed 值保留原始类型。"""
        c = _cat("c1", "ch1", "f", [1, "1", True])
        w = contract.compile_combined_where([c])
        assert w["values"] == [1, "1", True]

    def test_rejects_non_scalar(self):
        with pytest.raises(LinkedExplorationError):
            _cat("c1", "ch1", "f", [{"x": 1}])

    def test_rejects_unknown_op(self):
        with pytest.raises(LinkedExplorationError):
            contract.parse_condition({
                "kind": "category", "condition_id": "c", "chart_id": "ch",
                "field": "f", "values": ["a"], "op": "sql: drop table"})

    def test_range_finite_bounds_only(self):
        """E09 相关：NaN/Infinity 端点拒绝。"""
        with pytest.raises(Exception):
            _rng("c1", "ch1", "pop", gte=float("nan"))
        with pytest.raises(Exception):
            _rng("c1", "ch1", "pop", lte=float("inf"))

    def test_range_at_most_one_bound_per_side(self):
        with pytest.raises(Exception):
            _rng("c1", "ch1", "pop", gte=1, gt=2)

    def test_range_lower_le_upper(self):
        with pytest.raises(Exception):
            _rng("c1", "ch1", "pop", gte=500, lte=100)

    def test_range_empty_rejected(self):
        with pytest.raises(Exception):
            _rng("c1", "ch1", "pop")


class TestMapLibreCompile:
    def test_typed_in_no_to_string(self):
        """编译必须保留 typed identity（禁止 to-string 归一）。"""
        c = _cat("c1", "ch1", "f", [1, "1", True])
        expr = contract.where_to_maplibre(contract.compile_combined_where([c]))
        assert expr == ["in", ["get", "f"], ["literal", [1, "1", True]]]
        assert "to-string" not in str(expr)

    def test_eq_null(self):
        c = _cat("c1", "ch1", "f", [], include_null=True)
        expr = contract.where_to_maplibre(contract.compile_combined_where([c]))
        assert expr == ["==", ["get", "f"], None]

    def test_between_closed_bounds(self):
        """E08：闭区间两端 >= <=。"""
        c = _rng("c1", "ch1", "pop", gte=100, lte=500)
        expr = contract.where_to_maplibre(contract.compile_combined_where([c]))
        assert expr == ["all", [">=", ["get", "pop"], 100], ["<=", ["get", "pop"], 500]]

    def test_open_bounds(self):
        c = _rng("c1", "ch1", "pop", gt=0, lt=100)
        expr = contract.where_to_maplibre(contract.compile_combined_where([c]))
        assert expr == ["all", [">", ["get", "pop"], 0], ["<", ["get", "pop"], 100]]

    def test_integer_float_normalization(self):
        c = _rng("c1", "ch1", "pop", gte=100.0)
        expr = contract.where_to_maplibre(contract.compile_combined_where([c]))
        # 100.0 → 100（MapLibre JSON 编码一致）
        assert expr == [">=", ["get", "pop"], 100]

    def test_none_where_returns_none(self):
        assert contract.where_to_maplibre(None) is None

    def test_contains_rejected(self):
        with pytest.raises(LinkedExplorationError):
            contract.where_to_maplibre(
                {"op": "contains", "field": "f", "value": "x"})

    def test_supported_by_mapspec_filter(self):
        """编译结果必须落在 mapspec_filter 支持子集内（导出可用）。"""
        from app.services.mapspec_filter import supported_filter

        conds = [
            _cat("c1", "ch1", "type", ["a", "b"], include_null=True),
            _rng("c2", "ch2", "pop", gte=10, lt=99),
        ]
        expr = contract.where_to_maplibre(contract.compile_combined_where(conds))
        assert supported_filter(expr), expr


class TestDigest:
    def test_stable(self):
        c1 = _cat("c1", "ch1", "type", ["a"])
        c2 = _rng("c2", "ch2", "pop", gte=1)
        assert contract.conditions_digest([c1, c2]) == contract.conditions_digest(
            [_cat("c1", "ch1", "type", ["a"]), _rng("c2", "ch2", "pop", gte=1)])

    def test_empty(self):
        assert contract.conditions_digest([]) == "g46-empty"

    def test_order_matters_for_and(self):
        c1 = _cat("c1", "ch1", "type", ["a"])
        c2 = _rng("c2", "ch2", "pop", gte=1)
        assert contract.conditions_digest([c1, c2]) != contract.conditions_digest([c2, c1])


class TestChartSpec:
    def test_range_only_count(self):
        with pytest.raises(Exception):
            contract.ChartSpec(chart_id="c", kind="range", field="f",
                               aggregation="sum", value_field="v")

    def test_agg_needs_value_field(self):
        with pytest.raises(Exception):
            contract.ChartSpec(chart_id="c", kind="category", field="f",
                               aggregation="mean")

    def test_bins_bounds(self):
        with pytest.raises(Exception):
            contract.ChartSpec(chart_id="c", kind="range", field="f", bins=64)

    def test_unknown_kind(self):
        with pytest.raises(Exception):
            contract.ChartSpec(chart_id="c", kind="pie", field="f")
