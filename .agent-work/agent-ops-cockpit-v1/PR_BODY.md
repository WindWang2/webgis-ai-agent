## feat(frontend): spatial agent operations cockpit for missions, evidence, swarm and resources

**Baseline:** `origin/master = faa453a8935101378c23eb6694a42c3616d9c670` (verified again immediately before this PR; no new master commits during the work)
**Branch:** `frontend/spatial-agent-ops-cockpit-v1` · **Worktree:** `../webgis-wt-agent-ops-cockpit-v1`
**Local evidence only; no online CI wait; do not auto-merge.**

---

## Why

After #1320/#1327/#1328/#1329 the harness backend (Mission runtime, SkillPolicy, Evidence/Claim, swarm ledger) far exceeds what the chat UI exposes — none of it is observable. Code search confirms no mission timeline/cockpit exists. This branch adds the read-side console: **Mission / Execution-Swarm / Skill / Evidence / Resources / Trace**, all rendered from backend projections, with safe operator actions on the existing lifecycle routes.

## What's in the box

**Backend — one additive read-only router** (`app/api/routes/cockpit.py`, schema `cockpit.v1`):
- `GET /cockpit/health` (no auth, mirrors mission-runtime health discipline)
- `GET /cockpit/missions` — org-scoped unfinished-mission list with the jobs.ts protocol (`has_active`, `poll_after_ms`)
- `GET /cockpit/missions/{id}` (record + diagnostics), `/{id}/timeline` (checkpoint anchors, desc, ≤32), `/{id}/swarm` (durable task receipts incl. UNRESOLVED)
- `GET /cockpit/sessions/{sid}/skill|evidence|trace` — process-local hot-path projections, served **only** behind `require_owned_session` (same ownership-proof chain as jobs.py / audit S33), all payloads via the contracts' own bounded dicts (no CoT/secrets; TraceEvent truncates at source: ≤8 keys × 96 chars, closed stage vocabulary)
- Zero write endpoints: operator actions stay on the existing `/mission-runtime/missions/{id}/start|suspend|resume|cancel` (lease fencing, 409). Kill-switch `GIS_MISSION_RUNTIME` → `503 cockpit_disabled`.

**Frontend** (`frontend/components/cockpit/`, `lib/api/cockpit.ts`, `lib/cockpit/use-cockpit-projection.ts`):
- New `LeftTab 'cockpit'` — append-only seam exactly like the ops console (`hud-types.ts`, `nav-rail.tsx`, `workbenchSlice.ts` MODE_TABS, `context-panel.tsx`)
- `useCockpitProjection` = `useBoundedPoll` (six polling disciplines) + **revision-monotonic guard** (late lower-revision responses are dropped) + **server-cadence adaptation** (`poll_after_ms` clamped to [3s, 30s]) — transport-agnostic, so stale-session/out-of-order/reconnect are handled at the ingest layer, not per-transport
- Operator actions: two-step inline confirm, constant `worker_id="ops-console"`, **no optimistic writes** — success only triggers a re-projection; 409 renders as lease conflict while the view keeps showing the server's state
- All lists windowed via `useVirtualRows` (verified with 1200 synthetic events: mounted rows bounded, honest total count shown)
- i18n: new `cockpit` namespace (zh-CN/en-US, parity enforced by existing key-completeness test); status always color+text (color-blind safe); `role=tablist`/`aria-live` semantics; reduced-motion respected for the live indicator
- Honest states: kill-switch empty state (explicitly says it's the server switch, not a frontend failure); process-local data labeled "current process only, cleared on restart"; empty ≠ fake data anywhere

## Phase 0 recon (summarized; full docs in `.agent-work/agent-ops-cockpit-v1/`)

- Baseline already had: mission detail/diagnostics/lifecycle REST (ADR-0197), DB-backed MissionStore/SwarmRun ledger, process-local skill/claim/trace stores, chat SSE. Missing: any mission list/timeline/swarm/skill/evidence/trace read API and any mission/swarm UI.
- Decision: compose existing stores behind ONE new read-only router; v1 touches no existing backend module (zero overlap with parallel hot files), frontend attaches at the ops-console seam.

## Parallel ownership / overlap check (re-verified pre-PR)

- vs **#1335** (`fix/harness-claim-mission-failclosed`): touches `session_ctx.py`, `mission_runtime/{service,store}.py`, evidence modules. Our branch reads `session_ctx.get_turn_context` but modifies none of these files. No overlap. Note: #1335 widens `list_swarm_runs_for_mission` with org_id — we call `store.list_swarm_runs_for_mission(mission_id)` today; post-#1335 signature stays call-compatible (kwarg optional) — tracked as a post-merge convergence item, not a blocker.
- vs **#1336** (GeoAI): we do not touch `frontend/app/geoai/**` or `frontend/components/geoai/**`. Shared-file appends: `app/main.py` (1 import + 1 include_router line each side), `frontend/lib/i18n/messages.ts` (namespace registration lines at different anchors). Trivial, mechanical merges. We also moved our goal-loop ledger out of the repo root into `.agent-work/agent-ops-cockpit-v1/` specifically to avoid colliding with #1336's root ledger file.
- vs **#1351 / #1352** (opened mid-run: data-quality harmonization / eval factory v2): re-checked immediately before opening this PR — **zero file overlap** with either (they also carry a root `.goal-loop-ledger.md`, which our branch no longer tracks). Master gained no new commits during the entire run (still `faa453a8`), so no rebase was required.

## ADR / key decisions

1. Additive read-only projection router, not reads inside mission_runtime (avoids the #1335 hot file entirely; single kill-switch)
2. Session-scoped reads gated by `require_owned_session`, not raw session_id (S33 bug class)
3. Revision-guarded polling instead of a second SSE stack (chat SSE is turn-scoped and monolithic; jobs.ts polling is the established observability precedent) — snapshot applier is transport-agnostic so an SSE transport can be added later without UI changes
4. Frontend renders server state verbatim; `missionAllows()` is a UI-side mirror of server TRANSITIONS used only to disable buttons — the server remains the sole enforcement point (409 surfaces)
5. v1 mission list = ACTIVE only (terminal history would require touching `MissionStore`, the #1335 hot file; deferred)

## Compatibility / kill-switch / rollback

- Entire surface dies cleanly with `GIS_MISSION_RUNTIME=0` (503 + honest UI empty state); frontend health probe gates all polling
- No existing endpoint/DTO modified; OpenAPI snapshot refreshed via the repo's official `API_SNAPSHOT_UPDATE=1` flow — diff is 100% additive (0 deletions)
- Rollback = remove the router include line + the 4 seam edits; nothing else is coupled

## Local verification (actual results)

| Suite | Command | Result |
|---|---|---|
| Backend cockpit routes | `pytest tests/unit/cockpit -q` | **15 passed** |
| Mission runtime regression | `pytest tests/unit/mission_runtime -q` | **23 passed** |
| API compat (snapshot) | `pytest tests/quality/test_api_compatibility.py -q` | **12 passed** |
| Frontend cockpit (client/kernel/panel) | `pnpm vitest run lib/api/cockpit.test.ts lib/cockpit components/cockpit` | **24 passed** |
| Frontend targeted sweep (cockpit + store + i18n + layout + poll hook) | `pnpm vitest run …` | **25 files, 192 passed** |
| Typecheck | `pnpm typecheck` (app + test configs) | **pass** |
| Lint | `pnpm lint` (--max-warnings 0) | **pass** |
| Backend lint | `ruff check app/api/routes/cockpit.py tests/unit/cockpit app/main.py` | **pass** |
| Final Oracle re-run (2nd consecutive pass) | see review memo | **identical results** |

Performance notes: virtualization asserted by mounted-row budget under 1200 synthetic events; polling cadence bounded ≥3s by the existing kernel (240 req/min budget discipline); no heavyweight backend suite was run concurrently with frontend builds. All numbers are local-worktree evidence, not SLO claims.

## Independent adversarial review

`review/SPATIAL_AGENT_OPERATIONS_COCKPIT_REVIEW.md` (Subagent B, independent). Findings: **2×P1 (both fixed red-test→fix→regression, commit `cacc3cdc`)** — (1) revision-guard baseline survived `resetKey` so a new mission's lower revision was misclassified as a stale projection after switching missions; (2) `resume` soft-declines with HTTP 200 `{ok:false, reason:LEASE_HELD}` (recovery coordinator does not raise) and the UI presented it as success; both now have dedicated regression tests. P2×2 fixed in-pass (wrong LiveDot i18n key + invalid tailwind token; 409 test upgraded to a real `ApiError`). 7 false positives investigated and dismissed with evidence in the memo. Review covered architecture, correctness/fail-open, concurrency (stale sessions, double-POST, revision races), security/tenancy, performance/memory, backward compat, observability honesty, frontend race/stale state, and contract drift (frontend DTOs vs backend responses, field-by-field). Final memo verdict: **READY — recommend merge** (with the standing "local evidence only / do not auto-merge" caveat).

## Known limits / honesty

- Skill/evidence/trace are **process-local** on the backend (in-memory, 256-session LRU; trace ring 64/session): the UI says so explicitly; multi-replica deployments see per-replica state only
- Mission timeline = checkpoint anchors; the state machine has no per-transition journal, and the UI does not invent intermediate states
- No mission retry endpoint exists upstream; retry semantics = resume/recovery path, rendered as such
- Local evidence only; no online CI waited on. **Do not auto-merge.**
