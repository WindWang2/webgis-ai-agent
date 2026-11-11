# INTERFACES.md — G16 shared-surface ledger

## Files G16 owns (new)

- `app/lib/cartography/label_authoring.py` — model, caps, resolution, staleness, units, preview/reset semantics (pure).
- `app/tools/label_authoring_tools.py` — Pi tools.
- `frontend/lib/mapspec-runtime/label-authoring.ts` — TS twin of the pure model + match-expression builders.
- `frontend/components/map/label-editor.tsx` (+ colocated test).
- `tests/unit/cartography/label_authoring/`, `tests/cartography/test_label_authoring_*.py`, golden corpus `tests/cartography/golden_corpus/label_authoring/`.

## Existing files — minimal additive edits

| File | Edit | Conflict watch |
|---|---|---|
| `app/lib/cartography/mapspec_schema.py` | +`LabelAuthoringOverride/LabelAuthoringConfig/LabelForbiddenZone` models; +`MapSpecLayerLabel.authoring`; +`MapLabelConfig.obstaclePolicy/forbiddenZones` | G15 edits `MapSpecFrame` in same file — different classes; additive only, no reformat |
| `app/lib/cartography/label_collision.py` | optional authoring fields on CollisionLabel/Placement + obstacle identity + policy param | twin parity corpus must stay green for legacy inputs |
| `app/services/mapspec_to_svg.py` | obstacle extraction (line/poly/zones/components) under geometry_v1; authoring feed into label_requests (id → layer/feature identity) | no whole-file refactor |
| `frontend/lib/mapspec-compiler/label-solver.ts` | mirror of solver changes | keep 1:1 with python |
| `frontend/lib/mapspec-compiler/mapspec-to-svg.ts` | mirror of obstacle extraction + authoring feed | |
| `frontend/lib/mapspec-compiler/types.ts` / `types.generated.ts` | add label authoring types (check generation pipeline; hand-additive if generated only at release) | |
| `frontend/lib/mapspec-runtime/runtime.ts` | authoring-aware label sublayer build (+pinned/callout sublayers, evidence events) | label-only fast path preserved |
| `frontend/lib/mapspec-runtime/label-layout.ts` | extend `labelOnlyLayerChange` set with authoring keys | |
| `frontend/lib/mapspec/user-mutation.ts` | +`patchLayerLabelAuthoring` client | |
| `frontend/lib/store/slices/toolSlice.ts` | +`edit_labels` MapToolId | #2032 may touch store slices — single union member |
| `frontend/components/map/map-toolbar-hud.tsx` | +label tool button (SketchToolGroup pattern) | #2032 conflict zone — minimal block |
| `frontend/components/map/map-panel.tsx` | mount `<LabelEditor mapRef={...}/>` (1 line) + click-gating branch | #2032 conflict zone — 1-2 lines |
| `frontend/messages/{en-US,zh-CN}/map.json` | +toolbar/editor strings | |
| `app/services/mapspec/{intents,mutation_registry,mutation_handlers}.py`, `app/schemas/mapspec_mutation_schema.py`, `app/contracts/mapspec_intents.py` | +PatchLayerLabelAuthoringIntent wiring (registry pattern = 4 small additions) | G15 adds its own intents — distinct names |
| `app/tools/__init__.py` or wherever register_* is called | register label tools | |

## Contracts consumed (not modified)

- `apply_gis_mutation` (origin/CAS/idempotency/provenance), mutation registry descriptor semantics.
- `FEATURE_ID_KEYS` + `_wfid` identity (G14 extends additively; G16 resolution accepts WFIDs transparently).
- `content_revision` on sources.
- ToolRegistry `@tool` + `_forward_evidence` ABI; result envelope via mapspec_store adapter.
- Existing diagnostics vocabulary — new codes added via the same factory allowlist (`_render_diagnostic`); check `app/lib/cartography/diagnostics*` for the authoritative list and extend it there.

## Cross-PR compatibility statements

- G15: `frameId` on overrides is optional & only consumed when `layout.frames` exists (G15). Until merge, default frame context None ⇒ frameId-bearing overrides are reported `out_of_scope` in single-frame consumers (honest, not error).
- G14: no dependency on #2075; both raw chain ids and WFIDs work today on master.
- #2032: no theme/token/layout edits; label editor uses existing button/chip styles from toolbar components.
