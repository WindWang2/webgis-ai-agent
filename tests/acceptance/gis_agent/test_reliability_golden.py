"""G07 golden manifest integrity and real-dispatch scenario verification."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.lib.harness.lab.golden import REQUIRED_CATEGORIES, load_golden_tasks
from .corpus import scenarios_by_id
from .runner import TaskScenarioRunner

pytestmark = pytest.mark.cartography


def test_manifest_has_twelve_complete_oracle_pairs():
    tasks = load_golden_tasks()
    assert len(tasks) == 12
    assert {task.category for task in tasks} == set(REQUIRED_CATEGORIES)
    scenarios = scenarios_by_id()
    for task in tasks:
        assert task.positive or task.focused_tests
        assert task.negative or task.focused_tests
        assert task.oracle["invariant"]
        assert task.oracle["reference_space"]
        assert task.oracle["units"]
        assert task.oracle["nodata"]
        assert task.oracle["ownership"]
        assert task.coverage["browser_renderer"] == "not_run"
        assert task.coverage["live_services_opt_in"] == "not_run"
        for scenario_id in (*task.positive, *task.negative):
            scenario = scenarios[scenario_id]
            expected_ids = {exp.id for exp in scenario.expectations}
            assert set(task.expectations[scenario_id]).issubset(expected_ids)
        for relative in task.focused_tests:
            path = Path(relative)
            assert not path.is_absolute() and ".." not in path.parts
            assert (Path(__file__).resolve().parents[3] / path).is_file()


@pytest.mark.asyncio
async def test_all_selected_acceptance_scenarios_pass(g05_runner: TaskScenarioRunner):
    scenarios = scenarios_by_id()
    selected = sorted({
        scenario_id
        for task in load_golden_tasks()
        for scenario_id in (*task.positive, *task.negative)
    })
    assert len(selected) >= 24
    failures = []
    for scenario_id in selected:
        result = await g05_runner.run(scenarios[scenario_id])
        if not result.ok:
            failures.append(result.summary_line())
            failures.extend(
                f"  {check.expectation_id}: {check.detail}"
                for check in result.checks
                if check.verdict.value != check.expected_verdict
            )
            if result.error:
                failures.append(f"  runner: {result.error}")
    assert not failures, "\n".join(failures)
