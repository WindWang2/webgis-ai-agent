# DEDUP_MATRIX — G14 vs latest master + open PRs

Legend: present / partial / missing / owned-by-other / blocked.

| Capability | Status | Evidence (master unless noted) |
|---|---|---|
| Server-side attr edit (set_fields/rename) + preview/confirm/undo | present | `app/services/feature_edit/**`, ADR-0277, PR #1873 → keep as compat base; extend, not rewrite |
| Geometry repair/move/snap ops | present | `geometry_ops.py` (repair_geometry/move_by/snap_to_ref) |
| CAS + journal + MapSpec stamp + analysis staleness propagation | present | `executor.py` CAS; `ref_lifecycle.py` OVERWRITE staleness; `analysis_reuse.input_ref_revisions` |
| Pi feature-edit tools (preview/commit/history) | present | `app/tools/feature_edit_tools.py` (registry, tier 2) |
| **create_features / delete_features ops** | missing | `contracts.EDIT_OPS` closed set; `plan.py` requires existing targets; `FeatureDelta` update-only |
| **replace_geometry (vertex edit) op** | missing | no op accepts explicit geometry payload |
| **split / merge ops + lineage** | missing | no split/merge anywhere in feature_edit; no lineage projection |
| **Topology rules (declared no_overlap etc.) + located preview** | missing | no topology module in feature_edit (data_quality is dataset-level repair, not edit-time rules) |
| **Empty typed dataset creation** | missing | ingest accepts empty FC (`geojson_guard` passes `features: []`) but no typed-create endpoint/tool; plan requires targets |
| **Sketch → persistent dataset promotion** | missing | `sketch-editor.tsx:568-587` save = client blob download; no REST |
| **Read-only source → editable derived copy** | missing | `dataset_read_only` finding code reserved but unused; no copy path |
| **Real-layer map gestures → server transactions** | missing | sketch gestures only touch `wb-sketch*` self layers |
| Selection/attribute-table refresh after edits | partial | `refreshDatasetLayer` exists for agent/approve paths; no handling for created/deleted/split ids in selection store; `_wfid` not in FE id chains |
| MapSpec commit undo (whole agent submission) | owned-by-other | #2035 merged — separate mechanism, do not touch |
| External data refresh / influence preview / incremental adoption | owned-by-other | #2022 open (G10) — consume only existing revision/invalidation interfaces |
| UI global redesign | owned-by-other | #2032 open — only additive local components/tokens |
| Pi pin / bridge / RPC | owned-by-other | G12 (`goal-g12-pi-native-integration-*` worktree exists) — consume ToolRegistry only |
| Capability qualification | owned-by-other | G13 — provide real descriptors, consume existing registry rules |
| Multi-frame publication (G15) / label overrides (G16) | owned-by-other | they consume our stable IDs/lineage additively |
| Map Studio single-source export | present | #2034 merged; edits refresh data revision → export picks up new data via source content_revision |

Rules: implemented items above become compatibility regressions (existing tests must stay green); nothing in the "missing" list is being implemented by an open PR (checked #2021/#2022/#2032 changed-file sets at task start).
