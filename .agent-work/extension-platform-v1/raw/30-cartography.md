# 30 — Cartography Model/Component Registry, MapSpec, Renderer/Exporter Contracts

Repo: webgis-ai-agent-extension-platform-v1 (read-only investigation). All paths relative to repo root unless noted.

## 1. File inventory

MapSpec domain (session desired-state):
- `app/services/mapspec/store.py` — MapSpec JSON persistence (L155 `MapSpecStore`): atomic disk writes (L79 `_atomic_write_json_sync`), revision snapshots (retention L54), Redis map_state dual-write ordering contract (L10-16). Storage root under `settings.DATA_DIR` (L40-44).
- `app/services/mapspec/lifecycle_engine.py` (1964 L) — `MapSpecLifecycleEngine` (L522); intents InitProject/SetView/UpsertLayer/RemoveLayer/SetLayout (L188-224), PatchComponentIntent (L284), transactional candidate-validate-commit (REL-06/07, L10-15), blocking validation codes (L56-60), 96KB per-component cap (L369), `MapSpecResult` (L51) carrying `cartography_findings` + `cartographic_review` (L74-84).
- `app/services/mapspec/coordinator.py` — `compile_via_cli` (L23): MapSpec → MapLibre style.json + index.html via TS CLI `frontend/lib/mapspec-compiler/cli.ts`; `validate` (L99) pre-compile structural checks.
- `app/services/mapspec/pipeline.py` — layer ingestion (GeoJSON profiling, raster PNG, DataFabric proxy, view calc) `process_layer_ingestion` (L37).
- `app/services/mapspec/checkpoint.py` — physical snapshots/rollback.
- `app/services/mapspec_source.py` — source-shape knowledge (ADR-0008/0050): `store_data` (L31) classifies geojson/raster/data_fabric; inline carrier cap `INLINE_FEATURE_LIMIT=5000` (L24).
- `app/services/mapspec_store.py`, `app/services/mapspec_compile_coordinator.py`, `app/services/mapspec_layer_pipeline.py` — legacy façade/re-export layer.
- `app/services/gis_harness/components.py` — backend component contract: `ComponentType` Literal (L18, 20 types), `Position` (L46), `ComponentPlacement` (L53), `CartographyComponent` (L110) with `to_mapspec()`/`from_legacy()`.

Cartography catalog (`app/lib/cartography/`, ~12k L):
- `component_registry.py` — `MapComponentDescriptor` (L37) + `ComponentRegistry` (L408), 17 seed descriptors (L78-405).
- `component_renderers.py` — renderer/exporter machine-truth matrix `_SUPPORT_MATRIX` (L40), `validate_against_descriptors` (L204).
- `model_library.py` — `MapModel` (L150), `MapModelRegistry` (L483), classification methods (L52-99), `PALETTE_KINDS` (L110), `validate_model_library` (L559).
- `model_packs/` (6 files) + `composition_packs/` (7 files) — domain extension packs (ADR-0101 D1).
- `composition_templates.py` — `MapCompositionTemplate`/`ComponentSlot` (L22/L42), registry (L231).
- `component_templates.py` — per-component style/preset templates (L15), registry (L526).
- `composition_validation.py` — deterministic composition rule execution (L111).
- `themes.py` — `PaletteDescriptor` (L65), `CartographicThemeDescriptor` (L208), `CartographicThemeRegistry` (L435).
- `palettes.py` — `COLOR_PALETTES` (L6), WCAG contrast utils (L51-76), CIEDE2000 (L320), heatmap paints (L184).
- `semantic_checks.py` (2634 L) — deterministic cartographic QA; entry `evaluate_cartography_semantics` (L1558).
- `quality_loop.py` — `review_cartography` (L490), `review_and_repair_cartography` (L531), fingerprinting (L252).
- `layout_solver.py` — deterministic slot solver V2/V3 (L1-50 doc), page profiles.
- `layout_constraints.py` — zone capacity/exclusive tables, collision/orphan detection.
- `export_component_catalog.py` — exports backend catalog to `frontend/lib/map-components/component-catalog.generated.json` (L7-13, `build_catalog` L33).
- `thematic_spec.py` — canonical `legend_spec` contract (ADR-0078), `spec_to_paint` (L14-47 doc).
- `pdf_renderer.py` — Matplotlib A4 PDF engine (`generate_map_pdf` L35).
- `chart_kinds.py`, `bivariate.py`, `dot_density.py`, `heatmap_contract.py`, `isoline_model.py`, `extrusion_model.py`, `label_engine.py`, `classify.py`, `visualization_plan.py`, `runtime_repair.py`, `component_taxonomy.py`, `catalog_docs.py`, `verdict_summary.py`, `design_system.py`, `raster_render.py`.

Services/tools:
- `app/services/cartography_service.py` — `CartographyService` (L13): classify delegation (L17-28), `build_thematic_style` (L36); classification algorithms live in `app/lib/cartography/classify.py` (ADR-0012 keeps it as engine).
- `app/services/cartography_runtime.py` — session evaluation harnesses: `evaluate_cartographic_session` (L427, desired vs observed), `_advance_runtime_cartographic_repair` (L632); harness registry (L43-50).
- `app/services/analysis_cartography_converter.py` / `raster_cartography_converter.py` — analysis/raster → MapSpec layer renderers (kept separate per ADR-0017); default paints (L28-34).
- `app/services/mvt.py` — MVT 2.1 tile encoder (vector tile serving, ADR-0047).
- `app/services/mapspec_to_svg.py` — Python SVG compiler `compile_mapspec_to_svg` (L285), byte-parity twin of `frontend/lib/mapspec-compiler/mapspec-to-svg.ts` (fmt parity L34-52), for WeasyPrint PDF reports.
- `app/tools/cartography_tools.py` — `register_mapspec_cartography_tools` (L201): webgis_project_init/state_get/view_set/layer_upsert (L393)/layout_set (L664)/validate (L715)/compile_maplibre (L734)/checkpoint (L756)/rollback (L780)/runtime_validate (L806)/cartography_status (L829).
- `app/tools/cartography.py` — `register_cartography_tools` (L96): apply_layer_style, create_thematic_map, export_map (`ExportMapArgs` L44), export_batch_maps (L57), control_floating_chart (L77).
- Frontend schema truth: `frontend/lib/mapspec-compiler/types.ts` — `MapSpec` (L222), `MapSpecLayer` (L110), sources (L50-82), `MapSpecComponent` (L169), `MapSpecLayoutConfig` (L214); `frontend/lib/map-kit/exporter.ts` (canvas/PNG/PDF/SVG exporter), `frontend/lib/map-components/registry.ts` (live renderers).

## 2. MapSpec schema

MapSpec is a plain JSON dict on the backend — no Pydantic model. Authoritative schema is the TS interface `frontend/lib/mapspec-compiler/types.ts:222`:
- `version: string`; `view?: MapSpecView` (center/zoom/pitch/bearing, L129); `sources: Record<string, MapSpecSource>` (geojson L50 | vector/MVT L63 | raster imageRef+bounds L72); `layers: MapSpecLayer[]` (L110: id/source/type union circle|line|fill|symbol|heatmap|raster|fill-extrusion/paint StyleMethod/layout/label/filter/sourceLayer/cluster); `layout?: MapSpecLayoutConfig` (L214: legend/controls/margins/`components?: MapSpecComponent[]`); `thresholds` (maxFeatures/timeoutMs).
- Paint values are `StyleMethod` discriminated union (constant/interpolate/step/match/field, types.ts:1-37).
- `MapSpecComponent` (types.ts:169) mirrors backend `CartographyComponent` (`app/services/gis_harness/components.py:110`): id/type/enabled/position/priority/style/options/compatibility/variant/placement/templateId/schemaVersion.
- Source-shape rules enforced backend-side: `app/services/mapspec_source.py:31-136` (raster vs data_fabric vs ref: vs inline; inline cap L77-84).
- Layers also carry top-level `legend_spec` (canonical thematic contract, `app/lib/cartography/thematic_spec.py:14-47`) — single source for paint + legend UI (ADR-0078).
- MapSpec is the "single desired cartographic state" (types.ts:166-168 comment; ADR-0054/0056/0057 in docs/adr).

## 3. Registries (components, models, variants, palettes)

Mechanism: module-level singleton registries, `load_builtins()` on first `get_*_registry()`, `reset_*_registry()` for tests. All are in-process, seed-data + optional pack augmentation; no external plugin loader, no persistence.

- Components: `component_registry.py:426-436` `register()` — **collision = raise** `ValueError("duplicate component descriptor id")` (L427-428); indexes by id/type/category(+prefix); `_version` generation counter for derived-cache invalidation (L414, 438-440). Descriptor fields (L37-75): id/category/type/placement_domain/supported_outputs/compatible_map_models/compatible_artifact_types/required_context/renderer_support/exporter_support/default_variant+variants/default_position+allowed_positions/cardinality/dependencies/conflicts/requires_layer_binding/priority/runtime_status(`native|planned|unavailable` L12)/tags + V4 additions: states/size_range/collision_class(`chrome|legend|panel|canvas|none` L17)/responsive/interactions/accessibility (L29-34: role, label_zh, keyboard_operable, contrast_checked).
- Map models: `model_library.py:483-536`. `register()` (L504-507) **silently ignores duplicate ids**; packs duplicate → raise in `load_builtins` (L498-502). Alias collision = first-wins + warning log (L509-515). Fields (L150-199): maplibre_layer_type, classification, default_palette, deck_gl/kepler/qgis cross-references, `runtime_status: native|planned` (L169), aliases, pitfalls_zh, accepted_artifact_types, recommended_components, `fallback_model_id` degradation chain (L187), default_theme, chart_needs, interaction_needs, data_preconditions_zh. `resolve()` = alias-aware O(1) (L517-519).
- Variants: plain string lists on descriptors (e.g. legend variants L132-137); component templates must have `variant` in descriptor's list when native (`component_templates.py:585-594`); chart kind variants via `chart_kinds.py` vocab.
- Palettes/themes: `palettes.py:6` `COLOR_PALETTES` + `NATIVE_HEATMAP_COLORS` (L141) are raw hex tables; semantic families in `PALETTE_KINDS` (`model_library.py:110-142`, colorblind_safe flags); rich descriptors derived in `themes.py:106` `build_palette_descriptors()`; `CartographicThemeRegistry` (L435) load is **dict-comprehension last-wins, no collision check** (L443-447).
- Packs: `model_packs/` + `composition_packs/` loaded deterministically after seeds (`model_library.py:495-502`; `composition_templates.py:240-246`), dup id = raise. `model_packs/_base.py:11-15` `FRONTEND_RUNTIME_LAYER_TYPES` is the machine truth gating native vs planned.
- Registry self-validation (fail-closed): `ComponentRegistry.validate` (component_registry.py:493-536) — unknown category, default_variant not in variants, dangling dependencies/conflicts, `compatible_map_models` must resolve, and renderer/exporter support must match the truth matrix (`component_renderers.py:204-239`, drift = issue). `validate_model_library` (model_library.py:559-629): Style-Spec enum membership, palette/classifier existence, fallback chain resolvable + no self-loop, chart_needs/theme vocab, native layers must be in frontend runtime set (L612-622), planned models must register pitfalls (L623-624).
- Cross-boundary export: `export_component_catalog.py:33-133` builds the frontend contract JSON (schemaVersion 4) — componentTypes + rendererSupport/exporterSupport + palettes + themes + chart kinds/states/operations; parity test `tests/unit/test_component_catalog_parity.py`.

## 4. Composition/validation pipeline and renderer/exporter contracts

Mutation pipeline: tool (webgis_*) → `MapSpecStore` (`app/services/mapspec/store.py:155`) → `MapSpecLifecycleEngine` (lifecycle_engine.py:522): build in-memory candidate → structural validate (`app/services/mapspec/coordinator.py:99`) → blocking codes INVALID_SOURCE_REF / INVALID_STOPS_COUNT / NON_INCREASING_STOPS reject the mutation (lifecycle_engine.py:56-60) → checkpoint → atomic save (disk then Redis, store.py:10-16) → returns `MapSpecResult` with `cartography_findings` + `cartographic_review` (L74-84, ADR-0078: structural validity ≠ thematic correctness).

Component composition: planner composes instances (`app/services/gis_harness/planner.py:876-887` composer + layer role→model map) → `validate_component_composition` (`composition_validation.py:111-260`): composition-template slots required/forbidden/max_count (L142-172); planned/unavailable component present = error (L181-191); descriptor.conflicts pair (L192-202); dependencies missing (L203-209); allowed_positions (L210-216); output_target support (L217-222); model↔component compatibility per bound layer (`compatible_map_models`, L228-238); binding-level legend-family conflicts per layerId (L66-108, L240-246); zone collisions = warning; orphan layerId = error (L248-256). Errors → `build_default_components` fallback with evidence (planner.py:903-931).

Desired-state quality loop: `evaluate_cartography_semantics` (`semantic_checks.py:1558`) — deterministic checks (paint↔legend equivalence, classification integrity, visual-variable overload L1198, CRS, no-data…), embedded source profiles only, never fake passes → `review_and_repair_cartography` (`quality_loop.py:531`): bounded auto-repair (auto_safe / auto_with_semantic_risk repairability, L289-421), fingerprint-guarded. Runtime convergence: `cartography_runtime.py:427` evaluates desired MapSpec vs observed frontend snapshot; repair advance L632.

Renderer/exporter contracts (multi-target):
- Live interactive: frontend `map-components/registry.ts` renderers; contract = `MapSpec.layout.components` (types.ts:169-212); truth matrix `component_renderers.py:40-178` (LIVE_TARGET="interactive" L27).
- MapLibre compile: `coordinator.py:23` TS CLI → style.json + html + compile-report (errors/warnings/stats); frontend `reconciler.ts` runtime.
- SVG vector: `mapspec_to_svg.py:285` Python compile target, byte-parity twin with `frontend/lib/mapspec-compiler/mapspec-to-svg.ts` (attribute-injection escaping L12-20, number parity L34-52, DPI oversample boost L71-84) — used by WeasyPrint PDF reports.
- PNG/PDF canvas export: agent tool `export_map` (`app/tools/cartography.py:44-70`) drives the frontend exporter (`frontend/lib/map-kit/exporter.ts`, per matrix notes in component_renderers.py:44-110); formats png/pdf/svg; `pdf_renderer.py:35` A4 matplotlib path for report PDFs; `export_layout` component supplies paper/orientation/dpi (component_registry.py:311-322).
- MVT tiles: `app/services/mvt.py` serves `/api/v1/layers/data/{ref}/tiles/` for vector sources (types.ts:63-70).
- Export honesty/degradation: planned components must not appear in final maps (composition_validation.py:181); SVG export of 3D honestly degrades with `export_degraded=true` (model_library.py:456); support matrices only describe "implemented" (component_renderers.py:18-19).

## 5. Accessibility semantics

- `ComponentAccessibility` (`component_registry.py:29-34`): role (img/heading/text/note/group/table/dialog), label_zh, keyboard_operable, contrast_checked; set per descriptor (e.g. L88, 198, 320-321 export_layout keyboard_operable=False).
- Color safety: `colorblind_safe` per palette family (model_library.py:110-142); WCAG contrast + print-safety derivation (`palettes.py:51-76`, `themes.py:92-104`); theme validation enforces colorblind-safe-first/print constraints (`themes.py:504-527`); perceptual-ramp + CIEDE2000 min-delta (`palettes.py:320,393,406`).
- QA semantics: Bertin visual-variable overload check (`semantic_checks.py:1198-1273`); disclosure components (methodology_note / uncertainty_panel / decision_panel, component_registry.py:359-404) are "methodological honesty" product surfaces.

## 6. Extension seams and gaps for a Cartography Extension SDK

Seams that already exist:
- Registry `register()` APIs are public (`component_registry.py:426`, `model_library.py:504`, `composition_templates.py:247`, `component_templates.py:539`) and packs pattern (`model_packs/`, `composition_packs/`) demonstrates a deterministic seed+pack load order with fail-loud collisions.
- Catalog export (`export_component_catalog.py`) gives a versioned (schemaVersion) machine contract to the frontend; `registry_version()` (component_registry.py:438) exists for cache invalidation.
- Truth-matrix cross-validation (`component_renderers.py:204`) + per-registry `validate()` make "descriptor must not lie" testable — a natural SDK conformance hook.
- `runtime_status=planned` + `fallback_model_id` degradation chain is a first-class extension lane (register schema now, renderer later).

Gaps for an SDK:
1. No external plugin loader: all registries are hard-coded Python seeds + in-repo packs; no manifest/entry-point discovery, no namespacing of third-party ids (collision policy = raise for components/composition templates, silent-ignore for models registered via `register()`, last-wins for themes — inconsistent).
2. `ComponentType` is a closed Literal (`components.py:18-43`) mirrored in TS (types.ts:171-201) and in `_SUPPORT_MATRIX` reverse-coverage check (component_renderers.py:234-238) — adding a component type touches 4+ files (backend Literal, TS union, support matrix, descriptor); no single-source codegen for the union.
3. Renderer/exporter registration is declarative only: the "matrix" records support but there is no backend-side renderer plugin interface — live renderers and canvas exporters live in frontend TS; an SDK extension must ship frontend code and update the matrix + catalog by hand.
4. Composition compatibility is list-based (`compatible_map_models` explicit lists, e.g. component_registry.py:113-129) — O(lists) maintenance and easy to miss new models; no rule DSL or predicate registration.
5. Accessibility is metadata-only (role/label flags); no automated contrast/aria verification pipeline beyond `contrast_checked` boolean and palette-level WCAG math.
6. `MapSpec` has no backend Pydantic schema — validation is TS (`runtime-validate.ts`) + structural checks (`coordinator.py:99`); third-party component `options` payloads are free-form dicts (`CartographyComponent.options`, components.py:124) with only a 96KB size gate (lifecycle_engine.py:369); a per-type options schema registry does not exist.
7. Registry state is per-process singletons — multi-worker consistency relies on identical seeds; no dynamic registration API over the wire, no unload/revoke path (only module-level `reset_*` for tests).
