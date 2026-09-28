"""failure_corpus 可重放性（G08）：八类故障 × 确定性分类/裁决/预算阶梯。

消费链（README 图谱）：scripts/gis_bench_v2.py + tests/unit/gis_harness/
test_recovery_scenario_v5.py。本文件补齐可重放性主断言：逐案例
「签名 → 分类 → 首步动作」对齐，预算阶梯结构性收敛到 abort_with_disclosure
（无无限循环的证明面）。
"""
from __future__ import annotations

from app.evaluation.failure_corpus import (
    FAILURE_CATEGORIES,
    FailureCase,
    build_failure_corpus,
    category_coverage,
    evaluate_failure_case,
    get_failure_corpus,
)
from app.services.gis_harness.failure_taxonomy import HarnessFailureClass


def test_corpus_schema_and_full_category_coverage():
    cases = build_failure_corpus()
    assert len(cases) == 17
    assert len({c.case_id for c in cases}) == len(cases)
    assert {c.category for c in cases} <= set(FAILURE_CATEGORIES)
    cov = category_coverage(cases)
    assert sum(cov.values()) == len(cases)
    assert set(cov) == set(FAILURE_CATEGORIES)
    assert all(cov[cat] >= 1 for cat in FAILURE_CATEGORIES), cov


def test_get_failure_corpus_cached_and_stable():
    first = get_failure_corpus()
    second = get_failure_corpus()
    assert first == second
    assert all(isinstance(c, FailureCase) for c in first)
    assert [c.case_id for c in build_failure_corpus()] == [
        c.case_id for c in first
    ]


def test_every_case_replays_to_expected_classification_and_action():
    for case in get_failure_corpus():
        verdict = evaluate_failure_case(case)
        assert verdict.case_id == case.case_id
        assert verdict.classified == case.expected_class, case.case_id
        assert verdict.first_action == case.expected_action, case.case_id


def test_budget_ladder_converges_to_disclosure():
    for case in get_failure_corpus():
        verdict = evaluate_failure_case(case)
        assert verdict.budget_ladder, case.case_id
        assert verdict.exhausted_action == "abort_with_disclosure", case.case_id


def test_cancellation_fails_closed_immediately():
    cancels = [c for c in get_failure_corpus() if c.category == "cancellation"]
    assert len(cancels) == 2
    for case in cancels:
        verdict = evaluate_failure_case(case)
        assert verdict.classified == HarnessFailureClass.CANCELLED, case.case_id
        # 取消不可重试：首步即披露终态，预算阶梯不展开。
        assert verdict.first_action == "abort_with_disclosure", case.case_id
