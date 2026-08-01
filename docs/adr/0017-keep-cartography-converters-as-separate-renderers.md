# Keep cartography converters as separate renderers — do not merge into CartographyEngine

**Status:** accepted

We will **not** merge `analysis_cartography_converter` and `raster_cartography_converter`
into a unified `CartographyEngine` (a deepened `CartographyService`). The engine stays the
legend producer; the two converters stay separate renderers.

## Context

Architecture-review Candidate #2 (2026-08-01 report) proposed unifying vector & raster
cartography converters into a single `CartographyEngine`, claiming: "Cartography logic is
split across three converters; `raster_cartography_converter` duplicates RGB color
interpolation math instead of delegating to `CartographyService`."

This is a variant of the consolidation ADR-0012 already rejected. ADR-0012 rejected
*deleting* `CartographyService` (framed as a "shallow facade over a non-existent
`CartographicStyle`"). This report re-skinned the same idea as *merging the converters
into* the engine. A code investigation found the alleged duplication does not exist.

## What the investigation found

### 1. The "duplicated RGB math" is two different mechanisms

**`CartographyService.get_color_from_palette`** (`cartography_service.py:21-29`) is a
**discrete bucket lookup** — it returns one hex string per classification break for vector
features:
```python
n = len(palette)
idx = min(int(value * n), n - 1)
return palette[idx]
```

**`raster_cartography_converter.render_array_to_png`** (`raster_cartography_converter.py:124-128`)
is numpy **vectorized continuous per-pixel interpolation** between adjacent stops:
```python
scaled = np.clip(norm * (n_stops - 1), 0, n_stops - 1)
lower = np.floor(scaled).astype(int)
frac = (scaled - lower)[..., None]
rgb = rgb_stops[lower] * (1 - frac) + rgb_stops[upper] * frac
```

`CartographyService` has **no interpolation at all**. The interpolation exists only in the
raster path because MapLibre `image` sources cannot data-drive color — pixels must be
shaded at render time. The two are different algorithms serving different rendering domains,
not duplicated math.

### 2. The palette set is already shared

`COLOR_PALETTES` is defined **once** in `cartography_service.py:10`.
`raster_cartography_converter` does not re-define it — it lazy-imports it
(`raster_cartography_converter.py:105, 201`). The sharing the report implies is missing is
already present.

### 3. The 3 files are one engine + two consumers, not three copies

- **Engine**: `CartographyService.build_thematic_style` + `build_legend_spec` builds the
  legend (called from `app/tools/cartography.py`, `templates.py`,
  `advanced_spatial.py`, `spatial_stats.py`, `spatial.py`).
- **Vector consumer**: `analysis_cartography_converter` takes an already-built `legend_spec`
  as *input* and emits vector MapSpec paint (step/interpolate/match via
  `_resolve_paint_color`). It does **zero** color math and imports **nothing** from
  `CartographyService` — it is the inverse concern (style translation, not classification).
- **Raster consumer**: `raster_cartography_converter` renders PNG pixels, a domain MapLibre
  forces for image sources.

Merging the consumers into the engine would pull PIL (PNG rendering) and GeoJSON geometry
inference into the classification engine — coarsening a clean producer/consumer boundary
for zero dedup gain.

## Decision

Keep the three-way structure: `CartographyService` as the legend-producing engine;
`analysis_cartography_converter` and `raster_cartography_converter` as separate renderers
consuming the engine's `legend_spec` / `COLOR_PALETTES` output. Do not introduce a
`CartographyEngine` that owns all three.

## Relationship to ADR-0012

ADR-0012 rejected deleting `CartographyService` as a facade. This ADR closes the adjacent
variant: merging the downstream converters *into* the engine. Both rest on the same
verified facts — `CartographyService` is a deep classification engine, and the converters
are non-duplicating consumers of its output, not parallel reimplementations.

## What we are not doing

- No merger of the converters into `CartographyService` / a new `CartographyEngine`.
- No relocation of `COLOR_PALETTES` (it is already the single source, shared via import).
- No introduction of a unified cartographic-style value object (still rejected by ADR-0007).

## Trigger to revisit

Reopen only if **genuine duplicated color/legend logic appears** — e.g., a second module
re-implementing Fisher-Jenks/quantile classification, or a second definition of
`COLOR_PALETTES`. A re-suggestion framed as "unify the converters" or "duplicated RGB math"
does not meet this bar unless it points at actual duplicated *code*, not at two different
rendering mechanisms (discrete classification vs continuous pixel shading) that happen to
both touch color.
