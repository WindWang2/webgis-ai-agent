# PR_HANDOFF — G21-DIRECTED-OD-FLOW

- PR: https://github.com/WindWang2/webgis-ai-agent/pull/2266 (OPEN, base `master`)
- Branch: `zcode/goal-g21-directed-od-flow-20261110T132034Z`
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/goal-g21-directed-od-flow-20261110T132034Z`
- Base SHA: `cc2dc69001a79159eddccc56a4551c9d9a9ebcf1` (origin/master at rebase; branch merged it)
- Head SHA at handoff: `1897c4ca6d2b8edbb80b7e33ed84f5ea84b0e37e`
- Commits on branch since base: 12 (feature + merge + merge-repair + docs + review fix)

## Delivered behavior
Directed/undirected-total/net OD flow product with stable node/edge identity, visible separated curves + ▶ direction marks, node/edge focus on the shared SelectionContext, atomic MapSpec family apply with CAS, UI (Studio panel) + Pi (registered tools, early pinning) editing the same product, and single-image PNG/SVG/PDF export with completeness verdicts.

## Verification summary (local, real)
- Backend OD/flow/schema/registry suites: 259 passed after rebase; 83 + 20 passed after review fixes (1 skipped: vendored Pi RPC not built).
- Full `tests/cartography`: 3677 passed / 5 failed — all 5 reproduced on clean origin/master (baseline, out of scope).
- Frontend: `tsc --noEmit` clean; flow/carto-ir/compiler/map-panel suites passed (124 + 513 + 74 across rounds).
- Browser journey (isolated uvicorn + next dev + synthetic tiles): upload → inspect → preview (gross retained 100.0%, net coverage 100.0%) → apply → live curves/arrows/nodes/legend → refresh restore; node click hits candidate list with the node first.
- Independent review: non-blocking; 3 P2 fixed (commit 5dfe567f9), 1 P2 declined with the locked-contract rationale; rereview recorded in REVIEW.md.

## Known limitations
- FlowFocusCard browser render covered by component tests (headless probe could not open the right panel).
- Playwright headless GL loses context across HMR reloads (built-in browser unaffected).
- Online CI not awaited (per task authorization); no auto-merge/release.
- Pre-existing master failures listed above are not fixed.

## Local scratch services still running (isolated ports, this task only)
- backend `127.0.0.1:38221` (in-memory session store, `USE_REDIS=false`), frontend `:32173`, synthetic tiles `:38222`; state under `.scratch/g21-ui/` (gitignored). Stop with the recorded PIDs in `.scratch/g21-ui/backend.pid` / TaskStop if no longer needed.
