"""V2 mission scenario corpus tests（D4）。

任务级 hermetic 回放：中断/恢复/取消/目标修订/预算/fencing/破坏性任务
不可盲重跑 —— 真实 MissionRuntimeService + 冻结时钟 + sqlite 内存。
"""
from __future__ import annotations

import pytest

from app.evaluation.mission_corpus import build_mission_corpus
from app.evaluation.mission_driver import run_mission_scenario


def test_corpus_covers_mission_capabilities():
    scenarios = build_mission_corpus()
    tags = {t for s in scenarios for t in s.tags}
    for capability in (
        "recovery", "cancel", "goal-revision", "budget",
        "fencing", "destructive", "suspend",
    ):
        assert capability in tags, f"mission capability {capability} missing"
    assert len(scenarios) >= 8


@pytest.mark.parametrize("scenario", build_mission_corpus(), ids=lambda s: s.scenario_id)
def test_mission_scenario(scenario):
    result = run_mission_scenario(scenario)
    assert result.passed, f"{scenario.scenario_id}: {result.failures[:3]}"


def test_mission_scenarios_deterministic_double_run():
    for scenario in build_mission_corpus():
        a = run_mission_scenario(scenario)
        b = run_mission_scenario(scenario)
        assert a.passed == b.passed
        assert a.failures == b.failures
        assert a.metrics == b.metrics


def test_cancel_is_terminal_and_idempotent():
    scenarios = {s.scenario_id: s for s in build_mission_corpus()}
    result = run_mission_scenario(scenarios["MSN-cancel-terminal-idempotent"])
    assert result.passed, result.failures[:3]
    assert result.metrics.get("mission_cancel_terminal_ok") is True


def test_destructive_tasks_never_blind_rerun():
    scenarios = {s.scenario_id: s for s in build_mission_corpus()}
    result = run_mission_scenario(scenarios["MSN-swarm-destructive-unresolved"])
    assert result.passed, result.failures[:3]
    assert result.metrics.get("mission_unresolved_ok") is True
