# 0051. Harness Evaluation V2 + MapSpec Reliability + Multi-Worker Decision

**Date:** 2026-08-11
**Status:** Accepted

## Context

The Performance & Reliability Convergence V2 + Harness–Cartography Closed Loop
audit found four classes of false-success / reliability defects:

1. **Harness "didn't-error = success".** `MapSpecValidity` equated a mutation
   that returned without error with a valid MapSpec; `CursorResolutionRate` was
   a tautological ref-prefix check (the real ref format `ref:<type>-<id>` never
   matched the legacy colon pattern, so the rate was *structurally always 100*).
   The harness was a long-lived global accumulator with no run/session/turn
   correlation.
2. **MapSpec validation was non-gating.** `apply_mutation` warn-and-save-always:
   invalid MapSpecs persisted to disk + Redis; no transaction / rollback; eager
   Redis layer write could half-commit; file IO was non-atomic; checkpoints
   re-copied every ref on every snapshot (write amplification).
3. **`process_layer_ingestion` blocked the event loop** (the sibling
   `source_profile` path was offloaded; the hot upsert path was not).
4. **Multi-worker assumption violated.** ADR-0006 assumes single-uvicorn-per-pod,
   but `deploy/k8s` runs `replicas: 2` + HPA `minReplicas: 2, maxReplicas: 10`.
   Per-process state (`_session_executed_sets`, `_dispatch_result_cache`,
   `_harness`, per-session `asyncio.Lock` tables, disk MapSpec writes) is not
   cross-pod safe.

## Decisions

### 1. Harness Evaluation V2 (evidence-driven, no false success)

- `MapSpecValidity` is a **tiered ladder** from real evidence:
  `NOT_EVALUATED → MUTATION_REJECTED → MUTATION_ACCEPTED → SEMANTIC_VALID →
  COMPILE_VALID → RUNTIME_VALID`. "Didn't error" is only `MUTATION_ACCEPTED`,
  not valid. `is_compiled` (the real `validate()` outcome from the lifecycle
  engine) lifts a mutation to `SEMANTIC_VALID`. Missing evidence = 0.0, never
  100.0.
- `CursorResolutionRate` resolves refs against the real `SessionStore`
  (`ref_resolver.make_session_store_resolver`): exists + session-scoped +
  typed-prefix match. Cross-session / nonexistent refs do not count. Fixed the
  ref-pattern to the real `ref:<type>-<id>` dash format.
- Every evidence record carries `run_id / session_id / turn_id / tool_call_id /
  mapspec_revision / checkpoint_id / compile / runtime` correlation — no
  cross-session pool.
- `evaluate_evidence()` gate policy: a dimension with no evidence **fails**
  (`require_evaluated=True`), or is exempted when legitimately N/A.
- Legacy float surface (`compute_*`, `evaluate_all`, `get_telemetry_summary`)
  preserved for existing consumers, now computed from real evidence.

### 2. MapSpec transaction semantics + atomic IO + dedup

- `apply_mutation`: build candidate → semantic-validate → **reject mutations
  that introduce NEW blocking errors** (`INVALID_SOURCE_REF`, `INVALID_STOPS_COUNT`,
  `NON_INCREASING_STOPS`) → checkpoint → `save_mapspec` → sync Redis layers, with
  rollback-to-snapshot on any failure. No half-commit.
- `process_layer_ingestion`, `deepcopy`, content-hash, and disk IO all run via
  `asyncio.to_thread` — no event-loop blocking (measured 50k-feature upsert lag
  ≈ 59 ms vs ≈ 300 ms if on-loop).
- Atomic writes (`tempfile` + `os.replace`); disk-before-Redis order.
- Checkpoint **content-addressed ref blobs** + **whole-checkpoint hash dedup**
  for auto checkpoints. A second identical checkpoint writes 0 new bytes.
  Explicit checkpoint ids always materialize (rollback-by-name contract).
- Validator fix: `INVALID_STOPS_COUNT` is method-aware (`interpolate` ≥2,
  `step` ≥1) — a single-threshold step + default is valid MapLibre (the analysis
  converter emits exactly this).

### 3. Cartography closed loop (deterministic semantic checks)

- `app/lib/cartography/semantic_checks.py` connects GIS profile ↔ MapSpec:
  `SOURCE_LAYER_REF` / `EMPTY_DATA` (errors), `GEOMETRY_LAYER_TYPE` /
  `STOPS_DATA_RANGE` / `INTERPOLATE_NUMERIC_FIELD` / `LEGEND_FIELD_CONSISTENCY`
  (warnings). Missing profile → `not_evaluated`, never a fake pass.
- Empty-data (zero features) is an error, not a silent map success.

### 4. Multi-worker decision

The architecture is **single-process-per-pod by design** (Pi subprocess
ownership + localhost HTTP callback — ADR-0006). Multi-pod HA therefore
requires **session affinity (sticky routing)** so a session's requests reach one
pod. This ADR does **not** change that invariant; instead it adds defense-in-depth:

- **MapSpec mutations** take a Redis-backed distributed per-session lock
  (`app/services/distributed_lock.py`), so even without sticky routing two pods
  cannot concurrently mutate the same session's MapSpec (no lost update). Falls
  back to an in-process `asyncio.Lock` when Redis is unavailable (single-worker /
  tests). TTL + token-checked release + best-effort renewal.
- **Pi path** remains per-pod-safe (localhost callback) — no change.
- **Deployment guidance**: for HA without sticky routing, keep `replicas` low and
  rely on the Redis lock; the Pi subprocess per pod caps horizontal scale anyway.

## Consequences

- Positive: invalid MapSpecs can no longer reach "valid" status; refs must really
  resolve; concurrent same-session mutations serialize; checkpoints no longer
  amplify writes; event loop stays responsive during large upserts.
- Negative: a mutation that previously warn-and-saved an invalid MapSpec now
  **fails fast** (the intended behavior). Callers that relied on invalid specs
  being silently persisted must handle the `is_error` result.
- The Redis lock adds one network round-trip per mutation when Redis is reachable.
- Cross-pod correctness for the (non-Pi) legacy ChatEngine per-process state is
  still bounded by the single-process-per-pod + sticky-routing invariant; a full
  Redis move of `_session_executed_sets` / `_dispatch_result_cache` is deferred.

## Evidence

- `tests/cartography/` (@cartography, 19 tests): transaction rejection, real ref
  resolution (resolved / not-found / wrong-session / type-mismatch), validity
  ladder via the real engine, semantic checks, fault injection (save failure →
  rollback), checkpoint dedup, gate policy, concurrency.
- `tests/benchmarks/test_perf_mapspec_e2e.py` (@perf): 50k-feature upsert
  event-loop lag 58.93 ms; checkpoint dedup 2nd-write 0 B; scaling 1k/10k/50k.
- Full unit + cartography suite: 1100+ pass, no regression vs the 1076 baseline.
