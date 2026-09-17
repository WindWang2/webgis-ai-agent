# GAP ANALYSIS — ProjectKnowledgeProjection vs baseline `faa453a8`

Legend: EXISTS (reuse as-is) / PARTIAL (extend without duplicating) / MISSING (build in `app/services/project_knowledge/`).

## Milestone entities

| Entity | Status | Where / how |
|---|---|---|
| Place / AOI | PARTIAL | Session place identity exists as gis_memory `resolved_place`/`boundary_ref` kinds with bbox (`gis_memory/contract.py:18-27`); AOI as claim `Scope.aoi_ref` (`evidence_claim/contracts.py:141-181`); place *naming* cache `viewport_naming.py`. **Missing**: a project-scoped AOI/place *index* that joins these to `project_id` with liveness. Build: projection rows referencing memory ids / aoi_refs, no new place store. |
| DatasetVersion | EXISTS (source) | `ProjectDataset.version_fingerprint` (`project_service.py:368-390`), dataset pins/snapshots in data fabric (ADS DS5). **Missing**: projection row linking dataset → project index entry. Join key: `ds_<16hex>` id + `version_fingerprint`. |
| Method / Skill | PARTIAL | SkillPolicy procedures (`gis_harness/skills/**`, ADR-0182) are session-plane; `successful_strategy` memory kind exists but recipe truth routed to ADR-0069 ledger (`gis_memory/store.py:252-283`). **Missing**: project-level "method X produced product Y with evidence Z" edge. Join keys: capability/algorithm/tool triple on `ArtifactLineage` (`models/project.py:296-300`). |
| Mission | EXISTS (source) | `MissionRecord` with `project_id` nullable + ref buckets (`mission_runtime/contracts.py:277-306`). **Missing**: projection edges mission→artifacts/products for cross-mission retrieval (missions already hold them as refs; projection indexes them per project). Join key: `msn-*` mission_id, buckets `artifact_refs`/`map_product_refs`/`evidence_refs`. |
| Artifact | EXISTS (source) | Session `ArtifactRecord` (`artifact_registry.py:86-117`) + durable `Artifact`/`ArtifactRevision` rows (`models/project.py:229-388`). **Missing**: nothing for the source; projection adds retrievability + reuse verdicts. Join keys: artifact uuid row id; session refs `ref:*`; content identity `content_sha256`/`content_fingerprint`. |
| MapProduct | EXISTS (source) | `map_products` per-project `version_no` + `product_fingerprint` + `lineage_kind` (`map_product_service.py:87-206`). **Missing**: projection row exposing head/superseded state for reuse checks. Join key: `(project_id, version_no)`. |
| Claim | PARTIAL | `ClaimStore` is **in-memory session-scoped only** (`evidence_claim/store.py:1-40`); statuses + verification exist. **Missing**: durable cross-session claim index. GAP the projection must be honest about: it can only index claims that were ingested in a live process; persistence of claims themselves is out of scope (would duplicate EvidenceGraph). Project rows reference `claim_id` strings opportunistically. |
| Preference | EXISTS | ADR-0069 `carto_project_facts` + gis_memory preference routing (`models/project.py:390-451`). Reuse as-is; projection surfaces preferences as index entries with `preference` relation, never re-stores values. |
| FailurePattern | PARTIAL | gis_memory `provider_failure` kind, TTL 7d, `tool_failure` evidence only (`contract.py:25, 95, 109`); `failure_taxonomy.py` session-plane; `recovery_ledger.py` session-plane. **Missing**: project-level "historical failures as bounded warnings" retrieval surface. Projection indexes failure memory ids + mission `failure_state.error_code` (`contracts.py:220-225`). |

## Milestone relations

| Relation | Status | Notes |
|---|---|---|
| used | MISSING | New edge: (mission|session) → artifact/dataset. Material already on `MissionRefs` + `WorkflowRun` manifest; projection just records the (src, dst) pair. |
| derived_from | EXISTS (source) | `ArtifactLineage` parents + `source_dataset_id` (`lineage_service.py:35-51`); `RelationType.derived_from` in claim graph. Projection mirrors edge ids only. |
| applies_to | MISSING | (method|skill) → (AOI|dataset scope). No existing home; build as projection edge with `Scope`-like bounded fields. |
| produced | EXISTS (source) | `WorkflowRun`→`artifacts` manifest; mission `artifact_refs`. Projection re-indexes. |
| verified_by | PARTIAL | Claim verification (`VerificationResult`) session-plane; `review_passed` evidence source in memory. Projection stores claim_id/status snapshot + recomputes on read. |
| supersedes | EXISTS (source) | Memory supersede chain (`gis_memory/store.py:189-247`), claim `RelationType.supersedes`, artifact `replaces` + `replacement_chain` (`artifact_registry.py:176-186`), map product fork/restore lineage. Projection must surface, not re-compute. |
| failed_with | PARTIAL | Mission `MissionFailureState.error_code` (`contracts.py:220-225`); memory `provider_failure`. Projection joins them per project with bounded warning rendering. |
| reusable_for | MISSING | **The core new capability** — verdict-tagged pointers: (asset, for_scope) → `exact | recompute_partial | not_reusable`. Nothing like it exists; build in projection. |

## Milestone capabilities

| Capability | Status | Gap to close |
|---|---|---|
| Project-scoped spatial+temporal index | PARTIAL | `AssociativeIndex` (ADR-0190) is process-local, org-scoped, memory-records-only, no persistence, no project scope prefilter beyond scope_id, no temporal fields. Build a durable per-project index table + thin hot cache; reuse bbox-cell + token ideas from `associative_index.py:143-330`. |
| Pre-mission retrieval of reusable assets | MISSING | No API answers "what can mission M reuse". Build retrieval over the projection with liveness (ref/`resolve_live_ref` analog), version (fingerprint equality), scope (AOI/temporal overlap à la `Scope.overlaps`) validation. |
| Reuse decision verdicts | MISSING | Nothing exists. Must be deterministic and fingerprint-based (see DECISIONS D4). Never name-similarity: note `retrieval.py:87-105` *does* grant partial-name bonus — do NOT import that into reuse verdicts. |
| Historical failures as bounded warnings | PARTIAL | `[GIS_MEMORY]` block already renders `provider_failure` lines (`gis_memory/projection.py:98-99`). Project-level warning surface = new bounded render over projection rows. |
| ProjectContextCard with strict budget | PARTIAL | `<active_project_workspace>` block (counts + 5+5 names, `project_context_types.py:83-99`), `[GIS_MEMORY]` 1100-char block, `MemoryContextCard` per-hit card. **Missing**: single project-wide card with ids/summary/refs, item+char budget, no payloads. |
| Staleness invalidation on dataset/artifact/claim revision | PARTIAL | Patterns exist: fingerprint-on-read (`ProjectFingerprint`), `invalidate_for_dataset` (`gis_memory/store.py:360-392`), `ref_lifecycle` authority + hooks, #1355 (pending) event bridge. Missing: projection-owned invalidation on artifact-revision/head change and claim-status change (see DECISIONS D3). |
| Strict tenant/project isolation | EXISTS (pattern) | Reuse `_caller_may_access_project` gate + org-equality SQL predicates + `org_id` stamped at write (`gis_memory/contract.py:213-215` precedent). |

## Where the projection's storage lives (decision)

**New table via the repo's canonical pattern** — one bounded table `project_knowledge_entries` (model + migration ≥0093), sync SQLAlchemy DAO free functions, caller commits, `org_id` NOT NULL + composite indexes + partial unique index for active entries, row budget per `(org_id, project_id)` mirroring `SCOPE_BUDGET` (`gis_memory/store.py:48-53`). This mirrors `gis_spatial_memories` (ADR-0183) and `carto_project_facts` (ADR-0069). An in-process hot index (optional, phase 2) may cache entries like `AssociativeIndex` but durability and correctness live in the table.

**Rejected**: (a) new kinds in `gis_spatial_memories` — kind vocab is closed by DB CheckConstraint and semantically "learned GIS facts", not identity projections; would pollute retrieval and the write gate. (b) In-memory only — not invalidatable/durable across restarts. (c) JSON blob on `projects.metadata_json` — unbounded, no indexes, no per-entry status.

**Join keys (authoritative, never re-minted)**: `org_id`; `project_id` (`proj_*`); dataset `ds_*` + `version_fingerprint`; artifact row uuid (durable) or `ref:*` (session) + `content_sha256`; workflow `wf_*` / revision `wfrev_*`; mission `msn-*`; map product `(project_id, version_no)` + `product_fingerprint`; claim_id (opaque, session-plane); memory_id of gis_memory rows; carto fact id (ADR-0069).
