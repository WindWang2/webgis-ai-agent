"""G05 正例端到端场景（真实 producer→dispatcher→MapSpec→导出证据链）。

每个参数化用例 = 一个 ScenarioContract：runner 经真实 dispatch 缝 +
settle_turn 驱动，断言来自 TaskEvidence 投影（projections.py），场景
通过条件 = 每条期望判定 == 场景期望（负例见 test_task_failures.py）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from .corpus import scenarios_by_id
from .runner import TaskScenarioRunner

# W1 冒烟先跑 F01；W2 扩全量时切换到 corpus 全集。
_SMOKE_IDS = ["f01-school-points-map"]


@pytest.mark.asyncio
async def test_positive_scenarios_e2e(g05_runner: TaskScenarioRunner):
    scenarios = [scenarios_by_id()[sid] for sid in _SMOKE_IDS]
    failures = []
    for scenario in scenarios:
        result = await g05_runner.run(scenario)
        if not result.ok:
            failures.append(result.summary_line())
            for c in result.checks:
                if c.verdict.value != c.expected_verdict:
                    failures.append(
                        f"  {c.expectation_id}: {c.detail}")
            if result.error:
                failures.append(f"  runner: {result.error}")
    assert not failures, "\n".join(failures)
