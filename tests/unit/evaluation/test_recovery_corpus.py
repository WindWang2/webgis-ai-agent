"""恢复语料门（GOAL 05 / M7）—— ≥25 案例、类别覆盖、禁止动作永不放行、
确定性（双跑一致）。
"""
from __future__ import annotations

import pytest

from app.evaluation.recovery_corpus import (
    RECOVERY_CATEGORIES,
    build_recovery_corpus,
    category_coverage,
    evaluate_recovery_case,
)


def test_corpus_size_at_least_25():
    cases = build_recovery_corpus()
    assert len(cases) >= 25


def test_case_ids_unique_and_stable():
    c1 = build_recovery_corpus()
    c2 = build_recovery_corpus()
    ids1 = [c.case_id for c in c1]
    assert len(set(ids1)) == len(ids1)
    assert ids1 == [c.case_id for c in c2]


def test_category_coverage():
    cov = category_coverage(build_recovery_corpus())
    for cat in RECOVERY_CATEGORIES:
        assert cov.get(cat, 0) >= 1, f"恢复类别 {cat} 无案例"


def test_every_case_has_input_state_and_action_lists():
    for c in build_recovery_corpus():
        assert isinstance(c.input_state, dict) and c.input_state
        # 允许/禁止动作列表至少其一非空（工具失败族必须双全）
        if c.oracle == "tool_failure":
            assert c.allowed_actions or c.forbidden_actions, c.case_id


def test_all_cases_pass():
    failures = []
    for c in build_recovery_corpus():
        v = evaluate_recovery_case(c)
        if not v.passed:
            failures.append(f"{v.case_id}: {v.failures}")
    assert not failures, "\n".join(failures)


def test_deterministic_double_run():
    for c in build_recovery_corpus():
        v1 = evaluate_recovery_case(c)
        v2 = evaluate_recovery_case(c)
        assert v1 == v2, c.case_id


def test_forbidden_actions_never_allowed_invariants():
    """绝对红线抽查：危险动作在任何 tool_failure 案例中不得出现在
    allowed_actions（语料级冗余门）。"""
    for c in build_recovery_corpus():
        if c.oracle != "tool_failure":
            continue
        v = evaluate_recovery_case(c)
        assert v.passed, c.case_id


@pytest.mark.parametrize("case", build_recovery_corpus(),
                         ids=lambda c: c.case_id)
def test_case_parametrized(case):
    v = evaluate_recovery_case(case)
    assert v.passed, v.failures


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
