# GIS Data / Artifact / Workspace Foundation V3 — Architecture Map

> Phase 0 deliverable. Synthesizes six read-only audits (data model, ingest, flow,
> cache/materialization, workspace, scale) of `master@222a994f` into the V3 design.
> Rule: **evolve existing systems; never build a second Data Fabric / Artifact Registry.**

## 1. Authoritative ownership today (audit synthesis)

| Concern | Owner | Location |
|---|---|---|
| Session artifact existence/status/lineage | `ArtifactRecord` + `ArtifactGraph` | `app/services/artifact_registry.py` |
| Artifact type vocabulary (21 ids) | `ArtifactTypeRegistry` / `SEED_ARTIFACT_TYPES` | `app/lib/gis/artifacts.py` |
| Ref payload truth | Session store (memory LRU / Redis 4h TTL) | `app/services/session_data.py`, `session_data_redis.py` |
| Ref metadata (store-time descriptor) | `RefDescriptor` (`content_hash` reserved, always None; `content_revision` counter) | `app/schemas/ref_descriptor.py` |
| Unified profile (zero-scan projections) | `DatasetProfile` | `app/lib/gis/dataset_profile.py` |
| Full-scan fallback profiler (unbounded) | `profile_geojson_source` | `app/services/spatial_meta_profiler.py` |
| Durable artifact + lineage | DB `Artifact`, `ArtifactLineage` | `app/models/project.py`, `app/services/lineage_service.py` |
| Session→durable materialization | content-addressed promotion store | `app/services/project_artifact_promotion.py` |
| Run reproducibility | RunManifest + `run_fingerprint` | `app/services/provenance/` |
| Dataset identity fingerprint | `compute_dataset_fingerprint` (identity, not content) | `app/services/provenance/fingerprint.py` |
| Analysis result reuse | `analysis_key` + shape/raster-fp guards | `app/lib/gis/analysis_reuse.py`, `tool_dispatch_service.py` |
| Raster math disk cache | `make_artifact_key` (mtime+size identity) | `app/lib/artifact_cache.py` |
| Disk reclamation (exports/reports/uploads) | age sweeps | `app/services/artifact_lifecycle.py` |
| Map desired state + checkpoints | MapSpec store (disk, 20 revisions) | `app/services/mapspec/` |
| Project/workspace persistence | Postgres Project/ProjectDataset/WorkflowRun/MapProductVersion | `app/models/project.py` |
| Remote source adapters + materialization | Data Fabric | `app/services/data_fabric/` |
| Catalog (in-memory, brute force, per-worker) | `SpatialCatalogService` | `app/services/data_fabric/spatial_catalog.py` |

## 2. Confirmed problems V3 addresses (evidence-backed)

1. **No stable artifact identity.** Session refs are `ref:<prefix>-<uuid16>` (random per
   store); DB artifact ids are random per run; `RefDescriptor.content_hash` is permanently
   None. The only content-stable address is the promotion store, used only for workflow-run
   artifacts.
2. **Three disjoint artifact-type vocabularies** share the column name `artifact_type`:
   21-id session registry vs `vector|raster|analysis` (DB) vs 10-value `OutputSemanticType`.
3. **Profile unification incomplete + unbounded fallback.** `DatasetProfile` is the declared
   single contract, but `spatial_meta_profiler.profile_geojson_source` still emits untyped
   camelCase dicts and does an O(features × fields) full scan with full sorts.
4. **No data quality contract.** Quality hints are scattered (`ProjectDataset.quality_status`,
   spatial_quality_service, ad-hoc checks); no shared diagnostic taxonomy/status algebra.
5. **Lifecycle fragmentation.** 5-state session machine (`valid|stale|expired|superseded|failed`)
   vs stateless DB artifacts vs 2-state catalog availability vs 4-state layer pipeline vs
   5-value promotion `ContentStatus`. No shared algebra or persistence-tier concept.
6. **Versioning signals exist but are inert.** `content_revision` (per-ref counter) is
   consumed by no reuse/cache key; `ArtifactRecord.revision` is never set; `replaces` chain
   is the only version history. No schema/metadata fingerprint separation, no change class.
7. **Lineage bridge missing.** Session `ArtifactGraph` (in-memory, truncated inputs ≤16) and
   DB `ArtifactLineage` (durable, cycle-checked) never reconcile; `Artifact.storage_ref`
   identity drift (ref id vs mapspec layer id) breaks resume reconstruction.
8. **Non-FC results never become artifacts.** Stats/tables/charts ride `raw_result` with no
   ref/artifact/lineage; `scientific_evidence` is dropped at every persistence seam.
9. **No unified catalog.** Artifact discovery = per-session ledger + brute-force in-memory
   `SpatialCatalogService`; uploads/refs/fabric datasets/project datasets live in four
   disconnected metadata models.
10. **Workspace snapshot gap.** MapSpec survives restart but ref payloads die at 4h TTL →
    hollow layers with no detection; ad-hoc (non-workflow) layers are never promoted; no
    one-shot restorable workspace snapshot; no durable session↔project binding.
11. **Reuse/cache keys lack identity.** `analysis_key` = tool+args(ref ids, no content, no
    algorithm version); `tool_cache` 24h entries keyed by args only; `data/artifacts` disk
    cache has no age sweep and leaks `.meta`-less orphans.
12. **Upload ingest gaps.** No content hash/dedup; extension-only format detection; CSV
    silently EPSG:4326; GBK CSV → HTTP 500; only `files[0]` processed; shallow profile.

## 3. V3 design (additive layers over existing owners)

New package `app/lib/data/` (pure contracts/algorithms, no I/O) + `app/services/data_catalog/`
(stateful services) + `app/tools/data_discovery.py` (agent surface). Existing owners keep
their roles; V3 adds the unifying contract and bridges.

```
app/lib/data/
  vocabulary.py      # ArtifactType (maps 21-id registry + goal base types), LogicalRole,
                     # PersistenceTier, QualityStatus, LifecycleState algebra + mappings
                     # from all five existing state vocabularies
  artifact_contract.py  # ArtifactContract V3 pydantic model (goal section 三 field list),
                     # bridge builders: from_artifact_record(), from_ref_descriptor(),
                     # from_db_artifact(), from_raster_descriptor(); bounded summaries
  fingerprints.py    # unified FingerprintSet (content/schema/metadata) + change
                     # classification (metadata-only/content/schema/crs) + reuse key
                     # builder (inputs fps + operation/version + normalized args + crs)
  quality.py         # DataQualityIssue taxonomy + QualityReport + remediation hints;
                     # checks run against profiles (bounded)
  lifecycle.py       # lifecycle state machine (declared→…→deleted/error) + transition
                     # validation + TTL/GC policy per persistence tier
  staleness.py       # staleness verdicts (valid/stale/recompute/invalid) + propagation
                     # planning over a lineage graph (pure functions)
  large_data.py      # small/medium/large policy + bounded summary shapes

app/services/data_profile/
  profiler.py        # bounded Dataset Profile V3 service: vector/raster/table profiles
                     # with sampling, row caps, profile-quality flag; cache keyed by
                     # content fingerprint / content_revision; feeds DatasetProfile V3
  (reuses session store / RefDescriptor; does NOT rescan when descriptor suffices)

app/services/data_catalog/
  catalog.py         # unified read-only catalog over: session artifact ledger (all
                     # sessions of caller), uploads, fabric datasets (wraps
                     # SpatialCatalogService), project datasets; filter/search bounded;
                     # optional SQLite/JSON persistence index for cross-restart search
  lineage_query.py   # unified lineage queries over session ArtifactGraph + DB
                     # ArtifactLineage: parents/children/roots/downstream/impact

app/services/workspace/
  snapshot.py        # WorkspaceSnapshot: artifact versions + mapspec ref + layer refs +
                     # settings; save/restore/clone; additive to MapSpec store (no
                     # Map State takeover)

app/services/data_ingest/
  pipeline.py        # Detect→Validate→RegisterSource→InferMetadata→ReadCRS→Profile→
                     # QualityCheck→RegisterArtifact→optional Materialize; rollback on
                     # failure; additive to upload path (content hash, dup detection,
                     # encoding fallback, multi-format detect)
```

### Key decisions

- **D1 Identity**: `stable_identity = sha256` over the *deterministic computation key*
  (producer + normalized args + input content fingerprints) when derivable, else
  content fingerprint, else None (honest). Ephemeral `ref_id` remains the runtime handle;
  `ArtifactContract` carries both plus `lineage_id`.
- **D2 Types**: goal base types (vector/raster/table/…) are the *contract-level*
  `ArtifactType` coarse class; the existing 21-id registry remains the *fine* vocabulary.
  Mapping is explicit and validated; DB `vector|raster|analysis` maps to coarse class.
- **D3 Lifecycle**: one state algebra in `vocabulary.py`; `artifact_registry` states map
  onto it; `persistence` tier (ephemeral/session/workspace/persistent) drives GC policy.
- **D4 Profile**: never scan when a `RefDescriptor` exists; sampling + caps when it
  doesn't; every profile records `profile_quality` (complete/sampled/partial) and is
  cached by fingerprint; invalidated by revision change.
- **D5 Reuse**: upgrade `analysis_key` composition at the `analysis_reuse` layer only —
  append ref `content_revision`/content fingerprint + algorithm version; keep kill switch.
- **D6 Catalog**: read-only federation over existing stores; no second write path.
- **D7 Boundaries**: no changes to algorithm implementations, cartography templates,
  workflow planner, Pi routing, Map State core, or GeoCompute scheduler. Additive,
  versioned, backward-compatible touches only.
- **D8 Big data**: agent surface returns refs + bounded summaries (`summary()` on every
  contract/profile object with hard char caps); tools never inline payloads.

## 4. Phase plan

P1 contract/vocabulary → P2 profiler → P3 quality → P4 lifecycle+versioning →
P5 lineage → P6 catalog → P7 workspace snapshots → P8 reuse/materialization → P9 GC →
P10 agent tools + ingest → P11 large-data policy → P12 perf → P13 reliability corpus →
P14 review/docs.


## 5. Review outcomes (Phase 14) & wiring status

Four independent reviewers (architecture/regression, GIS metadata/reproducibility,
storage/security, integration/performance) audited the branch. Fixed majors:

- GC plan/execute protection consistency: persistence-tier (workspace/persistent)
  and retained-lineage-root rules are now enforced inside `collect_orphan_refs`
  (the single delete path), not just in the dry-run planner.
- Workspace snapshot restore no longer fabricates `valid` status for dead
  payloads: re-bound lineage records whose ref is unprobeable are marked
  `expired` (`marked_expired` in the result).
- DataCatalog.search: naive/aware datetime sort crash fixed (uploads coerced
  to UTC-aware); blocking sync DB access moved off the event loop.
- Disk artifact sweep: grace period (default 1h, `ARTIFACT_SWEEP_GRACE_S`)
  protects in-flight publishes and meta-write-failure artifacts from orphan sweeps.
- CRS classification: structural token classifier (`classify_crs_kind`) with
  unknown → no-assumption semantics; compound projected names ("WGS 84 / UTM
  zone 50N", proj4 `+datum=`) no longer misread as geographic.
- Redis overwrite: revision bump now requires a payload digest change —
  byte-identical re-persist (checkpoint/rollback restore) no longer produces
  false reuse misses.
- Staleness propagation is wired: ref overwrite/rollback now triggers
  downstream `stale` marking via the ref_lifecycle hook (fire-and-forget,
  best-effort, ledger-metadata only — no invalidation-storm surface).
- Misc: `PropagationReport.to_dict` AttributeError, fingerprint crs priority,
  contract fingerprint.crs population, ingest CRS dict-form normalization +
  off-loop payload hashing, raster NaN-nodata masking / band cap / TIFF
  colon-date parsing, profile honesty fixes (absent-key null_rate, no
  fabricated geometry counts, (0,0) excluded from extent, malformed
  coordinates tolerated).

Deliberately deferred (foundation scope, explicit contract for follow-ups):

- `SessionLineageQuery.project_lineage` DB bridge is tested-by-contract only
  (no consumer yet); gate on callers before expanding.
- GC planner/snapshot save-restore/ingest pipeline are service-layer complete
  with tests but have no tier-2 tools/routes yet; agent surface today is the
  six discovery tools + the wired staleness hook.
- `DatasetProfileV3` → AlgorithmResolver camelCase adapter intentionally left
  to the workflow-planner lane (V2 `DatasetProfile` remains the resolver input).
