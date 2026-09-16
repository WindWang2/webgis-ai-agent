"""V2 report additive functions tests（D6）。

分组聚合 / JSON 报告 / 基线 diff（回归归因）/ 双遍稳定性 —— 既有
render_markdown 字节兼容不在此重复（上游既有测试覆盖）。
"""
from __future__ import annotations

import pytest

from app.evaluation.runner import CaseResult
from app.evaluation.report import (
    aggregate_by_group,
    diff_against_baseline,
    render_json,
    render_markdown,
    result_to_entry,
)


def _result(cid, group, *, passed=True, metrics=None, failures=None):
    return CaseResult(
        case_id=cid, group=group, name=cid,
        status="pass" if passed else "fail",
        passed=passed, metrics=metrics or {}, failures=failures or [],
    )


def test_markdown_shape_unchanged():
    """既有 markdown 契约：表头含全部 _METRIC_ORDER 列（字节兼容护栏）。"""
    from app.evaluation.report import _METRIC_ORDER

    md = render_markdown([_result("X-1", "poi")])
    assert md.startswith("# GIS Agent Benchmark Report")
    for key in _METRIC_ORDER:
        assert f"| {key} " in md or f"| {key} |" in md


def test_aggregate_by_group_rates_and_metric_means():
    results = [
        _result("A-1", "g1", passed=True, metrics={"task_correct": True,
                                                   "capability_precision": 1.0}),
        _result("A-2", "g1", passed=False, metrics={"task_correct": False,
                                                    "capability_precision": 0.5}),
        _result("B-1", "g2", passed=True, metrics={}),
    ]
    agg = aggregate_by_group(results)
    assert set(agg) == {"g1", "g2"}
    assert agg["g1"]["pass_rate"] == 0.5
    assert agg["g1"]["metrics"]["task_correct"] == {"declared": 2, "aggregate": 0.5}
    assert agg["g1"]["metrics"]["capability_precision"]["aggregate"] == 0.75
    # None 指标（未声明）不虚构：组内无人声明 → 键干脆不出现
    assert "task_correct" not in agg["g2"]["metrics"]


def test_render_json_stable_and_case_locatable():
    results = [
        _result("A-1", "g1", passed=False, metrics={"task_correct": False},
                failures=["task: expected poi, got od"]),
        _result("A-2", "g1"),
    ]
    r1 = render_json(results, manifest={"k": 1})
    r2 = render_json(list(reversed(results)), manifest={"k": 1})
    assert r1["cases"] == 2
    assert r1["manifest"] == {"k": 1}
    # entries 顺序跟随输入（确定性），但内容与顺序无关
    assert [e["case_id"] for e in r1["entries"]] == ["A-1", "A-2"]
    assert [e["case_id"] for e in r2["entries"]] == ["A-2", "A-1"]
    entry = r1["entries"][0]
    assert entry["failures"] == ["task: expected poi, got od"]
    assert entry["metrics"] == {"task_correct": False}


def test_diff_against_baseline_buckets():
    baseline = render_json([
        _result("A-1", "g", passed=True, metrics={"task_correct": True}),
        _result("A-2", "g", passed=False, metrics={},
                failures=["x"]),
        _result("A-3", "g", passed=True, metrics={"task_correct": True}),
    ])
    current = render_json([
        # A-1 回归：pass → fail
        _result("A-1", "g", passed=False, metrics={"task_correct": False},
                failures=["task: expected poi, got od"]),
        # A-2 修复：fail → pass
        _result("A-2", "g", passed=True, metrics={"task_correct": True}),
        # A-3 指标漂移：同 pass 但声明指标变化
        _result("A-3", "g", passed=True, metrics={"task_correct": False}),
        # A-4 新案例
        _result("A-4", "g"),
    ])
    diff = diff_against_baseline(current, baseline)
    assert [f["case_id"] for f in diff["new_failures"]] == ["A-1"]
    assert diff["new_failures"][0]["failures"] == ["task: expected poi, got od"]
    assert [f["case_id"] for f in diff["fixed"]] == ["A-2"]
    assert [m["case_id"] for m in diff["metric_moved"]] == ["A-3"]
    assert diff["new_cases"] == ["A-4"]
    assert diff["missing_cases"] == []


def test_diff_ignores_elapsed_ms():
    baseline = render_json([_result("A-1", "g")])
    drifted = _result("A-1", "g")
    drifted.elapsed_ms = 99999
    current = render_json([drifted])
    diff = diff_against_baseline(current, baseline)
    assert not diff["new_failures"]
    assert not diff["metric_moved"]


def test_self_diff_is_clean():
    """同 commit 两遍：diff 归因必须为空（Oracle 稳定性锚）。"""
    results = [
        _result("A-1", "g", metrics={"task_correct": True}),
        _result("A-2", "g", passed=False, failures=["x"]),
    ]
    diff = diff_against_baseline(render_json(results), render_json(results))
    assert not diff["new_failures"]
    assert not diff["fixed"]
    assert not diff["metric_moved"]
    assert not diff["new_cases"]
    assert not diff["missing_cases"]


def test_result_to_entry_excludes_elapsed():
    r = _result("A-1", "g")
    r.elapsed_ms = 123
    entry = result_to_entry(r)
    assert "elapsed_ms" not in entry
