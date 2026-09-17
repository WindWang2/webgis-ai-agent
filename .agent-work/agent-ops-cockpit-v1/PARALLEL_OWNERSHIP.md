# PARALLEL_OWNERSHIP — overlap matrix vs open PRs

Branches in flight (all based on `origin/master` `faa453a8`):
- **THIS branch**: `frontend/spatial-agent-ops-cockpit-v1` — read-side Spatial Agent Operations Cockpit UI (+ additive backend projection endpoints if approved).
- **#1335**: `origin/fix/harness-claim-mission-failclosed` — backend fail-closed hardening only.
- **#1336**: `origin/zcode/geoai-promptable-foundation-platform-11` — GeoAI promptable platform (backend + one standalone frontend route).

## 1. File-level ownership

### Owned by THIS branch (proposed new/modified files)

| Area | Files |
|---|---|
| Cockpit UI components | `frontend/components/cockpit/**` (NEW — mission timeline, DAG view, skill view, claim view, resource view, replay view) |
| API client | `frontend/lib/api/mission-runtime.ts` (NEW), optional `frontend/lib/api/cockpit.ts` (NEW) |
| Hooks | `frontend/lib/hooks/use-mission-*.ts` (NEW), optional `use-cockpit-poll.ts` (NEW) |
| Navigation seam | `frontend/lib/store/hud-types.ts` (append `'cockpit'` to `LeftTab` union, line 100), `frontend/components/layout/nav-rail.tsx` (append one TAB_GROUPS row, after line 78), `frontend/components/layout/context-panel.tsx` (append one `activeTab === 'cockpit'` render block, mirror of lines 519-523), `frontend/messages/en-US/layout.json` + `frontend/messages/zh-CN/layout.json` (boundary label key) |
| i18n namespace | `frontend/messages/{en-US,zh-CN}/cockpit.json` (NEW) + 1-line registration in `frontend/lib/i18n/messages.ts` (if required by loader) |
| Tests | colocated `*.test.ts(x)` next to each new module; `frontend/test/` perf/a11y suites if cross-cutting |
| Backend (additive, if approved) | `app/api/routes/mission_runtime.py` (additive GET sub-routers ONLY — list/checkpoints/swarm-runs) or a NEW `app/api/routes/cockpit.py` router + 1-line include in `app/main.py`; `tests/unit/mission_runtime/test_read_api.py` (NEW) |

### Owned by #1335 (its 11 touched files — treat as read-only for us)

```
app/services/gis_harness/completion/pipeline.py
app/services/gis_harness/evidence_claim/census.py
app/services/gis_harness/evidence_claim/grounding.py
app/services/gis_harness/evidence_claim/verify.py
app/services/gis_harness/hotpath_convergence/claim_ingest.py
app/services/gis_harness/hotpath_convergence/pi_card.py
app/services/gis_harness/hotpath_convergence/session_ctx.py
app/services/mission_runtime/service.py
app/services/mission_runtime/store.py
tests/unit/gis_harness/test_evidence_claim_graph_v1.py
tests/unit/gis_harness/test_hotpath_convergence_v1.py
```

### Owned by #1336 (frontend-relevant files — treat as read-only for us)

```
frontend/app/geoai/page.tsx
frontend/components/geoai/geo-prompt-math.ts
frontend/components/geoai/geoai-panel.tsx
frontend/components/geoai/geoai-panel.test.tsx
frontend/lib/i18n/messages.ts            (1-line namespace registration)
frontend/messages/en-US/geoai.json
frontend/messages/zh-CN/geoai.json
(backend: app/main.py 2-line router include; app/lib/modelops/*, app/services/modelops/*, app/tools/geoai_tools.py — GeoAI domain, no overlap with cockpit backend reads)
```

## 2. Collision risk matrix

| File / seam | This branch | #1335 | #1336 | Risk | Mitigation |
|---|---|---|---|---|---|
| `frontend/lib/i18n/messages.ts` | +1 line (cockpit namespace) | — | +5 lines (geoai namespace) | **MEDIUM** — both add entries to the same registration map | Pure append; keep our diff to one line; trivial 3-way merge. Check PR-merge order before rebasing. |
| `app/main.py` router includes | +1 line (only if we add a new router file) | — | +2 lines | **MEDIUM** if we add `cockpit.py`; **ZERO** if we only extend `mission_runtime.py` router | Prefer extending `app/api/routes/mission_runtime.py` with additive GET endpoints → `main.py` untouched. |
| `app/api/routes/mission_runtime.py` | additive GET endpoints (list/checkpoints/swarm-runs) | — | — | **LOW** — #1335 does not touch routes; its store/service signature changes (e.g. `list_swarm_runs_for_mission(..., org_id=)`) are consumed by our new route code | Code our reads against the **post-#1335 signatures** (org-scoped) so both merge orders work; route file itself untouched by #1335. |
| `app/services/mission_runtime/{service,store}.py` | read-only imports | **OWNER** | — | **MEDIUM** if we edited them; **ZERO** if read-only | Never edit; if a read helper is missing, add it in the ROUTE module or a NEW module (e.g. `app/services/mission_runtime/projections.py`). |
| `app/services/gis_harness/hotpath_convergence/session_ctx.py` | read-only via accessors | **OWNER** (tenant|session keying) | — | **MEDIUM** if edited | Never edit; read via `get_turn_context` / `get_or_create_claim_store` / `set_*` accessors only, tolerate `None`/evicted. |
| `frontend/lib/store/hud-types.ts` | +`'cockpit'` in LeftTab union | — | — | **ZERO** (neither PR touches it) | Append-only union member. |
| `frontend/components/layout/nav-rail.tsx` | +1 TAB_GROUPS row | — | — | **ZERO** | Follow ADR-0142 append-only rule (comment at lines 77-78). |
| `frontend/components/layout/context-panel.tsx` | +1 render block | — | — | **ZERO** | Mirror the `'ops'` block at lines 519-523. |
| `frontend/messages/*/layout.json` | +1 boundary key | — | — | **ZERO** | Append-only key. |
| `frontend/messages/{en-US,zh-CN}/cockpit.json` | NEW files | — | — | **ZERO** | #1336 adds `geoai.json` — different file names. |
| `frontend/components/map/**` | untouched | — | untouched | — | Both PR bodies declare this domain off-limits; keep it that way. |
| `frontend/components/geoai/**`, `frontend/app/geoai/**` | untouched | — | **OWNER** | **ZERO** | Never import from `components/geoai`. |

## 3. Stable seams to use (so merges stay trivial)

1. **Navigation**: append-only entries in three places — `LeftTab` union (`hud-types.ts:100`), `TAB_GROUPS` (`nav-rail.tsx`, new row after the `'ops'` row at line 78), render switch (`context-panel.tsx`, new `activeTab === 'cockpit'` block after line 523). This is the exact pattern ADR-0142 used for `'ops'` and it merges cleanly by construction.
2. **Backend reads**: additive GET endpoints on the existing `mission_runtime` router (or a new router file if we must not touch shared files at all). Read via `get_mission_runtime()` facade + `store` getters with `org_id` scoping — matching #1335's hardened signatures.
3. **i18n**: brand-new namespace file `cockpit.json` (no edits to shared namespaces), single-line registration in `messages.ts`.
4. **Data flow**: consume `SkillPolicyDecision.to_bounded_dict()`, `Claim.to_bounded_dict()`, `pi_card.build_hotpath_pi_context` outputs — all bounded, all stable, all owner-maintained by #1335's hardening. Do not fork these projections client-side.
5. **Session guard**: reuse the INV-2 pattern (`use-sse-stream.ts:543-553`) and the hydrate-then-delta reset pattern (`use-session-plan.ts`) rather than inventing new session-switch semantics.
