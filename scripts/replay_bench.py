#!/usr/bin/env python
"""Replay Benchmark CLI（B9/B10，ADR-0183 决策六；ADR-0212 基线/归因扩展）。

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
        diff_payloads,
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
    parser.add_argument("--diff", default=None,
                        help="master 侧 **report** JSON（含 projection，"
                             "即 -f json 的输出；基线文件无投影 → delta 标注 "
                             "projection_absent）：结构化 delta 下钻")
    parser.add_argument("--shrink", default=None,
                        help="scenario_id：红场景最小化（输出最小可复现 + "
                             "removed 收据；绿场景无操作退出 0）")
    parser.add_argument("--shrink-max-rounds", type=int, default=48)
    parser.add_argument("--shrink-max-candidates", type=int, default=256)
    parser.add_argument("--write-baseline", dest="write_baseline",
                        default=None,
                        help="把本次运行的确定性投影写为基线（人工显式更新）")
    parser.add_argument("--force", action="store_true",
                        help="配合 --write-baseline：允许在存在 red 场景时写基线")
    parser.add_argument("--only-failed", action="store_true",
                        help="报告只保留未通过场景")
    parser.add_argument("-f", "--format", default="json",
                        choices=["json", "csv", "md"])
    parser.add_argument("-o", "--output", default="-",
                        help="输出路径（- = stdout）")
    args = parser.parse_args()

    if args.write_baseline and args.baseline:
        print("--write-baseline 与 --baseline 互斥（写基线时不做比对）",
              file=sys.stderr)
        return 2

    corpus = build_corpus()
    scenarios = select_scenarios(corpus, args.suite, only=args.only,
                                 limit=args.limit)
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    if args.shrink:
        return _run_shrink(args, scenarios)

    report = asyncio_run_suite(scenarios, seed=args.seed,
                               profile=args.profile, resume_path=args.resume)
    if args.write_baseline:
        # 防呆（review P2-4）：红场景进基线会把回归钉进基线 —— 需 --force。
        if report.get("red") and not args.force:
            print(f"refusing to write baseline with {report['red']} red "
                  "scenario(s); fix them or pass --force", file=sys.stderr)
            return 1
        write_baseline(report, args.write_baseline,
                       corpus_version=CORPUS_VERSION)
        print(f"baseline written: {args.write_baseline} "
              f"({report.get('green')} green / {report.get('red')} red)")
        return 0
    if args.baseline:
        report["baseline_compare"] = compare_results(report, args.baseline)
    if args.diff:
        report["diff_compare"] = diff_payloads(
            _load_report(args.diff), report,
            baseline=args.diff, current="<current-run>")
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
    if args.diff:
        compare = report.get("diff_compare") or {}
        summary = compare.get("summary") or {}
        if summary.get("drifted") or summary.get("new") or summary.get("missing"):
            print(
                "differential replay: "
                f"{summary.get('drifted', 0)} drifted / "
                f"{summary.get('new', 0)} new / "
                f"{summary.get('missing', 0)} missing "
                f"(vs {args.diff})", file=sys.stderr)
            for delta in (compare.get("deltas") or [])[:20]:
                sid = delta.get("scenario_id")
                if delta.get("kind") != "digest_drift":
                    print(f"  - {sid}: {delta.get('kind')}", file=sys.stderr)
                    continue
                print(
                    f"  - {sid}: ok {delta.get('baseline_ok')} → "
                    f"{delta.get('current_ok')} "
                    f"({delta.get('delta_count')} delta(s))",
                    file=sys.stderr)
                for item in (delta.get("delta") or [])[:6]:
                    print(
                        f"      · turn={item.get('turn')} "
                        f"{item.get('kind')} key={item.get('key')}: "
                        f"{item.get('baseline')} → {item.get('current')}",
                        file=sys.stderr)
            print(
                "each delta names the exact turn/aspect/key that changed "
                "(decision/gate/dispatch/receipt level) — triage before "
                "accepting.", file=sys.stderr)
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


def _load_report(path: str) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_shrink(args, scenarios) -> int:
    """--shrink：单场景红 → 确定性 delta-debug 最小化 → 最小复现 JSON。"""
    import asyncio
    import json

    from app.lib.harness.replay.bench import apply_faults
    from app.lib.harness.replay.replayer import OfflineReplayer
    from app.lib.harness.replay.shrink import replayer_oracle, shrink_scenario

    matches = [s for s in scenarios
               if s.scenario_id == args.shrink]
    if not matches:
        print(f"scenario not found: {args.shrink}", file=sys.stderr)
        return 2
    scenario = apply_faults(matches[0]) if matches[0].faults else matches[0]
    replayer = OfflineReplayer(seed=args.seed)
    oracle = replayer_oracle(replayer)

    async def _go():
        return await shrink_scenario(
            scenario, oracle,
            max_rounds=args.shrink_max_rounds,
            max_candidates=args.shrink_max_candidates)

    result = asyncio.run(_go())
    if not result.reproduced and result.candidates_tried == 0:
        print(f"scenario {args.shrink} is green — nothing to shrink",
              file=sys.stderr)
        return 0
    print(json.dumps({
        "minimal_scenario": result.scenario.__dict__ | {
            "turns": [
                {**{k: v for k, v in t.__dict__.items() if k != "expect"},
                 "expect": t.expect}
                for t in result.scenario.turns
            ],
        },
        **result.as_dict(),
    }, ensure_ascii=False, indent=1, default=str))
    if not result.reproduced:
        print("shrink did not converge within budget — see "
              "truncated_by_budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
