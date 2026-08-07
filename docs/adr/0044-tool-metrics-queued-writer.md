# 0044. Tool Metrics: queued writer, real rotation, true percentiles

**Date:** 2026-08-07
**Status:** Accepted

## Context

`app/services/tool_metrics.py` records one JSONL row per tool dispatch. The
previous implementation had three goal-violating defects:

1. **Sync file I/O on the event loop**: every `record_tool_call` did
   `open(LOG_PATH, "a")` → `write` → `close` — three syscalls per tool call,
   in the async dispatch wrapper. Cheap on page-cached local disk (~1 µs), but
   a stall under disk contention, rotation, or network filesystems, and it
   serializes every tool call behind the file.
2. **Rotation was promised, not implemented**: the docstring promised 10 MB
   rotation with 5 backups; the code comment said "轮转留到后续" — the file
   grew unbounded for the process lifetime.
3. **`max` mislabeled as `p99`**: the digest's `top_p99` list was `max_ms`
   ranked by `max_ms` — max latency reported as a percentile.

## Decisions

1. **Producer → bounded queue → daemon writer thread.** `record_tool_call`
   keeps the in-memory aggregator (lock-protected) and enqueues the JSONL row
   (queue bound 8192; full queue **drops the row** — backpressure never blocks
   the caller). A daemon writer drains in batches (512 rows, or a 0.1 s idle
   flush) with one `open` per batch, and exits via an `atexit` sentinel.
2. **Real size-based rotation**: before each append the writer checks
   `os.path.getsize`; past `_MAX_LOG_BYTES` (10 MB) it shifts `.1 → .2 → …
   → .5` and moves the current file to `.1` (oldest dropped). No unbounded
   growth anywhere.
3. **True percentiles via a bounded log2 histogram** (33 bins, 0.5 ms to
   hours): `aggregator_snapshot()` and the digest now report real `p50/p95/p99`
   estimates plus `max` (honestly labeled). No raw event retention — memory is
   bounded per tool.
4. **Test contract updated for async writes**: tests poll the log file for
   rows instead of asserting synchronously, and pin rotation, percentile, and
   backpressure behavior (`tests/test_tool_metrics.py`).

## Consequences

- The event loop never opens the metrics file; a stalled disk cannot block
  tool dispatch. Caller-thread cost measured ~24 µs → ~23 µs per call on
  page-cached local disk (the I/O syscalls are gone from the loop; the win is
  robustness under contention, not micro-latency).
- Metrics file growth is capped at ~10 MB + 5 backups.
- The digest and snapshot expose real p50/p95/p99 + max + count + hit/error
  rates, feeding the performance harness (Phase F).
- Up to 8192 rows (~1 MB) can be lost on a hard crash between flushes —
  acceptable for operational metrics (the aggregator snapshot remains
  in-process and is emitted to logs on digest/shutdown).
