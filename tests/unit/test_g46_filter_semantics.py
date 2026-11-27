"""G46：mapspec_filter null 相等语义与 evaluate_where 逐算子对齐（E08/E07）。

三处同一合同：服务端权威求值（evaluate_where）、前端 live MapLibre、
SVG/PNG/PDF 导出（mapspec_filter）。本测试用同一 fixture 断言两端一致。
"""
from __future__ import annotations

from app.services.feature_pages import _validate_ast, evaluate_where
from app.services.mapspec_filter import evaluate_filter, supported_filter
from app.services.linked_exploration import contract


def _feat(props):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": props}


FEATURES = [
    _feat({"f": None}),      # 显式 null
    _feat({"g": 1}),         # f 缺席
    _feat({"f": 1}),
    _feat({"f": "1"}),
    _feat({"f": True}),
    _feat({"f": 0}),
    _feat({"f": ""}),
]


def _assert_both_sides(where_raw: dict, feat: dict) -> bool:
    where = _validate_ast(where_raw)
    expr = contract.where_to_maplibre(where)
    assert supported_filter(expr), expr
    left = evaluate_where(where, feat)
    right = evaluate_filter(expr, feat)
    assert left == right, (
        f"semantics diverge for {where_raw} on {feat.get('properties')}: "
        f"evaluate_where={left} maplibre={right}")
    return left


class TestNullEqualityAlignment:
    def test_eq_null_hits_missing_and_explicit_null(self):
        """MapLibre 语义：缺失与显式 null 都取 null；null==null 命中。"""
        explicit = _assert_both_sides(
            {"op": "eq", "field": "f", "value": None}, FEATURES[0])
        missing = _assert_both_sides(
            {"op": "eq", "field": "f", "value": None}, FEATURES[1])
        present = _assert_both_sides(
            {"op": "eq", "field": "f", "value": None}, FEATURES[2])
        assert explicit and missing and not present

    def test_ne_null_hits_value_only(self):
        explicit = _assert_both_sides(
            {"op": "ne", "field": "f", "value": None}, FEATURES[0])
        missing = _assert_both_sides(
            {"op": "ne", "field": "f", "value": None}, FEATURES[1])
        present = _assert_both_sides(
            {"op": "ne", "field": "f", "value": None}, FEATURES[2])
        assert not explicit and not missing and present

    def test_eq_number_vs_string_vs_bool(self):
        """E06：1 / "1" / true 互不命中对方字面量。"""
        f1 = FEATURES[2]   # f=1
        assert _assert_both_sides({"op": "eq", "field": "f", "value": 1}, f1)
        assert not _assert_both_sides({"op": "eq", "field": "f", "value": "1"}, f1)
        assert not _assert_both_sides({"op": "eq", "field": "f", "value": True}, f1)
        ftrue = FEATURES[4]  # f=True
        assert not _assert_both_sides({"op": "eq", "field": "f", "value": 1}, ftrue)
        assert _assert_both_sides({"op": "eq", "field": "f", "value": True}, ftrue)
        fstr = FEATURES[3]   # f="1"
        assert _assert_both_sides({"op": "eq", "field": "f", "value": "1"}, fstr)

    def test_zero_and_empty_string_distinct(self):
        assert _assert_both_sides({"op": "eq", "field": "f", "value": 0}, FEATURES[5])
        assert _assert_both_sides({"op": "eq", "field": "f", "value": ""}, FEATURES[6])
        assert not _assert_both_sides({"op": "eq", "field": "f", "value": ""}, FEATURES[5])
        assert not _assert_both_sides({"op": "eq", "field": "f", "value": 0}, FEATURES[6])

    def test_int_float_cross_type_equal(self):
        feat = _feat({"n": 1.0})
        assert _assert_both_sides({"op": "eq", "field": "n", "value": 1}, feat)

    def test_in_typed(self):
        where = {"op": "in", "field": "f", "values": [1, "1", True, None]}
        assert _assert_both_sides(where, FEATURES[0])   # null in values
        assert _assert_both_sides(where, FEATURES[1])   # 缺席 → null in values
        assert _assert_both_sides(where, FEATURES[2])   # 1
        assert _assert_both_sides(where, FEATURES[3])   # "1"
        assert _assert_both_sides(where, FEATURES[4])   # True
        assert not _assert_both_sides(where, FEATURES[5])  # 0 not in
        assert not _assert_both_sides(where, FEATURES[6])  # "" not in

    def test_numeric_comparisons_skip_non_numbers(self):
        """非数值（含 bool/字符串/null）不命中数值比较 —— 两端一致。"""
        for op in ("gte", "gt", "lte", "lt"):
            where = {"op": op, "field": "f", "value": 0}
            for feat in (FEATURES[0], FEATURES[3], FEATURES[4], FEATURES[6]):
                _assert_both_sides(where, feat)  # 全部 False（两端一致即可）
        # 真正数值比较
        assert _assert_both_sides({"op": "gte", "field": "f", "value": 0}, FEATURES[5])

    def test_between_bounds(self):
        """E08：断点值闭开两端一致。"""
        for feat, val in ((FEATURES[5], 0),):
            assert _assert_both_sides(
                {"op": "between", "field": "f", "gte": 0, "lte": 0}, feat)

    def test_combined_and_or_not(self):
        where = {
            "op": "and",
            "terms": [
                {"op": "or", "terms": [
                    {"op": "eq", "field": "f", "value": 1},
                    {"op": "eq", "field": "f", "value": None},
                ]},
                {"op": "not", "term": {"op": "eq", "field": "g", "value": 1}},
            ],
        }
        assert _assert_both_sides(where, FEATURES[0])   # f null 命中，g 缺席 ≠ 1
        assert _assert_both_sides(where, FEATURES[2])   # f=1，g 缺席
        assert not _assert_both_sides(where, _feat({"f": 1, "g": 1}))


class TestLegacyBareKeyTypeId:
    """旧式裸键 $type/$id（adapter 实际发射的 ["==","$type",t] 一族）。

    P3 修复回归：``in`` 分支曾在 $type/$id 解析之前提前返回，
    ["in","$type",["literal",[…]]] 恒判 False（supported_filter 却放行）
    —— 整层要素在 SVG/PNG/PDF 导出中被静默排除。
    """

    def test_supported_filter_accepts_bare_key_in(self):
        assert supported_filter(["in", "$type", ["literal", ["Point", "LineString"]]])
        assert supported_filter(["in", "$id", ["literal", ["f1", "f9"]]])

    def test_in_type_matches_geometry_family(self):
        point = _feat({"f": 1})
        assert evaluate_filter(["in", "$type", ["literal", ["Point", "LineString"]]], point)
        assert not evaluate_filter(["in", "$type", ["literal", ["Polygon"]]], point)
        # Multi* 按几何族归约（与 == 分支的 removeprefix("Multi") 同口径）
        multi = {"type": "Feature",
                 "geometry": {"type": "MultiPolygon", "coordinates": [[[[0, 0]]]]},
                 "properties": {}}
        assert evaluate_filter(["in", "$type", ["literal", ["Polygon"]]], multi)

    def test_in_id_matches_feature_id(self):
        feat = _feat({"f": 1})
        feat["id"] = "f1"
        assert evaluate_filter(["in", "$id", ["literal", ["f1", "f9"]]], feat)
        assert not evaluate_filter(["in", "$id", ["literal", ["f9"]]], feat)
        numeric = _feat({})
        numeric["id"] = 7
        assert evaluate_filter(["in", "$id", ["literal", [7, 8]]], numeric)

    def test_eq_ne_type_id_unchanged_after_reorder(self):
        """挪位后 == / != 分支语义不变（$type/$id 仍解析）。"""
        point = _feat({})
        point["id"] = "p1"
        assert evaluate_filter(["==", "$type", "Point"], point)
        assert not evaluate_filter(["!=", "$type", "Point"], point)
        assert evaluate_filter(["==", "$id", "p1"], point)
        assert not evaluate_filter(["==", "$id", "other"], point)
