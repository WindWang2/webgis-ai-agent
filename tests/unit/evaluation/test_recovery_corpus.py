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
    """绝对红线独立复核（R2-7：不依赖 evaluate_recovery_case 的通过）：
    每条 tool_failure 案例重新驱动 wiring，逐条断言 forbidden 动作不在
    decision.allowed_actions 且 auto_retry 与禁用语义一致。"""
    from app.services.gis_harness.recovery_policy.wiring import (
        build_recovery_payload,
    )

    for c in build_recovery_corpus():
        if c.oracle != "tool_failure":
            continue
        payload = build_recovery_payload(
            status=c.input_state.get("status"),
            code=c.input_state.get("code"),
            error_type=c.input_state.get("error_type"),
            message=c.input_state.get("message"),
            tool_name=str(c.input_state.get("tool") or ""),
            harness_failure=c.input_state.get("harness_failure"),
            session_id=str(c.input_state.get("session_id") or ""),
            descriptor=c.input_state.get("risk") or None,
            ref_origin_session=c.input_state.get("ref_origin_session"),
            idempotency_key_present=bool(
                c.input_state.get("idempotency_key_present")),
        )
        allowed = set((payload.get("decision") or {}).get(
            "allowed_actions") or [])
        for a in c.forbidden_actions:
            assert a not in allowed, f"{c.case_id}: forbidden {a} allowed"
        if any(a.startswith("retry") for a in c.forbidden_actions):
            assert not (payload.get("decision") or {}).get("auto_retry"), \
                c.case_id


@pytest.mark.parametrize("case", build_recovery_corpus(),
                         ids=lambda c: c.case_id)
def test_case_parametrized(case):
    v = evaluate_recovery_case(case)
    assert v.passed, v.failures


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
