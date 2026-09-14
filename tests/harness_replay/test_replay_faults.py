"""故障注入契约测试（B6）：10 类故障全部 fail-closed，禁止静默 pass。"""
from __future__ import annotations

import copy

import pytest

from app.lib.harness.replay.faults import FAULT_TYPES, apply_faults, assert_fault_contract
from app.lib.harness.replay.replayer import OfflineReplayer
from app.lib.harness.replay.scenarios import build_corpus

pytestmark = pytest.mark.cartography

_GREEN_SINGLE = "core-point_distribution-01"
_GREEN_MULTI = "mt-user_followup-01"


def _by_id(scenario_id: str):
    return next(s for s in build_corpus() if s.scenario_id == scenario_id)


@pytest.fixture(scope="module")
def green_single():
    return _by_id(_GREEN_SINGLE)


@pytest.fixture(scope="module")
def green_multi():
    return _by_id(_GREEN_MULTI)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_type", FAULT_TYPES)
async def test_fault_is_fail_closed(fault_type, green_single, green_multi):
    base = green_multi if fault_type in ("pi_restart", "judge_unavailable") \
        else green_single
    target_turn = 1 if base is green_multi else 0
    scenario = copy.deepcopy(base)
    scenario.faults = [{"type": fault_type, "target_turn": target_turn}]
    broken = apply_faults(scenario)

    replayer = OfflineReplayer(seed=5)
    result = await replayer.replay_scenario(broken)
    violation = assert_fault_contract(
        broken,
        [t.gate_result for t in result.turns],
        [t.goal_satisfaction for t in result.turns],
        result.ok,
    )
    assert violation is None, violation


@pytest.mark.asyncio
async def test_healthy_baseline_stays_green(green_single):
    """对照组：无故障场景保持绿（排除"注入器恒红"的假阳性）。"""
    result = await OfflineReplayer(seed=5).replay_scenario(green_single)
    assert result.ok is True


@pytest.mark.asyncio
async def test_store_transient_recovery_semantics(green_single):
    """首写失败 + 重试成功：CQ 恢复，但 MSV 按会话比例诚实红。"""
    scenario = copy.deepcopy(green_single)
    scenario.faults = [{"type": "store_transient", "target_turn": 0}]
    result = await OfflineReplayer(seed=5).replay_scenario(apply_faults(scenario))
    checks = result.turns[0].gate_result["checks"]
    assert checks["CartographicQuality"]["passed"] is True
    assert checks["MapSpecValidity"]["passed"] is False  # 失败收据永久计入
    assert result.turns[0].goal_satisfaction["status"] == "pass"


@pytest.mark.asyncio
async def test_judge_unavailable_l5_fail_closed(green_multi):
    scenario = copy.deepcopy(green_multi)
    scenario.faults = [{"type": "judge_unavailable", "target_turn": 1}]
    result = await OfflineReplayer(seed=5).replay_scenario(apply_faults(scenario))
    goal = result.turns[1].goal_satisfaction
    assert goal["status"] == "not_evaluated"
    assert goal.get("reason") in ("visual_judge_disabled", "no_visual_judgement")
