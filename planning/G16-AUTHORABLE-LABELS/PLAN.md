# PLAN — G16-AUTHORABLE-LABELS

## Architecture decisions (locked after recon)

### A. Override storage — `layer.label.authoring` (per-layer, typed, bounded)

`MapSpecLayerLabel.authoring: Optional[LabelAuthoringConfig]` with `overrides: List[LabelAuthoringOverride]` (cap `MAX_LABEL_OVERRIDES_PER_LAYER = 256`).

Why per-layer nesting (not a spec-level table): identity = (session [MapSpec itself] → layer.id → layer.source implicit → featureId). Cross-layer id mixing impossible by construction; runtime label sublayer builder and export compiler both already read `layer.label` at a single point; reconcile label-only fast path already watches the `label` key. Scale answer: honest typed rejection (`authoring_limit_exceeded`) beyond caps instead of an unbounded inline list or a dangling artifact ref (WP02 allows artifact-ref for large sets; we disclose the boundary instead of shipping an unconsumed ref).

`LabelAuthoringOverride` fields (additive, all optional except target):
- `featureId: StrictStr` — canonical `str(value)` per existing FEATURE_ID_KEYS/`_wfid` contract (`1` vs `"1"` collapse = existing contract; explicit test).
- `idKey: Optional[StrictStr]` — which key resolved it (`_wfid`, `id`, `OBJECTID`, ... ; None = GeoJSON top-level `feature.id`). Needed to re-resolve + to detect stale resolution.
- `hidden / pinned: Optional[bool]`; `text: Optional[str]` (≤120 code points); `callout: Optional[bool]`
- `offset: Optional[{dx, dy}]` — **canonical unit = em** (dimensionless, relative to the label's own font size). Live MapLibre `text-offset` is natively em; export offset_px = em × font_px (= base_size × dpi/72) — DPI-proportional by construction, no absolute screen px persisted, no DPI doubling. Conversions documented in one module (`label_authoring.units`).
- `priority: Optional[int]` (smaller placed first; user priority sorts ahead of auto).
- `origin: "user" | "agent"`; `sourceRevision: Optional[Number]` (content_revision at authoring); `frameId: Optional[StrictStr]` (G15 scope, inert until frames exist); `note: Optional[StrictStr]` (≤80 chars).

`MapLabelConfig` (G16-owned per §D) gains:
- `obstaclePolicy: Optional[Literal["circles_legacy", "geometry_v1"]] = None` (None ⇒ circles_legacy byte-compatible).
- `forbiddenZones: Optional[List[LabelForbiddenZone]]` (≤8 zones, ≤128 verts each) — explicit no-text polygons w/ identity `zone:<id>`.
- `maxPinnedConflicts` disclosure budget not needed — conflicts reported as typed findings, bounded by override cap.

### B. Solver fusion (Python `label_collision.py` + TS twin)

`CollisionLabel` gains optional `hidden`, `pinned`, `offsetEm {dx,dy}`, `priorityUser`, `layerId`, `featureId`. Behavior when all None ⇒ byte-identical legacy (compat lock). With authoring:
1. hidden labels → suppressed `reason="hidden_by_user"` (visible evidence, budget not consumed).
2. pinned labels placed FIRST in (priorityUser ?? 0, id) order at anchor+offsetEm×font_px, box inserted into grid; pinned-pinned/pinned-auto overlap → placement still "placed" but carries `conflict: true` + stats.pinned_conflicts (user wins; conflict surfaced, never silently moved).
3. auto labels then solved with legacy greedy; they must avoid pinned boxes (grid already seeded).
Obstacles gain optional identity `{kind, layerId, featureId}`; a label never blocked by an obstacle of its own (layerId, featureId) — explicit `geometry_v1` policy only. Legacy anonymous boxes ⇒ exact legacy behavior.

### C. Obstacle extraction (geometry_v1) — `mapspec_to_svg.py` + TS `mapspec-to-svg.ts`

- line layers: per-segment AABB widened by resolved `line-width`/2 (paint-resolved, default 1) — never whole-line bbox.
- polygon layers: ring segments as boundary ink (fill NOT an obstacle — 区内标签可放)； explicit forbidden zones cover “禁入面”.
- circles as today; identity attached.
- Page components (legend/north arrow…): component placement rects added as obstacles when geometry_v1 (bounded ≤16).
- Budget: spatial prefilter (viewport clip + grid bucket) then deterministic **interleaved** allocation across layers (round-robin by priority order, cap `MAX_OBSTACLES_PER_SOLVE`), remainder disclosed via stats.obstacles_truncated — no more silent first-200 starvation. Legacy policy keeps first-200 exactly.

### D. Mutation — one new intent

`PatchLayerLabelAuthoringIntent(layer_id, ops: List[LabelAuthoringOp] ≤200, labels_config_patch optional)`:
- op kinds: `set` (merge patch onto override, creating if absent), `reset` (scope: visibility|position|text|all), `remove_zone`/`set_zone` folded into labels_config_patch? — zones go through `labelsConfig` patch on MapLabelConfig (SetLayout already replaces layout; we do a targeted merge in our handler).
- Handler: lock + CAS(expected_revision) + validate (caps, ids resolvable against source when source inline/ref-resolvable; NaN/Inf/oversized text rejected as typed findings, never silently) + user-wins provenance + auto-checkpoint + label-only reconcile.
- Preview: pure `preview_label_authoring(spec, ops)` in `label_authoring.py` (before/after per target, conflicts, non-executable reasons) — same function serves REST preview + Pi tool + FE tests.

### E. Live rendering (runtime.ts)

For layers with authoring:
- auto sublayer `${id}-label`: `text-field` = match(ids → override text, default field); hidden ids excluded via filter match; manual (non-pinned) offsets via `text-offset` match expression (data-driven).
- pinned sublayer `${id}-label-pinned` (only when pinned overrides exist): `text-allow-overlap: true`, fixed offsets, sort-key first — one extra sublayer per authored layer, not per label.
- callout sublayer `${id}-label-callout` (only when callout overrides exist): single GeoJSON source (anchor→label-edge segments) + line layer.
- Stale/orphan overrides dropped from live projection with runtime evidence event (not silently).

### F. Pi tools (new `app/tools/label_authoring_tools.py`)

- `webgis_label_inspect(session_id, layer_id?, feature_refs?)` → bounded override summary + why-not-shown with typed reasons & sources (authoring_policy | solver_predicted | live_observation | not_observed | not_evaluated).
- `webgis_label_author(session_id, layer_id, ops, preview_only?)` → preview or apply via same intent/preview function (agent origin); evidence forwarded.
- `webgis_label_reset(session_id, layer_id, feature_refs, scope)` folded into author (reset ops) — one write tool, honest surface.

### G. UI

- `edit_labels` tool id in toolSlice + toolbar button; `label-editor.tsx` mounted beside SketchEditor.
- Hit: `queryRenderedFeatures` on `${layerId}-label` / `-label-pinned` sublayers; identity via FEATURE_ID_KEYS + properties fallback (maplibre top-level id unreliable — sketch-editor lesson).
- Drag: local preview (setPaintProperty-free: update a transient offset via match expression update on pointermove throttled), commit ONE mutation on pointerup; Escape cancels; arrows nudge ±0.25em (commit on keyup-or-debounce); panel actions for hide/pin/reset/text (input shows “修改标签文字”, not “修改数据”).
- Undo via recordCommand snapshot pattern; refresh persistence via committed spec reconcile.

### H. Units recap

canonical offset unit = em (font-relative). Conversions (single source `label_authoring/units` py + `label-authoring.ts` ts):
- live px = em × font_px_live (font_px_live = label.size live px)
- export units = em × font_px_export; font_px_export = base_size × dpi/72
- rotation/angle degrees unchanged; geographic anchors always lon/lat EPSG:4326 (spec-wide CRS).

## Build order (vertical slice first)

1. WP02 core: `app/lib/cartography/label_authoring.py` (model/resolution/staleness/units/preview/reset semantics + caps) + schema wiring + roundtrip/boundary tests.
2. WP05/07 solver: authoring-aware solve (py+ts twins) + golden corpus + parity fixtures.
3. WP06 obstacles: extraction (py+ts) + identity + budget fairness + zone/components.
4. WP03 mutation intent + handlers + registry + Body + route wiring + user-wins + tests.
5. WP09 live runtime overrides + compiler mirror + FE twin model + mapspec-to-svg.ts wiring.
6. WP10 Pi tools + why-not-shown taxonomy + stub journey test.
7. WP04 UI: toolSlice/toolbar/label-editor/i18n + component tests.
8. WP11 staleness/lineage fixtures + completion projection.
9. WP12 perf/bench + ADR + docs + acceptance matrix run.
10. Independent review → fixes → re-review → PR.
