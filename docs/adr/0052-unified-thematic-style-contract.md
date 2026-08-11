# 52. Unified thematic-style contract — `legend_spec` as the single source of truth

Date: 2026-08-12

## Status

Accepted — supersedes [ADR-0007](0007-no-unified-cartographic-style-module.md).

## Context

ADR-0007 deferred a unified cartographic-style module on a single, explicit condition: it
would earn its keep only when `applyMapSpecToMap` gained a **live** caller — i.e. when "the
live map renders from compiled MapSpec." Until then the MapSpec `paint.color` path was
headless-only (Playwright validator, eval scoring, static exporter), so a shared internal
representation would satisfy a *hypothetical* seam, not a real one. That reasoning was
**correct at the time**: with only one live consumer (`legend_spec` → `<ThematicLegend>`),
unifying was speculative.

### What changed — the revisit trigger is now satisfied

[ADR-0036](0036-mapspec-runtime.md) deleted the orphaned `applyMapSpecToMap` (zero callers)
and replaced it with `MapSpecRuntime`, a reconciliation engine that IS the live map path.
`map-panel.tsx` no longer hand-builds MapLibre paint inline; it calls
`hudStateToMapSpec(...)` → `runtime.reconcileAsync(spec)`, and the runtime applies the spec's
`paint` dict straight to MapLibre. The live map now renders from a derived MapSpec — exactly
the condition ADR-0007 named. MapSpec `paint` and the legend are now **two parallel live
consumers** of the same thematic intent.

### Why unification now has real value — the drift is concrete

With two live consumers, the absence of a shared representation stopped being hypothetical
and became a live, **undetectable** bug. Code evidence:

1. `CartographyService.build_thematic_style` (`app/services/cartography_service.py`) computes
   `breaks`/`colors` but **does not modify the GeoJSON** — no `fill_color` is baked into
   features.
2. `create_thematic_map` (`app/tools/cartography.py`) returns the unmodified GeoJSON plus a
   separate `legend_spec`.
3. The frontend SSE handler (`use-sse-stream.ts`) keeps `legend_spec`, sets
   `style: { color: accentColor }`, and **drops** the `style_def` carrying the breaks/colors.
4. The `hudStateToMapSpec` adapter (`frontend/lib/mapspec-runtime/adapter.ts`) painted every
   feature as `["coalesce", ["get", "fill_color"], color||"#16a34a"]`. Features had no
   `fill_color`, so the whole layer rendered as a flat single color.
5. `<ThematicLegend>` (`frontend/components/map/thematic-legend.tsx`) read `legend_spec` and
   rendered a full 5-class graduated palette.

**Net result: a `create_thematic_map` result rendered as a flat green/orange map while the
legend showed a classified palette — maximal drift, and no check could catch it** because
paint and legend were two independent data paths with no common source and no consistency
assertion. This is precisely the "thematic correctness is never asserted" gap ADR-0007
called a *deferred feature*; with MapSpecRuntime live, it became an *architecture flaw*.

Secondary drift sites the same investigation surfaced:
- `palette_colors` computed three different ways for the same palette name (midpoint sampling
  in `build_thematic_style`, verbatim `[:5]` truncation in `h3_binning`, raw slice in
  `kde_contours`).
- `continuous` `legend_spec` omitted `field`, so the vector converter's continuous arm
  silently fell back to constant paint.
- the raster legend `min`/`max` used raw `np.min/np.max` (NaN when nodata present) while the
  PNG render correctly used a finite mask — legend and baked raster disagreed.
- `CartographyService.build_thematic_style` accepted NaN/Inf into classification, poisoning
  quantile/Jenks breaks.
- three independent "field identities": `source.metadata.field` (filter), `legend_spec.field`
  (legend UI), and the hardcoded `fill_color`/`stroke_color` property keys (paint).
- `evaluate_cartography_semantics` (deterministic cartography checks) had **zero production
  callers**; the Harness's `SEMANTIC_VALID` tier was actually structural validity
  (`coordinator.validate`), so a structurally-valid-but-thematically-wrong MapSpec passed.

## Decision

Promote **`legend_spec`** — already the cross-boundary wire format and already a
discriminated union (`graduated | continuous | categorical | divergent`) on both sides — from
"legend-only" to the **canonical thematic style**: the single source of truth for thematic
classification + visual encoding. Both MapSpec `paint` and the legend UI become deterministic
**projections** of the same `legend_spec`.

This is delivered as a **contract + pure helpers**, not a service:

- `app/lib/cartography/thematic_spec.py` — the canonical contract module. It owns:
  - the single classification-result path (delegates the algorithm to
    `CartographyService.classify` — it never reimplements Jenks/quantile);
  - one palette-resolution function (`resolve_thematic_colors`), collapsing the three-way
    divergence;
  - finite/NaN/null filtering at one seam (`finite_numbers`) so nulls cannot poison breaks;
  - the single paint projection (`spec_to_paint`), consumed by BOTH the vector converter and
    the semantic checks;
  - canonical builders (`build_graduated_spec`, `build_continuous_spec`,
    `build_categorical_spec`, `build_divergent_spec`), `normalize_legend_spec` for legacy
    payloads, and `thematic_field` (the single field identity).
- `frontend/lib/mapspec-runtime/thematic-paint.ts` — the frontend mirror
  (`legendSpecToColorExpression`) producing MapLibre-native expressions (the runtime applies
  paint directly, unlike the headless compiler which lowers `StyleMethod` via
  `compileStyleMethod`; both produce byte-identical expressions). Includes a no-data guard so
  null/missing values are diverted to a no-data color instead of being coerced by `to-number`
  into the lowest class.
- The `hudStateToMapSpec` adapter derives `fill-color`/`line-color`/`circle-color` from
  `legendSpecToColorExpression(layer.legend_spec)`; when no thematic spec is present it keeps
  the legacy `fill_color` coalesce + flat fallback byte-for-byte (non-thematic layers
  unchanged). The legend filter range uses `thematicField(legend_spec)` so paint, filter and
  legend share one field identity.
- The vector converter (`analysis_cartography_converter`) delegates paint derivation to
  `spec_to_paint` (single construction site) and now **attaches** `legend_spec` to its output
  layer (previously dropped on the vector path — an asymmetry that made drift undetectable).

### New deterministic semantic checks (`app/lib/cartography/semantic_checks.py`)

`evaluate_cartography_semantics` gains profile-/legend-driven checks, each NOT_EVALUATED when
its evidence is absent (never a fake pass): `LEGEND_STYLE_EQUIVALENCE` (paint field/colors == legend),
`CLASSIFICATION_CARDINALITY` (breaks/categories/labels/colors/paint-stops agree),
`CLASSIFICATION_DOMAIN_COVERAGE`, `CATEGORICAL_DOMAIN_CONSISTENCY`, `NO_DATA_SEMANTICS`,
`DIVERGENT_DOMAIN`, `PALETTE_CARDINALITY`. The `_paint_methods` helper now also recognizes
MapLibre-native `{property,type:"categorical",stops}` paint (the composite-builder form),
closing a categorical blind spot.

### Harness closure

`MapSpecLifecycleEngine` runs `evaluate_cartography_semantics` at commit and attaches the
findings to `MapSpecResult.cartography_findings`; the Pi agent harness surfaces error-severity
findings in `MapSpecValidityEvidence.semantic_errors`. **Tier logic is unchanged**: structural
validity (`is_compiled`) ≠ thematic correctness; the findings are the evidence channel.
Profile-dependent checks report NOT_EVALUATED, so a missing source profile is never recorded
as a thematic pass.

## What stays separate (respecting ADR-0012 / ADR-0017)

This is **not** a God `CartographyService`:

- `CartographyService` remains the canonical classification engine (Fisher-Jenks, quantiles,
  equal-interval, LISA) — ADR-0012. `thematic_spec` *delegates* to `classify`; it does not
  reimplement classification.
- `analysis_cartography_converter` and `raster_cartography_converter` remain separate
  renderers — ADR-0017. They *consume* the contract; they are not merged.
- `COLOR_PALETTES` stays single-sourced in `app/lib/cartography/palettes.py`.

The seam is a **data contract + pure projections**, not a service that owns conversion. The
two converters keep their distinct best-effort/geometry-inference/PNG-rendering
responsibilities; they just read/write the same canonical shape.

## Backward compatibility

- The `legend_spec` wire shape is preserved exactly; new fields (`method`, `labels`,
  `nodata`, `unit`, `title`) are **optional and additive**. `normalize_legend_spec` accepts
  legacy shapes (e.g. `colors` alias, unsorted breaks, `continuous` without `field`).
- Existing producers (`create_thematic_map`, `h3_binning`) are routed through the canonical
  builder; their output `legend_spec` shape is unchanged (tests pin it), only the
  *construction* converges (consistent palette, finite-filtered, no-data aware).
- Non-thematic layers (`apply_layer_style` single-color, plain results) keep the
  `fill_color`/`#16a34a` flat path byte-for-byte; the adapter only overrides color when a
  valid `legend_spec` is present.

## Consequences

- The live map's paint and the legend overlay are now both projections of one
  `legend_spec`, so legend/map color and field drift is **impossible by construction** and
  **detectable by check** (`LEGEND_STYLE_EQUIVALENCE`).
- Thematic paint derivation is O(classes) per layer — it reads `legend_spec`, never scans
  features. (The adapter's pre-existing O(features) geometry introspection is unchanged and
  is not classification.)
- `divergent` is now a first-class mode (contract + builder + projection + `DIVERGENT_DOMAIN`
  check) even though no producer emits it yet, so a future emitter cannot silently drift.
- The contract is polyglot (Python producer, TS consumer). The two projections are kept
  consistent by cross-check tests (`tests/cartography/test_thematic_convergence.py`,
  `frontend/lib/mapspec-runtime/thematic-paint.test.ts`) that assert byte-identical output
  mode-for-mode — the anti-pattern guarded against is two handwritten types that
  *semantically* diverge, not the unavoidable polyglot mirror.

## Relationship to prior ADRs

- **Supersedes ADR-0007** (its revisit trigger is satisfied by ADR-0036).
- **Honors ADR-0012** (`CartographyService` stays the engine; no `CartographicStyle` service
  replaces it — the contract is a data shape + helpers, not a facade).
- **Honors ADR-0017** (the two converters stay separate renderers; not merged into a
  `CartographyEngine`).
- **Honors ADR-0015** (legend emitters still attach `legend_spec` at top level; the contract
  formalizes what that payload means).
- **Builds on ADR-0036** (the live MapSpec path whose existence justifies the contract).
