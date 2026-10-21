#!/usr/bin/env python
"""G05 task-level 验收本地 CLI（run / compare / report / pin）。

薄壳：场景事实源 = tests/acceptance/gis_agent/corpus.py；执行 = 同目录
TaskScenarioRunner（真实 dispatch 缝）。本脚本只做编排、报告与 pin。

用法（在仓库根目录）：
    python scripts/g05_task_acceptance.py pin                 # 生成场景 pin
    python scripts/g05_task_acceptance.py run [--profile smoke|focused|heavy]
                                              [--scenario ID]...
    python scripts/g05_task_acceptance.py compare baseline.json run.json
    python scripts/g05_task_acceptance.py report run.json [--coverage]

- profile：smoke（5 场景，常规开发机 <1min）/ focused（除栅格外全部）/
  heavy（全量 21）。
- 报告落 reports/g05/（gitignored）；对比输出逐场景 first-failing-stage。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

REPORT_DIR = REPO_ROOT / "reports" / "g05"
PIN_PATH = (REPO_ROOT / "tests" / "acceptance" / "gis_agent" / "fixtures"
            / "task_scenarios" / "scenarios.json")

SMOKE_IDS = [
    "f01-school-points-map",
    "f02a-heatmap-method",
    "f02b-sparse-heatmap-degraded",
    "f03-admin-aggregation",
    "f13-cross-session-recovery",
]


def _init_env() -> None:
    """复用 tests/conftest.py 的 _ENV_BASELINE（单一事实源，不重复维护）。"""
    from tests.conftest import _ENV_BASELINE  # noqa: PLC0415
    for key, value in _ENV_BASELINE.items():
        os.environ.setdefault(key, value)


def _load_corpus():
    from tests.acceptance.gis_agent.corpus import all_scenarios
    return all_scenarios()


def _select(profile: str, only_ids):
    scenarios = _load_corpus()
    if only_ids:
        by_id = {s.id: s for s in scenarios}
        missing = [i for i in only_ids if i not in by_id]
        if missing:
            raise SystemExit(f"unknown scenario ids: {missing}")
        return [by_id[i] for i in only_ids]
    if profile == "smoke":
        by_id = {s.id: s for s in scenarios}
        return [by_id[i] for i in SMOKE_IDS]
    if profile == "focused":
        return [s for s in scenarios
                if not any(t in s.tags for t in ("raster",))]
    return scenarios


async def _run(scenarios, out_path: Path) -> int:
    from tests.acceptance.gis_agent.reporting import (
        coverage_matrix,
        render_text_report,
        summarize_runs,
    )
    from tests.acceptance.gis_agent.runner import (
        TaskScenarioRunner,
        ensure_registry_injected,
    )
    registry = ensure_registry_injected()
    runner = TaskScenarioRunner(registry)
    runs = []
    for scenario in scenarios:
        started = time.monotonic()
        result = await runner.run(scenario)
        print(f"[{time.monotonic() - started:6.1f}s] {result.summary_line()}")
        runs.append(result)
    report = {
        "summary": summarize_runs(runs),
        "runs": [r.model_dump(mode="json") for r in runs],
        "coverage": coverage_matrix(scenarios, runs),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
    print(render_text_report(runs))
    print(f"\nreport written: {out_path}")
    ok = all(r.ok for r in runs)
    return 0 if ok else 1


def _cmd_run(args) -> int:
    _init_env()
    scenarios = _select(args.profile, args.scenario)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else REPORT_DIR / f"run-{stamp}.json"
    return asyncio.run(_run(scenarios, out))


def _cmd_pin(args) -> int:
    scenarios = _load_corpus()
    payload = {
        "kind": "g05-task-scenarios",
        "schema_version": "task_scenario.v1",
        "count": len(scenarios),
        "scenarios": [
            s.model_dump(mode="json") for s in scenarios
        ],
    }
    PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    if PIN_PATH.exists() and PIN_PATH.read_text(encoding="utf-8") == text \
            and not args.force:
        print(f"pin up-to-date: {PIN_PATH} ({len(scenarios)} scenarios)")
        return 0
    PIN_PATH.write_text(text, encoding="utf-8")
    print(f"pin written: {PIN_PATH} ({len(scenarios)} scenarios)")
    return 0


def _cmd_compare(args) -> int:
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    curr = json.loads(Path(args.run).read_text(encoding="utf-8"))
    base_by = {r["scenario_id"]: r for r in base.get("runs", [])}
    curr_by = {r["scenario_id"]: r for r in curr.get("runs", [])}
    new_failures, fixed = [], []
    for sid, r in curr_by.items():
        prev = base_by.get(sid)
        if prev is None:
            continue
        was_ok, is_ok = _ok_of(prev), _ok_of(r)
        if not is_ok and was_ok:
            new_failures.append((sid, _first_mismatch(r)))
        elif is_ok and not was_ok:
            fixed.append(sid)
    missing = [sid for sid in base_by if sid not in curr_by]
    added = [sid for sid in curr_by if sid not in base_by]
    print(f"baseline={args.baseline} run={args.run}")
    print(f"new_failures={len(new_failures)} fixed={len(fixed)} "
          f"missing_in_run={len(missing)} added_in_run={len(added)}")
    for sid, first in new_failures:
        print(f"  NEW-FAIL {sid} first_failing_stage={first}")
    for sid in fixed:
        print(f"  FIXED    {sid}")
    for sid in missing:
        print(f"  MISSING  {sid} (baseline 有而本次未跑——profile 不同或中断)")
    for sid in added:
        print(f"  ADDED    {sid}")
    return 1 if new_failures or missing else 0


def _ok_of(run: dict) -> bool:
    return not run.get("error") and all(
        c.get("verdict") == c.get("expected_verdict")
        for c in run.get("checks", []))


def _first_mismatch(run: dict) -> str:
    for c in run.get("checks", []):
        if c.get("verdict") != c.get("expected_verdict"):
            return f"{c.get('face')}/{c.get('expectation_id')}"
    return "runner" if run.get("error") else ""


def _cmd_report(args) -> int:
    data = json.loads(Path(args.run).read_text(encoding="utf-8"))
    print(json.dumps(data.get("summary", {}), ensure_ascii=False, indent=1))
    if args.coverage:
        for row in data.get("coverage", []):
            flag = "OK " if row.get("ok") else "BAD"
            print(f"  {flag} {row['scenario']:<42} {row['family']:<4} "
                  f"turns={row['turns']} checks={row['checks']} "
                  f"faces={','.join(row['faces'])}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="g05_task_acceptance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="运行场景（真实 dispatch 缝）")
    p_run.add_argument("--profile", choices=["smoke", "focused", "heavy"],
                       default="smoke")
    p_run.add_argument("--scenario", action="append", default=None,
                       help="指定场景 id（可多次）")
    p_run.add_argument("--out", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_pin = sub.add_parser("pin", help="生成/校验场景 pin JSON")
    p_pin.add_argument("--force", action="store_true")
    p_pin.set_defaults(func=_cmd_pin)

    p_cmp = sub.add_parser("compare", help="对比两次 run 报告")
    p_cmp.add_argument("baseline")
    p_cmp.add_argument("run")
    p_cmp.set_defaults(func=_cmd_compare)

    p_rep = sub.add_parser("report", help="汇总报告（可含覆盖矩阵）")
    p_rep.add_argument("run")
    p_rep.add_argument("--coverage", action="store_true")
    p_rep.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
