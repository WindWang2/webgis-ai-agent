# TEST_MATRIX — existing conventions + concrete cockpit test plan

## 1. Existing test conventions (verified at faa453a8)

### Frontend (vitest 4)

- Config: `frontend/vitest.config.ts` — jsdom, `globals: true`, `testTimeout: 15000`, setup `frontend/test/setup.ts`, include `**/*.{test,spec}.{ts,tsx}`, exclude `tests/e2e/**` (Playwright), coverage v8 thresholds `lines 75 / functions 70 / statements 75 / branches 60`, alias `@` → frontend root.
- **Exact commands** (from `frontend/package.json` scripts):
  - `pnpm test` → `vitest run` (full suite)
  - `pnpm test:watch` → `vitest`
  - `pnpm test:coverage` → `vitest run --coverage`
  - `pnpm test:ci` → `vitest run --coverage --reporter=default --reporter=junit --outputFile=test-results.junit.xml`
  - Single file / subset (as #1336 used): `pnpm vitest run components/cockpit test/i18n` (path filters after `vitest run`)
  - Typecheck gate: `pnpm typecheck` → `tsc --noEmit && tsc -p tsconfig.test.json --noEmit`
  - Lint gate: `pnpm lint` (eslint, `--max-warnings 0`, includes jsx-a11y)
  - i18n keys: `pnpm i18n:extract`
- Placement: colocated `*.test.ts(x)` next to sources; cross-cutting suites in `frontend/test/`.
- Mock style: `vi.fn()`/`vi.spyOn` with `globals: true`; helpers `frontend/test/test-utils.tsx`, `frontend/test/__mocks__/`, `frontend/test/in-memory-local-storage.ts`; fetch stubbing per test file (see `lib/api/transport.test.ts`, `lib/api/geocompute.test.ts`).
- Relevant exemplars to copy:
  - SSE invariants/stale-session: `frontend/lib/hooks/use-sse-stream.invariant.test.ts`, `use-sse-adversarial.test.ts`, `use-sse-stream.a3-tdd.test.ts`
  - Bounded polling: `frontend/lib/hooks/use-cluster-poll.test.ts`, `use-cluster-run-events.test.ts`
  - Ops console journey: `frontend/components/sidebar/ops/ops-journey.test.tsx`, `ops-ui.test.tsx`, `ops-registration.test.ts`
  - Virtualization/stress: `frontend/test/workbench-virtual-10k.test.tsx`, `workbench-stress-500.test.tsx`
  - a11y: `frontend/test/workbench-editing-a11y.test.tsx`; reduced-motion hook `use-prefers-reduced-motion.ts`
  - i18n contract: `frontend/test/i18n/`

### Backend (pytest)

- Config: `pytest.ini` — `testpaths=tests`, `asyncio_mode=auto`, per-test `timeout=60` (`timeout_method=thread`), default `addopts` carries `--cov=app` (strip with `-o addopts=` for targeted runs, as #1335/#1336 PR bodies did).
- Markers: `heavy`, `perf`, `cartography`, `real_services` (self-skip without `REAL_SERVICES=1`).
- Parallel: `pytest-xdist>=3.8.0` (`requirements-dev.txt:16`), repo gatebook command `pytest tests/unit -q -n 2` (ADR-0153) — **use `-n 2` max**.
- **Exact commands**:
  - Targeted (new projection endpoints): `pytest tests/unit/mission_runtime -o addopts= -q`
  - Existing mission suites: `pytest tests/unit/mission_runtime/test_lifecycle.py tests/unit/mission_runtime/test_lease_fencing.py tests/unit/mission_runtime/test_crash_recovery.py tests/unit/mission_runtime/test_swarm_resources_goal.py -o addopts= -q`
  - With parallelism: `pytest tests/unit/mission_runtime tests/unit/gis_harness -o addopts= -q -n 2`
  - API contract style: `tests/unit/api_contract/_contract_util.py` + `tests/test_medium_api_contracts.py` are the existing route-contract harnesses; evidence-claim hardening suites: `tests/unit/gis_harness/test_evidence_claim_graph_v1.py`, `test_hotpath_convergence_v1.py`
- Hermeticity pattern to reuse: `MissionStore(factory=...)` injectable session factory (`store.py:115`) + `tests/unit/mission_runtime/conftest.py` `runtime` fixture; `reset_mission_runtime_for_tests()` (`service.py:231`).

## 2. Concrete cockpit test matrix

| # | Test | Layer / file (proposed) | What it asserts | Pattern donor |
|---|---|---|---|---|
| 1 | Mission DTO normalization contract | FE `frontend/lib/api/mission-runtime.test.ts` | `normalizeMissionRecord()` maps raw `MissionRecord.model_dump` → view model: `state` ∈ MissionState vocab, `frontier` 5 buckets each ≤64, `resource_budget` 4 dicts, `lease_*`, `schema_version === 'mission.v1'`; unknown state → explicit `'unknown'` (never crash); missing optional fields default | `lib/api/workflow-runtime.test.ts`, `lib/api/geocompute.test.ts` |
| 2 | Claim/evidence DTO contract | FE `frontend/lib/api/cockpit.test.ts` | bounded claim projection fields (`claim_id, claim_type, status ∈ {supported, partially_supported, unsupported, contradicted, stale, unknown}`, `supporting/contradicting_evidence_refs` ≤16, `confidence`); verification result `positive_proof` semantics: `status='supported'` requires `positive_proof=true`; truncated strings stay ≤ bounds | DTO fixtures copied verbatim from `evidence_claim/contracts.py::to_bounded_dict` |
| 3 | Skill decision DTO contract | FE `frontend/lib/api/cockpit.test.ts` | `mode ∈ {none, guide, execute_guided, shadow, blocked, fallback}`; `shadow_candidate` nullability; `critical_obligations` ≤4; honest empty when backend returns 404/evicted | fixture from `skills/hotpath.py::SkillGuidanceBundle.to_bounded_dict` |
| 4 | Timeline/snapshot revision guard | FE `frontend/lib/hooks/use-cockpit-snapshot.test.ts` | `applySnapshot` drops `revision < current`, no-op on equal, applies greater; per-kind revision independence (mission vs swarm vs trace) | new, modeled on INV-2 tests + `mapspec/session-cursor` CAS |
| 5 | Stale-session isolation | FE same file | snapshot/event tagged `session_id=A` applied while active session = B → dropped, view untouched; session switch aborts in-flight fetch and clears to empty BEFORE refetch (hydrate-then-delta) | `use-sse-stream.invariant.test.ts` (INV-2), `use-session-plan.test.ts` |
| 6 | Out-of-order / cursor accumulation | FE `use-cockpit-trace.test.ts` | `after_seq` pages applied in order; duplicate page (same cursor) idempotent; gap → refetch from last good cursor; 404 → `'notfound'` honest channel; 500-ring cap (501st oldest dropped) | `use-cluster-run-events.test.ts` |
| 7 | Poll discipline | FE `use-cockpit-poll.test.ts` | `enabled=false` → 0 requests; hidden tab pauses; 3 consecutive errors stop with error state; `resetKey` (missionId) switch aborts + resets; interval clamped ≥ server `poll_after_ms` floor | `use-cluster-poll.test.ts` |
| 8 | Operator action revision/lease guard | FE `frontend/components/cockpit/mission-actions.test.tsx` | confirm dialog required before POST; POST body `{worker_id:'ops-console'}`; 409 → renders `lease_owner`/`lease_epoch` message + retry (re-GET first), NO retry of the POST itself; 503 → "disabled"; 404 → refresh; terminal states (`complete/failed/cancelled`) disable buttons; success replaces state with returned record (no optimistic write) | `map-action-acks.test.ts`, transport no-POST-retry assertions in `transport.test.ts` |
| 9 | Virtualization perf (1000+ events) | FE `frontend/test/cockpit-virtual-1000.test.tsx` | render 1200 synthetic timeline/trace rows → mounted rows ≤ window + 2×overscan; scroll to bottom updates window; total content height = n×rowHeight; interaction latency budget (assert no full-list mount) | `frontend/test/workbench-virtual-10k.test.tsx` |
| 10 | Wall-clock perf | FE `frontend/lib/hooks/*.perf.test.ts` | 1000-event buffer flush/aggregation under budget (e.g. <50ms per applied page) | `incremental-think.perf.test.ts` |
| 11 | a11y & reduced motion | FE `frontend/test/cockpit-a11y.test.tsx` + eslint jsx-a11y | tables/lists have accessible names; action buttons focusable with labels; `aria-live="polite"` status region announces mission state changes; animated indicator renders static when `prefers-reduced-motion` mocked true | `workbench-editing-a11y.test.tsx` |
| 12 | i18n parity | FE `frontend/test/i18n/` (add cockpit cases) | `en-US/cockpit.json` and `zh-CN/cockpit.json` key sets identical; no raw literals in cockpit components (extract scan) | existing `test/i18n` suite; run `pnpm i18n:extract` clean |
| 13 | Projection endpoint contract | BE `tests/unit/mission_runtime/test_cockpit_read_api.py` (or `tests/unit/api_contract/`) | each new GET returns exact shape (assert key sets against `to_bounded_dict()` outputs); org isolation: mission in org A invisible to org B (404/empty); 503 `cockpit_disabled` when `GIS_MISSION_RUNTIME=off`; `limit` clamped (≤200 lists, ≤32 checkpoints, ≤64 trace) | `tests/unit/mission_runtime/*` fixtures + `tests/unit/api_contract/_contract_util.py` |
| 14 | Trace hygiene (no CoT/secrets) | BE `tests/unit/mission_runtime/test_cockpit_trace_hygiene.py` | trace payload contains no keys from banned list (`chain_of_thought, cot, raw_llm, messages, thinking`), no `is_reasoning` content, no tool `arguments`; `FINAL_VERDICT` protected records survive trim; `after_seq` cursor skips compacted segments | `pi_card.py` strip precedent; `trace_store` V6 tests |
| 15 | Kill-switch / flag parity | BE same file | `GIS_SKILL_POLICY=0` → skill endpoint honest-empty; `GIS_TRACE_PERSIST=0` → trace endpoint honest-empty (not fabricated data) | `flags.py` env-truthy semantics (#1335 aligns mission kill switch to `off/no`) |

### Command cheat-sheet (exact)

```bash
# Frontend (from frontend/)
pnpm test                                    # full suite (coverage thresholds enforced)
pnpm vitest run lib/api/mission-runtime.test.ts lib/api/cockpit.test.ts          # DTO contracts
pnpm vitest run lib/hooks components/cockpit                                     # hooks + cockpit components
pnpm vitest run test/cockpit-virtual-1000.test.ts                                # perf/virtualization
pnpm vitest run test/i18n components/cockpit                                     # i18n + cockpit (the #1336-style gate)
pnpm typecheck && pnpm lint                  # gates

# Backend (from repo root) — targeted, coverage addopts stripped, -n 2 max
python -m pytest tests/unit/mission_runtime -o addopts= -q
python -m pytest tests/unit/mission_runtime tests/unit/gis_harness/test_hotpath_convergence_v1.py -o addopts= -q -n 2
```

### Invariants the whole matrix protects (cross-reference DECISIONS.md)

1. Server projections are the only truth (D8) — tests 1-3 pin DTOs verbatim to backend bounded dicts.
2. Stale anything never pollutes the view (D4) — tests 4-7.
3. Operator actions are confirm-guarded and lease-legible (D8) — test 8.
4. Scale is bounded server- and client-side (D3/D6) — tests 9-10, 13.
5. No CoT/secrets in any cockpit payload (D8) — tests 14-15.
