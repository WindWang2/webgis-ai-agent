"""G41 独立 oracle（WP12）：预期值全部**手写**，不调用生产 assign 生成 expected。

小数据：
    a = [1, 5, 10, 15, 20, 25, 30, 35, 40]
    b = [1, 2, 3, 4,  5,  6,  7,  8,  9]
    manual breaks a=[10,20], b=[3,6], n=3

``upper_inclusive`` (>=，恰等断点归上类) 手算：
    ia: a<10 → 0；10<=a<20 → 1；a>=20 → 2
    ib: b<3  → 0；3<=b<6  → 1；b>=6  → 2
    逐要素 (ia,ib)：(0,0)(0,0)(1,1)(1,1)(2,1)(2,2)(2,2)(2,2)(2,2)
    class = ib*3+ia → 0,0,4,4,5,8,8,8,8
    joint[ib][ia] = [[2,0,0],[0,2,1],[0,0,4]]

``legacy_lower_inclusive`` (>，恰等断点归下类) 手算：
    class → 0,0,0,4,4,5,8,8,8
"""
from __future__ import annotations

import pytest

from app.services.bivariate_reliability_authoring.classify import (
    assign_axis,
    axis_intervals,
    compute_bivariate,
)
from app.services.bivariate_reliability_authoring.contract import parse_plan
from app.services.bivariate_reliability_authoring.reliability import compute_reliability
from app.services.bivariate_reliability_authoring.stats import (
    admit,
    scan_pairs,
    value_kind,
)
from tests.unit.bivariate_reliability.helpers import make_fc

A = [1, 5, 10, 15, 20, 25, 30, 35, 40]
B = [1, 2, 3, 4, 5, 6, 7, 8, 9]
ROWS = [{"a": float(a), "b": float(b)} for a, b in zip(A, B)]

# 9 个互不相同、非对称的颜色，用于暴露任何未转置/未反转的色阵错误。
ASYMMETRIC = [f"#{i:02x}{i:02x}{i:02x}" for i in range(10, 10 + 9)]


def _plan(rule="upper_inclusive", *, swap=False, flip=False, colors=True):
    body = {
        "n": 3,
        "axis_a": {"field": "a", "unit": "人", "label": "收入",
                   "method": "manual", "breaks": [10, 20]},
        "axis_b": {"field": "b", "unit": "%", "label": "比率",
                   "method": "manual", "breaks": [3, 6]},
        "boundary_rule": rule,
        "swap_axes": swap,
        "flip_rows": flip,
    }
    if colors:
        body["colors"] = list(ASYMMETRIC)
    else:
        body["matrix_id"] = "BiPurpleOrange"
    return parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                       "bivariate": body})


def _compute(plan):
    spec = plan.bivariate
    scan = scan_pairs(make_fc(ROWS)["features"], spec)
    return scan, compute_bivariate(scan, spec)


# ── 端点闭开合同（手算）─────────────────────────────────────────────────

@pytest.mark.parametrize("value,rule,expected", [
    (9.999, "upper_inclusive", 0), (10, "upper_inclusive", 1),
    (20, "upper_inclusive", 2), (10, "legacy_lower_inclusive", 0),
    (20, "legacy_lower_inclusive", 1), (20.001, "legacy_lower_inclusive", 2),
])
def test_boundary_rule_hand_computed(value, rule, expected):
    assert assign_axis(value, [10, 20], rule) == expected


def test_intervals_are_explicit_and_unit_aware():
    upper = axis_intervals([10, 20], "upper_inclusive", "人")
    assert [x["label"] for x in upper] == [
        "[−∞, 10) 人", "[10, 20) 人", "[20, +∞) 人"]
    assert (upper[0]["lower_closed"], upper[0]["upper_closed"]) == (True, False)
    legacy = axis_intervals([10, 20], "legacy_lower_inclusive", "%")
    assert [x["label"] for x in legacy] == [
        "(−∞, 10] %", "(10, 20] %", "(20, +∞] %"]
    assert (legacy[2]["lower_closed"], legacy[2]["upper_closed"]) == (False, True)


# ── 成对分类 / 联合计数 / 颜色（手算）──────────────────────────────────

def test_upper_inclusive_joint_counts_and_classes():
    scan, result = _compute(_plan("upper_inclusive"))
    assert scan.valid_pairs == 9
    assert result.class_by_feature == [0, 0, 4, 4, 5, 8, 8, 8, 8]
    assert result.joint == [[2, 0, 0], [0, 2, 1], [0, 0, 4]]
    assert result.colors == ASYMMETRIC
    assert [c["count"] for c in result.cells] == [0, 0, 4, 0, 2, 1, 2, 0, 0]
    assert [c["class_index"] for c in result.cells] == [6, 7, 8, 3, 4, 5, 0, 1, 2]


def test_legacy_lower_inclusive_differs_explicitly():
    scan, result = _compute(_plan("legacy_lower_inclusive"))
    assert result.class_by_feature == [0, 0, 0, 4, 4, 5, 8, 8, 8]
    assert result.joint == [[3, 0, 0], [0, 2, 1], [0, 0, 3]]
    assert result.boundary_rule == "legacy_lower_inclusive"


def test_swap_axes_transposes_counts_colors_and_class_index():
    plan = _plan("upper_inclusive", swap=True)
    scan, result = _compute(plan)
    # 手算：class = ia*3+ib → 0,0,4,4,7,8,8,8,8
    assert result.class_by_feature == [0, 0, 4, 4, 7, 8, 8, 8, 8]
    # joint[r=ia][c=ib] = [[2,0,0],[0,2,0],[0,1,4]]
    assert result.joint == [[2, 0, 0], [0, 2, 0], [0, 1, 4]]
    # display[ia*3+ib] = base[ib*3+ia]
    assert result.colors[7] == ASYMMETRIC[1 * 3 + 2] == ASYMMETRIC[5]
    assert result.colors[1] == ASYMMETRIC[1 * 3 + 0] == ASYMMETRIC[3]
    assert result.colors[3] == ASYMMETRIC[0 * 3 + 1] == ASYMMETRIC[1]
    assert result.colors[5] == ASYMMETRIC[2 * 3 + 1] == ASYMMETRIC[7]
    assert result.colors[4] == ASYMMETRIC[1 * 3 + 1] == ASYMMETRIC[4]
    # 图例轴整体转置：行 = 原 A、列 = 原 B
    from app.services.bivariate_reliability_authoring.classify import row_col_meta
    meta = row_col_meta(plan.bivariate, result)
    assert meta["row"]["field"] == "a" and meta["col"]["field"] == "b"
    assert meta["row"]["unit"] == "人" and meta["col"]["unit"] == "%"
    assert meta["swap_axes"] is True


def test_flip_rows_reverses_screen_cell_order_not_the_classification():
    """F1 复审：flip_rows 是**纯呈现**反转，必须真的改变屏幕格序/数值端点，
    且箭头一致；不能出现「图面逐格不变却把 ↑ 改成 ↓」的误导。"""
    _, plain = _compute(_plan("upper_inclusive", flip=False))
    plan = _plan("upper_inclusive", flip=True)
    scan, result = _compute(plan)
    # 类别索引与 paint 不受呈现方向影响（翻转不是重新分类）。
    assert result.class_by_feature == plain.class_by_feature
    assert result.joint == plain.joint
    assert result.colors == plain.colors
    # 屏幕格序真的反了：行块整体倒序，行内列序不变。
    n = 3
    plain_rows = [plain.cells[sr * n:(sr + 1) * n] for sr in range(n)]
    expected = [c["class_index"] for row in reversed(plain_rows) for c in row]
    assert [c["class_index"] for c in result.cells] == expected
    # 数值端点与格序一致：flip 顶行是低 B，plain 顶行是高 B。
    assert result.cells[0]["row_value_class"] == 0
    assert plain.cells[0]["row_value_class"] == 2
    assert result.cells[0]["row_interval"]["label"] == plain.intervals_b[0]["label"]
    assert plain.cells[0]["row_interval"]["label"] == plain.intervals_b[2]["label"]
    # 每个格子的计数仍来自同一联合矩阵，且颜色与 class_index 同源。
    for c in result.cells:
        assert c["count"] == result.joint[c["row"]][c["col"]]
        assert c["color"] == result.colors[c["class_index"]]
    from app.services.bivariate_reliability_authoring.classify import row_col_meta
    meta = row_col_meta(plan.bivariate, result)
    assert meta["row"]["arrow"] == "down"
    assert meta["row"]["direction"] == "low_to_high"


# ── 分型 / 缺失对账（手算）─────────────────────────────────────────────

def test_value_kinds_and_strict_admission():
    assert value_kind(True) == "bool"
    assert value_kind("12.5") == "numeric_string"
    assert value_kind(float("nan")) == "nan"
    assert value_kind(float("inf")) == "inf"
    assert value_kind(None) == "null"
    assert admit(True)[0] is False
    assert admit("12.5")[0] is False
    assert admit(0)[0] is True and admit(0)[1] == 0.0
    assert admit(float("inf"))[0] is False


def test_missing_reconciliation_and_no_silent_float():
    rows = [
        {"a": 1.0, "b": 1.0},      # valid pair
        {"a": 2.0, "b": None},     # only B missing
        {"a": None, "b": 3.0},     # only A missing
        {"a": None, "b": None},    # both missing
        {"a": "4", "b": 4.0},      # numeric_string → only A missing
        {"a": True, "b": 5.0},     # bool → only A missing
        {"a": float("inf"), "b": 6.0},
    ]
    plan = _plan("upper_inclusive")
    scan = scan_pairs(make_fc(rows)["features"], plan.bivariate)
    assert scan.total_features == 7
    assert scan.valid_pairs == 1
    # A 无效的行共 4 条：null / 数字字符串 / bool / Inf（B 都有效）
    assert scan.only_a_missing == 4
    assert scan.only_b_missing == 1
    assert scan.both_missing == 1
    recon = scan.reconcile()
    assert recon["balanced"] is True
    assert recon["accounted"] == 7
    assert scan.kind_counts_a["numeric_string"] == 1
    assert scan.kind_counts_a["bool"] == 1
    assert scan.kind_counts_a["inf"] == 1


# ── 可靠性映射 oracle（手算）───────────────────────────────────────────

def test_reliability_bucket_mapping_hand_computed():
    rows = [
        {"est": 10.0, "se": 0.0},    # 零误差必须保留 → b0 / 0.9
        {"est": 11.0, "se": 0.5},    # t=0.25 → b1 / 0.683333
        {"est": 12.0, "se": 1.0},    # t=0.50 → b2 / 0.466667
        {"est": 13.0, "se": 1.5},    # t=0.75 → b3 / 0.25
        {"est": 14.0, "se": None},   # 缺失 → unknown / 0.5
        {"est": 15.0, "se": 3.0},    # 越界 clamp → b3，clipped=1
    ]
    plan = parse_plan({
        "layer_id": "L1", "mode": "reliability_dual_channel",
        "reliability": {
            "estimate_field": "est", "estimate_unit": "m", "estimate_k": 4,
            "measure": {"kind": "standard_error", "value_field": "se",
                        "unit": "m", "source": "author_declared",
                        "method_note": "测试声明：仪器标准误"},
            "visual": {"channel": "opacity", "min_value": 0, "max_value": 2,
                       "min_output": 0.25, "max_output": 0.9,
                       "unknown_output": 0.5, "buckets": 4, "clip": "clamp"},
        }})
    result = compute_reliability(make_fc(rows)["features"], plan.reliability)
    # 越界误差使用独立桶，不折叠进任何有效可靠性等级（独立评审修复）。
    assert result.bucket_by_feature == [
        "b0", "b1", "b2", "b3", "unknown", "out_of_domain"]
    assert [b["output"] for b in result.buckets[:4]] == [
        0.9, 0.683333, 0.466667, 0.25]
    assert result.scan.zero_measure == 1
    assert result.scan.clipped == 1
    assert result.scan.unknown_measure == 1
    assert result.measure_meta["author_declared"] is True
    cases = dict((c[0], c[1]) for c in result.bucket_paint["cases"])
    assert cases["unknown"] == 0.5 and cases["b0"] == 0.9
    assert cases["out_of_domain"] == 0.5
    od = [b for b in result.buckets if b["key"] == "out_of_domain"][0]
    assert od["count"] == 1


def test_negative_measure_is_invalid_not_low_reliability():
    from app.services.bivariate_reliability_authoring import AuthoringError
    rows = [{"est": 1.0, "se": 1.0}, {"est": 2.0, "se": -1.0}]
    plan = parse_plan({
        "layer_id": "L1", "mode": "reliability_dual_channel",
        "reliability": {
            "estimate_field": "est", "estimate_unit": "m", "estimate_k": 2,
            "measure": {"kind": "variance", "value_field": "se",
                        "unit": "m²", "source": "author_declared",
                        "method_note": "测试声明：方差"},
            "visual": {"channel": "opacity", "min_value": 0, "max_value": 2,
                       "min_output": 0.25, "max_output": 0.9,
                       "unknown_output": 0.5, "buckets": 4},
        }})
    with pytest.raises(AuthoringError) as exc:
        compute_reliability(make_fc(rows)["features"], plan.reliability)
    assert exc.value.code == "INVALID_MEASURE_VALUES"
