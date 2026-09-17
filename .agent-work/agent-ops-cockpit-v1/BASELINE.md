# BASELINE — Spatial Agent Operations Cockpit (Phase 0 reconnaissance)

- **Baseline SHA**: `faa453a8935101378c23eb6694a42c3616d9c670` (short `faa453a8`)
- **Worktree branch**: `frontend/spatial-agent-ops-cockpit-v1` (based on `origin/master` `faa453a8`)
- **Date of recon**: 2026-09-16
- **Repo root**: `C:\Users\wangj.KEVIN\projects\webgis-wt-agent-ops-cockpit-v1`

## 1. What master (faa453a8) already contains relevant to this goal

The tail of master is the "harness convergence" series — the exact subsystems the cockpit
must visualize are all landed on master as backend services (most without read-side HTTP APIs):

| Commit | Content | Cockpit relevance |
|---|---|---|
| `faa453a8` #1329 | Hot-path Convergence — Mission × SkillPolicy × Evidence | `app/services/gis_harness/hotpath_convergence/` binds mission_id, skill bundle, ClaimStore per session |
| `b44c1c9b` #1328 | Spatial Evidence / Claim / Provenance Graph (Direction 03) | `app/services/gis_harness/evidence_claim/` (contracts, store, verify, grounding, contradiction, freshness, graph) |
| `14a47cc6` #1327 | Production GIS Skill Policy (Direction 02) | `app/services/gis_harness/skills/` (`policy.py` SkillPolicyDecision with mode/confidence/shadow) |
| `e21314a5` #1326 | fail-closed durability fixes for #1322–#1325 | mission/cartography durability |
| `d4480ca6` #1321 | cartography feedback evaluation | — |
| `87829572` #1320 | **Durable GIS Mission Runtime** (Direction 01, ADR-0197) | `app/services/mission_runtime/` + `app/models/mission.py` + REST router `app/api/routes/mission_runtime.py` |

Other master capabilities the cockpit can reuse (all with existing HTTP read APIs):
- Workflow Runtime V5/V6 DAG reads + node retry/cancel: `app/api/routes/workflow_runtime.py`
- GeoCompute cluster observability (runs/events/metrics/workers/stuck/ledger limits): `app/api/routes/geocompute.py`
- Durable jobs list/cancel/retry: `app/api/routes/jobs.py`
- Chat SSE stream with per-turn event ids + resume: `app/api/routes/chat.py:1020`, `app/utils/sse.py`, `app/services/chat/event_resume.py`
- Existing ops-grade frontend: `frontend/components/sidebar/ops/*` (ADR-0142 ops console) — the closest existing "cockpit" surface (GeoCompute cluster oriented, NOT mission oriented).

Existing docs on master to honor:
- `docs/adr/0197-durable-gis-mission-runtime.md` (mission envelope, lease_epoch fencing, bounded ledger)
- `docs/adr/0182-harness-resource-governor-v1.md`, `docs/adr/0187-specialist-subagent-swarm-orchestrator.md`,
  `docs/adr/0184-execution-graph-incremental-replan.md`, `docs/adr/0183-harness-replay-benchmark-explainability.md`,
  `docs/adr/0182-gis-skill-procedure-library-v1.md`
- `CONTEXT.md` glossary (Session/ref_id/SessionStore/SessionPlan/GIS Harness), `UBIQUITOUS_LANGUAGE.md`
- Prior audit: `.agent-work/full-master-audit-2026-09/01-repository-map.md`, `02-architecture-map.md`,
  `03-harness-flow.md`, `07-frontend-findings.md`

## 2. Open PR #1335 — `origin/fix/harness-claim-mission-failclosed`

`git log origin/fix/harness-claim-mission-failclosed --oneline -10` (4 commits on top of `faa453a8`):

```
5b97b281 test(harness): settle→verify never invents SUPPORTED (#1334)
a22a0c24 fix(mission): kill-switch off/no; swarm create requires org match (#1332)
81b40a1f fix(hotpath): tenant+session ClaimStore scope; fail-closed claim_type (#1331/#1333)
92145507 fix(evidence): fail-closed verify + project proof fields (#1330)
```

`git diff --stat origin/master...origin/fix/harness-claim-mission-failclosed` (11 files, +488/−83):

```
app/services/gis_harness/completion/pipeline.py         | 17 ++-
app/services/gis_harness/evidence_claim/census.py       | 22 ++-
app/services/gis_harness/evidence_claim/grounding.py    |  8 +-
app/services/gis_harness/evidence_claim/verify.py       | 95 ++++++++++--
app/services/gis_harness/hotpath_convergence/claim_ingest.py | 72 ++++-----
app/services/gis_harness/hotpath_convergence/pi_card.py |  7 +-
app/services/gis_harness/hotpath_convergence/session_ctx.py | 55 +++++--
app/services/mission_runtime/service.py                 |  6 +-
app/services/mission_runtime/store.py                   | 25 +++-
tests/unit/gis_harness/test_evidence_claim_graph_v1.py  | 103 ++++++++++++-
tests/unit/gis_harness/test_hotpath_convergence_v1.py   | 161 +++++++++++++++++-
```

PR body (from `gh pr view 1335`): "fix(harness): fail-closed claim verify, tenant scope, mission ownership (#1330–#1334)".
Key behavioral changes the cockpit must respect:
- `mission_runtime_enabled()` now treats `off`/`no` as disabled (was only `0/false/False`).
- `create_swarm_run` requires mission exists + matching `org_id` (`TransitionRejected("ORG_REQUIRED"/"ORG_MISMATCH")`).
- `list_swarm_runs_for_mission(mission_id, *, org_id=None)` gained an org filter (good for a read projection).
- ClaimStore keyed by `tenant_id|session_id`; claim verify never invents SUPPORTED from missing evidence.
- **No frontend files touched.** Zero file overlap with a frontend-only cockpit branch.

## 3. Open PR #1336 — `origin/zcode/geoai-promptable-foundation-platform-11`

`git log origin/zcode/geoai-promptable-foundation-platform-11 --oneline` (11 commits, ADR-0198 GeoAI promptable segmentation platform).

Files relevant to frontend / missions / evidence (from `git diff --stat origin/master...`):

```
frontend/app/geoai/page.tsx                        |   7 +   (new route: renders GeoAiPanel)
frontend/components/geoai/geo-prompt-math.ts       | 133 ++++
frontend/components/geoai/geoai-panel.test.tsx     | 263 ++++
frontend/components/geoai/geoai-panel.tsx          | 563 +++++
frontend/lib/i18n/messages.ts                      |   5 +   (registers geoai namespace)
frontend/messages/en-US/geoai.json                 |  28 +
frontend/messages/zh-CN/geoai.json                 |  28 +
app/main.py                                        |   2 +   (registers /api/v1/geoai router)
app/lib/modelops/geo_prompt.py, multimodal.py, app/services/modelops/*, app/tools/geoai_tools.py  (backend, GeoAI domain)
```

PR body confirms: `#1335`'s files were treated as read-only by #1336 (intersection = ∅) and
`frontend/components/map/**` belongs to other local tracks — same rule applies to us.
The route is a standalone Next.js page at `/geoai`; it does NOT touch `nav-rail.tsx`,
`hud-types.ts`, `context-panel.tsx`, or `app/page.tsx`.

## 4. Existing cockpit/timeline/mission-UI code on master (frontend search)

Searched frontend for: `mission`, `timeline`, `cockpit`, `swarm`, `evidence`, `claim`,
`skill policy`, `resource`, `replay`:

- **NO mission UI, NO swarm UI, NO evidence/claim UI, NO skill-policy UI, NO replay UI exists.**
  (`frontend/lib/types/session-plan.ts` is the closest "plan envelope" UI contract — SessionPlan, ADR-0076, a different concept than Mission.)
- **Existing ops-grade surfaces** (the pattern donors):
  - `frontend/components/sidebar/ops/ops-console.tsx` — ADR-0142 ops console root; internal views
    cluster / plan / runtime / breaker / health + full-screen `Wallboard`. Mounted from
    `frontend/components/layout/context-panel.tsx:520` under LeftTab `'ops'`.
  - `frontend/components/sidebar/ops/{cluster-dashboard,plan-console,runtime-section,breaker-panel,system-health-panel,wallboard,stuck-runs-panel,workers-table}.tsx`
  - `frontend/components/sidebar/workflow/runtime-inspector.tsx` + `run-inspector.tsx` + `recovery-actions.tsx` — workflow DAG/instance inspection (data via `frontend/lib/api/workflow-runtime.ts`).
  - `frontend/lib/hooks/use-cluster-poll.ts` (bounded polling kernel) and
    `frontend/lib/hooks/use-cluster-run-events.ts` (after_id cursor event channel).
- **Timeline-ish surfaces**: chat tool-call chains in `frontend/lib/hooks/use-sse-stream.ts`
  (`ToolCallEntry` rows), `plan-waterfall.tsx` (GeoCompute plan stages), `undo-history-panel.tsx`.
  None is a mission timeline.

## 5. Platform facts (from `.agent-work/full-master-audit-2026-09/01-repository-map.md`)

- Backend: FastAPI + SQLAlchemy async + alembic; Redis optional (`USE_REDIS=false` → in-memory).
- Frontend: Next.js 16 + MapLibre + Zustand 5 + vitest 4 + next-intl. No react-virtual/virtuoso
  (custom `use-virtual-rows` hook instead).
- Python 3.13. Tests: `tests/` (pytest, markers heavy/perf/cartography/real_services), frontend colocated `*.test.ts(x)`.
