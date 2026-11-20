# PLAN — G14 durable vector editing

## Design decisions (frozen before implementation)

### D1 — Op vocabulary extension (closed set, additive)
New ops in `EDIT_OPS`:
- `create_features` — `{op, features: [{geometry, properties?}]}`, ≤ MAX_CREATED_PER_PLAN (50), each ≤1024 positions. Server-assigned WFIDs = `wf1-<sha256(plan_id:op_index:child_index)[:16]>` (deterministic → idempotent replay & redo keep identical identities). Properties may not set `_wfid`/`id` reserved fields.
- `delete_features` — no per-op params (targets from plan target resolution). Deltas carry full feature snapshots (bounded by entry-byte guard).
- `replace_geometry` — `{op, geometry}` applied to each target (frontend computes final geometry after gesture; 1 gesture = 1 plan = 1 revision). Type must stay base-compatible; validity guard as existing.
- `split_feature` — `{op, line: LineString coords, wfid: target}` → children = shapely split parts (≥2 else typed finding). Parent retired (delta presence=removed) + children created; fields inherited (copy); lineage parent→children.
- `merge_features` — `{op, field_policy: {mode: "first"|"require_identical", overrides?}}` targets = 2..MAX_MERGE_PARENTS (10). unary_union; result must be single-part else finding; differing fields disclosed in op summary (explicit inheritance rule); lineage parents→child.

### D2 — Delta model (backward compatible)
`FeatureDelta` gains optional `presence: "created"|"removed"` (absent = modified, old semantics) and `feature_before`/`feature_after` (full feature dicts for created/removed). `apply_deltas_to_features`: removed → filter out by wfid; created → append if wfid absent (idempotent); modified → existing path. `invert_deltas`: created↔removed with snapshots; modified unchanged. Old journals (no presence) replay identically.

### D3 — Transaction: single existing executor path
commit_plan unchanged in shape: re-derive deltas (with plan_id seed for created wfids) → apply structural deltas → CAS on (revision, fingerprint) → overwrite → journal entry (+`lineage` optional field) → stamp/broadcast. Entry byte guard at preview: serialized deltas > MAX_JOURNAL_ENTRY_BYTES (256KB) → finding `entry_too_large` (new code), honest rejection.

### D4 — Topology (declared, local, additive)
Dataset envelope may carry `"topology": {"rules": ["no_overlap"|...]}` (survives overwrite; set at dataset create/promote or via qualification endpoint). At preview/commit when dataset declares rules (or op sets `validate_topology: true`):
- `no_overlap`: STRtree neighborhood of affected features; pairwise intersection area > tolerance_m²-equivalent (deg² computed via Geod; default 1 m²) → located finding (pair + bbox) — commit blocked.
- `no_self_intersection`: polygons valid (existing) + lines simple.
- `no_duplicate_points`: identical xy among affected.
Rules never applied to layers that don't declare them (V10).

### D5 — Editable qualification & dataset provisioning (`datasets.py` new module)
- `GET .../feature-edits/datasets/{ref}/qualification` → {editable, reason?, geometry_types, feature_count, read_only, topology}.
- `POST .../feature-edits/datasets` {name, geometry_type, topology?} → ingest empty typed FC (pipeline registers artifact → visible to Pi/tools) + mapspec layer mount; V01.
- `POST .../feature-edits/datasets/promote-sketch` {features, name, topology?} → ingest sketch FC with provenance metadata `origin: sketch`; V02.
- `POST .../feature-edits/datasets/{ref}/editable-copy` → for read_only refs (envelope `read_only: true` or artifact source_type fabric/external): copy payload to new ref with `derived_from` metadata; original untouched; V20.
Read-only marker honored by build_plan → `dataset_read_only` finding with copy hint.

### D6 — Frontend bridge (additive, no global redesign)
- `toolSlice`: new MapToolId `split_feature`; edit-target state `editTarget: {layerId, refId} | null` + setters; sketchDirty stays.
- `lib/edit/layer-edit-bridge.ts` (new): submitEditOps(ops, targets) → preview REST → owner approve REST (manual gesture = owner channel) → on receipt: refreshDatasetLayer-equivalent update, selection prune (retired wfids), undo registration via `featureEditCommand`, opLog entry. Draft geometry stays client-side during gesture (V04/V05).
- `components/map/feature-layer-editor.tsx` (new): gestures on the target layer's rendered features + dedicated vertex overlay for selected feature; create (point click / line / polygon draw), delete, vertex drag, split-line draw, merge (from selection ≥2). Only active when editTarget set; mutually exclusive with other tools (existing toolSlice toggle).
- `sketch-editor.tsx`: add "保存为数据集" (save as dataset) button → promote-sketch REST → success: reset sketch, layer appears via SSE mapspec mirror; keep existing download button.
- EditProposalCard + types + i18n: new op labels (zh/en).
- `_wfid` appended to frontend FEATURE_ID_KEYS chains (feature-id.ts, layer-data.ts) so post-edit features stay addressable; attribute table shows it as ordinary column.

### D7 — Pi tools (additive)
- Existing preview/commit tools accept new ops via same `ops` param (schema widened). Create/delete/split/merge/replace_geometry are geometry-class → never `safe` → CONFIRMATION_REQUIRED unless user approves (existing card flow); manual UI edits bypass via owner REST.
- New narrow tool `vector_dataset_provision` (create empty typed dataset / promote inline FC ≤ bounds; artifact_creation) and `vector_feature_lineage` (cacheable_read; bounded lineage projection). All in ToolRegistry with real descriptors (consume G13 rules).

### D8 — Analysis/MapSpec/project closure (consume existing)
Edit commit → overwrite → descriptor revision + staleness propagation (existing) + mapspec stamp (existing) + `dataset_updated` broadcast (existing). Journey test proves: service-area edit → schools stats artifact flips stale → recompute → export reflects new revision. Sketch-promoted dataset is artifact-registered → attachable via existing ProjectService.attach_dataset (V19/V02).

## Work order (vertical slice first)
1. WP01 done (BASELINE/DEDUP + call graph; gap proofs via quick scripts during WP03 tests).
2. WP03+WP04+WP05+WP07 backend core (contracts→wire_types→journal→geometry_ops→plan→executor) with unit tests — the vertical server path.
3. WP02 datasets module + WP06 topology + WP10 tools + WP11 journey tests.
4. WP08+WP09 frontend bridge + gestures + sketch promotion + tests.
5. WP12 bounds/perf + review + PR.

## Verification mapping
See TEST_MATRIX.md (V01–V20 → test files).
