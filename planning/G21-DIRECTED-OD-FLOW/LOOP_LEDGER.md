# LOOP_LEDGER — G21

## 2026-11-10 — Loop 0: identity and independent baseline
- Created durable goal and loaded planning/engineering-guideline skills.
- Confirmed target remote and GitHub repository identity; base branch `master`.
- Recorded pre-existing main-workspace modifications and did not touch them.
- Fetched/pruned origin; latest `origin/master` = `816705218a5aa6202bf15d745cb31cddb3437f44`.
- Listed all open PRs, recent merged PRs and open issues; detailed related reviews pending.
- Created independent branch/worktree with no shared branch reuse.
- Next: related PR review details + current OD source/test baseline.

## Test ledger
No project tests run yet.

## Process/resource ledger
No services, heavy builds, temporary databases, queues, or task-specific ports started.

## Loop 1 — existing regression baseline
- PASS (base 816705218): `flock /home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock /home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest -q tests/unit/test_od_flow_runtime.py` → 9 passed in 96.28s. Environment: Python 3.13.17, one numeric thread.
- Frontend first attempt did not start tests: Vitest 4.1.11 rejected unsupported `--minWorkers`; exact error recorded. Next attempt must use actual `--help`, not repeat the command.
- Verified converter flow branch `app/services/analysis_cartography_converter.py:581-605` consumes metadata `weight_field`, so custom input field produces a paint expression for a property not emitted by the tool.
- Verified `recipes.py:1164` preferred `od_matrix` first and `planner.py:1018-1029` binds primary source to first preferred capability. Current golden/conformance tests encode the old cost-matrix prerequisite and must be updated for corrected flow-table semantics, not preserved as golden.

## Loop 2 — frontend baseline and architecture decisions
- PASS (base 816705218): `flock /home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock pnpm exec vitest run lib/mapspec-compiler/flow-layer.test.ts --no-file-parallelism --maxWorkers=1` from isolated `frontend/` → 1 file, 3 tests passed (Vitest 4.1.12). Existing Vite native-config ESM warning is non-fatal and unchanged.
- Environment fix: standard `pnpm install --frozen-lockfile --offline` succeeded, 766 reused packages, no downloads.
- Related PR researcher completed: all relevant PR review/inline/conversation collections empty; no approvals assumed. Frontend/export researcher completed; no second runtime or sprite-based export path will be introduced.
- Decision: use a G21 flow product service + existing SessionStore refs; preview does not mutate MapSpec. Apply must be one lifecycle transaction for all flow layers/sources. Existing map-operation chains explicitly are not atomic for mixed upserts, so they are not sufficient for this product apply.
- Decision: preserve legacy straight build semantics, but add stable render IDs with legacy-ID mappings. Legacy already-saved old specs continue to compile; old origin/destination pseudo-total-line API will return an explicit migration error instead of retaining wrong golden behavior.

## Loop 3 — TDD red slices
- EXPECTED RED: new `tests/unit/test_flow_product_mapspec.py` fails at collection because `ApplyFlowProductIntent` does not exist. This is the intended public-contract red, not an environment failure.
- Began v1.10 additive `layer.flow` and atomic lifecycle implementation. No production behavior has been claimed complete yet.

## Loop 4 — MapSpec vertical slice green
- PASS: `pytest -q --no-cov tests/unit/test_flow_product_mapspec.py tests/unit/test_mutation_registry_consistency.py` → 19 passed.
- Delivered in this slice: additive v1.10 `layer.flow` + regenerated TS types; `ApplyFlowProductIntent` and HTTP body codec; same-session product/source/artifact validation; source revision guard; atomic family replacement; durable presentation preservation; existing checkpoint/CAS/undo registry integration and lock targets.
- Test correction during TDD: user presentation is stored as `layout.visibility=none`, not legacy `visible=false`; assertion adjusted to actual public MapSpec contract.
- Registry consistency expectations explicitly updated for the new intent (18 HTTP bodies, touches_layers, auto-checkpoint, journal label); no gate was weakened.

## Loop 5 — OD planner/descriptor correction green
- Initial public planner test failed to `poi_distribution_overview`; actual plan inspection first showed the exact user phrase missed mobility intent, then exposed the `od_matrix` prerequisite. Fixes were limited to OD intent words, recipe/descriptor/methodology bindings and corresponding evaluation expectations.
- PASS: `pytest -q --no-cov tests/unit/gis_harness/test_od_flow_planner.py tests/unit/gis_harness/test_model_injection.py` → 21 passed.
- Conformance hardening found duplicate family ID after renaming; restored stable existing ID `od-matrix` (IDs are compatibility keys), with flow-only queries/name.
- Full corpus initially exposed 108 misses for the generated `OD通勤联系` phrase; added that OD-local term rather than relaxing assertions.
- PASS: `pytest -q --no-cov tests/unit/gis_harness/test_conformance_corpus.py tests/unit/gis_harness/test_benchmark_harness.py tests/unit/gis_harness/test_od_flow_planner.py` → 15 passed, including full 20,088-case corpus and benchmark. Existing kriging divide-by-zero warnings (3) remain outside G21.
