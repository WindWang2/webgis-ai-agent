#!/usr/bin/env python3
"""Run the G07 golden manifest through the existing real-dispatch runner."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.lib.harness.lab.golden import GoldenTask, load_golden_tasks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", default=[], help="Golden task id; repeatable")
    parser.add_argument("--out", default=str(_REPO_ROOT / "reports" / "g07" / "golden.json"))
    return parser


async def _run(tasks: list[GoldenTask], output: Path) -> int:
    from tests.acceptance.gis_agent.corpus import scenarios_by_id
    from tests.acceptance.gis_agent.runner import (
        TaskScenarioRunner,
        ensure_registry_injected,
    )

    scenarios = scenarios_by_id()
    selected = sorted({
        scenario_id for task in tasks for scenario_id in (*task.positive, *task.negative)
    })
    runner = TaskScenarioRunner(ensure_registry_injected())
    results = {}
    for scenario_id in selected:
        result = await runner.run(scenarios[scenario_id])
        results[scenario_id] = result
        print(result.summary_line())

    failed = [result for result in results.values() if not result.ok]
    focused = sorted({test for task in tasks for test in task.focused_tests})
    report = {
        "kind": "gis_harness_reliability_report.v1",
        "seed": 20261107,
        "summary": {
            "golden_tasks": len(tasks),
            "acceptance_scenarios": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "focused_tests_not_run": focused,
        },
        "production_chain": {
            "tool_dispatch": "live_dispatch_stub_llm",
            "gis_fixtures": "synthetic_local",
            "llm_decision": "scripted_not_evaluated",
            "browser_renderer": "not_run",
            "live_services_opt_in": "not_run",
        },
        "tasks": [
            {
                "id": task.task_id,
                "category": task.category,
                "execution_mode": task.execution_mode,
                "positive": list(task.positive),
                "negative": list(task.negative),
                "expectations": {key: list(value) for key, value in task.expectations.items()},
                "focused_tests": list(task.focused_tests),
                "focused_tests_status": "not_run" if task.focused_tests else "not_applicable",
                "oracle": task.oracle,
                "coverage": task.coverage,
                "disclosures": list(task.disclosures),
            }
            for task in tasks
        ],
        "runs": [results[key].model_dump(mode="json") for key in sorted(results)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"report written: {output}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    from tests.conftest import _ENV_BASELINE

    for key, value in _ENV_BASELINE.items():
        os.environ.setdefault(key, value)
    args = _parser().parse_args(argv)
    tasks = load_golden_tasks()
    if args.task:
        wanted = set(args.task)
        missing = sorted(wanted - {task.task_id for task in tasks})
        if missing:
            print(f"unknown golden task ids: {', '.join(missing)}", file=sys.stderr)
            return 2
        tasks = [task for task in tasks if task.task_id in wanted]
    return asyncio.run(_run(tasks, Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
