# 0045. Tool Cache Singleflight (stampede protection)

**Date:** 2026-08-07
**Status:** Accepted

## Context

`cached_tool` (`app/lib/tool_cache.py`) guards against repeat work on cache
*miss*: two users — or one plan wave after the parallel tool dispatch
(commit `1faaba1`) — issuing the same expensive tool call concurrently both
missed and both computed (OSM query, H3, KDE, interpolation, raster warp,
DEM/spectral products). The Redis cache only helps the *second* caller after
the first *finishes*; concurrent misses are a stampede.

## Decision

1. **SET NX lock with a random token and a TTL** on the cache-miss path
   (`<cache_key>:lock`, `px` 120 s default, per-tool override via
   `lock_ttl=`). The lock winner computes, publishes the value via
   `set_cached` **before** releasing, then releases with a Lua
   compare-and-delete so a slow winner can never delete a successor's lock.
2. **Followers poll** the cache value with exponential backoff (0.05 s →
   1 s) and additionally watch lock liveness: if the lock disappears without
   a value (winner's compute failed and released), they take over immediately
   instead of waiting out the TTL.
3. **Bounded degradation, never deadlock**: stale locks expire via TTL;
   Redis errors on acquire/poll fall back to direct compute (same as
   pre-singleflight behavior); a compute exceeding the lock TTL degrades to a
   bounded duplicate. No distributed deadlock or permanent lock is possible
   (goal §7).
4. **Defaults on** for all `cached_tool` usages (`singleflight=False` opts
   out). Miss-path overhead is 2 extra Redis round-trips (SET NX + DEL/eval);
   the warm hit path is unchanged.

## Consequences

- 5 concurrent identical 0.5 s calls: 5 computes → 1 compute, all callers get
  the same result (~0.75 s wall, one CPU/OSM/GDAL execution).
- Followers' latency ≈ the winner's compute latency (they wait instead of
  computing in parallel); on the async path the wait runs in a thread
  (`asyncio.to_thread`), never blocking the event loop; the sync path bounds
  its wait to 30 s so waiting followers don't squat threadpool threads.
- `cache_hit_var` records followers as misses (they did pay a wait); a
  "singleflight-wait" signal in tool metrics is a possible follow-up.
