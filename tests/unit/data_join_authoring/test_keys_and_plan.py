"""G38 键/空值/类型政策 + 计划契约/digest（J04/J05 锚点）。"""
from __future__ import annotations

import pytest

from app.services.data_join_authoring import contracts
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.keys import (
    KeyFieldSpec,
    canonical_key_value,
    row_key,
)
from app.services.data_join_authoring.plan import parse_plan
from tests.unit.data_join_authoring.fixtures import attr_plan


SPEC = KeyFieldSpec(left_field="k", right_field="k")


# ── J05：类型分族 + 空值政策 ─────────────────────────────────────────

def test_type_families_never_conflate():
    s001 = canonical_key_value("001", SPEC)
    s1 = canonical_key_value("1", SPEC)
    n1 = canonical_key_value(1, SPEC)
    bT = canonical_key_value(True, SPEC)
    empty = canonical_key_value("", SPEC)
    assert len({s001, s1, n1, bT, empty}) == 5
    assert s001 != s1 != n1 != bT != empty


def test_numeric_family_int_float_equivalent():
    """数值族统一（federation numeric normalization 语义）：1 == 1.0。"""
    assert canonical_key_value(1, SPEC) == canonical_key_value(1.0, SPEC)
    assert canonical_key_value(2, SPEC) != canonical_key_value(2.5, SPEC)


def test_null_missing_never_match():
    assert canonical_key_value(None, SPEC) is None
    # 行内 null / 字段缺失 → 整键 None（null 对 null 也不匹配）
    assert row_key({"k": None}, [SPEC], "left") is None
    assert row_key({}, [SPEC], "left") is None


def test_nan_inf_rejected_as_keys():
    assert canonical_key_value(float("nan"), SPEC) is None
    assert canonical_key_value(float("inf"), SPEC) is None


def test_composite_key_is_tuple_and_null_propagates():
    specs = [KeyFieldSpec(left_field="prov", right_field="p"),
             KeyFieldSpec(left_field="county", right_field="c")]
    k1 = row_key({"prov": "13", "county": "正定县"}, specs, "left")
    k2 = row_key({"p": "13", "c": "正定县"}, specs, "right")
    assert k1 == k2
    assert row_key({"prov": "41", "county": "正定县"}, specs, "left") != k1
    assert row_key({"prov": "13"}, specs, "left") is None


def test_transforms_applied_and_visible():
    spec = KeyFieldSpec(left_field="k", right_field="k", transform="trim")
    assert canonical_key_value(" 110101 ", spec) == ("s", "110101")
    spec_cf = KeyFieldSpec(left_field="k", right_field="k", transform="casefold")
    assert canonical_key_value("AbC", spec_cf) == ("s", "abc")
    spec_z = KeyFieldSpec(left_field="k", right_field="k",
                          transform="zfill", zfill_width=6)
    assert canonical_key_value("001", spec_z) == ("s", "000001")


def test_unknown_key_type_typed_blocked():
    with pytest.raises(DataJoinError) as ei:
        canonical_key_value([1, 2], SPEC)
    assert ei.value.code == "type_conflict"


def test_unknown_transform_rejected():
    with pytest.raises(DataJoinError) as ei:
        KeyFieldSpec(left_field="k", right_field="k", transform="lower")
    assert ei.value.code == "invalid_plan"


# ── 计划契约 / digest ────────────────────────────────────────────────

def test_digest_changes_with_transform_and_keys():
    base = parse_plan(attr_plan())
    with_transform = parse_plan(attr_plan(
        keys=[{"left_field": "adcode", "right_field": "adcode",
               "transform": "trim"}]))
    composite = parse_plan(attr_plan(
        keys=[{"left_field": "adcode", "right_field": "adcode"},
              {"left_field": "prov", "right_field": "prov"}]))
    assert len({base.normalized_digest(), with_transform.normalized_digest(),
                composite.normalized_digest()}) == 3


def test_digest_changes_with_source_and_policy():
    base = parse_plan(attr_plan())
    other_source = parse_plan(attr_plan(
        right={"kind": "table_rows", "session_id": "s", "ref": "ref:pop2"}))
    inner = parse_plan(attr_plan(keep_side="inner"))
    d = {base.normalized_digest(), other_source.normalized_digest(),
         inner.normalized_digest()}
    assert len(d) == 3


def test_vocabulary_rejections():
    """parse_plan 统一转 typed invalid_plan（词表 fail-closed）。"""
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(mode="spatial_line_polygon"))
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(null_policy="nulls_match"))
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(output_cardinality="many_to_many"))
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(keep_side="right"))
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(aggregates=[{"function": "median", "right_field": "pop"}]))
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(spatial={"predicate": "within"}))  # attribute 模式拒绝


def test_spatial_cardinality_consistency():
    parse_plan({"mode": "spatial_point_in_polygon",
                "left": {"kind": "session_ref", "session_id": "s", "ref": "r"},
                "right": {"kind": "session_ref", "session_id": "s", "ref": "r2"},
                "spatial": {"predicate": "within", "multi_match": "expand_all"},
                "output_cardinality": "one_to_many"})
    with pytest.raises(DataJoinError):
        parse_plan({"mode": "spatial_point_in_polygon",
                    "left": {"kind": "session_ref", "session_id": "s", "ref": "r"},
                    "right": {"kind": "session_ref", "session_id": "s", "ref": "r2"},
                    "spatial": {"predicate": "within", "multi_match": "expand_all"},
                    "output_cardinality": "many_to_one"})  # 不一致


def test_budget_clamped_to_hard_caps():
    plan = parse_plan(attr_plan(budgets={
        "max_source_rows": 99_999_999, "max_join_pairs": 10, "max_output_rows": 5}))
    eff = plan.effective_budgets()
    assert eff["max_source_rows"] == contracts.HARD_MAX_SOURCE_ROWS
    assert eff["max_join_pairs"] == 10
    assert eff["max_output_rows"] == 5


def test_unknown_budget_rejected():
    with pytest.raises(DataJoinError):
        parse_plan(attr_plan(budgets={"deadline": 10}))


def test_fail_closed_error_codes():
    with pytest.raises(ValueError):
        DataJoinError("no_such_code", "x")
