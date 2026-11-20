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

## Loop 6 — Studio panel, live browser journey and three production fixes (2026-11-20)

### Live verification setup (isolated, real services)
- Rebuilt isolated `.venv` (Python 3.12.15) in this worktree after the host reset the main venv; `requirements.lock` + `requirements-dev.txt` (pytest 9.2.0).
- Real backend `uvicorn app.main:app` on `127.0.0.1:38221` (in-memory session store after the original Redis on 16379 disappeared; `USE_REDIS=false`), isolated sqlite `DATA_DIR=.scratch/g21-ui`, `pnpm dev` frontend on `:32173`, synthetic CORS tile server on `:38222`. All scratch-only; nothing committed.
- Session `g21-ui-6a6f00712986` with the Chengdu OD fixture (A→B=8, B→A=3, A→C=4, C→A=2).

### Fix 1 — ref-source-resolver 304 misread as definitive failure
- Evidence: backend log showed `GET /api/v1/layers/data/<flow-ref> … 304 Not Modified` immediately after apply; resolver `scheduleFetch` treated the ApiError(304) as failure → tombstone → flow layers never mounted (stale/expired). The browser HTTP cache revalidates the ETagged `/layers/data` response once any earlier fetch (panel preview) populated it.
- Fix: resolver fetch now passes `cache: 'reload'` (additive `ApiFetchOptions.cache` passthrough in `frontend/lib/api/transport.ts`), and a residual 304 is no longer tombstoned (left unresolved for retry). Regression tests: `lib/mapspec/ref-source-resolve.test.ts` (+2).

### Fix 2 — reconciler skipped source update when content_revision unchanged
- Evidence (live map introspection via React-fiber walk in a Playwright probe): HUD-expanded sources carried the hydrated payload (4/4/3 features) while the committed `*-source` entries the mounted layers point at stayed empty. `sourceDefinitionsEqual` short-circuited on equal `content_revision` without comparing `inlineData`, so the client-side payload landing produced no `source:update`/layer recompile.
- Fix: the revision short-circuit now also compares the inlineData fingerprint (WeakMap-cached). Regression tests: `lib/mapspec-compiler/reconciler.test.ts` (+2, incl. a no-false-update case).

### Fix 3 — flow legend verification rejected the encoded-field legend contract
- Evidence: `tests/cartography/test_flow_publication_formats.py` failed with `flow_legend_stale` because verification still required `legend.field == "weight"` while the product emits the continuous encoded field `weight_encoded` (0–1) with `source_field`/`source_domain` disclosing the original unit domain.
- Fix: verification accepts `weight`/`weight_encoded` and additionally checks `source_field`/`source_domain` alignment with `encoding` (stricter, not looser).

### Live browser acceptance (built-in Kimi browser, real app + real backend)
- Directed flow renders on the live map: two separated curves per node pair, yellow ▶ direction marks along each curve, blue node circles sized by total flow, legend `分向流量（人次；保留 gross 100.0%）` with original `trips` domain 2–8. Screenshots captured in `.scratch/g21-ui`.
- Refresh/restore: committed MapSpec (revision ≥2) restores 4 layers/3 sources, panel shows `已应用到地图`, product ref, gross retained 100.0%, net magnitude coverage 100.0%.
- Click path: map click on the node position returns the node first in the candidate popup (`选择要素 (5)`: 武侯区 then the 4 edges) after the node-first sort fix; stable object id uses `properties.node_id`/`id` (MapLibre index fallback removed). Playwright `map.project` + `queryRenderedFeatures` confirmed 6 hits at the node point.
- Panel journey: inspect (auto field binding), preview (stats 4/4/4, edges 4, gross retained 100.0%, nodes 3/3), one-transaction apply, refresh recovery — all exercised in the browser.
- Export: backend three-format test (SVG 96/300 DPI, WeasyPrint PDF, pdftotext/pdftoppm PNG readback) passes after Fix 3.

### Known honest gaps
- In-browser FlowFocusCard render (inbound/outbound/net list after the node click) is covered by component tests (`components/flow/flow-product-panel.test.tsx`); the live headless probe could not open the right 制图 panel to screenshot the card (automation-environment limitation, not a product failure). Selection popup + node-first ordering + stable id were verified live.
- Playwright headless GL loses the WebGL context across HMR reloads (map shows `地图暂不可用`; `重试地图` recovers); the built-in browser does not exhibit this.
