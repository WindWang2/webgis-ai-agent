"""Workflow V6 — adapters_science 词表与参数投影（P4 补强：W5，0 测试模块）。

纯函数面：science_executable 确定性词表、_aggs_for 的 AgsSpec 参数族、
_group_field 的字段提取优先级。
"""
from __future__ import annotations

from app.services.workflow_runtime import adapters_science as AS


def test_wired_vocabulary_is_exact() -> None:
    assert AS._WIRED == ("category_breakdown", "admin_aggregation",
                         "rate_aggregation")
    for cap in AS._WIRED:
        assert AS.science_executable(cap) is True
    assert AS.science_executable("kriging_surface") is False
    assert AS.science_executable("") is False
    assert AS.science_executable(None) is False  # type: ignore[arg-type]


def test_aggs_category_breakdown_counts_only() -> None:
    assert AS._aggs_for("category_breakdown", {}) == [{"func": "count"}]


def test_aggs_admin_aggregation_sums_value_field_when_present() -> None:
    assert AS._aggs_for("admin_aggregation", {}) == [{"func": "count"}]
    aggs = AS._aggs_for("admin_aggregation", {"value_field": "pop"})
    assert {"func": "count"} in aggs
    assert {"func": "sum", "field": "pop"} in aggs


def test_aggs_rate_aggregation_counts_distinct_group() -> None:
    aggs = AS._aggs_for("rate_aggregation", {"field": "district"})
    funcs = [(a["func"], a.get("field", "")) for a in aggs]
    assert ("count", "") in funcs
    assert ("distinct_count", "district") in funcs


def test_aggs_unknown_capability_falls_back_to_count() -> None:
    assert AS._aggs_for("mystery", {}) == [{"func": "count"}]


def test_group_field_priority_and_clamp() -> None:
    # field > group_by > admin_field > default；超长截断到 64。
    assert AS._group_field({"field": "f1", "group_by": "g"}, "d") == "f1"
    assert AS._group_field({"group_by": "g", "admin_field": "a"}, "d") == "g"
    assert AS._group_field({"admin_field": "a"}, "d") == "a"
    assert AS._group_field({}, "d") == "d"
    assert len(AS._group_field({"field": "x" * 100}, "d")) == 64
