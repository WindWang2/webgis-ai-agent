"""GIS Benchmark Factory V2 CLI（离线确定性；零 LLM / 零网络）。

用法（工作区根目录）：

    # 全量 V2 基准（V2 语料 + 专用驱动器），输出 JSON + markdown
    python scripts/gis_bench_v2.py --out reports/bench_v2

    # 基线对照（回归归因：new_failures / fixed / metric_moved）
    python scripts/gis_bench_v2.py --out reports/bench_v2 \
        --baseline reports/bench_v2/report.json

    # 仅语料清单（计数 + version_hash；CI 漂移信号，秒级）
    python scripts/gis_bench_v2.py --manifest-only --out reports/bench_v2

退出码：有 new_failures（对照基线时）或有 case 失败（无基线时）→ 1。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

# Windows 控制台 GBK 输出兜底（markdown 含中文）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")


def _v2_results():
    from app.evaluation.cartography_axes_corpus import (
        build_cartography_axes_corpus,
        run_cartography_axes_case,
    )
    from app.evaluation.evidence_corpus import build_evidence_corpus
    from app.evaluation.hard_negative_corpus import build_hard_negative_corpus
    from app.evaluation.mission_corpus import build_mission_corpus
    from app.evaluation.mission_driver import run_mission_scenario
    from app.evaluation.runner import GISBenchmarkRunner
    from app.evaluation.security_corpus import build_security_corpus
    from app.evaluation.skill_policy_corpus import build_skill_policy_corpus

    runner = GISBenchmarkRunner()
    results = []
    for builder in (
        build_skill_policy_corpus,
        build_hard_negative_corpus,
        build_evidence_corpus,
        build_security_corpus,
    ):
        results.extend(asyncio.run(runner.run(builder())))
    results.extend(
        run_mission_scenario(s) for s in build_mission_corpus())
    results.extend(
        run_cartography_axes_case(c) for c in build_cartography_axes_corpus())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="GIS Benchmark Factory V2")
    parser.add_argument("--out", default="reports/bench_v2",
                        help="output directory for report.json / report.md")
    parser.add_argument("--baseline", default="",
                        help="path to baseline report.json for diff")
    parser.add_argument("--manifest-only", action="store_true",
                        help="only write the corpus manifest (fast drift check)")
    args = parser.parse_args()

    from app.evaluation.index import corpus_manifest, write_manifest
    from app.evaluation.report import (
        aggregate_by_group,
        diff_against_baseline,
        render_json,
        render_markdown,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = corpus_manifest()
    write_manifest(out_dir / "manifest.json", manifest)

    if args.manifest_only:
        total = sum(m["count"] for m in manifest.values())
        print(f"manifest: {len(manifest)} corpora, {total} cases "
              f"-> {out_dir / 'manifest.json'}")
        return 0

    results = _v2_results()
    report = render_json(results, manifest=manifest)
    report["groups"] = aggregate_by_group(results)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(results), encoding="utf-8")

    print(f"cases={report['cases']} passed={report['passed']} "
          f"failed={report['failed']} skipped={report['skipped']}")
    for group in sorted(report["groups"]):
        g = report["groups"][group]
        print(f"  {group}: pass_rate={g['pass_rate']} ({g['passed']}/{g['cases']})")

    exit_code = 0
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        diff = diff_against_baseline(report, baseline)
        (out_dir / "diff.json").write_text(
            json.dumps(diff, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"baseline diff: new_failures={len(diff['new_failures'])} "
              f"fixed={len(diff['fixed'])} metric_moved={len(diff['metric_moved'])} "
              f"new_cases={len(diff['new_cases'])} missing_cases={len(diff['missing_cases'])}")
        if diff["new_failures"]:
            exit_code = 1
    elif report["failed"]:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
