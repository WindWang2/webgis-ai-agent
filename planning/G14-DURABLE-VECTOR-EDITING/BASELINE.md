# BASELINE — G14-DURABLE-VECTOR-EDITING

- Base: `origin/master` = `816705218a5aa6202bf15d745cb31cddb3437f44` (2026-11-10, fetched at task start; equals research snapshot)
- Branch: `zcode/goal-g14-durable-vector-editing-20261110T133233Z`
- Worktree: `webgis-worktrees/goal-g14-durable-vector-editing-20261110T133233Z`
- Open PRs at start: #2032 (UI redesign — additive-only contact), #2021 (G11 briefs), #2022 (G10 data refresh — consume-only), dependabot chores. All dedup-listed PRs (#1873,#1875,#1973,#1975,#2011–#2017,#2033–#2036) verified MERGED.

## Existing feature_edit stack (master)

- `app/services/feature_edit/contracts.py` — closed op vocabulary `set_fields/rename_field/repair_geometry/move_by/snap_to_ref`, finding codes, bounds (20k dataset / 200 plan targets / 10 geometry targets / 1024 positions / journal 128 entries+64KB / plans TTL 10min).
- `wire_types.py` — FeatureDelta = update-only (fields before/after + geometry before/after). No birth/death.
- `journal.py` — pure apply/invert; `apply_deltas_to_features` addresses by `_wfid`, skips misses; invert = swap before/after.
- `identity.py` — `wf1-<sha256(fp:index)[:16]>` materialization at first edit; FEATURE_ID_KEYS resolution.
- `executor.py` — the ONLY write path: `_edit_lock` (turn passthrough → process lock, else distributed+process), CAS on (content_revision, payload fingerprint), re-derive at commit, overwrite + journal append + plan status, out-of-lock MapSpec stamp (`RefreshDataRevisionIntent`) + `dataset_updated` broadcast. Undo/redo = invert head, same CAS path.
- `plan.py` — build_plan pure preview; target resolution REQUIRES existing ids/selection (empty dataset cannot create first feature); materialization preview.
- `service.py` — orchestration shared by REST + tools; `agent_commit` policy gate (geometry ops never auto).
- `policy.py` — confirm_all / allow_safe_attribute (owner-only REST).
- routes `/api/v1/chat/sessions/{sid}/feature-edits/*` (preview/plan/decision/undo/redo/history/policy); tools `vector_feature_edit_preview/commit/history` in ToolRegistry (Pi surface).
- Frontend: `lib/api/feature-edit.ts`, EditProposalCard, SSE wiring (`refreshDatasetLayer`), workbench undo `featureEditCommand`, sketch-editor transient `wb-sketch` (save = client blob download only).

## Confirmed gaps (WP01 reproduction targets)

1. Empty dataset first feature: `plan.py:246` `invalid_target` when no ids/selection — create op absent from vocabulary.
2. Real (ref-backed) layer vertex editing: sketch-editor only queries `wb-sketch*` self-managed layers (`sketch-editor.tsx:364-389`); no gesture bridges to server plans on real layers.
3. Create/delete not invertible through journal: `FeatureDelta` has no presence semantics; `apply_deltas_to_features` cannot add/remove; journal can only update existing wfids.

## Key integration facts (from exploration)

- Ingest → `{"geojson": FC, ...}` envelope + descriptor + artifact ledger registration (`register_artifact`) makes a ref visible to `list_datasets`/Pi. Envelope sibling keys survive feature_edit overwrite (`{**payload, "geojson": new_fc}`).
- Analysis staleness: `ref_lifecycle.invalidate_ref_caches(reason=OVERWRITE)` → `detect_source_change` (compares ledger `source_revision_token` vs descriptor content_revision) → `propagate_staleness` flips dependents valid→stale; `analysis_reuse` requires `input_ref_revisions` match → recompute after edit. **Already fires on every feature_edit overwrite.**
- MapSpec source stamp: `RefreshDataRevisionIntent` writes `content_revision` on spec sources → SSE mirror + `ref-source-resolver` revision invalidation → frontend `refreshDatasetLayer` (called by SSE paths on commit receipts).
- Sketch promotion hook point: `sketch-editor.tsx` save bar (currently blob download).
- `session_data_manager.store` mints new refs (prefix), `overwrite` bumps content_revision + recomputes descriptor + invalidates caches.
- Frontend `_wfid` not yet in FEATURE_ID_KEYS chains (would show as ordinary property column).
- No `dataset_updated` WS consumer on frontend (primary channel is mapspec stamp→SSE) — keep that.

## Bounds discipline

Heavy builds `-j2`; pytest workers per pytest.ini; `OMP_NUM_THREADS=1` etc. for numeric libs; single heavy phase at a time; `webgis-heavy-stage.lock` flock in shared git dir if parallel heavy stages needed.
