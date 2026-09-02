# ADR-0093: GIS Runtime Correctness & Concurrency V5

**Date:** 2026-09-02
**Status:** Accepted
**Supersedes:** none (extends ADR-0091 V4 interactive runtime and ADR-0092 reproducible runtime)

## Context

The post-V4 audit (#1108–#1113) surfaced a cluster of runtime-correctness defects that
shared one root shape: **implicit state whose lifecycle was enforced by convention
instead of by structure**.

- The singleton Pi bridge's turn lock leaked under cancellation re-delivery (#1108):
  the finally awaited Redis I/O before `release()`, and a second `CancelledError`
  skipped the release, hanging every session until process restart.
- The same-ref overwrite path (#1112) invalidated browser caches only via TTL —
  no mechanism told the browser *the content behind a stable URL changed*.
- Redis capacity eviction (#1111) forgot two of three process-local caches —
  ghost spatial indexes and tiles kept serving evicted refs.
- Upload ownership (#1109) still grand-fathered NULL/NULL legacy rows — an
  enumerable IDOR.
- Tool boundaries (#1110, and six more found in the V5 review sweep) stripped
  FeatureCollection `crs` members by forwarding bare feature lists.

V5's objective is therefore **not** more GIS capability. It is: make the existing
runtime provably correct under multi-session concurrency, cancellation, cache
mutation, and CRS-carrying workloads.

## Problems

| # | Problem | Root shape |
|---|---------|-----------|
| 1 | Turn lock leak under cancel re-delivery | implicit ownership (probe `locked()` or assume) |
| 2 | One global turn lock = one turn per PROCESS for all sessions | singleton bottleneck |
| 3 | Ghost derived caches after eviction/overwrite | hand-copied invalidation triplets |
| 4 | Browser renders stale geometry after same-ref overwrite/rollback | URL carries no content version |
| 5 | CRS stripped at tool boundaries | list-vs-FC duality at the seam |
| 6 | Legacy anonymous uploads world-readable | capability = enumerable id |

## State Ownership (unchanged truth sources)

V5 introduces **no second truth**. Existing owners keep their role:

- **SessionPlan** — plan/truth for conversational planning (unchanged)
- **MapSpec** — map desired-state (unchanged)
- **Session store (Memory/Redis)** — authoritative ref payload
- **PiTurnRegistry (Redis)** — cross-pod active-turn identity
- **Project runtime** — durable workflow/project truth (unchanged)

New V5 structures are **coordinators over existing truth**, never parallel stores:
`_ActiveTurnEntry`/`PiBridgePool` (process-local turn execution), `ref_lifecycle`
(invalidation coordinator), `content_revision` (an integer stamped onto existing
descriptors).

## Concurrency Model & Turn Ownership (V5-A/B)

### TurnLease (from #1123)

Lock invariants (all structurally enforced, none by `locked()` probing):

- **INV-P1** every successful acquire is released exactly once (idempotent lease).
- **INV-P2** a turn can never release another turn's acquisition (owner-checked;
  stale lease release is refused and logged).
- **INV-P3** register failure/cancellation cannot leak the lock (register runs
  inside the lease-protected try; outer-finally backstop).
- **INV-P4** unregister failure/cancellation cannot leak the lock (synchronous
  release precedes the shielded, bounded unregister await).

### Session-keyed active-turn table (V5-B-1)

The single module-global token slot became `_active_turns: dict[session_id,
_ActiveTurnEntry]` (token, turn_id, run_id, owning bridge). Every consumer —
`dispatch_tool` token binding, evidence correlation, `abort()` — resolves by
session id. Unregister is turn-id-checked so a late duplicate cannot clear a
successor's entry.

### Bridge pool (V5-B-2)

`PI_BRIDGE_POOL_SIZE` (default **1**) workers, each a full `PiBridge` owning its
own Pi subprocess and event queue. Sessions get **stable affinity**
(`hashlib.md5(session_id) % N` — deterministic across restarts, unlike `hash()`).

Why affinity rather than a shared queue: the vendor Pi subprocess processes one
prompt at a time and events land on that worker's own queue; they are not
routable across workers. Affinity preserves per-session ordering (A1 → A2 on the
same worker) while disjoint sessions execute in parallel on different
subprocesses. Cross-worker coordination (abort routing, callback token binding)
is worker-agnostic because it flows through the session-keyed table and the
session-scoped dispatch rendezvous.

At the default pool size 1 the behavior is byte-identical to the historical
singleton; N>1 is opt-in scaling, not a semantics change.

## Ref Lifecycle (V5-C)

`app/services/ref_lifecycle.py` is the single invalidation contract:

```
invalidate_ref_caches(session_id, [ref_id...], reason=RefInvalidationReason, include_payload_cache=bool)
```

- Reasons: `OVERWRITE | DELETE | EVICT | EXPIRE | ROLLBACK | REPLACE`
- Both session-store backends route **every** write path (store-eviction,
  overwrite, delete, clear) through it.
- `invalidate ≠ delete`: lifecycle invalidation drops projections only; the
  authoritative payload deletion stays with the store.
- `include_payload_cache=False` for the Memory backend (its in-process store IS
  the payload truth); `True` for Redis (this process holds only a parse cache).

## Cache Dependency Model (V5-D)

```
Authoritative ref payload (session store)
       ├── descriptor (store-time compute; stamped with live revision on read)
       ├── ref_payload_cache (Redis-backend parse cache)
       ├── spatial_index_cache (STRtree per (session, ref))
       ├── tile_lru_cache (gzip MVT bytes per (session, ref, z, x, y))
       ├── browser HTTP cache (ETag/304 + max-age + v=<revision>)
       └── frontend resolved source (MapLibre vector source via _tileUrl)
```

Any authoritative mutation MUST invalidate the first four immediately and the
last two via the revision bump. This matrix is enforced by a parametrized test
(`tests/unit/test_ref_lifecycle_v5.py`) — a future write path that forgets one
projection fails the suite instead of shipping a #1111.

## Content Revision (V5-E)

`RefDescriptor.content_revision: int` — monotonic per ref identity
(store → 1, overwrite → +1, delete → dropped). Both backends maintain it
(Redis: `HINCRBY` on a per-session hash, atomic with the payload write) and
`get_ref_descriptor` stamps the **live** value onto every read (descriptor
snapshots can lag).

Frontend `buildMvtTileUrl` (single template replacing three hand-copies) appends
`v=<revision>`; the reconciler sees a changed source URL and MapLibre re-adds
the source — a cache-busting refetch without any full-FC refetch, preserving the
MVT data-plane advantage. The 30s `max-age` (#1116 mitigation) stays as defense
in depth for URLs cached before the mechanism deployed.

Why a counter, not a content hash: invalidation needs change **detection**, not
change **identity**. A full-payload sha256 would re-introduce the O(payload)
serialization V3 removed from the store hot path.

## Failure Semantics

- Cancellation at any teardown await: lease backstop releases; shielded
  best-effort unregister/abort with bounded budget; cleanup failures log and
  continue (INV-P1..P4 hold in all paths).
- Redis failures in turn registration/unregistration: best-effort, never block
  the turn lifecycle.
- Randomized acceptance: 120-iteration cancel storm leaves the lock free and
  the next session immediately serviceable (#1108 acceptance).

## Security Boundaries (carried into V5)

- Skill sandbox: restricted builtins + per-file load isolation + creation
  dry-run + frame/introspection import/attribute blockers (#1120, #1126).
- Upload ownership: NULL/NULL grandfather closed; migration minted random
  tokens; full ownership matrix tested (#1124, #1125).
- Session authorization predicates (`_authorize`, `authorize_session_write`,
  `load_context`) all fail closed on legacy rows.

## Performance Constraints (protected)

- 150k-POI MVT path unchanged: tile URLs only gain a query param.
- Same-object overwrite fast path (plan_mode re-persisting step results skips
  the O(features) descriptor recompute — phase-E review G-2).
- TypeAdapter per-field cache keeps oversized-bypass scalar validation sub-µs.
- No global cache clears on invalidation — all drops are per-(session, ref).

## Compatibility

- Pool default 1: no deployment change required.
- `content_revision` is additive (descriptor dict + TS interface optional field).
- Tile URLs gain `v=` only when a revision exists; pre-V5 cached URLs keep
  working (endpoints ignore the unknown param).

## Rejected Alternatives

- **`lock.locked()` probe release (#1115)** — ownership-blind; releases the
  wrong turn's acquisition. Rejected; superseded by the lease.
- **Per-session turn lock on a single subprocess** — the lock is not the
  bottleneck; the single event queue is. Splitting the lock would break the
  queue-drain attribution invariant for zero parallelism gain.
- **Full payload content hash in the store hot path** — re-introduces
  O(payload) serialization per write; the counter suffices for detection.
- **Second SessionPlan / MapSpec / artifact store / workflow engine** — all
  rejected; V5 only adds coordinators over existing truth.
- **Deleting caches on every ref read miss** — would thrash the data plane;
  invalidation stays write-path-only.

## Deferred Work

- Browser-cache amplifiers on the frontend beyond the URL revision (e.g.
  ref-source FC cache lifetime) — tracked for V5.x.
- Pool autoscaling / per-worker queue-depth telemetry.
- `_raster_path_cache` (5s TTL) explicit invalidation.
- Redis TTL expiry (4h) does not fire invalidation hooks for process-local
  caches (time-based ghosting is bounded by LRU pressure and idle sweeps).
