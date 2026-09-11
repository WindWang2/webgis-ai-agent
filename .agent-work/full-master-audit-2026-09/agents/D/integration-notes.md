# Agent D — Integration Notes

## 1. Migration graph validation (authoritative, via alembic ScriptDirectory)

- **Heads: exactly 1** — `c0d8322aa2cb` (merge revision). 39 revisions total, 0 duplicate revision ids, 0 orphan down_revisions.
- Chain around the collision point (all four `0034_*` branch off `0033_geocompute_v6_cluster`, unified by the merge):
  ```
  c0d8322aa2cb <- (0034_data_fabric_v7_facts_feedback, 0034_geocompute_v7_dataflow,
                   0034_lakehouse_catalog, 0034_workflow_v5_runtime)
  0034_*       <- 0033_geocompute_v6_cluster <- 0032_harness_v5_resume_anchors
                 <- 0031_revision_indexes <- 0030_revision_no_unique <- 0029 ...
  ```
- Merge revision `c0d8322aa2cb` upgrade/downgrade are deliberate documented no-ops (commit 819db8b4); correct for a pure unification point.
- **Recent-10 upgrade/downgrade symmetry review**: additive DDL with existence guards is the repo convention (0022–0034); no destructive DDL in the last 10 revisions; downgrades drop exactly what upgrades created:
  - 0034_workflow_v5_runtime: 4 new tables + indexes + checks, downgrade reverse-drops (103L upgrade, symmetric).
  - 0034_geocompute_v7_dataflow: 6 ops / 6 ops symmetric.
  - 0033_geocompute_v6_cluster: 11 ops / 10 ops (one guarded create in upgrade whose downgrade guard-equivalent) — acceptable.
  - 0030_revision_no_unique: upgrade dedups duplicate `(artifact_id, revision_no)` rows by **renumbering** (append-only evidence preserved) before creating the unique index; downgrade only drops the index and honestly documents renumbering as irreversible — data-safe design.
  - g1109_legacy_owner_tokens: backfills random owner tokens for legacy anonymous conversations; downgrade is a no-op (cannot restore NULLs) — irreversible by design, documented.
- **Lock-risk / data-safety read**: all recent DDL is CREATE TABLE/INDEX with `IF NOT EXISTS`-style guards; 0030's dedup does a full-table `ORDER BY` scan + row updates inside the migration transaction (bounded by artifact_revisions being a per-artifact ledger; acceptable at current scale, worth noting for very large ledgers).
- preflight `migration_heads` + NNNN watermark checks pass (migration_watermark=34, max seq=34).
- Residual nit: four coexisting `0034_*` filenames contradict the renumber-to-0035 protocol written in 0034_workflow_v5_runtime's own docstring → finding D-10 (P3, docs/ops clarity only).

## 2. generated_staleness RED (16 artifacts) — root-cause analysis

Mechanism (app/lib/quality/artifact_graph.py): each declared artifact records `input_fingerprint = sha256(canonical([(path, sha256(content))]))` over `generator + declared inputs` (directories expanded to `*.py`, bounded 400 files, over-limit fails loudly). Stale = recorded fingerprint ≠ recomputed. Ledger: `docs/quality/generated-artifacts.json`.

### 2.1 What went red and why (per-artifact input attribution, git-verifiable)

| Stale artifact | Decisive input changes after last true ledger refresh |
|---|---|
| QUALITY_MANIFEST.md / quality-manifest.json | `app/lib/gis/algorithms/**` + `capabilities/**` (f576798d merge-align 29 files; 47bcb4c6 3 files), `app/tools/**` (534531e1 10 files + others), `app/lib/quality/manifest.py` (47bcb4c6), gen script itself (f94ac27c) |
| QUALITY_REPORT.md / quality-report.json | depends on quality-manifest.json + contract-drift-report.json (regenerated never) + manifest.py |
| CONTRACT_DRIFT_REPORT.md / contract-drift-report.json | `app/api/routes/**` — nearly every route file touched post-ledger (71373b2f, 0180c2e9, b7bc2d22, a1286c69 chat.py, 70130fa9 task.py, 672918d8 project.py, 518e14d7 upload.py, …) + drift.py (baa2ceeb) + gen script (53f5ef75) |
| SECURITY_CONTROLS.md | security_manifest.py (f473ab24), gen script (7b3d373c) |
| CANCELLATION_COVERAGE.md / RESOURCE_SAFETY.md | `app/lib/geo_analysis/**` (38efd467 7 files, 2297e125 3, a13fa7a6 3, …) |
| CHAOS_FAULT_REGISTRY.md | tests/fixtures/chaos.py (46e0e73f), gen script (1a92e0fb) |
| DETERMINISM.md | `app/lib/gis/algorithms/**` (77312b92 8 files, …) |
| realtime-contract.json | ws.py (1bc7f862), ws_service.py (fd63781e), sse.py (dc55183a), api_compat.py (47bcb4c6) |
| BENCHMARK_MANIFEST.md | algorithm_registry.py + algorithms/** |
| frontend-behavior.json | gen script only (46e0e73f) — byte content coincidentally unchanged, so `gen_frontend_behavior.py --check` passes while the fingerprint ledger (stricter) is red |
| RELEASE_READINESS.json/.md | gen script (97a97abc) — internal LIVE_GATES re-execute preflight/staleness, so readiness --check is red for the same upstream reason |

### 2.2 The remediation attempt at HEAD is itself defective

`git show 2aabdc43 -- docs/quality/generated-artifacts.json`: exactly two `input_fingerprint` values hand-edited (`cf90ad23…` → `b3ea0091…`, both drift-report entries), no corresponding regeneration of the reports themselves, no `--update` for the other 14 entries. Recomputed fingerprint at HEAD is `2d9b63c5…` ≠ recorded `b3ea0091…` — the hand-patched value matches neither the pre-merge nor the post-merge state (likely computed in a dirty worktree mid-rebase). Consequences: preflight `generated_staleness` RED (16), `generated_hand_edits` ok only because input fingerprints don't match (hand-edit detection is defined as inputs-unchanged+content-changed), quick lane (`quality_runner quick`) cannot exit 0 at master. → Finding D-2 (P1).

### 2.3 Correct remediation sequence (for the master agent)

1. Fix D-1 first on affected machines (or run on Linux CI): `gen_drift_report.py`/`gen_quality_manifest.py` currently crash at `Settings()` when the local resolver fake-IPs `overpass.openstreetmap.fr`.
2. Run all gen scripts (§2.1 table) + refresh `tests/quality/snapshots/realtime-contract.json` (via api_compat) + `openapi.json` snapshot if the drift gate requires.
3. `python scripts/check_generated_staleness.py --update`, then re-run preflight until `generated_staleness` and `generated_hand_edits` are both green.
4. Add CI guard: commits touching only `generated-artifacts.json` (fingerprint-only patches) are rejected.

## 3. ADR watermark vs duplicate numbers — consistency spot-check

- Mechanism: `ownership.json.adr_watermark = 129`. Duplicates **above** watermark → preflight RED; at/below → tolerated "known limitation" (adr.py:111-121). Current state: 10 duplicate clusters, all ≤ 129 → preflight `adr_watermark` ok; dangling-link ratchet ok (7 baseline entries).
- **Ambiguity is real in prose**: CHANGELOG.md cites "ADR-0118" at line 444 (Professional Cartographic Rendering V5), line 466 (Spatial Data Lakehouse & Cube V6), line 589 (Harness V5) — three distinct documents sharing the number; "ADR-0104" at line 555 (session-transient state) vs line 713 (GIS Extension Platform V1) — two distinct documents. docs/ body references were not exhaustively scanned, but the pattern is established. Conclusion: the watermark mechanism prevents *new* collisions (allocator + preflight + merge-sim) but toleration of historical duplicates transfers the disambiguation burden to readers, and CHANGELOG does not carry it. → Finding D-8 (P3) with the 5 concrete CHANGELOG sites.
- CHANGELOG↔ADR existence: no dangling ADR file references found beyond the 7 baselined ones (ratchet green).

## 4. Python/TS contract + OpenAPI mechanism

- OpenAPI: FastAPI native; snapshot gate at `tests/quality/snapshots/openapi.json` + `app/lib/quality/api_compat.py` (diff-based compatibility checks, `tests/quality/test_api_compatibility.py`). Backend-only coverage.
- Frontend drift gate: `app/lib/quality/drift.py` scans `/api/...` string literals in frontend/lib|app|components|hooks vs backend routes — path-level only, **no field-level validation**.
- Spot check (upload): field-level drift confirmed — `crs` nullable backend / non-nullable TS; 9 V4 response fields missing from TS (`frontend/lib/api/upload.ts:22-33` vs `app/api/routes/upload.py:97-128`). → Finding D-6 (P2). Recommendation: generate `frontend/lib/api` types from the OpenAPI snapshot (openapi-typescript) with a CI byte gate.

## 5. Quality-gate script correctness (task-listed scripts)

- `check_generated_staleness.py` / `check_integration_preflight.py`: logic sound (deterministic fingerprints, bounded dir expansion with loud failure, ownership parity, ratchets). Both executed live; results above.
- `allocate_adr.py` / `allocate_migration.py`: correct max+1 with collision refusals and watermark reminders; `allocate_migration` correctly refuses multi-head graphs (currently single head). No bypass found.
- `gen_drift_report.py` / `gen_quality_manifest.py`: byte `--check` semantics correct; CWD-relative output paths (D-12); crash on fake-IP DNS environments via Settings import (D-1).
- `quality_runner.py`: lane composition correct overall; `changed` profile's app→tests mapping broken for 2-segment roots (D-5); `quick` lane ordering ensures gates block; full lane serial per resource discipline; `perf` isolation matches CI contract (`NIGHTLY_ONLY_PERF_FILES`).

## 6. Environment-dependent reproducibility note for the master agent

The audit machine (win32, fake-IP DNS) cannot: import `app.main` (D-1 + D-7), run `gen_drift_report`/`gen_quality_manifest` (D-1), or run the full pytest suite (fcntl). All live gate executions in this audit that did pass (staleness, preflight, alembic graph, ADR scan, security/resource/determinism/chaos gen --check, frontend-behavior --check) used modules that avoid the Settings import chain. Verification of D-1's boot-failure was done via direct import probes; on a clean Linux CI the same code boots, so D-1's blast radius is environment-specific — severity kept at P1 because fake-IP resolvers are a mainstream deployment/dev reality for this codebase's locale, and the failure mode is a hard crash with no override switch.
