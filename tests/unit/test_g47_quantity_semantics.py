"""G47 数量语义与面积比例（WP02 / WP04）单测。

守的纪律：

* 缺失、零、负数、布尔、字符串、NaN **分开**统计 —— 零不冒充缺失，缺失不
  参与总量，负数不被取绝对值；
* 面积 ∝ 数量靠**共同系数**成立，逐圆 clamp 被禁止；极端跨度直接 typed
  拒绝（``EXTREME_VALUE_RANGE``），不用"最小可见圆"伪装比例；
* 率/密度不是可加总量，必须在计划层就被拒绝（见 test_g47_contract_errors）。
"""
from __future__ import annotations

import math

import pytest

from app.services.cartogram_authoring.contract import clamp_plan_budget
from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.geography import prepare_geography
from app.services.cartogram_authoring.quantities import (
    MIN_VISIBLE_RADIUS,
    build_area_scale,
    classify_value,
    reference_circles,
    scan_quantities,
)
from tests.unit.g47_fixtures import (
    CELL,
    ORIGIN,
    frozen,
    grid_features,
    plan,
    square,
)

#: 3×3 网格的固定 9 个值（避免用短列表触发"长度不一致"守卫）。
V9 = [100, 200, 300, 400, 500, 600, 700, 800, 900]


def _nine(first: object, fill: object = 0) -> list:
    """9 个单元的值：首元素自定义，其余用 ``fill`` 补齐。"""
    return [first] + [fill] * 8


def _scan(features, **kwargs):
    opts = {
        "field_name": "pop", "unit": "人",
        "zero_policy": "list_only", "missing_policy": "list_only",
    }
    opts.update(kwargs)
    return scan_quantities(features, **opts)


def _vals(values):
    return grid_features(values=values)


# ── classify_value：五类判定 ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,kind,value,reason",
    [
        (None, "missing", None, ""),
        (0, "zero", 0.0, ""),
        (0.0, "zero", 0.0, ""),
        (12, "positive", 12.0, ""),
        (2.5, "positive", 2.5, ""),
        (-1, "invalid", None, "negative"),
        (True, "invalid", None, "boolean"),
        (False, "invalid", None, "boolean"),
        ("123", "invalid", None, "non_numeric"),
        ("", "invalid", None, "non_numeric"),
        (float("nan"), "invalid", None, "nonfinite"),
        (float("inf"), "invalid", None, "nonfinite"),
    ],
)
def test_classify_value_keeps_categories_separate(raw, kind, value, reason):
    """bool 先于 int 判定；NaN/Infinity 不当作可比值；负数不取绝对值。"""
    got_kind, got_value, got_reason = classify_value(raw)
    assert got_kind == kind
    assert got_value == value
    assert got_reason == reason


def test_bool_is_not_counted_as_one():
    """``True`` 在 Python 里是 ``int`` 子类，但绝不能算成 1 个人：typed 拒绝。"""
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals([True] + V9[1:]))
    assert ei.value.code == "FIELD_NOT_NUMERIC"
    assert ei.value.details["reasons"] == ["boolean"]


# ── 字段级拒绝 ──────────────────────────────────────────────────────────

def test_field_absent_everywhere_is_not_found_not_all_missing():
    feats = _vals(V9)
    for f in feats:
        f["properties"].pop("pop")
    with pytest.raises(AuthoringError) as ei:
        _scan(feats)
    assert ei.value.code == "FIELD_NOT_FOUND"
    # "字段不存在" 与 "所有要素都缺失" 是两件事，不能混报。
    assert ei.value.code != "MISSING_QUANTITY"


def test_string_value_reports_offenders_without_silent_repair():
    values = list(V9)
    values[1] = "n/a"
    values[3] = "many"
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals(values))
    err = ei.value
    assert err.code == "FIELD_NOT_NUMERIC"
    assert err.details["invalid_count"] == 2
    offenders = {o["feature_id"]: o["reason"] for o in err.details["offenders"]}
    assert offenders == {"r0c1": "non_numeric", "r1c0": "non_numeric"}
    assert err.correction_hint  # 必须给出可执行的下一步


def test_negative_value_is_rejected_rather_than_absolute():
    values = list(V9)
    values[4] = -50
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals(values))
    assert ei.value.code == "FIELD_NOT_NUMERIC"
    assert ei.value.details["reasons"] == ["negative"]
    assert ei.value.details["invalid_count"] == 1


# ── 身份与 geography 层对齐 ────────────────────────────────────────────

def test_float_feature_ids_are_not_demoted_to_row_placeholder():
    """浮点 id 必须与 geography 层的裁决一致，不能落入 ``#rowN`` 占位。

    占位 id 与 geography 单元对不上：正值圆被静默丢弃，却仍计入 ``total`` 与
    ``max_value``（共同系数 k），``zeros``/``missing`` 还会报出幻影 id。
    """
    feats = [
        square("a", ORIGIN[0], ORIGIN[1], CELL, {"name": "A", "pop": 900}),
        square(1.5, ORIGIN[0] + CELL, ORIGIN[1], CELL, {"name": "B", "pop": 300}),
        square(123.0, ORIGIN[0], ORIGIN[1] + CELL, CELL, {"name": "C", "pop": 0}),
        square(7, ORIGIN[0] + CELL, ORIGIN[1] + CELL, CELL,
               {"name": "D", "pop": None}),
    ]
    scan = _scan(feats)
    # 整数值 id 去小数（123.0 → "123"）、非整数值原样（1.5 → "1.5"）。
    assert [e.feature_id for e in scan.entries] == ["a", "1.5", "123", "7"]
    assert [e.feature_id for e in scan.positives] == ["a", "1.5"]
    assert scan.zeros == ["123"]
    assert scan.missing == ["7"]
    assert all(not e.feature_id.startswith("#row") for e in scan.entries)


def test_numeric_feature_ids_join_geography_units():
    """正值单元身份必须与 geography 准备的单元重合（不漏、不幻影）。

    compute 层用 ``u.feature_id in positive_ids`` 取圆：两边 id 只要差一点，
    该区域的圆就静默消失，而它的值还在 ``total``/``max_value`` 里。
    """
    feats = [
        square(1.5, ORIGIN[0], ORIGIN[1], CELL, {"name": "B", "pop": 300}),
        square(123.0, ORIGIN[0] + CELL, ORIGIN[1], CELL, {"name": "C", "pop": 900}),
    ]
    scan = _scan(feats)
    prepared = prepare_geography(feats, source_crs="EPSG:4326",
                                 target_crs="EPSG:3857", snap_tolerance_m=1.0,
                                 min_shared_length_m=10.0)
    unit_ids = {u.feature_id for u in prepared.units}
    assert unit_ids == {e.feature_id for e in scan.entries}
    assert {e.feature_id for e in scan.positives} <= unit_ids


# ── 零 / 缺失策略 ───────────────────────────────────────────────────────

def test_zero_is_kept_as_identity_not_merged_into_missing():
    values = [0, 100, 0, 400, 0, 100, 0, 400, 0]
    scan = _scan(_vals(values))
    assert scan.zeros == ["r0c0", "r0c2", "r1c1", "r2c0", "r2c2"]
    assert scan.missing == []
    assert scan.counts() == {"total_units": 9, "positive": 4, "zero": 5,
                             "missing": 0, "invalid": 0}
    # 总量只由正值构成：零不贡献、也不被当成缺失排除。
    assert scan.total == pytest.approx(1000.0)
    assert scan.max_value == 400.0
    assert scan.min_positive == 100.0


def test_missing_value_does_not_enter_total():
    values = list(V9)
    values[0] = None
    scan = _scan(_vals(values))
    assert scan.missing == ["r0c0"]
    assert scan.total == pytest.approx(sum(V9[1:]))
    assert scan.valid_units == 8


def test_zero_policy_reject_is_explicit():
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals(_nine(0, 100)), zero_policy="reject")
    assert ei.value.code == "ZERO_QUANTITY"
    assert ei.value.details["zero_ids"]


def test_missing_policy_reject_is_explicit():
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals(_nine(None, 100)), missing_policy="reject")
    assert ei.value.code == "MISSING_QUANTITY"


def test_all_zero_is_zero_total_not_generic_no_quantity():
    """全零与"没有正值"可执行下一步不同：前者要换字段，后者要看数据。"""
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals([0] * 9))
    assert ei.value.code == "ZERO_TOTAL"


def test_all_missing_is_no_valid_quantity():
    with pytest.raises(AuthoringError) as ei:
        _scan(_vals([None] * 9))
    assert ei.value.code == "NO_VALID_QUANTITY"


def test_summary_is_bounded_and_counts_are_honest():
    feats = grid_features(rows=4, cols=4,
                          values=[None] * 8 + [0] * 4 + [5] * 4)
    scan = _scan(feats)
    summary = scan.summary()
    assert len(summary["missing_ids"]) <= 12
    assert summary["counts"] == {"total_units": 16, "positive": 4, "zero": 4,
                                "missing": 8, "invalid": 0}
    assert summary["truncated"] is False


# ── 面积比例 ────────────────────────────────────────────────────────────

def test_area_is_exactly_proportional_to_value():
    """``area = π·k·v`` 对每个正值成立（相对误差 ~1e-16，不是"接近"）。"""
    scan = _scan(_vals(V9))
    scale = build_area_scale(scan, max_radius=36.0)
    for entry in scan.positives:
        expected = math.pi * scale.coefficient * entry.value
        got = scale.area(entry.value)
        assert abs(got - expected) / expected < 1e-12
        # 半径平方比 == 数量比（这才是读者比较的依据）。
        assert abs(scale.radius(entry.value) ** 2
                   - scale.coefficient * entry.value) < 1e-12


def test_one_shared_coefficient_no_per_circle_clamp():
    """所有圆的 ``r²/v`` 必须相同 —— 一旦逐圆 clamp 就会出现两个系数。"""
    scan = _scan(_vals([1, 4, 9, 16, 25, 36, 49, 64, 81]))
    scale = build_area_scale(scan, max_radius=40.0)
    ratios = {round(scale.radius(e.value) ** 2 / e.value, 9) for e in scan.positives}
    assert len(ratios) == 1
    receipt = scale.as_receipt()
    assert receipt["shared_coefficient"] is True
    assert receipt["per_circle_clamp"] is False


def test_max_radius_is_honoured_exactly():
    scan = _scan(_vals([100, 900] + [500] * 7))
    scale = build_area_scale(scan, max_radius=36.0)
    assert scale.max_radius == 36.0
    assert abs(scale.radius(scale.max_value) - 36.0) < 1e-12


def test_extreme_range_is_rejected_not_clamped_to_visible_minimum():
    """跨度 1e12 时最小圆半径低于可见阈值 —— 必须拒绝，而不是放大成"可见小圆"。"""
    values = [1e-6, 1e6] + [1e5] * 7
    with pytest.raises(AuthoringError) as ei:
        build_area_scale(_scan(_vals(values)), max_radius=36.0)
    assert ei.value.code == "EXTREME_VALUE_RANGE"
    assert ei.value.details["min_radius"] < MIN_VISIBLE_RADIUS
    assert "clamp" in ei.value.message


def test_visible_radius_boundary_is_exact_not_approximate():
    """可见阈值是**可复算**的边界：恰好在其上方通过、下方拒绝。

    ``r_min = max_radius * sqrt(v_min / v_max)``，因此
    ``v_min = (MIN_VISIBLE_RADIUS / max_radius)² * v_max`` 就是分界值。
    """
    max_radius, v_max = 36.0, 1e9
    boundary = (MIN_VISIBLE_RADIUS / max_radius) ** 2 * v_max

    accepted = build_area_scale(
        _scan(_vals([boundary * 1.01, v_max] + [v_max] * 7)),
        max_radius=max_radius)
    assert accepted.min_radius >= MIN_VISIBLE_RADIUS

    with pytest.raises(AuthoringError) as ei:
        build_area_scale(
            _scan(_vals([boundary * 0.99, v_max] + [v_max] * 7)),
            max_radius=max_radius)
    assert ei.value.code == "EXTREME_VALUE_RANGE"


def test_reference_circles_halve_radius_each_step():
    scan = _scan(_vals([100, 1600] + [400] * 7))
    scale = build_area_scale(scan, max_radius=36.0)
    refs = reference_circles(scale, count=3)
    assert [r["value"] for r in refs] == [1600.0, 400.0, 100.0]
    r = [ref["radius"] for ref in refs]
    assert r[0] > r[1] > r[2]
    assert r[0] / r[1] == pytest.approx(2.0, rel=1e-6)


# ── 计划预算与冻结锚 ────────────────────────────────────────────────────

def test_budget_rejects_instead_of_truncating():
    p = plan(max_features=5)
    with pytest.raises(AuthoringError) as ei:
        clamp_plan_budget(p, features=9, adjacency_edges=12)
    assert ei.value.code == "BUDGET_EXCEEDED"


def test_budget_passes_at_boundary():
    p = plan(max_features=9)
    clamp_plan_budget(p, features=9, adjacency_edges=12)


def test_frozen_source_anchor_carries_revision_and_fingerprint():
    src = frozen(grid_features(), revision=11)
    anchor = src.anchor()
    assert anchor["ref_id"] == "ref:g47-fixture"
    assert anchor["content_revision"] == 11
    assert anchor["content_fingerprint"].startswith("g47f-")
    assert "content_revision" in anchor["snapshot_guarantee"]
