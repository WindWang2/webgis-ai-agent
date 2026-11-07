#!/usr/bin/env python3
"""Harness reliability replay benchmark（G07 M6 性能切片证据脚本）。

可复现采集 replay bench 热路径的 before/after 原始数据，落盘
``docs/quality/evidence/harness_reliability_bench_report.json``。

测量矩阵（全部固定 seed=11、顺序执行、无网络/无 LLM）：
  - suite 缩放：全量 corpus（140 场景）× profile small/medium/large ×
    重复 5 次 → wall / CPU / maxRSS / payload UTF-8 bytes / entry 数。
  - resume flush before/after：``flush_interval=1`` 精确等价旧「每场景
    全量重写」语义（每次写完整 entries dict），``flush_interval=16``
    为新默认 → 同一 run 内对比（真实运行，非模拟）。
  - context_bytes_proxy 口径微基准：str() 旧公式 vs canonical_json
    UTF-8 新公式（仅计量函数本身；该面是报告计量，不是速度优化，差异
    如实记录）。

用法：
    python scripts/perf/bench_harness_reliability.py
    # 缩小样本（冒烟）：
    BENCH_LIMIT=30 BENCH_REPEATS=3 python scripts/perf/bench_harness_reliability.py
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import resource
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

SEED = 11
REPEATS = int(os.environ.get("BENCH_REPEATS") or 5)
LIMIT = int(os.environ.get("BENCH_LIMIT") or 0)  # 0 = 全量 corpus
REPORT_PATH = ROOT / "docs/quality/evidence/harness_reliability_bench_report.json"


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _pct(samples, q):
    s = sorted(samples)
    return round(s[min(len(s) - 1, int(q * len(s)))], 3)


def _summary(samples):
    return {
        "n": len(samples),
        "min_ms": round(min(samples), 3),
        "p50_ms": _pct(samples, 0.5),
        "p95_ms": _pct(samples, 0.95),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
    }


async def measure_suite(scenarios, *, profile, resume_path=None,
                        flush_interval=16):
    """单次 run_suite 计时；返回 wall/cpu/maxRSS/payload bytes/entry 数。"""
    rss_before = _rss_mb()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    report = await asyncio.wait_for(
        __import__("app.lib.harness.replay.bench", fromlist=["run_suite"])
        .run_suite(scenarios, seed=SEED, profile=profile,
                   resume_path=resume_path, flush_interval=flush_interval),
        timeout=600)
    wall_ms = (time.perf_counter() - wall0) * 1000
    cpu_ms = (time.process_time() - cpu0) * 1000
    payload_bytes = sum(e.get("context_bytes_proxy") or 0
                        for e in report["entries"])
    return {
        "wall_ms": round(wall_ms, 3),
        "cpu_ms": round(cpu_ms, 3),
        "max_rss_mb": round(max(_rss_mb(), rss_before), 1),
        "entries": len(report["entries"]),
        "green": report["green"],
        "red": report["red"],
        "payload_utf8_bytes": payload_bytes,
    }


async def main() -> dict:
    from app.lib.harness.replay.bench import select_scenarios
    from app.lib.harness.replay.scenarios import build_corpus

    corpus = build_corpus()
    scenarios = select_scenarios(corpus, "all",
                                 limit=LIMIT or None)
    print(f"[env] corpus={len(corpus)} selected={len(scenarios)} "
          f"repeats={REPEATS} seed={SEED} rss={_rss_mb():.0f}MB")

    # 冷态一次（graph/digest 一次性投影，单独披露，不进 before/after）。
    cold = await measure_suite(scenarios[:2], profile="small")
    print(f"[cold] warm-up 2 scenarios: {cold['wall_ms']:.0f}ms")

    results = {"cold_start_2scenarios_ms": cold["wall_ms"], "profiles": {},
               "resume_flush": {}}

    for profile in ("small", "medium", "large"):
        runs = [await measure_suite(scenarios, profile=profile)
                for _ in range(REPEATS)]
        walls = [r["wall_ms"] for r in runs]
        results["profiles"][profile] = {
            "scenario_count": len(scenarios),
            "wall": _summary(walls),
            "cpu_p50_ms": _pct([r["cpu_ms"] for r in runs], 0.5),
            "max_rss_mb": max(r["max_rss_mb"] for r in runs),
            "payload_utf8_bytes": runs[0]["payload_utf8_bytes"],
            "entries": runs[0]["entries"],
            "green": runs[0]["green"],
            "red": runs[0]["red"],
            "runs": runs,
        }
        print(f"[{profile}] wall p50={_pct(walls, 0.5):.1f}ms "
              f"p95={_pct(walls, 0.95):.1f}ms "
              f"payload={runs[0]['payload_utf8_bytes']}B "
              f"green={runs[0]['green']}/{runs[0]['entries']}")

    # resume flush before/after：同一 corpus、同一 run 内对比。
    tmp_dir = ROOT / "data" / ".webgis-agent" / "perf"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for label, interval in (("before_flush_every_scenario", 1),
                            ("after_flush_interval_16", 16)):
        state = str(tmp_dir / f"g07c_resume_{label}.json")
        os.path.exists(state) and os.remove(state)
        runs = []
        for _ in range(REPEATS):
            os.path.exists(state) and os.remove(state)
            runs.append(await measure_suite(
                scenarios, profile="small", resume_path=state,
                flush_interval=interval))
        walls = [r["wall_ms"] for r in runs]
        results["resume_flush"][label] = {
            "flush_interval": interval,
            "scenario_count": len(scenarios),
            "wall": _summary(walls),
            "cpu_p50_ms": _pct([r["cpu_ms"] for r in runs], 0.5),
            "max_rss_mb": max(r["max_rss_mb"] for r in runs),
            "runs": runs,
        }
        print(f"[resume/{label}] wall p50={_pct(walls, 0.5):.1f}ms "
              f"p95={_pct(walls, 0.95):.1f}ms")
    for f in tmp_dir.glob("g07c_resume_*.json"):
        f.unlink()

    # context_bytes_proxy 口径微基准（计量函数本身，非 run_one 全路径）。
    from app.lib.harness.replay.bench import apply_profile
    from app.lib.harness.replay.determinism import canonical_json
    probe = apply_profile(select_scenarios(
        corpus, "all", only="core-heatmap-01")[0], "large")
    ops = [op for t in probe.turns for op in t.ops]

    def _str_formula():
        return sum(len(str(op.arguments)) for op in ops)

    def _utf8_formula():
        return sum(len(canonical_json(op.arguments).encode("utf-8"))
                   for op in ops)

    loops = 200
    t0 = time.perf_counter()
    for _ in range(loops):
        _str_formula()
    str_ms = (time.perf_counter() - t0) * 1000 / loops
    t0 = time.perf_counter()
    for _ in range(loops):
        _utf8_formula()
    utf8_ms = (time.perf_counter() - t0) * 1000 / loops
    results["context_proxy_formula"] = {
        "sample": "core-heatmap-01 profile=large",
        "op_count": len(ops),
        "str_chars": _str_formula(),
        "utf8_bytes": _utf8_formula(),
        "str_formula_ms_per_call": round(str_ms, 4),
        "utf8_formula_ms_per_call": round(utf8_ms, 4),
        "note": "计量口径修正，不是速度优化；str() 不计非 ASCII 真实字节",
    }
    print(f"[proxy] str={str_ms:.3f}ms utf8={utf8_ms:.3f}ms per call "
          f"(str_chars={results['context_proxy_formula']['str_chars']} "
          f"utf8={results['context_proxy_formula']['utf8_bytes']}B)")

    results["unmeasured"] = [
        {
            "dimension": "artifact_bytes",
            "status": "not_evaluated",
            "reason": "BenchEntry has no artifact-byte source; payload_utf8_bytes is not relabeled",
        },
        {
            "dimension": "emitted_events",
            "status": "not_evaluated",
            "reason": "offline replay does not expose production SSE event counts",
        },
        {
            "dimension": "rendering_frames",
            "status": "not_evaluated",
            "reason": "offline replay has no browser rendering frames",
        },
        {
            "dimension": "replay_mutations_scaling",
            "status": "not_evaluated",
            "reason": "Redis-backed mutation replay was unavailable in this environment",
        },
    ]
    results["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "seed": SEED,
        "repeats": REPEATS,
        "scenario_count": len(scenarios),
        "corpus_size": len(corpus),
        "max_rss_mb_end": round(_rss_mb(), 1),
        "concurrency": 1,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"[report] wrote {REPORT_PATH}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
