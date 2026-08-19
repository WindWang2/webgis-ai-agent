# 36. MapSpecRuntime — Declarative Map Reconciliation Engine

Date: 2026-08-02

## Status

Accepted. **Decision 2 (HUD Zustand as source of truth) is superseded by ADR-0054.**
Reconciliation-only boundary and imperative view/actions still stand.

## Context

The frontend had two parallel map-rendering worlds that did not meet:

1. **A declarative compiler** (`frontend/lib/mapspec-compiler/`, ~1140 LOC) emits a MapLibre v8 `style.json` from a `MapSpec` — pure, well-tested, with a CLI and a headless Playwright validator.
2. **An imperative live UI** (`frontend/components/map/map-panel.tsx`, 637 LOC) hand-builds MapLibre sources/layers/paint/filter expressions inline, guarded by **six React refs** (`isUpdatingRef`, `renderLayersRef`, `renderTimeoutRef`, `lastInteractiveIdsRef`, `viewportWriteTimerRef`, `layersRef`) fighting React/MapLibre lifecycle races, plus two `eslint-disable react-hooks/exhaustive-deps` suppressions.

The single bridge between them — `applyMapSpecToMap()` in `lib/map-kit/renderer.ts` — had **zero callers** (grep-confirmed orphan). The view-only `diffLiveStateVsMapSpec()` seed in `compiler.ts` likewise had zero callers. The documented race workaround (`deferredPop`/`poppedRef`/`safePop` in `map-action-handler.tsx`) existed because `export_map`'s `map.once('render')` outlived effect cleanup.

This decision extracts a deep `MapSpecRuntime` module that reconciles a derived `MapSpec` against the live map via minimal diff/patch, replacing the orphaned bridge and the manual ref/effect render machinery.

## Decisions

Reached via `/grilling` (10-question decision tree). Resolutions:

1. **Boundary**: `MapSpecRuntime` owns *reconciliation* only. The action queue and command catalogue (`fly_to`, `export_map`, snapshot) stay imperative — those are one-shot verbs that cannot be diffed.
2. **State source**: `MapSpec` is *derived/ephemeral* via a pure `hudStateToMapSpec` adapter. The HUD Zustand store remains the source of truth; mutation sites are unchanged (mirrors the adapter/shim pattern of ADRs 0033–0035).
3. **Reconcile strategy**: tiered incremental diff/patch. View stays imperative (`flyTo`); layer-set changes are minimal add/remove + `syncLayerZOrder`; paint/filter changes recompile the layer (remove + re-add) rather than diffing individual properties.
4. **Diff home**: the pure `diffSpecs` lives in `mapspec-compiler/reconciler.ts` (alongside `compileMapSpec`, sharing the spec domain model); the MapLibre-effectful patch applier lives in the runtime.
5. **Layout/surface**: new `frontend/lib/mapspec-runtime/` package; `MapSpecRuntime` class with `reconcile(nextSpec)` / `getAppliedSpec()` / `dispose()`.
6. **Migration**: strangler, collapsed to **2 commits, no flag** (commit 1 = runtime + tests, unused; commit 2 = switchover + deletion). The flag was dropped because its committed-immediate deletion provided no real rollback safety over the unit-test net.
7. **Action-queue race**: deferred to a follow-up. Only incidental simplification of `deferredPop`/`safePop` is permitted if the render rewrite removes the need; no new `map.once('render')` teardown is added here.
8. **Testing**: three layers — pure Vitest on `diffSpecs` + `hudStateToMapSpec` (47 cases); mock-Map Vitest on the patch applier's call sequencing (10 cases). A dedicated Playwright integration harness for the runtime is deferred (the existing `runtime-validate.ts` serves the compiler's static HTML, not the React-wired runtime — extending it would require a new bundling step).
9. **Backward compatibility**: `applyMapSpecToMap` and `diffLiveStateVsMapSpec` are deleted outright (zero callers → no shims needed, unlike ADRs 0033–0034 which preserved callers).
10. **ADR number**: 0036.

## Consequences

- **Leverage**: `map-panel.tsx`'s ~225-line render `useEffect` + its 6 refs + the `styledata` re-listen machinery collapse into one `runtime.reconcile(spec)` call. Orphan cleanup is folded into the diff (layers/sources absent from the spec are removed by the patch), eliminating the separate `removeOrphanCustomLayers` calls.
- **Locality**: all MapSpec→MapLibre paint/filter/geometry-fan-out logic lives in the pure `hudStateToMapSpec` adapter; the diff policy lives in `reconciler.ts`; the side-effectful application lives in `runtime.ts`. Three crisp seams.
- **Testability**: the adapter (the load-bearing, behavior-preserving piece) is pinned by 19 unit tests asserting byte-identical MapLibre paint/filter expressions to the inline code it replaces.
- **Reuse**: the runtime reuses `renderer.addGeoJsonSource`/`addImageSource`/`addRasterTileSource` (preserving the F28 image cache-buster and F31 geojson ref-cache optimizations) and `renderer.syncLayerZOrder`.
- **Trade-off**: `hudStateToMapSpec` is non-trivial (it replicates the conditional multi-sublayer fan-out: fill+outline+point+optional extrusion, three heatmap modes, legend range filters). Adapter fidelity is the highest risk; mitigated by heavy tests and commit 2's real-app validation.
