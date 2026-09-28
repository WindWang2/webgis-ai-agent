"""case / fixtures / report 消费面（G08）：schema 负例、fixture 确定性、
报告往返与基线 diff。

覆盖入口：case.py（契约 schema 正负例）/ fixtures.py（确定性 builder）/
report.py（markdown/json/聚合/基线 diff 往返）/ runner.py 的
FIXTURE_BUILDERS 与 CaseResult 面（重执行入口由 gis_harness 消费线覆盖，
此处锁数据契约面）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.evaluation.case import GISBenchmarkCase, NumericAssertion, ScriptStep
from app.evaluation.report import (
    aggregate_by_group,
    diff_against_baseline,
    load_baseline,
    render_json,
    render_markdown,
    result_to_entry,
    write_baseline,
)
from app.evaluation.runner import FIXTURE_BUILDERS, CaseResult


def _result(case_id: str, group: str, passed: bool) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        group=group,
        name=case_id,
        status="pass" if passed else "fail",
        passed=passed,
        metrics={"task_correct": True} if passed else {"task_correct": False},
        failures=[] if passed else ["boom"],
    )


# ── case.py schema 正负例 ────────────────────────────────────────────────


def test_case_schema_rejects_invalid_group():
    with pytest.raises(ValidationError):
        GISBenchmarkCase(id="x", name="x", group="no-such-group", query="q")


def test_numeric_assertion_schema_rejects_unknown_op():
    with pytest.raises(ValidationError):
        NumericAssertion(source="step_result", op="!=", value=1.0)


def test_script_step_defaults():
    step = ScriptStep(tool="t")
    assert step.args == {}
    assert step.expect_error_contains is None


def test_model_summary_format():
    case = GISBenchmarkCase(id="I-1", name="n", group="form", query="q")
    assert case.model_summary() == "I-1 [form] n"


# ── fixtures 确定性 ──────────────────────────────────────────────────────


def test_fixture_builders_registry_matches_module_functions():
    from app.evaluation import fixtures

    assert set(FIXTURE_BUILDERS) == {
        "admin_boundaries_chengdu",
        "chengdu_schools",
        "chengdu_schools_large",
        "od_edges",
        "od_edges_50k",
        "pm25_stations",
        "pm25_stations_sparse",
    }
    for name, builder in FIXTURE_BUILDERS.items():
        assert builder is getattr(fixtures, name)


def test_ndvi_pair_deterministic_golden_value():
    from app.evaluation.fixtures import ndvi_pair

    red1, nir1, golden1 = ndvi_pair()
    red2, nir2, golden2 = ndvi_pair()
    assert red1 == red2 and nir1 == nir2
    assert golden1 == golden2 == pytest.approx(0.25)


def test_chengdu_schools_fixture_is_feature_collection():
    from app.evaluation.fixtures import chengdu_schools

    fc = chengdu_schools(24)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 24


def test_mapspec_with_legend_flags_toggle_components():
    from app.evaluation.fixtures import mapspec_with_legend

    both = mapspec_with_legend(legend=True, scale_bar=True)
    none = mapspec_with_legend(legend=False, scale_bar=False)
    assert both["version"] == "1.0"
    types_both = {c["type"] for c in both["layout"]["components"]}
    types_none = {c["type"] for c in none["layout"]["components"]}
    assert {"legend", "scale_bar"} <= types_both
    assert types_none.isdisjoint({"legend", "scale_bar"})


def test_demo_induced_skill_is_untrusted_benchmark_contract():
    from app.evaluation.fixtures import demo_induced_skill

    skill = demo_induced_skill()
    assert skill.id == "induced.demo_poi"
    assert "never trusted" in skill.description
    assert skill.capability_requirements
    assert skill.capability_requirements[0].hard_gate is True


# ── report 渲染 / 聚合 / 基线 diff ───────────────────────────────────────


def test_render_markdown_lists_cases_and_failures():
    results = [_result("A-1", "poi", True), _result("A-2", "poi", False)]
    md = render_markdown(results)
    assert "A-1" in md and "A-2" in md
    assert "## Failures" in md and "boom" in md


def test_aggregate_by_group_counts_and_metric_rates():
    results = [
        _result("A-1", "poi", True),
        _result("A-2", "poi", False),
        _result("B-1", "od", True),
    ]
    agg = aggregate_by_group(results)
    assert agg["poi"]["cases"] == 2
    assert agg["poi"]["passed"] == 1 and agg["poi"]["failed"] == 1
    assert agg["poi"]["pass_rate"] == 0.5
    assert agg["poi"]["metrics"]["task_correct"] == {
        "declared": 2,
        "aggregate": 0.5,
    }
    assert agg["od"]["passed"] == 1


def test_render_json_entry_is_stable_shape():
    entry = result_to_entry(_result("A-1", "poi", True))
    assert entry["case_id"] == "A-1"
    assert entry["group"] == "poi"
    assert entry["passed"] is True
    assert "elapsed_ms" not in entry  # 时间面不是回归语义


def test_baseline_roundtrip_no_diff_and_regression_detected(tmp_path):
    baseline_results = [_result("A-1", "poi", True), _result("A-2", "od", True)]
    path = tmp_path / "baseline.json"
    write_baseline(baseline_results, path)
    baseline = load_baseline(path)

    same = diff_against_baseline(render_json(baseline_results), baseline)
    assert same["new_failures"] == [] and same["metric_moved"] == []
    assert same["missing_cases"] == [] and same["new_cases"] == []

    regressed = [_result("A-1", "poi", True), _result("A-2", "od", False)]
    diff = diff_against_baseline(render_json(regressed), baseline)
    assert [f["case_id"] for f in diff["new_failures"]] == ["A-2"]
