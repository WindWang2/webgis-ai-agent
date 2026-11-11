# INTERFACES.md — G14 shared-surface contact list

## Additive-only contacts in shared files
- `app/services/feature_edit/contracts.py`, `wire_types.py`, `journal.py`, `plan.py`, `executor.py`, `service.py`, `geometry_ops.py` — G14-owned (primary write area).
- `app/schemas/feature_edit_schema.py` — extend EditOpModel union additively (new optional fields; keep extra=forbid).
- `app/api/routes/feature_edits.py` — new endpoints under existing router prefix.
- `app/tools/feature_edit_tools.py` — new tools + widened ops passthrough.
- `app/tools/__init__.py` — only if a new module file is added (prefer keeping tools in feature_edit_tools.py).
- Frontend: `lib/types/feature-edit.ts`, `lib/api/feature-edit.ts`, `lib/store/slices/toolSlice.ts` (additive MapToolId + editTarget), `lib/layers/feature-id.ts` + `lib/store/layer-data.ts` (append `_wfid` to FEATURE_ID_KEYS), `components/map/sketch-editor.tsx` (save-as-dataset button only), `components/chat/edit-proposal-card.tsx` (op labels), `messages/{zh-CN,en-US}/{map,chat}.json` (new keys).
- No edits to: mapspec schema/models (G15/G16 own label/frame fields), Pi vendor/bridge (G12), capability registry internals (G13), global theme/layout (#2032).

## Contracts G14 exposes to siblings (additive, optional consumers)
- Stable identity: `_wfid` on edited datasets (existing); created features' wfids deterministic per plan.
- Journal entry optional `lineage`: `{"events": [{"kind":"split"|"merge", "parent_wfids": [...], "child_wfids": [...], "seq": N}]}` + REST `GET .../feature-edits/{ref}/lineage?wfid=` bounded projection (receipt also carries `created_wfids`/`retired_wfids`). G15/G16 may consume; absence of lineage in old data = normal (no requirement).
- Receipt additive fields: `created_wfids`, `retired_wfids`, `lineage_events` (bounded).
- Envelope additive key `topology` on datasets G14 creates/promotes (G13 descriptors read envelope as-is).

## Contracts G14 consumes
- `session_data_manager` store/overwrite/get/get_ref_descriptor/set_map_state_fields (existing semantics).
- `IngestPipeline.ingest` for dataset creation/promotion (registers artifacts → catalog/Pi visibility).
- `RefreshDataRevisionIntent` via `mapspec_stamp.apply_data_revision_stamp` (existing).
- `ref_lifecycle` staleness propagation via overwrite reason=OVERWRITE (existing, no changes).
- ToolRegistry `@tool` + session_id runtime injection (existing).
- `broadcast_ws_event("dataset_updated", ...)` (existing; frontend consumption remains optional/none).

## Parallel-PR conflict surface
- #2032 (UI redesign): shares `map-toolbar-hud.tsx` potentially — limit to importing new local component + minimal insertion; list conflict sections in PR body.
- #2022 (G10 refresh): shares nothing directly; we only read descriptor content_revision; re-check at PR time.
- G15/G16 worktrees: share `wire_types.py`-adjacent mapspec files — we do not touch mapspec models.
