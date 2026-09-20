#!/usr/bin/env python
"""Replay Benchmark CLI（B9/B10，ADR-0183 决策六；ADR-0204 基线/归因扩展）。

``app/lib/harness/replay/bench.py`` 的命令行入口：离线确定性重放基准 ——
suite 选择、seed、offline 强制、bounded 并发（默认 1）、JSON/CSV/MD 输出、
baseline 比对、only-failed、resume。

用法：
    python scripts/replay_bench.py                        # 全量 suite
    python scripts/replay_bench.py --suite core --seed 7
    python scripts/replay_bench.py --baseline base.json   # digest/计数漂移
    python scripts/replay_bench.py --write-baseline base.json  # 显式重建基线
    python scripts/replay_bench.py --only-failed -f md -o failed.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    from app.lib.harness.replay.bench import (
        compare_results,
        select_scenarios,
        write_baseline,
        write_report,
    )
    from app.lib.harness.replay.scenarios import CORPUS_VERSION, build_corpus

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="all",
                        help="all / core / multi-turn / faults")
    parser.add_argument("--only", default=None,
                        help="逗号分隔的 scenario_id 白名单")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--profile", default="small")
    parser.add_argument("--resume", default=None,
                        help="resume 状态文件（跳过已完成场景）")
    parser.add_argument("--baseline", default=None,
                        help="基线报告路径：比对 digest / 绿红计数漂移")
    parser.add_argument("--write-baseline", dest="write_baseline",
                        default=None,
                        help="把本次运行的确定性投影写为基线（人工显式更新）")
    parser.add_argument("--only-failed", action="store_true",
                        help="报告只保留未通过场景")
    parser.add_argument("-f", "--format", default="json",
                        choices=["json", "csv", "md"])
    parser.add_argument("-o", "--output", default="-",
                        help="输出路径（- = stdout）")
    args = parser.parse_args()

    corpus = build_corpus()
    scenarios = select_scenarios(corpus, args.suite, only=args.only,
                                 limit=args.limit)
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    report = asyncio_run_suite(scenarios, seed=args.seed,
                               profile=args.profile, resume_path=args.resume)
    if args.write_baseline:
        write_baseline(report, args.write_baseline,
                       corpus_version=CORPUS_VERSION)
        print(f"baseline written: {args.write_baseline} "
              f"({report.get('green')} green / {report.get('red')} red)")
        return 0
    if args.baseline:
        report["baseline_compare"] = compare_results(report, args.baseline)
    if args.only_failed:
        report = {
            **report,
            "entries": [e for e in report.get("entries") or [] if not e.get("ok")],
        }
    write_report(report, args.format, args.output)

    if args.baseline:
        drifts = (report.get("baseline_compare") or {}).get("drifts") or []
        if drifts:
            print(
                f"replay baseline drift: {len(drifts)} scenario(s) differ "
                f"from {args.baseline}", file=sys.stderr)
            for d in drifts[:20]:
                if d.get("kind") == "digest_drift":
                    decision_note = (
                        " decisions-changed"
                        if d.get("decisions_digest_drift") else "")
                    print(
                        f"  - {d['scenario_id']}: replay_digest drift "
                        f"(baseline_ok={d.get('baseline_ok')} "
                        f"current_ok={d.get('current_ok')})"
                        f"{decision_note}",
                        file=sys.stderr)
                else:
                    print(f"  - {d['scenario_id']}: {d.get('kind')}",
                          file=sys.stderr)
            print(
                "this is a regression ratchet: golden scenarios must not "
                "drift silently. if the change is intentional (corpus or "
                "algorithm update), regenerate explicitly:\n"
                "  python scripts/replay_bench.py --suite all "
                f"--write-baseline {args.baseline}",
                file=sys.stderr)
            return 1
    if report.get("red"):
        print(f"{report['red']} scenario(s) red", file=sys.stderr)
        return 1
    return 0


def asyncio_run_suite(scenarios, *, seed: int, profile: str,
                      resume_path) -> dict:
    import asyncio

    from app.lib.harness.replay.bench import run_suite

    return asyncio.run(run_suite(scenarios, seed=seed, profile=profile,
                                 resume_path=resume_path))


if __name__ == "__main__":
    raise SystemExit(main())
