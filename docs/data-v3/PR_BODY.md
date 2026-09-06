## Summary

Establishes the **Data Control Plane** for the WebGIS AI agent: a unified, versioned, traceable artifact semantic layer over the existing session registry / provenance / data-fabric systems — per the V3 foundation goal (§一–§四十三). **No second registry is created**: every V3 component is a read-only projection or an additive extension of an existing owner.

## Architecture

New contract layer `app/lib/data/` (pure, zero-I/O) + stateful services (`data_profile`, `data_catalog`, `data_lifecycle`, `workspace`, `data_ingest`) + agent surface (`tools/data_discovery.py`). Ownership map and design decisions: `docs/data-plane/v3-foundation/architecture.md`; decision record: `docs/adr/0103-data-artifact-workspace-foundation-v3.md`; phase-0 evidence: `docs/data-v3/audit/01..06`.

## Artifact Contract V3

- `ArtifactContract`: read-only projection aligned to the §三 field list (identity/version/type/role/source/storage/schema/geometry/crs/extent/temporal/raster/fingerprints/lifecycle/persistence/cacheability/reproducibility/diagnostics), hard-bounded (parents ≤16, fields ≤64, statistics ≤32, diagnostics ≤8) with an LLM-safe `summary()`.
- O(1) bridges from session `ArtifactRecord`, `RefDescriptor`, DB `Artifact`, raster descriptors — missing evidence stays `None`, never fabricated.
- Unified vocabulary: coarse `ArtifactCategory` (controlled extension; the 21-id fine registry stays authoritative for fine semantics), `LogicalRole` (§五 16 roles), `LifecycleState` algebra with a transition table, `QualityStatus`, `PersistenceTier`/`MaterializationPolicy` — plus explicit projections from all five existing state vocabularies.

## Dataset Profile & Quality

- Bounded profiling (§六): single-pass scan, 50k-row gate with deterministic stride sampling, Welford stats, capped unique sets, unit/temporal hints, coordinate-feasibility evidence; descriptor-first zero-scan projection (`PARTIAL`) vs opt-in deep scan (`COMPLETE/SAMPLED`); profile cache bound to `content_revision`.
- Quality contract (§七): 21-code taxonomy, four-state status (valid/warning/repairable/blocked), remediation hints per code, honest `checks_not_run` disclosure.

## Lifecycle, Versioning, Lineage, Staleness

- Lifecycle service (§八): V3-state transitions validated against the algebra then projected onto the session ledger; explicit role/persistence declaration channel.
- Versioning (§九): `SourceRevision` snapshots, cheap revision tokens, fingerprint-dominant change classification (crs > schema > content > metadata, unknown when evidence missing), bounded version chains from `replaces` edges.
- **Staleness propagation wired (§十一)**: ref overwrite/rollback now marks downstream artifacts `stale` via the ref_lifecycle hook (ledger-metadata only, fire-and-forget) — read-only impact analysis and lineage views available for agents.
- Unified lineage queries (§十): normalized nodes/edges over the session graph + a thin read-only DB bridge preserving the DATA-01 tenant-scoping contract.
- **Dispatch-seam lineage wired (§十 audit gap #1)**: `ToolRegistry` exposes a contextvar capture channel (`capture_arg_lineage_refs`) — ref resolution records the canonical input refs (aliases normalized) consumed by the current call, and `ToolDispatchService` registers minted artifacts with `inputs=[...]`. Chat-chained analyses are no longer orphans in the lineage graph; zero overhead when no capturer is active. Covered by 10 dedicated tests (capture/alias/self-ref-exclusion/bounding/chained-closure).

## Catalog, Workspace, Reuse, GC, Ingest

- Unified read-only catalog (§十七): session ledger + uploads + fabric datasets with §十七 filter axes, bounded results, honest per-source status (DB down ≠ empty).
- Workspace snapshots (§十五/§十六): artifact contracts + layer refs + mapspec fingerprint atomically persisted; verify-before-restore reports dead refs upfront; register-mode rebinds lineage **without** fabricating validity (dead payloads land `expired`); cross-session clone with honest ref semantics; session-id traversal hardening on list/cap paths; crash-orphaned `.tmp` snapshots swept.
- Reuse upgrade (§十三): input-ref `content_revision` recorded at production and rechecked at reuse (closes the same-shape attribute-overwrite blind spot); the revision review is independent of shape-snapshot availability; explicit `cacheable=False` honored; legacy records stay compatible; failure direction is conservative miss, never false hit.
- GC (§十四): dry-run planner + executor share the same protection rules (live refs, workspace/persistent tiers, retained lineage roots) enforced inside the single delete path; new `data/artifacts` disk sweep (temp leftovers, half-orphans, age) with a publish grace period; promotion-store usage diagnostics are report-only (protected data is never auto-deleted).
- Ingest pipeline (§二十五): detect → validate → profile → quality → register with sha256 content dedup (same payload reuses the ref), honest CRS handling (never fabricated 4326), compensating rollback.

## Integration & Boundaries

- Six agent discovery tools (`list/search_datasets`, `profile_dataset`, `describe_artifact`, `find_artifacts_by_role`, `get_lineage`) returning bounded summaries — agents consume refs + profiles + quality, never payloads (§十八/§三十二).
- Shared-file changes are strictly additive: registry `update_record_metadata` (merge now drops OLD keys under cap pressure — stale reuse evidence degrades to a conservative miss; fresh staleness evidence never silently lost) + metadata budget 12→24; reuse seam captures revision evidence best-effort; artifact_lifecycle gains sweep families. Algorithm implementations, cartography templates, workflow planner, Pi routing, Map State core, and GeoCompute scheduler are untouched (§三十五).

## Independent review (2 rounds, 8 perspectives)

Round 1 (architecture / reproducibility / storage / security / performance / GIS metadata / integration / regression) produced 1 blocking + 7 major findings — all fixed on-branch: GC executor protection parity, staleness wiring, CRS misclassification of compound projected names ("WGS 84 / UTM zone 50N"), ingest hashing off the event loop, snapshot restore honesty, catalog event-loop DB access, disk-sweep grace period. Round 2 closed the dispatch lineage gap, a Redis-backend `TypeError` (`_ref_revisions_key` signature), metadata truncation direction, snapshot path validation, numeric temporal false positives, and added report-only promotion-store diagnostics.

## Test results (local; no online CI used)

- **284 passed / 0 failed** in the final verification run: all of `tests/data/` (contract, vocabulary, fingerprints, profile, quality, profiler service, versioning, lifecycle, lineage queries, dispatch lineage inputs, catalog, workspace snapshots, reuse V3, GC, ingest, reliability corpus §三十四, perf smoke §三十三, review-fix regressions rounds 1+2) plus the affected shared-file suites (tool registry, registry offload/timing, session data incl. Redis backend, buffer caching, upload tools, API path traversal, plan-mode suites).
- `ruff check` clean on all changed files; `compileall` clean; V3 service import smoke OK.
- Note: API-level suites that transitively import `app/core/bridge_secret.py` / `rag/faiss_store.py` cannot run on Windows (`fcntl` is Unix-only) — a pre-existing baseline limitation, unrelated to this branch.

## Performance results (§三十三 smoke, in-lane with wide guards)

- 100k-feature vector profile: sampling gate engages (50k scanned, `sampled` quality), bounded scan time.
- 10k-entry table profile, wide lineage-graph traversal, 1000-long version chains, and 10k-entry catalog filtering all complete within structural bounds with explicit truncation flags; catalog/profile surfaces remain O(bounded caps) per query.

## Known limitations / deferred

- Snapshot payloads still die with session TTL; restore restores the metadata/lineage layer honestly (verify-first disclosure). Durable payload survival needs the promotion bridge — deferred.
- GC planner / snapshot save-restore / ingest pipeline are service-complete with tests but not yet exposed as tier-2 tools/routes; `get_ingest_pipeline` awaits an upload-route integration (existing upload face unchanged and unaffected).
- `compute_reuse_fingerprint` remains the contract-level formula; runtime reuse keys on analysis_key + shapes + raster fps + revisions (safety-equivalent, not yet op-identity-addressed).
- `data/project_artifacts` is observable (files/bytes/age) but uncapped; enforcement needs a retention-policy decision (contents are protected persistent data).
- `DatasetProfileV3` → AlgorithmResolver camelCase adapter intentionally left to the workflow-planner lane; V2 `DatasetProfile` remains the resolver input.
- `project_lineage` DB bridge is contract-tested only (no consumer yet); CRS_SUSPICIOUS is a reserved code (no producer in V3).

## Compatibility

All V3 state vocabulary projections are read-only; legacy records without V3 metadata behave exactly as before (verified by dedicated compat tests). Shared-file changes verified backward compatible per review (old callers bit-identical; metadata ceiling only raised; kill switches preserved).

🤖 Generated with [ZCode](https://zcode.ai)
