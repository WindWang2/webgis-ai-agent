# 0046. Performance Regression Harness

**Date:** 2026-08-07
**Status:** Accepted

## Context

The goal requires every optimization to be measured and any future PR to be
unable to silently degrade performance ("每次优化都有 baseline；以后任何 PR
都不容易把性能偷偷改坏"). Before this ADR, performance feedback was ad hoc:
measurements existed in CHANGELOG entries but nothing enforced them, and the
old test suite even contained a 111.6 G-pixel warp masquerading as a
correctness test (fixed in ADR-0042).

## Decisions

1. **Deterministic, no-network workloads** in
   `tests/benchmarks/test_perf_harness.py` (`-m perf` marker), covering the
   hot paths optimized in ADR-0042..0045:

   | Workload | Measures | Baseline (2026-08-07, dev box) |
   |---|---|---|
   | `raster_guard_rejection` | pathological resample rejected end-to-end | ~4.8 ms (was 2–5 min warp) |
   | `ref_resolution_batch` | 10 string args, one batched resolution | ~0.37 ms (was 10 serial RTTs) |
   | `metrics_enqueue` | `record_tool_call` caller cost | ~17 µs |
   | `dispatch_overhead` | registry.dispatch wrapper (sync tool) | ~0.31 ms |

2. **Three-level gate** (goal §10): each workload's median of 7 iterations is
   compared to the committed baseline —
   - `median ≤ max(floor, baseline × 1.75)` → PASS;
   - up to `max(floor, baseline × 4.0)` → PASS with warning (skip + message);
   - above → **HARD regression, test fails**.
   Medians + absolute floors + generous factors keep ordinary CI noise from
   flaking the gate while still catching order-of-magnitude regressions.

3. **Baselines are data, not lore**: `tests/benchmarks/baselines.json`
   (median + iterations per workload), refreshed only with
   `PERF_UPDATE_BASELINES=1` after an intentional, measured improvement.

## Consequences

- Any PR that makes the guard reject slowly, re-introduces per-string Redis
  RTTs, blocks the loop in metrics, or bloats dispatch overhead will fail
  `-m perf` — locally and (when wired into CI) in review.
- The harness is deliberately small (4 workloads, ~1.5 s) so it can run on
  every PR without slowing the suite.
- New workloads should be added as optimizations land — a workload without a
  baseline is auto-recorded on first run (skipped with a message).
