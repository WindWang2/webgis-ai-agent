# PR #1974 independent pre-landing repair ledger

Review checkout: `codex/pr-1974-reviewed`; original PR head `75a919c46`.
Review base: `40cda057a7142e33ba3d2f93cd410c3809507a08`.
Date: 2026-11-06 (Asia/Shanghai).
State: **SOURCE_FROZEN**, 2026-11-06 09:23 +08:00. No unresolved P0/P1/P2
findings remain in the reviewed PR source delta.

This ledger covers the uncommitted independent-review repairs. The original
implementation, existing reviewer repairs, all new production modules and their
key callers were read. No commit, push, version/lock change, ADR renumbering or
repository-wide generated-artifact regeneration was performed here.

## Fixed findings

| Severity | Confidence | Failure | Repair and regression evidence |
| --- | --- | --- | --- |
| P1 | 10/10 | A late projection response could replace the active session and replay its selection; reset, same-session reordering and A→B→A had the same risk. | Abort/request/live-generation guards; session identity validation; local selection generation wins. `situation-status-bar.test.tsx` covers delayed responses, reset and request order. |
| P2 | 10/10 | An owned claimed dataset with matching IDs could be validated against an unrelated host layer. | One desired MapSpec read binds layer to canonical source and compares canonical session refs; unknown layer/source fails closed. `test_claimed_ref_must_match_actual_host_source_even_with_matching_ids_and_revision`. |
| P2 | 10/10 | Table selections without a ref rejected valid `source.ref`/`source.ref_id` inputs because fallback only understood `url`. | Existing MapSpec source resolver and session alias resolver handle ref/ref_id/url. Parameterized source-shape and alias tests. |
| P2 | 10/10 | Cleared/chart-replaced selections could resurrect older pre-turn selections; stale chart state survived subsequent map/table selection. | Current IR state takes precedence; truly empty ledgers retain legacy fallback; chart state clears on map/table replacement. Scope/projection regressions. |
| P2 | 10/10 | The status bar stayed stale after persisted interaction acknowledgements and map/receipt changes. | One 500ms coalesced refresh, single-flight with a trailing refresh, subscriptions cleaned on unmount/session change; no interval polling. Status-bar coalescing and outbox acknowledgement tests. |
| P2 | 10/10 | Deferred undo recording could land in another session and the operation-ID dedup set grew for the life of the page. | Recheck original receipt/session generation after dynamic import; session-scoped maximum 128 IDs; explicit session on owner-confirm updates. Receipt-bus race/bounds tests. |
| P2 | 10/10 | Base revisions/noop receipts could count as executed, and client-claimed committed IR could establish authoritative user-wins. | Executed classification requires actual committed/partial evidence and a mutation/final revision; IR remains `user_claimed`; only server provenance establishes `user_explicit`/wins. Negative turn-chain tests retained. |
| P2 | 10/10 | Receipts without turn attribution disappeared when other journal turns existed. | Dedicated unattributed group retains these receipts; bounded output. Turn-chain no-turn receipt regression. |
| P2 | 10/10 | A failed temporal mutation emitted its outcome to the newly active session; old success could survive A→B→A. | A synchronous session-detach subscription spans queueing and request completion, gates response and all failure outcomes, and is released on settlement. Four temporal mutation ownership tests. |
| P2 | 10/10 | Measurement/highlight geometry could become an analysis AOI, while AOI deletion stopped suppressing stale geometry after the recent-display window rolled over. | Only box/freehand/polygon selection affordances qualify; retain latest AOI event kind separately from the 12-row display window; canonical affordance sequence is preserved. Scope geometry/deletion tests. |
| P2 | 10/10 | Selection restore saved IDs but omitted `id_field`, so the map generated no highlighter/filter expression. | Matching UI selection identity travels through bounded IR allowlist, snapshot and claimed scope back into selection-store. Map/table emitter regressions and an assertion against the actual MapLibre expression; the local dual-endpoint manifest is updated. |

Additional boundedness/numeric checks reject nonfinite revision/bbox values,
cap history/turn requests, reject oversized/non-ASCII epoch handles and avoid
duplicate feature IDs. These do not loosen authority or mutation contracts.

## Fixture corrections

The two old backend failures asserted `user_explicit`/`user_wins` from a client
`mutation_outcome=committed` claim alone. Their happy-path fixtures now include
successful server `_gis_provenance` entries. The independent negative test still
proves the claim alone cannot establish authority.

J10/J11 now use the actual mock session `s-j1`; J12 checks the existing shell
readiness helper instead of the nonexistent `composer-input` test ID.

## Validation commands and logs

Run from this checkout unless a frontend working directory is stated.

- Backend final: `.venv/bin/pytest --no-cov -q --timeout=45 tests/test_gis_situation_analysis_scope.py tests/test_gis_situation_selection_history.py tests/test_gis_situation_turn_chain.py tests/unit/test_situation_views_route.py tests/unit/test_interaction_scope_tool.py tests/unit/gis_harness/interaction_ir tests/unit/gis_harness/map_operations/test_service.py tests/acceptance/gis_agent/test_e5_multiturn_journeys.py` — **184 passed**, one pre-existing Starlette warning; `/tmp/pr1974-backend9.log`.
- Frontend final (frontend cwd): `npx vitest run --maxWorkers=2 --reporter=dot components/workbench/situation-status-bar.test.tsx lib/interaction lib/map-operations/receipt-bus.test.ts lib/temporal-map/mutation.test.ts components/map/map-components/panel-ir-emit.test.tsx` — **7 files, 54 passed**; `/tmp/pr1974-frontend6.log`.
- TypeScript (frontend cwd): `pnpm run typecheck` — **passed**, production and test TypeScript projects; `/tmp/pr1974-types5.log`.
- ESLint (frontend cwd): `pnpm exec eslint` on the 16 changed frontend implementation/test files, `--max-warnings 0`; `/tmp/pr1974-lint5.log`.
- Ruff: `.venv/bin/ruff check` on the 10 changed Python implementation/test files — passed; `/tmp/pr1974-ruff5.log`.
- Mypy: `.venv/bin/mypy app/services/gis_situation/analysis_scope.py app/services/gis_situation/selection_history.py app/services/gis_situation/turn_chain.py` — **3 source files passed**; `/tmp/pr1974-mypy3.log`.
- Browser (frontend cwd): `pnpm exec playwright test --config=/tmp/pr1974-playwright.config.ts e2e/journeys/j10-situation-restore.journey.ts e2e/journeys/j11-situation-six-states.journey.ts e2e/journeys/j12-situation-degradation.journey.ts --workers=1 --reporter=line` — **4 passed**; `/tmp/pr1974-e2e4.log`.
- Whitespace: `git diff --check` — passed.

The temporary Playwright config uses the repository config with the same tests,
port 3374, output `/tmp/pr1974-e2e-results`, and `next dev --webpack`. Default
Turbopack cannot resolve this isolated checkout's shared `node_modules` symlink
outside its workspace root. No repository build setting was changed. The first
webpack run found only the invalid J12 test selector (3 passed, 1 failed); that
fixture was corrected before the final run. Incidental mock network warnings
remain in browser logs.

The isolated old baseline also logged an AnalysisGraph network rejection because
its API reader returned a promise without awaiting it inside `try`. This is an
existing baseline issue, outside the PR delta. The parent integration worktree
already preserves the `return await apiFetch(...)` repair; integration supplies
that fix. No additional AnalysisGraph files were changed in this checkout.

The first final typecheck ran concurrently with Next development type generation
and saw transient missing `.next/dev/types` files (`/tmp/pr1974-types4.log`).
It was rerun after the browser server stopped; `/tmp/pr1974-types5.log` exited 0
with both TypeScript projects passing. Next's incidental AGENTS heading rewrite
was reverted, leaving no instruction-file delta.

The earlier backend run `/tmp/pr1974-backend6.log` had **83 passed** but was
interrupted while the default coverage report was tokenizing sources. A clean
`--no-cov` rerun (`/tmp/pr1974-backend7.log`) passed all 83 in 4.89s, followed by
the broader final 184-test run above. No interrupted run is counted as the final
verification.

## Integration ownership

Repository-wide generated docs/catalogs, ADR numbering, integration merges and
full-repository regression belong to the parent review worktree. Rebuild those
after collecting this source delta, especially the additive tool failure modes
`layer_unavailable` / `source_ref_mismatch`. This checkout deliberately updates
only the local Interaction IR contract manifest required for its payload schema.
