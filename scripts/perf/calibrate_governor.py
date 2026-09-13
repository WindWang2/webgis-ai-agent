"""Governor 校准 CLI（R17，ADR-0182 D12）。

跑 synthetic corpus（small/medium/large/extreme × vector/raster/acquisition/
cartography/export/long_context）穿过真实 governor 管线，采集：
- 各规模档的准入决策分布（accept/defer/degrade/reject）；
- 估算 vs 实际误差（memory/wall —— FakeExecutor 按估时 sleep，
  ``--time-scale`` 控制真实耗时占比）；
- 峰值在飞预留 / 通道水位 / 排队分位数。

产出（provisional 纪律，对齐 ADR-0159 ratchet）：
- 证据 JSON（默认 ``docs/dev/harness-resource-v1-calibration.json``）；
- 对 ``config/governor_budgets.json`` 的**建议**更新（只打印/写证据，
  不直接改预算 —— 预算变更走显式 PR review）。

Usage:
  python scripts/perf/calibrate_governor.py [--sessions N] [--tasks N]
      [--time-scale F] [--report OUT.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.governor.config import GovernorConfig, load_manifest  # noqa: E402
from app.services.governor.contract import Dimension  # noqa: E402
from app.services.governor.governor import HarnessResourceGovernor  # noqa: E402
from tests.governor.synthetic import (  # noqa: E402
    SCALES,
    FakeExecutor,
    run_mixed_corpus,
    synthetic_demand,
)


async def _calibrate(sessions: int, tasks: int, time_scale: float) -> dict:
    config = GovernorConfig()
    config.queue_max_wait_s = 8.0
    budgets = load_manifest(REPO_ROOT / "config" / "governor_budgets.json")
    gov = HarnessResourceGovernor(config, budgets=budgets)
    executor = FakeExecutor(gov, time_scale=time_scale, rng_seed=20260914)

    decisions: Counter = Counter()
    mem_errors: list = []
    wall_errors: list = []
    by_scale: dict = {}

    domains = ("vector", "raster", "acquisition", "cartography",
               "export", "long_context")

    async def one(sid: int, i: int) -> None:
        domain = domains[(sid + i) % len(domains)]
        scale = SCALES[(sid * 3 + i) % len(SCALES)]
        demand = synthetic_demand(f"cal-{sid}", domain, scale,
                                  turn_id=f"t-{sid}-{i}", seed=sid * 1000 + i)
        demand.max_wait_s = 8.0
        t0 = asyncio.get_event_loop().time()
        dec, res, ticket = await gov.admit_and_reserve(demand)
        decisions[dec.decision.value] += 1
        if res is None:
            return
        wall_expect = demand.estimate.adjudged(Dimension.WALL_TIME_S)
        wall_actual = wall_expect * (0.8 + 0.4 * ((sid * 7 + i) % 5) / 4.0)
        if time_scale > 0:
            await asyncio.sleep(min(wall_actual * time_scale, 5.0))
        mem_expect = demand.estimate.adjudged(Dimension.MEMORY_BYTES)
        mem_actual = mem_expect * (0.85 + 0.3 * ((sid * 11 + i) % 5) / 4.0)
        if mem_expect > 0:
            mem_errors.append(abs(mem_actual / mem_expect - 1.0))
        if wall_expect > 0:
            wall_errors.append(abs(wall_actual / wall_expect - 1.0))
        slot = by_scale.setdefault(scale, {"n": 0, "waits": []})
        slot["n"] += 1
        slot["waits"].append(asyncio.get_event_loop().time() - t0)
        await gov.complete(res, ticket,
                           actual={Dimension.WALL_TIME_S: wall_actual,
                                   Dimension.MEMORY_BYTES: mem_actual},
                           estimate=demand.estimate)

    await asyncio.gather(*(one(s, i) for s in range(sessions)
                            for i in range(tasks)))

    snap = gov.snapshot()
    scale_stats = {}
    for scale, slot in by_scale.items():
        waits = slot["waits"]
        scale_stats[scale] = {
            "n": slot["n"],
            "p50_wait_s": round(statistics.median(waits), 4),
            "p95_wait_s": round(sorted(waits)[int(len(waits) * 0.95)], 4),
        }
    return {
        "schema": "governor-calibration.v1",
        "inputs": {"sessions": sessions, "tasks_per_session": tasks,
                   "time_scale": time_scale},
        "decisions": dict(decisions),
        "estimate_error": {
            "memory_mean_abs_ratio": (round(statistics.mean(mem_errors), 4)
                                      if mem_errors else None),
            "wall_mean_abs_ratio": (round(statistics.mean(wall_errors), 4)
                                    if wall_errors else None),
            "n": len(mem_errors),
        },
        "per_scale": scale_stats,
        "peak": {
            "live_reservations": snap.get("live_reservations"),
            "channels": snap.get("channels"),
            "retries_global_left": snap.get("retries", {}).get("global_left"),
        },
        "estimate_error_basis": (
            "synthetic-jitter smoke only: 'actuals' are derived from the "
            "same estimates via a deterministic jitter formula, so the "
            "error ratios validate plumbing, NOT estimator accuracy. "
            "Replace with measured actuals from a real corpus before "
            "quoting these numbers as calibration evidence (review P2c)."
        ),
        "provisional_note": (
            "ALL budgets in config/governor_budgets.json remain provisional "
            "until maintainers activate updated thresholds from repeated "
            "calibration runs (ADR-0159 ratchet discipline)."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--tasks", type=int, default=12)
    parser.add_argument("--time-scale", type=float, default=0.0,
                        help="0 = 瞬时（排队/账面校准）；0.01+ 引入真实耗时"
                             "用于 estimate-error 校准")
    parser.add_argument("--report", type=Path,
                        default=REPO_ROOT / "docs" /
                        "harness-resource-v1-calibration.json")
    args = parser.parse_args()

    report = asyncio.run(_calibrate(args.sessions, args.tasks,
                                    args.time_scale))
    out = args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"[calibrate-governor] report -> {out}")
    print(json.dumps({
        "decisions": report["decisions"],
        "estimate_error": report["estimate_error"],
        "per_scale": report["per_scale"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
