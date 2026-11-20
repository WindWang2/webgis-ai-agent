"""T-B3：计划合同校验（验收 3/9 + 关闭词表 + 资源帽）。"""
from __future__ import annotations

import pytest

from app.services.thematic_authoring.contract import (
    MAX_BREAKS,
    MAX_CATEGORIES,
    PLAN_SCHEMA_VERSION,
    parse_plan,
)
from app.services.thematic_authoring.errors import ThematicAuthoringError


def _ok(**kw):
    base = {"layer_id": "result-1"}
    base.update(kw)
    return parse_plan(base)


def test_valid_manual_breaks_plan_digest_stable():
    p1 = _ok(mode="graduated_manual", breaks=[0, 10, 20, 50], palette="YlOrRd")
    p2 = _ok(mode="graduated_manual", breaks=[0, 10, 20, 50], palette="YlOrRd")
    assert p1.plan_digest() == p2.plan_digest()
    p3 = _ok(mode="graduated_manual", breaks=[0, 10, 20, 51], palette="YlOrRd")
    assert p1.plan_digest() != p3.plan_digest()


def test_reject_equal_breaks_with_index():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[0, 10, 10, 50])
    err = ei.value
    assert err.code == "BREAKS_NOT_STRICTLY_INCREASING"
    assert err.index == 2
    assert "10.0" in err.message


def test_reject_disorderly_breaks_no_autosort():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[0, 20, 10])
    assert ei.value.code == "BREAKS_NOT_STRICTLY_INCREASING"
    # 不自动排序后假装采用：拒绝才是合同（验收 3）


def test_reject_nonfinite_and_too_many_breaks():
    with pytest.raises(ThematicAuthoringError):
        parse_plan({"layer_id": "L", "mode": "graduated_manual",
                    "breaks": [0, float("nan"), 10]})
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[float(i) for i in range(MAX_BREAKS + 1)])
    assert ei.value.code == "INVALID_BREAKS"


def test_reject_unknown_version_and_method_and_mode():
    with pytest.raises(ThematicAuthoringError) as ei:
        parse_plan({"schema_version": 99, "layer_id": "L", "mode": "graduated_auto"})
    assert ei.value.code == "UNKNOWN_PLAN_VERSION"
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_auto", method="jenks_magic")
    assert ei.value.code == "UNKNOWN_METHOD"
    with pytest.raises(ThematicAuthoringError):
        parse_plan({"layer_id": "L", "mode": "paint_everything"})


def test_reject_bad_colors_and_oversized_tables():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[0, 10],
            class_colors=["not-a-color"])
    assert ei.value.code == "INVALID_COLOR"
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="categorical",
            categories=[{"key": f"k{i}", "color": "#000000"} for i in range(MAX_CATEGORIES + 1)])
    assert ei.value.code == "CATEGORY_TABLE_TOO_LARGE"
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="categorical", categories=[
            {"key": "a", "color": "#000000"}, {"key": "a", "color": "#111111"}])
    assert ei.value.code == "DUPLICATE_CATEGORY_KEY"


def test_mode_field_mismatch_rejected():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="continuous", breaks=[0, 10])
    assert ei.value.code == "MODE_FIELD_MISMATCH"
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="revert_auto", size_channel={
            "channel": "radius", "field": "pop", "min_value": 0, "max_value": 10,
            "min_size": 2, "max_size": 8, "scaling": "linear"})
    assert ei.value.code == "MODE_FIELD_MISMATCH"


def test_size_channel_domain_and_scaling_guards():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_auto", size_channel={
            "channel": "radius", "field": "pop", "min_value": 10, "max_value": 1,
            "min_size": 2, "max_size": 8})
    assert ei.value.code == "INVALID_SIZE_DOMAIN"
    # 负值域 + area_sqrt 拒绝（不能把非正值强塞进幂律，验收 12 同族）
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_auto", size_channel={
            "channel": "radius", "field": "delta", "min_value": -5, "max_value": 5,
            "min_size": 2, "max_size": 8, "scaling": "area_sqrt"})
    assert ei.value.code == "INVALID_SIZE_SCALING"
    # width 只允许 linear（线宽是视觉量）
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_auto", size_channel={
            "channel": "width", "field": "flow", "min_value": 0, "max_value": 9,
            "min_size": 1, "max_size": 9, "scaling": "area_sqrt"})
    assert ei.value.code == "INVALID_SIZE_SCALING"
    # 半径上限（80px 帽）
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_auto", size_channel={
            "channel": "radius", "field": "pop", "min_value": 0, "max_value": 9,
            "min_size": 2, "max_size": 800})
    assert ei.value.code == "INVALID_SIZE_RANGE"


def test_labels_and_colors_length_must_match_classes():
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[0, 10, 20], labels=["a"])
    assert ei.value.code == "LABEL_COUNT_MISMATCH"
    with pytest.raises(ThematicAuthoringError) as ei:
        _ok(mode="graduated_manual", breaks=[0, 10, 20], class_colors=["#000000"])
    assert ei.value.code == "COLOR_COUNT_MISMATCH"


def test_extra_fields_rejected_no_raw_paint_passthrough():
    with pytest.raises(ThematicAuthoringError):
        parse_plan({"layer_id": "L", "mode": "graduated_auto",
                    "paint": {"color": {"method": "match", "cases": []}}})
    with pytest.raises(ThematicAuthoringError):
        parse_plan({"layer_id": "L", "mode": "categorical",
                    "categories": [{"key": "a", "color": "#000000"}],
                    "evil": True})


def test_plan_version_constant():
    assert PLAN_SCHEMA_VERSION == 1
