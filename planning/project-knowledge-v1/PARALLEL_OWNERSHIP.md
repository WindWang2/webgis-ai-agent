# PARALLEL OWNERSHIP — overlap matrix vs open PRs (checked 2026-09-17 via `gh`)

My area (this branch): NEW `app/services/project_knowledge/**` (+ model/migration/tests); reads project_service, gis_memory, mission_runtime, artifact_*, evidence_claim, map_product_service, lineage_service.

| PR | Branch | Touches my area? | Files overlapping my read-perimeter | Risk of duplicate work | What to avoid |
|---|---|---|---|---|---|
| #1335 | `fix/harness-claim-mission-failclosed` | Indirect (semantic, not file) | `evidence_claim/{census,grounding,verify}.py`, `hotpath_convergence/{claim_ingest,pi_card,session_ctx}.py`, `mission_runtime/{service,store}.py` | LOW-MED: changes claim-verify fail-closed + tenant-scope behavior my projection *consumes* | Do not modify evidence_claim/** or mission_runtime/**; read claim status only through their public API; rebase onto #1335 before merge |
| #1336 | `zcode/geoai-promptable-foundation-platform-11` | No | `app/api/routes/geoai.py`, `app/lib/modelops/**`, frontend, `app/main.py` (additive) | NONE | Only shared anchor: `app/main.py` import/include lines + openapi snapshot; trivial |
| #1351 | `data/spatial-quality-harmonization-v1` | No | `app/services/data_quality/**`, `gis_harness/qualification_v8.py`, `capability_resolution.py` | NONE | Quality-status vocab already consumed via `ProjectDataset.quality_status`; no action |
| #1352 | `eval/gis-agent-benchmark-factory-v2` | No | `app/evaluation/**` (mission_corpus/mission_driver read missions) | NONE (future consumer, not competitor) | None; note it may later consume the projection for eval fixtures |
| #1353 | `frontend/spatial-agent-ops-cockpit-v1` | Adjacent concept (read-model), not files | `app/api/routes/cockpit.py` (new), `app/main.py` (1 line), `tests/quality/snapshots/openapi.json` | LOW: both are "bounded read projections" but theirs is mission/evidence/swarm ops cockpit (session/mission-scoped), mine is project-knowledge reuse | Do not build mission-status UI/API; expect 1-line `main.py` + openapi snapshot text conflicts, semantically orthogonal |
| #1354 | `rs/temporal-cube-sar-optical-v1` | No | `app/lib/geo_analysis/rs_*`, `app/tools/rs_*`, recipe packs | NONE | None |
| **#1355** | `harness/event-driven-spatial-ops-v1` | **YES — highest attention** | **`app/services/project_artifact_promotion.py`**, **`app/services/map_product_service.py`** (ingest hooks), `gis_world_state/mutation.py`, `gis_situation/turn_context.py`, `app/main.py`, `tests/conftest.py`, migration **`0092_spatial_events`** | MED on invalidation *concept*: their `invalidation_bridge` = event ref → ExecutionGraph node STALE + `invalidate_affected_claims` (first production caller); their `situation_projection` = session-scoped bounded ring (`_spatial_event_facts` ≤32); `portfolio.py` = read-only mission/event aggregation. Diff grep `knowledge|reuse|projection`: **no project-level knowledge index, no reuse-verdict, no cross-mission asset reuse** | (1) Treat `project_artifact_promotion.py` + `map_product_service.py` as **theirs** — never edit, only read (avoids hook-site merge conflicts). (2) Take migration number **≥0093**. (3) Do not re-wire claim invalidation — when `GIS_SPATIAL_EVENT_RUNTIME=1` their bridge already handles event→claim; my staleness invalidation stays projection-internal (fingerprint-on-read + explicit invalidate on the seams I own). (4) Do not build a portfolio/ops aggregate — `/portfolio/*` exists for that |
| #1356 | `cartography/multiscale-scene-intelligence-v1` | Barely | `UBIQUITOUS_LANGUAGE.md`, `mapspec_store.py`, `app/main.py` | LOW (doc-only overlap) | If adding UL terms, expect text conflicts; rebase |

## This branch OWNS (may create/modify)

- `app/services/project_knowledge/**` (new package: contracts, store, index/retrieval, verdict, context_card, invalidation, wiring)
- `app/models/project_knowledge.py` (new ORM) + `migrations/versions/00NN_project_knowledge*.py` (NN ≥ 93)
- `app/api/routes/project_knowledge.py` (new router) + 1 include line in `app/main.py` + openapi snapshot refresh
- `tests/test_project_knowledge_*.py` / `tests/unit/project_knowledge/**` (new)
- `planning/project-knowledge-v1/**`, `CHANGELOG.md` entry, `UBIQUITOUS_LANGUAGE.md` additions (expect rebase text conflicts)
- New env flag `GIS_PROJECT_KNOWLEDGE` (default OFF) — new name, no collision

## This branch MUST NOT touch (authoritative stores / others' turf)

- `app/services/gis_memory/**` (read via `get_active_memories`/`retrieve_memories` only)
- `app/services/gis_harness/evidence_claim/**`, `gis_harness/hotpath_convergence/**`
- `app/services/mission_runtime/**`, `app/services/harness_kernel/**`
- `app/services/artifact_registry.py`, `artifact_revisions.py`, `ref_lifecycle.py`, `ref_payload_cache.py` (subscribe to `register_ref_invalidation_hook` at most)
- `app/services/project_artifact_promotion.py`, `map_product_service.py` (#1355 turf), `project_service.py` (read-only; any helper addition there is forbidden — keep in new package)
- `app/services/lineage_service.py`, `session_plan.py`, `history_service_async.py`, `spatial_events/**` (#1355)
