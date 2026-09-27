"""goal_satisfaction / mission_driver / scenarios 可重放消费（G08）。

三个入口的既有消费分散在 tests/quality 与 scripts/gis_bench_v2.py；
本文件是专属测试线的可重放性锚：绿语料逐条可重放（passed 且非
false_pass）、任务终态语义（取消幂等）、端到端场景全链路走查。
"""
from __future__ import annotations

from app.evaluation.goal_satisfaction_corpus import (
    build_goal_satisfaction_cases,
    corpus_metrics,
    run_goal_satisfaction_case,
)


# ── goal_satisfaction_corpus ─────────────────────────────────────────────


def test_goal_corpus_unique_ids_and_counterfactual_flagging():
    cases = build_goal_satisfaction_cases()
    assert len(cases) >= 104
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    counterfactuals = [c for c in cases if c.case_id.startswith("GC-cf")]
    assert len(counterfactuals) >= 8
    # CF8（user-wins 披露案例）不反 PASS，其余七大反事实必须反 PASS。
    assert all(
        c.must_not_pass
        for c in counterfactuals
        if c.case_id != "GC-cf8-user-hid-required-view"
    )


def test_goal_case_replay_green_including_counterfactual():
    cases = build_goal_satisfaction_cases()
    by_id = {c.case_id: c for c in cases}
    for case in (cases[0], by_id["GC-cf1-empty-result"]):
        result = run_goal_satisfaction_case(case)
        assert result.passed, (case.case_id, result.failures)
        assert result.false_pass is False, case.case_id


def test_goal_corpus_metrics_shape_and_zero_false_pass():
    cases = build_goal_satisfaction_cases()
    metrics = corpus_metrics(
        [run_goal_satisfaction_case(c) for c in cases[:4]]
    )
    assert set(metrics) == {
        "total",
        "passed",
        "failed",
        "failed_case_ids",
        "false_pass_count",
        "false_pass_case_ids",
        "false_pass_rate",
        "by_family",
    }
    assert metrics["total"] == 4
    assert metrics["passed"] == 4
    assert metrics["false_pass_rate"] == 0.0


# ── mission_corpus + mission_driver ──────────────────────────────────────


def test_mission_scenario_replay_cancel_terminal_idempotent():
    from app.evaluation.mission_corpus import build_mission_corpus
    from app.evaluation.mission_driver import run_mission_scenario

    scenarios = {s.scenario_id: s for s in build_mission_corpus()}
    result = run_mission_scenario(scenarios["MSN-cancel-terminal-idempotent"])
    assert result.passed, result.failures


def test_hermetic_mission_runtime_context_is_consumable():
    """``hermetic_mission_runtime`` 此前无任何代码消费者（README 死语料
    清单登记项）—— 专属测试线将其激活为一等入口：上下文内外的场景重放
    都保持绿（hermetic 装配不改变场景语义）。"""
    from app.evaluation.mission_corpus import build_mission_corpus
    from app.evaluation.mission_driver import (
        hermetic_mission_runtime,
        run_mission_scenario,
    )

    scenario = build_mission_corpus()[0]
    with hermetic_mission_runtime():
        inside = run_mission_scenario(scenario)
    outside = run_mission_scenario(scenario)
    assert inside.passed, inside.failures
    assert outside.passed, outside.failures


# ── scenarios（端到端确定性场景）─────────────────────────────────────────


async def test_scenario_corpus_unique_and_replayable_end_to_end():
    from app.evaluation.scenarios import build_scenarios, run_scenario

    scenarios = build_scenarios()
    assert len(scenarios) == 7
    assert len({s.scenario_id for s in scenarios}) == 7
    target = scenarios[0]
    assert target.plan_cases, target.scenario_id

    result = await run_scenario(target)
    assert result.passed, result.failures
    assert result.case_results
    assert all(r.passed for r in result.case_results)
