# 0043. Tool Execution Policy (supersedes ADR-0003)

**Date:** 2026-08-07
**Status:** Accepted

## Context

ADR-0003 ("Hybrid Async Compute Model") decided that heavy spatial ops run in
Celery workers while agent tool calls run in-process on the event loop. The
architecture has since drifted from that decision:

- The heavy spatials (ST-DBSCAN, KDE, hotspot, Moran's I, Voronoi, buffer,
  overlay, IDW, ...) now run **in-process via `asyncio.to_thread`** at a single
  registry seam (`app/tools/registry.py:299-316`, commit `1faaba1`) — off the
  event loop, but not in Celery.
- Only three Celery tasks survive (`app/services/spatial_tasks.py`): NDVI,
  change detection, heatmap generation.
- A few *async* tools called blocking work directly on the loop (a 90s
  Chromium subprocess in `runtime_validator`, per-feature Python profiling in
  `mapspec_store.source_profile`, band algebra in `spectral_engine`) — now
  offloaded via `asyncio.to_thread` (commit adding this ADR).

ADR-0003's negatives also no longer hold (three task-tracking systems were
consolidated; see ADR-0013/ADR-0037).

## Decisions

The actual, normative execution policy is:

1. **Registry seam is the only execution decision point.** Every agent path
   (chat/SSE, plan mode, `/tools/execute`) funnels through
   `ToolRegistry._dispatch_impl` (`app/tools/registry.py:299-316`):
   - **sync tool** → `asyncio.to_thread` (default loop executor) — never on
     the event loop;
   - **async tool** → awaited directly; its body **must be await-only
     (non-blocking)**.
2. **Async tools that need CPU/blocking work must offload it themselves** via
   `asyncio.to_thread` around the blocking section (established in
   `runtime_validator`, `mapspec_store.source_profile`, `spectral_engine`).
   Regression tests in `tests/unit/test_execution_offload.py` pin this
   contract behaviorally (event-loop responsiveness during tool execution).
3. **Celery** is reserved for the three remaining long/background tasks
   (NDVI, change detection, heatmap); it is not the default for tool calls.
4. **Cancellation is cooperative** (unchanged from ADR-0003):
   `asyncio.to_thread` work cannot be preempted; cancelling a task detaches
   the thread, which runs to completion. Acceptable for the current workload.

## Consequences

- SSE/WebSocket responsiveness no longer depends on which tool runs, only on
  thread-pool saturation: the default executor bounds concurrent CPU-bound
  tools to `min(32, cpu+4)`; a dedicated bounded pool / semaphore is a
  candidate follow-up if saturation is observed.
- Execution-mode telemetry (`loop` vs `thread` vs `celery`) is a candidate
  follow-up; today `tool_metrics` records duration/cache/error/bytes only.
- ADR-0003 is superseded by this document for the execution-model decisions;
  its cooperative-cancellation caveat remains in force.
