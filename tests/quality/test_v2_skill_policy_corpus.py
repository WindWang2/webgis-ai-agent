"""V2 SkillPolicy corpus regression tests（D7）。

语料回放 = 生产 SkillPolicy.resolve 的策略裁决锁：全部 6 mode 覆盖、
kill-switch、induced 影子边界、双跑确定性。全离线、零 LLM。
"""
from __future__ import annotations

import pytest

from app.evaluation.runner import GISBenchmarkRunner
from app.evaluation.skill_policy_corpus import build_skill_policy_corpus


@pytest.fixture()
def runner():
    return GISBenchmarkRunner()


@pytest.fixture()
def _clean_policy_env(monkeypatch):
    monkeypatch.delenv("GIS_SKILL_POLICY", raising=False)
    yield
    monkeypatch.delenv("GIS_SKILL_POLICY", raising=False)


def test_corpus_size_and_shape():
    cases = build_skill_policy_corpus()
    assert len(cases) >= 10
    ids = [c.id for c in cases]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    for c in cases:
        assert c.policy_expectation is not None
        assert c.plan_only


@pytest.mark.parametrize("prefix", ["SP-", "SP-b", "SP-e", "SP-f", "SP-g", "SP-k", "SP-n", "SP-s"])
async def test_corpus_replay_by_shard(runner, _clean_policy_env, prefix):
    cases = [c for c in build_skill_policy_corpus() if c.id.startswith(prefix)]
    assert cases, f"shard {prefix} empty"
    results = await runner.run(cases)
    for r in results:
        assert r.passed, f"{r.case_id}: {r.failures[:3]}"


def test_all_reachable_modes_covered(_clean_policy_env):
    cases = build_skill_policy_corpus()
    modes = {
        c.policy_expectation.expected_mode
        for c in cases if c.policy_expectation.expected_mode
    }
    assert modes == {"execute_guided", "guide", "fallback", "none", "blocked"}


def test_kill_switch_row_pins_none_mode(_clean_policy_env):
    cases = build_skill_policy_corpus()
    row = next(c for c in cases if c.id == "SP-kill-switch-none")
    assert row.policy_expectation.disable_policy is True
    assert row.policy_expectation.expected_mode == "none"


def test_induced_skill_never_trusted(_clean_policy_env):
    """影子行：trusted 选择必须是 core 技能，induced 只能是 shadow 候选。"""
    cases = build_skill_policy_corpus()
    row = next(c for c in cases if c.id == "SP-shadow-induced-candidate")
    exp = row.policy_expectation
    assert exp.shadow_induced is True
    assert exp.expected_shadow_candidate == "induced.demo_poi"
    assert exp.expected_selected_skill != "induced.demo_poi"
