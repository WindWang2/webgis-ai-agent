# BASELINE — ProjectKnowledgeProjection v1 (project-knowledge-v1)

- **Baseline SHA**: `faa453a8935101378c23eb6694a42c3616d9c670` (origin/master, `feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (#1329)`)
- **Worktree / branch**: `../webgis-wt-project-knowledge-v1` @ `harness/project-knowledge-memory-v1`
- **Date**: 2026-09-17

## Open PR / branch landscape (as of 2026-09-17)

| PR | Branch | State | One-liner |
|---|---|---|---|
| #1335 | `fix/harness-claim-mission-failclosed` | OPEN | Fail-closed claim verify, evidence tenant scope, mission ownership fixes (#1330–#1334) |
| #1336 | `zcode/geoai-promptable-foundation-platform-11` | OPEN | GeoAI promptable foundation platform 11 (GeoPrompt artifacts, modelops, ADR-0198) |
| #1351 | `data/spatial-quality-harmonization-v1` | OPEN | Spatial quality + semantic harmonization autopilot |
| #1352 | `eval/gis-agent-benchmark-factory-v2` | OPEN | GIS agent benchmark factory v2 (hard negatives, mission-level eval) |
| #1353 | `frontend/spatial-agent-ops-cockpit-v1` | OPEN | Read-only agent ops cockpit for missions/evidence/swarm (frontend + `/cockpit` routes) |
| #1354 | `rs/temporal-cube-sar-optical-v1` | OPEN | Temporal cube + SAR/optical fusion harness |
| #1355 | `harness/event-driven-spatial-ops-v1` | OPEN | **Event-driven spatial ops + mission portfolio control plane** (spatial_events ledger, invalidation bridge, situation projection, `/portfolio` API, migration 0092) — base SHA identical to ours |
| #1356 | `cartography/multiscale-scene-intelligence-v1` | OPEN | Multiscale 2D/2.5D/3D scene intelligence + agent scene runtime |

Full overlap analysis: see `PARALLEL_OWNERSHIP.md`. Headline: **#1355 is the only material adjacent build** — it wires *event → invalidation → mission*, but its projections are session-scoped situation facts and a read-only mission/event portfolio; it builds **no** project-level knowledge index and **no** reuse-verdict machinery (`knowledge`/`reuse` grep of its diff hits only doc lines and the situation projector). It does, however, touch two files in our read-perimeter (`project_artifact_promotion.py`, `map_product_service.py`) and takes migration `0092_spatial_events` (our new table must be `0093+`).

## Audit issue landscape (1 para)

The harnessDirections (Directions 01–05, PRs #1320/#1327/#1328/#1329 merged; #1335, #1355 open) follow a cadence where each direction lands an adversarial review (`review/*.md`) and fixes audit findings in-band (e.g. `c8c7a902 fix(audit2): #1304–#1309`, `e21314a5 fail-closed durability for #1322–#1325`). Recurring audit themes that bind this feature: no second mutable truth (ADR-0076/0080/0082/0092 lineage), bounded storage ("Zero Big Data", refs only), fail-closed tenancy (org_id equality predicates everywhere, ADR-0139), value budgets on anything LLM-visible, and kill-switch-per-subsystem with default-off for behavior-changing paths. ProjectKnowledgeProjection must satisfy all five by construction.

## Key recent merged PRs relevant to this direction

- **#1320 / ADR-0197** — Durable GIS Mission Runtime (`app/services/mission_runtime/`, tables `gis_missions*`, migration 0091). Missions carry `org_id/user_id/project_id` + ref inventories (artifact/map_product/evidence buckets) — the future `applies_to`/`produced` relation source.
- **#1328 (Direction 03)** — Spatial Evidence / Claim / Provenance Graph (`app/services/gis_harness/evidence_claim/`): `ClaimStatus`, `RelationType` (incl. `derived_from`/`supersedes`/`invalidates`), `Scope.aoi_ref`/temporal, positive-proof invariant.
- **#1329 (Direction 04)** — Hot-path Convergence (`gis_harness/hotpath_convergence/`): mission bind, skill bind, claim ingest flags; `GIS_MISSION_HOTPATH` default OFF.
- Earlier: **ADR-0183/#1305-era** GIS Spatial Reasoning Memory (`app/services/gis_memory/`, table `gis_spatial_memories`, migration 0080) and **ADR-0190** proactive retrieval (`AssociativeIndex`, `ProactiveRetriever.awake`, `MemoryContextCard`).
- **ADR-0069** cartography project fact ledger (`carto_project_facts` table, `app/services/cartography/project_memory.py`) — the single preference-truth that gis_memory routes to.

## Planning-dir convention note

Repo convention for Phase-0 docs is `.agent-work/<feature-vN>/{BASELINE,CURRENT_ARCHITECTURE,PARALLEL_OWNERSHIP,GAP_ANALYSIS,DECISIONS,TEST_MATRIX}.md` (35 existing dirs; e.g. `.agent-work/durable-gis-mission-runtime-v1/`, `.agent-work/spatial-evidence-claim-graph-v1/`). A top-level `planning/` directory did **not** exist before this task; these files create `planning/project-knowledge-v1/` per task instruction and mirror the `.agent-work` doc structure. If the team prefers convention, symlink/move to `.agent-work/project-knowledge-v1/` before review.
