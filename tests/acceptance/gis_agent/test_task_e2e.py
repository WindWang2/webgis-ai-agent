"""G05 全量场景端到端（真实 producer→dispatcher→MapSpec→导出证据链）。

runner 经真实 dispatch 缝 + settle_turn 驱动；场景通过条件 = 每条期望
判定 == 场景期望（负例靠 expect-fail 语义达成，判错面必须翻红才通过）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from .corpus import all_scenarios
from .runner import TaskScenarioRunner


@pytest.mark.asyncio
async def test_all_scenarios_e2e(g05_runner: TaskScenarioRunner):
    failures = []
    for scenario in all_scenarios():
        result = await g05_runner.run(scenario)
        if not result.ok:
            failures.append(result.summary_line())
            for c in result.checks:
                if c.verdict.value != c.expected_verdict:
                    failures.append(
                        f"  {c.expectation_id} [{c.face}/{c.kind}]: {c.detail}")
            if result.error:
                failures.append(f"  runner: {result.error}")
    assert not failures, "\n".join(failures)
