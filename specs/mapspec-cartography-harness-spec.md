# Spec: MapSpec Cartography Harness (MapSpec 制图 Harness)

Triages the `pi-webgis-harness` v0.2.0 design document into an in-place refactor of the existing
Python(FastAPI) + Next.js WebGIS agent. Establishes a declarative **MapSpec** cartographic-intent
layer, a deterministic **MapSpec Compiler**, a headless **Runtime Validator**, and a hard-migrated
`webgis_*` tool catalog — coexisting with Redis `map_state` under a dual-write contract.

- Resolves: the design doc described a greenfield TS/npm package that does not match this repo.
- Respects ADR-001 (Fetch-on-Demand / `ref_id`), ADR-006 (Unified Tool Dispatch).
- Glossary for all terms below is in `CONTEXT.md` (MapSpec, Cartographic Intent vs. Runtime State,
  MapSpec Compiler, Spatial Meta Profile, Checkpoint, Tool Catalog (webgis_*), Runtime Validator,
  Eval Evidence).

## Problem Statement

Today, turning a natural-language cartography request ("make an earthquake distribution map;
magnitude controls point size, depth controls color") into a verified, replayable web map is a
high-hallucination, free-form coding task. The Agent emits ad-hoc MapLibre expressions and tool
calls against the existing `ToolRegistry`; nothing structurally guarantees that the result is
spatially correct, that the canvas is non-blank, or that the run can be diffed/rolled back. There
is no single declarative description of *what the map should be*, no deterministic compile step,
and no headless runtime acceptance — so the Agent's only feedback loop is the LLM's own prose.

From the user's perspective: map requests succeed or fail opaquely, manual UI tweaks silently
conflict with later Agent edits, and there is no way to compare two map attempts or revert a bad
one.

## Solution

Introduce a **dual-write** cartography architecture inside the existing agent:

1. A declarative **MapSpec** document holds *cartographic intent* — high-level style methods
   (`constant` / `interpolate` / `step` / `match` / `field`), layout (legend, controls, margins),
   view, and runtime thresholds. MapSpec is **authoritative**: data flows one way,
   Agent → MapSpec → MapSpec Compiler → `map_state` (Runtime State).
2. A deterministic **MapSpec Compiler** (framework-agnostic TS, shared by the live `map-kit` and
   the headless validator) compiles MapSpec → MapLibre `style.json` + `index.html`.
3. A headless **Runtime Validator** serves the compiled output statically and drives Playwright to
   verify `mapLoaded`, `mapIdle`, no console/page/network errors, non-blank canvas, and control
   layout — emitting PNG/trace/`report.json`.
4. The existing ~30 legacy tools are **hard-migrated** into an 11-tool `webgis_*` catalog, with a
   single legacy→canonical name table at the `ToolDispatchService` entry so Pi bridge, history
   replay, and tests cross one normalization boundary.
5. **Eval evidence** (MapSpec revisions, profiles, style.json, HTML, PNG, trace, report.json,
   cost) is auto-captured and scored on 5 computable dimensions; cartographic quality is deferred.

MapSpec layers reference data by `ref_id` at runtime (Fetch-on-Demand preserved); only at
**checkpoint** is the payload behind each ref materialized, so a checkpoint is self-contained and
replayable.

## User Stories

### MapSpec as cartographic intent
1. As the Agent, I want a single MapSpec document describing view/layers/style/layout/legend, so
   that every cartography decision lives in one declarative, diffable place.
2. As the Agent, I want MapSpec to use high-level style methods (`constant`/`interpolate`/
   `step`/`match`/`field`) instead of raw MapLibre expression arrays, so that I do not produce
   malformed nested expressions.
3. As a developer, I want MapSpec to be a versioned document, so that I can diff, roll back, and
   replay map states.
4. As the Agent, I want a `webgis_project_init` tool that initializes a fresh MapSpec with runtime
   thresholds, so that each cartography task starts from a known baseline.
5. As the Agent, I want a `webgis_state_get` tool that reads the current MapSpec and its meta
   profiles, so that I can ground the next edit in the actual state.

### Dual-write contract (MapSpec ↔ map_state)
6. As the system, I want every Agent cartography edit to write MapSpec (intent) *and* compile it
   into `map_state` (runtime), so that the live frontend renders from runtime state while intent
   stays declarative.
7. As the Agent, I want MapSpec to be the single authority, so that recompiling from MapSpec is
   always idempotent and reproduces the same runtime state.
8. As a user, when I manually adjust a layer's paint or viewport in the live UI, I want the
   frontend to warn me that this edit is transient and will be lost on the next Agent operation,
   so that silent data loss does not occur.
9. As a user, when I see the transient-edit warning, I want a "保留 (Keep)" action that calls
   `webgis_layer_upsert` to write my manual edit back into MapSpec, so that it persists.
10. As a developer, I want a pure function that diffs live `map_state` against the compiled
    MapSpec, so that the divergence detector has no side effects and is unit-testable.

### Spatial Meta Profile
11. As the Agent, I want `webgis_source_profile` to dissect a GeoJSON source into BBOX, suggested
    view, CRS, feature count, geometry types, field names/types/sample-values, and numeric field
    min/max/mean/histogram, so that I do not blind-guess breaks, palettes, or viewport.
12. As the Agent, I want the first dissectable layer to auto-write `view.center`/`view.zoom` on
    its first upsert (only when the view has not been explicitly set), so that the map frames the
    data without an extra manual step.
13. As the Agent, I want `webgis_layer_upsert` to auto-profile the source and inject the profile
    into MapSpec, so that profiling is not a separate manual step for the common case.
14. As a user, I want to be able to override the auto-injected view via `webgis_view_set`, so that
    auto-injection never traps me in a wrong frame.

### MapSpec Compiler (deterministic)
15. As the Agent, I want a deterministic compiler that turns a MapSpec into MapLibre `style.json`
    + `index.html`, so that "what this MapSpec renders to" has a single, repeatable answer.
16. As a developer, I want the compiler to be a framework-agnostic TS module imported by both the
    live `map-kit` and the headless validator, so that the two never drift on style semantics.
17. As the Agent, I want `webgis_compile_maplibre` to emit `style.json`, `index.html`, and a
    `compile-report.json`, so that compile success/failure is structured evidence, not prose.
18. As a developer, I want the compiler to handle `interpolate`→`interpolate+to-number+get`,
    `step`→`step+to-number+get`, and `match`→`match+get` mapping deterministically, so that the
    Agent never hand-writes these expressions.
19. As a developer, I want the compiler to split labels into an independent symbol layer and
    generate the legend, so that paint and label concerns stay separate and reproducible.
20. As a developer, I want compile to reject (with a structured report) invalid `stops` (e.g.
    non-strictly-increasing, fewer than two), unknown fields, and missing sources, so that errors
    surface before the browser ever runs.

### Tool catalog hard-migration
21. As the Agent, I want the 11 `webgis_*` tools to be the canonical tool names, so that the
    catalog is small, semantic, and matches the documented workflow.
22. As a developer, I want legacy tool names (`add_layer`, `set_view`, etc.) to route through a
    single `old→new` normalization table at `ToolDispatchService`'s entry, so that Pi bridge,
    history replay, and tests cross one boundary.
23. As a developer, I want stored conversation history containing legacy tool names to be
    translated through the table on replay, so that old sessions remain replayable after the
    hard migration.
24. As a developer, I want tier/domain tagging and `ToolDispatchService` dispatch (ADR-006) to
    remain unchanged in behavior, so that the migration only renames, not re-architects.

### Runtime Validator
25. As the Agent, I want `webgis_runtime_validate` to recompile the current MapSpec, serve the
    output statically, and drive headless Playwright, so that acceptance is self-contained and
    reproducible.
26. As a developer, I want the validator to assert `mapLoaded` + `mapIdle` within timeout, so that
    a half-loaded map never passes as "done".
27. As a developer, I want the validator to collect console errors, page errors, failed requests,
    and HTTP 4xx/5xx, so that silent runtime failures are caught.
28. As a developer, I want the validator to decode the canvas PNG and compute transparent ratio,
    dominant-color ratio, and luminance std-dev, so that a blank/near-blank map fails acceptance.
29. As a developer, I want the validator to detect `[data-webgis-control]` / `.maplibregl-ctrl`
    overflow and inter-control collisions, so that legends/scalebars/navigation don't overlap.
30. As a developer, I want the validator to write `report.json`, `map.png`, and `trace.zip` on
    every run, so that even a fatal failure (missing browser, navigation error) leaves a trace via
    `fatalError`/`pageErrors`.

### Checkpoint & replay
31. As the Agent, I want `webgis_checkpoint` to snapshot the current MapSpec *and* materialize the
    payload behind every `ref_id` it references into the snapshot directory, so that the snapshot
    is self-contained and replayable without the live Redis store.
32. As a user, I want to be able to roll back to a prior checkpoint, so that a bad cartography
    attempt can be undone.
33. As a developer, I want checkpoints to keep `ref_id` references intact at runtime while only
    materializing payloads at snapshot time, so that Fetch-on-Demand (ADR-001) is preserved.

### Validation layering & eval evidence
34. As the Agent, I want `webgis_validate` to check CRS, field existence, `stops` validity, view
    sanity, and source references *before* compile, so that the cheap deterministic layer rejects
    bad input early.
35. As a reviewer, I want every run to persist MapSpec revisions, meta profiles, style.json,
    index.html, PNG, trace, report.json, and cost stats, so that evaluation is evidence-based.
36. As a reviewer, I want a score on 5 computable dimensions (spatial/data 25%, task completion
    20%, browser runtime 15%, traceability/safety/repro 10%, tool-call efficiency/cost 10%), so
    that runs are comparable.
37. As a reviewer, I want the cartographic-quality dimension (20%) to be explicitly marked
    "pending visual-judge" and skipped, with scores normalized to an 80% max until it exists, so
    that the gap is visible rather than papered over.

### Safety boundaries
38. As an operator, I want local spatial data to be loadable only from within the project
    directory, so that the Agent cannot read arbitrary filesystem paths.
39. As an operator, I want every data source to require an explicit `url` or `dataPath`, so that
    no implicit data source is ever honored.
40. As an operator, I want the compiler and validator to never mutate source geometry, so that
    input data integrity is guaranteed.

## Implementation Decisions

### Architecture / data flow
- **Dual-write, MapSpec authoritative.** Two cooperating sources: Runtime State (`map_state` in
  `SessionStore`, Redis — *what renders*) and Cartographic Intent (MapSpec — *what it should be*).
  Flow is one-way: Agent → MapSpec → MapSpec Compiler → `map_state`. Live-UI edits write
  `map_state` directly and are **transient** (not back-synced); they persist only via an explicit
  `webgis_layer_upsert` ("保留" button).
- **MapSpec storage.** A versioned MapSpec document per session under `.webgis-agent/` (intent +
  revisions). `map_state` remains in Redis per ADR-004. The two are reconciled by recompiling
  MapSpec → `map_state`; never by reverse-compiling `map_state` → MapSpec.
- **ref_id at runtime, materialized at checkpoint.** MapSpec layers reference data by `ref_id`
  (ADR-001 preserved). `webgis_checkpoint` copies the payload behind each referenced `ref_id` into
  the snapshot dir so the checkpoint is self-contained.

### Modules
- **MapSpec Compiler** — new framework-agnostic TS module (`frontend/lib/mapspec-compiler/`).
  Pure `compile(mapspec) → { style, html, report }`. Imported by both `map-kit` (live) and the
  Node validator (headless). **GeoJSON sources only** this refactor.
- **Spatial Meta Profiler** — new TS profiler producing a Spatial Meta Profile (GeoJSON only).
  Auto-injected by `webgis_layer_upsert`; first dissectable layer auto-writes `view.center`/`zoom`
  when view is unset; overridable via `webgis_view_set`.
- **Live-state divergence detector** — pure TS `diffLiveStateVsMapSpec()` feeding the frontend
  "transient edit" warning + "保留" button.
- **MapSpec store + orchestration** — new Python seam coordinating MapSpec writes, dual-write into
  `map_state` via the compiler output, checkpoint materialization, and profile injection.
- **Runtime Validator** — new Node/Playwright script: static server over compiled output, headless
  Chromium, canvas/control/error checks, emits `report.json`/`map.png`/`trace.zip`.

### Tool catalog hard-migration
- The 11 `webgis_*` tools are canonical: `webgis_project_init`, `webgis_state_get`,
  `webgis_source_profile`, `webgis_view_set`, `webgis_layer_upsert`, `webgis_layout_set`,
  `webgis_validate`, `webgis_compile_maplibre`, `webgis_runtime_validate`, `webgis_checkpoint`,
  (one additional per the documented 11 — finalize name during impl, e.g. a layer-removal tool).
- Existing `app/tools/cartography.py`, `layer_manager.py`, `meta_tools.py`, `map_view.py`,
  `templates.py` are consolidated/renamed into the `webgis_*` catalog.
- **Hard migration, no aliases.** A central `old→new` name table lives at the
  `ToolDispatchService` entry (ADR-006's single chokepoint). All entry paths — Pi bridge, legacy
  ChatEngine, history replay, tests — normalize there. Stored history with legacy names is
  translated through the table on replay.
- Tier/domain tagging, `ToolDispatchResult` discriminant, Fetch-on-Demand, and self-heal hints
  (ADR-006) are unchanged; only tool names and the consolidation change.

### High-level style contract (MapSpec → MapLibre)
The compiler maps MapSpec high-level style to native expressions — the Agent never writes the
nested arrays:

| MapSpec                                          | MapLibre                                  |
|--------------------------------------------------|-------------------------------------------|
| `paint.color` + `type=circle`                    | `circle-color`                            |
| `paint.radius`                                   | `circle-radius` / `heatmap-radius`        |
| `paint.width` + `type=line`                      | `line-width`                              |
| `paint.color` + `type=fill`                      | `fill-color`                              |
| `label`                                          | independent `symbol` layer                |
| `interpolate`                                    | `interpolate` + `to-number` + `get`       |
| `step`                                           | `step` + `to-number` + `get`              |
| `match`                                          | `match` + `get`                           |

Stops must be strictly increasing and ≥2; invalid input is rejected by `webgis_validate`
(pre-compile) and again by the compiler's `report.json`.

### Runtime validator pass/fail contract
Pass requires: `mapLoaded=true`; `mapIdle` before timeout; no console/page errors; no failed
requests or HTTP 4xx/5xx; canvas captured and not fully-transparent / not near-monochrome;
controls within viewport; no control collisions. Canvas-blank is a *risk signal* (a legitimate
single-color map can false-positive) — a blank-canvas failure may not be claimed as "runtime OK,"
but a pass still requires human cartographic review before release.

### Eval scoring (5 dimensions, this refactor)
Spatial/data correctness 25%, task completion 20%, browser runtime 15%, traceability/safety/
reproducibility 10%, tool-call efficiency/cost 10% = 80% max. Cartographic quality (20%) is
deferred pending a future `webgis-visual-judge`; until then scores normalize to 80%.

## Testing Decisions

### Testing philosophy
Only test external behavior, not implementation details. Prefer the highest seam possible; the
fewer seams, the better. This feature crosses a TS/Python language boundary, so a single seam is
infeasible — three seams, each at the highest practical point.

### Seam A — MapSpec Compiler as a pure function (TS unit, new)
Assert `compile(mapspec) → { style, html, report }` exactly for fixture MapSpecs covering each
style method, the style→MapLibre mapping table, label-layer split, legend generation, and
rejection of invalid stops/fields/sources. Also covers `diffLiveStateVsMapSpec()` as a pure unit.
Lives alongside `frontend/lib/map-kit/*.test.ts` (vitest). This seam *is* Decision 4's
determinism contract — no browser needed.

### Seam B — `webgis_*` dispatch through `ToolDispatchService` (Python, existing seam)
The single chokepoint from ADR-006, already tested by `tests/test_tool_registry.py` and
`tests/unit/test_cartography.py`. Extend it (do not add per-tool seams) to assert:
- legacy→`webgis_*` name normalization for every legacy tool name (Decision 7);
- dual-write producing both MapSpec intent + `map_state` runtime from one tool call
  (Decisions 2/11);
- `webgis_checkpoint` materializing ref payloads into the snapshot dir (Decision 3);
- first-layer auto-view injection when view unset, and `webgis_view_set` override (Decision 13);
- `webgis_validate` rejecting invalid stops/fields/sources pre-compile (Decision 34).
Stored-history replay translation (legacy names) is asserted through this same seam.

### Seam C — Runtime validator over a fixture MapSpec (Node/Playwright, new, opt-in)
Drive the validator over a known-good and a known-blank fixture MapSpec → assert `report.json`
shape, `mapLoaded`/`mapIdle`, canvas-not-blank, control-overflow/collision fields, and 5-dimension
score determinism (Decisions 8/10). Expensive (Chromium); gated behind a marker mirroring the
existing pytest `heavy` marker so the default suite stays fast. Prior art: the existing
`heavy` marker convention and `tests/benchmarks/`.

### What is NOT tested at a new seam
- No per-module Python seams under the orchestration layer — behavior is covered at Seam B.
- No component-level React test for the "保留" button — it is a thin view over the pure
  `diffLiveStateVsMapSpec()` (Seam A) calling `webgis_layer_upsert` (Seam B).
- No live-Next.js end-to-end regression (out of scope per Decision 8).

## Out of Scope

- **PMTiles** profiling/protocol injection (deferred to "后续 Adapter").
- **OpenLayers, Cesium** renderer adapters; **PostGIS / OGC-API** profilers; **GDAL/PostGIS/QGIS
  spatial validator**; **visual-judge** (the deferred 20% dimension). All in the "后续 Adapter"
  queue.
- **Live Next.js regression testing.** The Runtime Validator targets compiled static output only.
- **Back-sync of live-UI edits to MapSpec.** MapSpec is authoritative; UI edits are transient by
  design (persist only via explicit `webgis_layer_upsert`).
- **Multi-worker deployment** of the Pi bridge result cache (a known ADR-006 follow-up, untouched).
- **Migration of `map_state` out of Redis.** Redis remains the runtime-state store (ADR-004).

## Further Notes

- The original `pi-webgis-harness` v0.2.0 design doc described a greenfield standalone TS/npm
  package (`pi install -l`, `npm install`, `src/*.ts`). That framing does not match this repo
  (Python + Next.js, Pi already integrated as a subprocess via `agent_pi_bridge.py`). This spec
  absorbs the doc's *concepts* (MapSpec, compiler, validator, eval) into the existing agent rather
  than building the standalone package. The doc's TS source filenames (`metadata.ts`,
  `maplibre-compiler.ts`) map to TS modules under `frontend/lib/mapspec-compiler/` here.
- Decisions 1–13 (the grilling record) and the 7 new `CONTEXT.md` glossary entries are the
  authoritative basis for this spec; if this spec and the glossary disagree, the glossary wins.
- The documented execution chain
  (`profile → layer_upsert → layout_set → validate → compile → runtime_validate → checkpoint`)
  is preserved as the Agent's recommended workflow.
