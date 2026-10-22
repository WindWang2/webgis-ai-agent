# 0103. GIS Data / Artifact / Workspace Foundation V3

**Date:** 2026-09-07
**Status:** Accepted
**Branch:** `feat/gis-data-artifact-workspace-v3`
**Audits:** `docs/data-v3/audit/01..06`（六路只读审计，master@222a994f）
**Design:** `docs/data-plane/v3-foundation/architecture.md`

## Context

The platform already owned GIS algorithms, GeoCompute/Data Fabric, cartography,
workflow/recipes, a Pi tool runtime and Map/Layer state — but the *data* those
systems consume had no unified identity. Phase-0 audits found:

- **Four coexisting artifact abstractions** (DB `Artifact`, session
  `ArtifactRecord`, `ArtifactDescriptor`, promotion content store) with two
  identity schemes (uuid vs session `ref:` string, minted random per store).
- **Three dataset vocabularies and three profile shapes** (RefDescriptor,
  spatial meta profiler camelCase, ArtifactRecord metadata) plus one unbounded
  full-scan profiler fallback that breaks at ~100k features.
- **Five lifecycle vocabularies** (session 5-state, stateless DB rows, catalog
  2-state, layer 4-state, promotion 5-state) with no shared algebra.
- **Lineage hole at the dispatch seam**: the dominant chat flow registered
  artifacts with no `inputs` — chained analyses were orphans in the session
  lineage graph.
- **Reuse/cache keys without content evidence**: `analysis_reuse` keyed on
  tool+args (ref ids), blind to same-shape in-place overwrites; `tool_cache`
  keyed on path strings.
- **Ingest without dedup or honest CRS** (duplicate uploads unreated; CSV
  silently EPSG:4326; no rollback registry).
- **Workspace restore gap**: `Artifact.storage_ref` / run outputs hold session
  cursors that dangle after the 4h TTL; snapshots of map state survive but
  their payloads die silently.
- Promotion content store (`data/project_artifacts`) grew unbounded and was
  unobservable.

## Decisions

1. **One read-only contract, zero second truth (extends ADR-0082).**
   `app/lib/data/` (pure contracts, no I/O, never imports `app.services`)
   defines the Artifact Contract V3: `vocabulary.py` (15 coarse
   `ArtifactCategory` + controlled extension, 16 `LogicalRole`s, a
   `LifecycleState` algebra with a validated transition table,
   `QualityStatus`, `PersistenceTier`/`MaterializationPolicy`) and
   `artifact_contract.py` (`ArtifactContract` with bounded fields and O(1)
   bridges `from_artifact_record / from_ref_descriptor / from_db_artifact /
   from_raster_descriptor`). Existing owners keep their roles; the contract
   only *projects* them. Missing evidence stays `None` — never fabricated.

2. **Fingerprints are the versioning primitive.** `fingerprints.py` provides
   canonical JSON hashing, `FingerprintSet` (content/schema/metadata/crs),
   `classify_change` (none/metadata_only/content/schema/crs, priority
   crs > schema > content > metadata; missing evidence → UNKNOWN, never
   "unchanged"), `staleness_verdict` (valid/stale/recompute/invalid) and
   `compute_reuse_fingerprint` (§13 formula). `versioning.py` adds
   `SourceRevision` snapshots (cheap mtime_ns:size tokens + rich fingerprints)
   and cycle-safe bounded version chains over `replaces` edges.

3. **Lifecycle transitions are validated, then projected.**
   `app/services/data_lifecycle/service.py` validates V3 state transitions
   against the vocabulary algebra and projects them onto the session ledger
   (5-state machine unchanged underneath). Role/persistence declaration is an
   explicit channel; staleness propagation rides the existing `ref_lifecycle`
   overwrite/rollback hooks (fire-and-forget, ledger metadata only) and marks
   the downstream closure stale.

4. **Lineage covers the dispatch seam.** `ToolRegistry` exposes a contextvar
   capture channel (`capture_arg_lineage_refs`): ref resolution records the
   canonical input refs (aliases normalized) consumed by the current call;
   `ToolDispatchService` wraps dispatch with the scope and passes
   `inputs=[...]` to `register_tool_artifact`. Chat-chained analyses are no
   longer orphans. Zero overhead when no capturer is active.

5. **Reuse is content-evidenced, never pointer-keyed.** `analysis_reuse`
   records per-input `content_revision` snapshots at production time and
   re-checks them on reuse (closes the same-shape attribute-overwrite blind
   spot); explicit `cacheable=False` vetoes; legacy records without evidence
   stay compatible (check skipped, never fabricated). Failure direction is
   conservative miss, never false hit. `GIS_ANALYSIS_REUSE=0` kill switch
   preserved.

6. **Profiling is bounded and honest.** `app/lib/data/profile.py` — single-pass
   Welford profilers for vector/table with a 50k-row gate and deterministic
   stride sampling (no randomness), field/sample/unique caps, profile quality
   (complete/sampled/partial/failed), unit/temporal hints by name convention;
   raster profiles from file headers + downsampled reads in
   `app/services/data_profile/profiler.py` (thread-offloaded; zero-scan
   descriptor projection stays the first choice). `quality.py` turns profiles
   into a bounded diagnostic taxonomy with a four-state verdict and
   remediation hints.

7. **Catalog = read-only federation.** `app/services/data_catalog/catalog.py`
   federates the session ledger, DB uploads, and the fabric spatial catalog
   with §17 query axes, hard limits, source-failure disclosure (an
   unavailable source is an error note, never a silent empty result) and
   LLM-bounded summaries. `lineage_query.py` unifies session-graph and
   project-DB lineage behind normalized nodes/edges with depth-gated,
   cycle-safe closures.

8. **Workspace snapshots are evidence, not a second map state.**
   `app/services/workspace/snapshot.py` persists artifact contracts + layer
   source refs + view/settings per session (atomic writes, 20-snapshot cap,
   traversal-guarded); `verify_snapshot` probes ref liveness *before* any
   restore so hollow layers are visible upfront; restore rebinds ledger
   lineage only (register mode) and never drives MapSpec. Cross-session clone
   honestly reports ref semantics (refs are session-scoped).

9. **Ingest dedups on content and never fabricates CRS.**
   `app/services/data_ingest/pipeline.py` (detect → validate → profile →
   quality → register with sha256 payload fingerprints in a thread) reuses an
   existing ref when the same content is re-ingested instead of minting a
   duplicate logical object, reports missing CRS honestly (RFC 7946 default is
   the upload face's documented behavior, not a fabricated claim), and
   compensates with ref deletion if registration fails after store.

10. **GC plans dry-run; a single delete path executes with in-lock
    re-verification.** `app/services/data_lifecycle/gc.py` plans orphans with
    the three protection rules (live references, workspace/persistent tiers,
    retained lineage roots); `collect_orphan_refs` enforces the same rules
    inside its lock — planner and executor cannot disagree. The disk sweep
    gained crash-temp/meta-orphan/age categories with a 1h grace window for
    in-flight publishes. Promotion content is *protected* and only reported
    (files/bytes/oldest age) — no auto-deletion of workspace-persistent data.

11. **Large-data policy is explicit.** `app/lib/data/large_data.py` classifies
    small/medium/large with an access-policy matrix aligned to the session
    store budget; unknown size degrades to medium discipline. Every
    agent-facing surface returns refs + bounded summaries (`summary()` with
    hard char caps and progressive degradation).

## Compatibility

- All shared-file changes are additive: `register_tool_artifact` gained
  optional kwargs; `analysis_reuse` guards skip legacy records; the disk
  sweep gained an isolated family; `MAX_RECORD_METADATA_KEYS` 12→24 is the
  only budget widening (session-scoped, per-record).
- No algorithm implementations, cartography templates, workflow planner, Pi
  routing, Map State core, or GeoCompute scheduler were modified.
- Test isolation: `tests/data/**` is self-contained (no DB/Redis required);
  the branch adds no alembic migration (workspace snapshots are session-disk
  artifacts; a project-attached snapshot table is future work, see below).

## Known limitations / deferred

1. Snapshot payloads still die with session TTL; restore restores the
   metadata/lineage layer. Durable payload survival needs the promotion
   bridge (or automatic promotion at run end) — deferred with an honest
   `verify_snapshot` disclosure.
2. `compute_reuse_fingerprint` remains the contract-level formula; runtime
   reuse keys on analysis_key + shapes + raster fps + revisions (a strict
   superset of safety, not yet op-identity-addressed).
3. `data/project_artifacts` is observable but uncapped; enforcement needs a
   retention policy decision (it holds protected persistent data).
4. DB-side catalog search (GIN indexes on tags/descriptor) is deferred until
   catalog sizes demand it; federation is in-memory with hard scan caps.
5. Layer-state staleness diagnostics in map tools (§22) are served through
   snapshot verification and lifecycle records; live map-tool surfacing is
   deferred to the Map State line.
