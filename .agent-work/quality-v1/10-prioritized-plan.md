# Phase 0 Synthesis — Prioritized Quality Platform Plan

Sources: `01-test-inventory.md` … `09-quality-architecture.md` (this directory). All claims there carry file:line evidence; this file records the decisions.

## Baseline facts (from audits)

- Suite: 10,329 collected backend tests / 793 files; markers `cartography`(682) `perf`(93) `heavy`(65) `real_services`(6); frontend 15 vitest files with coverage ratchets.
- Repo is **derivation-first**: registries are the only truth; catalogs/oracles/manifests are projections, each pinned by a byte-diff drift test (science, workflow, cartography).
- 20 existing gates (G1–G20 in 09-quality-architecture.md), incl. AST-walking authz meta-test, alerts↔metrics consistency, CI↔ci-local mirror contracts, release-gate DAG.
- **BROKEN ON MASTER**: `tests/unit/test_descriptor_coverage_gate.py:9` imports `scripts/check_tool_descriptor_coverage.py`, which is absent → full-suite pytest collection fails. P0 fix; rebuild the gate on the new QualityManifest compiler.
- Strong flake defenses exist (perf isolation #664, REAL_SERVICES arming #661, env pinning, fakeredis, zero sockets). Residual: ~66 sleeps in 30 files; 13 benchmark/perf files unmarked; 83 random-using files with only 22 seeded; a few order-dependence leaks.
- Observability: 4 coexisting memory-only trace models; correlation backbone solid (`RuntimeCorrelationFilter`, ContextVar ids); Prometheus near-empty; nothing persists; redaction layered and bounded.
- Chaos: 6 deterministic runtime-chaos suites (F1–F28), GeoCompute chaos (ADR-0096), cartography fault subset; no shared fault-injection utility; artifact-cache failure paths nearly untested; distributed-lock renewal swallows exceptions; artifact_registry runs `fail_on_degraded=False`.
- Coverage-risk top gaps: 29 untested tool wrappers (geocompute dispatch seam, lineage family, checkpoint/rollback/compile-maplibre), data-fabric SQL compiler untested, no cancellation checkpoints in `point_pattern.py` permutation loops, frontend result-normalizer mirror untested.
- E2E: `GISBenchmarkCase`/`GISBenchmarkRunner` is ~80% of a ScenarioSpec DSL; no scenario crosses CHAT→…→VERIFY; `render_verified` hard-coded None; no central fake LLM provider (27 independent patches).
- Security: controls strong (SEC-xx audit IDs), but path-traversal test is fake (re-implements validator), query-injection has zero net, artifact ownership untested, `allow_private` re-read from stored profile untested, error-oracle branch never e2e-tested.
- Performance: wall-clock harness w/ baselines.json (`warn ×1.75 / fail ×4.0`, fail-closed, `PERF_UPDATE_BASELINES=1`), structural counter contract tests exist; 6 wall-clock files unmarked; ingest has no gate; planning-context bytes measured but ungated.

## Architecture decision (ADR to write)

QualityManifest = **one more derivation**, not a new truth source:
`app/lib/quality/` (introspection + findings + gate logic, importable) →
`scripts/gen_quality_manifest.py` (`--check`) → committed `docs/quality/QUALITY_MANIFEST.md` + `quality-manifest.json` →
byte-diff drift test in `tests/quality/`. The rebuilt `scripts/check_tool_descriptor_coverage.py` is a thin wrapper over the same compiler (fixes the broken master import, keeps ADR-0103 semantics).

## Wave → commit plan (branch `feat/quality-reliability-platform-v1`)

| # | Waves | Deliverable |
|---|-------|-------------|
| C0 | — | Phase 0 audit docs (this dir) |
| C1 | W1 | `app/lib/quality/` manifest compiler + generator + gate; fixed descriptor gate; `tests/quality/test_quality_manifest_gate.py` |
| C2 | W2+W3 | ScenarioSpec DSL on `GISBenchmarkCase` (entry/observe/trace/budget sections); central fake LLM provider fixture; deterministic reference scenario corpus generator (≥500 scenarios, ≥5000 synthetic variants, drift test) |
| C3 | W4 | Contract drift gate suite → drift report artifact (tool↔impl, algorithm↔impl, capability↔producer, artifact↔serializer, map↔renderer/exporter, schema↔frontend client, route↔authz, recipe↔task family, uncertainty↔output, scale↔guard) |
| C4 | W5 | Trace completeness validator + per-task-class certification (reuses existing trace models) |
| C5 | W6 | Vendor-neutral observability: event emission facade + bounded-field rules, missing business metrics (queue depth, no-progress, compile failures), correlation field completion (`project_id`), no new backend |
| C6 | W7+W8 | `tests/fixtures/chaos.py` deterministic fault injection (fault IDs, explicit schedules) + chaos suites for top unprotected points (artifact cache corruption/eviction, lock renewal, registry degraded mode, duplicate ingest, cancel-during-write) |
| C7 | W9+W10 | Cancellation/deadline certification table (+ fix `point_pattern.py` checkpoints if warranted) + resource-safety certification (typed rejects, estimate-before-allocate audit) |
| C8 | W11+W12 | Structural perf gates (`kind: count/bytes` in baselines.json, marker sweep, shared counter helpers) + determinism/replay certification tests |
| C9 | W13+W14 | Scientific regression suite (CRS/units/NaN/zero-variance/… → reject|fallback|warn|disclose) + cartographic cross-system regression certification |
| C10 | W15 | Security regression platform: `tests/security/<area>/`, control→test manifest gate, fix fake path-traversal test, ownership + injection + error-oracle + allow_private regression tests |
| C11 | W16 | API compatibility suite: schema snapshots + additive/breaking classifier |
| C12 | W17+W18 | Flaky eradication (marker sweep, seed policy, order-dependence fixes, repetition runner) + shared GIS fixture architecture |
| C13 | W19+W20 | `scripts/quality` runner (quick/backend/frontend/science/…, JSON+MD summary, bounded concurrency) + static quality report artifact; docs/ADRs |
| R1/R2 | — | 8-reviewer rounds on diff; BLOCKER/CRITICAL/MAJOR → 0; final master sync + full local quality lane + PR |

## Resource discipline

- No `pytest -n auto`; runner caps concurrency (backend ≤4 workers if xdist present, else serial groups; frontend vitest default).
- Full suite run only at final validation; per-commit verification uses targeted lanes.
- LLM never called in tests (deterministic planner/fixtures only).
