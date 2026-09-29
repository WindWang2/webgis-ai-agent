#!/usr/bin/env python
"""Offline Harness Evaluation Lab CLI（E15）。

统一离线评测入口：ScenarioSpec（JSON 冻结资产）→ 统一 runner（编排既有
evaluator：replay oracle / settlement seam / GISBenchmarkRunner / 制图语义
检查 / 视觉 fixture / science oracles）→ 机器可读报告 + 人读摘要 + 显式基线。

用法（工作区根目录）：

    # 全部代表旅程规格
    python scripts/eval_lab.py

    # 指定规格 + 基线比对（digest / 裁决漂移）
    python scripts/eval_lab.py --spec tests/fixtures/lab/scenarios/j1-chengdu-schools.json \
        --baseline tests/fixtures/lab/baseline.json

    # 显式重建基线（有红拒绝；--force 才放行并留痕）
    python scripts/eval_lab.py --write-baseline tests/fixtures/lab/baseline.json

退出码：存在红 spec → 1；基线比对发现漂移 → 1；规格加载失败 → 2。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ScienceOracleAdapter:
    """science oracle 域 adapter（tests 域资产经脚本侧惰性注入；E15 D8）。

    判错完全委托 ``tests.science_oracles.run_case``（参考公式回放，零重算
    期望）；lab 侧只做聚合投影 —— GIS semantic correctness 维的贡献。
    """

    name = "science_oracles"

    def applies(self, spec) -> bool:
        return bool(spec.science_oracle_domains)

    async def __call__(self, spec, *, seed: int = 0):
        from tests.science_oracles import load_domain, run_case

        from app.lib.harness.lab.adapters import AdapterOutcome
        from app.lib.harness.lab.metrics import (
            FAIL,
            PASS,
            DimensionVerdict,
        )

        outcome = AdapterOutcome(adapter=self.name)
        total = 0
        passed = 0
        failures = []
        domains_run = []
        for domain in spec.science_oracle_domains:
            cases = load_domain(domain)
            if not cases:
                outcome.violations.append(
                    f"science oracle domain {domain!r} has no cases "
                    "(unknown domain or empty data)")
                continue
            domains_run.append(domain)
            for case in cases:
                total += 1
                ok, detail = run_case(case)
                if ok:
                    passed += 1
                else:
                    failures.append(f"{case.case_id}: {detail[:120]}")
        outcome.observations = {
            "domains": domains_run,
            "cases": total,
            "passed": passed,
        }
        if total:
            outcome.contributions.append(DimensionVerdict(
                dimension="gis_semantic_correctness",
                status=PASS if not failures else FAIL,
                value=f"{passed}/{total}",
                detail="; ".join(failures[:6]),
            ))
        return outcome


def _hermetic_env_pins() -> None:
    """CLI 侧 hermetic 缺省（tests/conftest.py 同款钉扎；显式 env 优先）。

    lab 是离线实验室：任何未显式配置的分布式依赖一律按"关闭"处理，
    T2 变异重放的 session 锁走进程内回退，而不是连接失败翻车。
    """
    import os

    os.environ.setdefault("USE_REDIS", "false")
    os.environ.setdefault("HARNESS_REPLAY_RECORD", "")


def main() -> int:
    _hermetic_env_pins()
    from app.lib.harness.lab.adapters import register_adapter
    from app.lib.harness.lab.journeys import default_spec_paths, load_spec
    from app.lib.harness.lab.report import (
        build_report,
        compare_baseline,
        write_baseline,
        write_report,
    )
    from app.lib.harness.lab.runner import LabRunner

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec", action="append", default=[],
        help="规格 JSON 路径（可重复；缺省 = tests/fixtures/lab/scenarios 全部）")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--baseline", default=None,
                        help="基线路径：比对 digest / 裁决漂移")
    parser.add_argument("--write-baseline", dest="write_baseline",
                        default=None,
                        help="把本次运行的确定性投影写为基线（显式更新）")
    parser.add_argument("--force", action="store_true",
                        help="配合 --write-baseline：允许在存在红时写基线")
    parser.add_argument("-f", "--format", default="json",
                        choices=["json", "md"])
    parser.add_argument("-o", "--output", default="-")
    parser.add_argument("--baseline-out", default=None,
                        help="基线比对报告的输出路径（缺省并入 stdout）")
    args = parser.parse_args()

    if args.write_baseline and args.baseline:
        print("--write-baseline 与 --baseline 互斥（写基线时不做比对）",
              file=sys.stderr)
        return 2

    register_adapter(ScienceOracleAdapter())

    try:
        paths = [Path(p) for p in args.spec] or default_spec_paths()
        specs = [load_spec(p) for p in paths]
    except Exception as exc:  # noqa: BLE001 — 规格加载失败 = CLI 2
        print(f"spec load failed: {exc}", file=sys.stderr)
        return 2
    if not specs:
        print("no specs selected", file=sys.stderr)
        return 2

    evals = asyncio.run(LabRunner(seed=args.seed).run_specs(specs))
    report = build_report(
        evals, seed=args.seed,
        spec_paths=[str(p) for p in paths])

    exit_code = 0 if report["red"] == 0 else 1
    if args.write_baseline:
        try:
            write_baseline(report, args.write_baseline, force=args.force)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"baseline written: {args.write_baseline}", file=sys.stderr)
    elif args.baseline:
        comparison = compare_baseline(report, args.baseline)
        if comparison["drifts"]:
            exit_code = 1
        payload = dict(report)
        payload["baseline_comparison"] = comparison
        write_report(payload, args.output, args.format)
        if args.baseline_out:
            write_report({"baseline_comparison": comparison},
                         args.baseline_out, "json")
    else:
        write_report(report, args.output, args.format)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
