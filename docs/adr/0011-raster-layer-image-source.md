# Raster layer support via single-resolution image sources

**Status:** accepted

We will add raster layer support to the MapSpec surface, using **MapLibre `image` sources**
(single-resolution georeferenced PNGs) rather than XYZ tile pyramids or COGs. This unblocks the
"Raster v2 converter" item by closing the three stacked blockers that made it impossible: rs_service
discarding computed arrays, MapSpec having no raster source type, and the converter having nothing
to convert.

## Context

CONTEXT.md's *Derived Layer* entry deferred raster with: "Raster analyses (NDVI, terrain,
reclassification) are excluded from this v1 — they discard their computed arrays and return only
statistics, so they cannot back a layer without their own raster-source-schema decision." A code
investigation confirmed all three blockers stack:

1. **Data discarded.** `rs_service.compute_ndvi` computes the NDVI array (`rs_service.py:143-149`)
   then returns *only* `ndvi_stats` (min/max/mean/std) + `vegetation_coverage` — the array is dropped
   at line 164. `fetch_dem` returns STAC HREF URLs, never downloading. The upload-raster path
   (`analyze_vegetation_index` → `UploadRecord` with `geometry_type:"raster_analysis"`) has **no
   consumer** that turns the record into a layer — "implicit semantics" is unrealized.
2. **No schema.** `MapSpecSource.type` is `"geojson"`-only (`types.ts:44`). No raster/PMTiles/tiff
   type exists anywhere; the compiler handles GeoJSON only.
3. **No converter.** (What the handoff called "Raster v2" — gated on 1 and 2.)

## Decision: single-resolution `image` source

Three formats were considered. We chose **(C) single-resolution image** over (A) XYZ tile pyramid
and (B) COG + TileJSON:

- **The actual use case is regional overlay, not global pan/zoom.** `compute_ndvi`/`fetch_dem`
  operate on small bboxes and explicitly downsample 4× (`rs_service.py:135-136`); tool descriptions
  warn against large bboxes. A single georeferenced image covering the bbox is the standard GIS
  treatment. Tile pyramids (A) would be elaborate machinery for a use case the tools deliberately
  avoid.
- **Minimal dependency footprint.** `rasterio` is already in use; `Pillow 12.2.0` and `matplotlib
  3.10.9` are available. No `rio-cogeo`/`rio-tiler`/`mercantile`/tile-server needed. This keeps the
  change compact and avoids deployment surface (a server-side tiler).
- **Fits the session-dir + checkpoint model.** One PNG at
  `.webgis-agent/<sid>/raster/<src_id>.png`; the MapSpec source carries an image ref + bounds.
  Checkpoint materializes the PNG the same way it materializes GeoJSON `ref:` payloads (ADR-0008's
  `mapspec_source.ref()` extends naturally to image refs).

This is a **deliberate v1**, not the final raster form. If a future need emerges to render global
imagery with pan/zoom, a later ADR can promote to XYZ or COG; the MapSpec source shape
(`type:"raster"` + bounds + image ref) changes incrementally, not by rewrite.

## Decision: dedicated raster→layer converter

The raster→MapSpec-layer renderer is a **new module**, `app/services/raster_cartography_converter.py`,
mirroring the existing vector `analysis_cartography_converter.py`. It does **not** live inside
`rs_service`. Chosen over (A) "rs_service renders itself" and (C) "the tool renders":

- **Symmetry with the vector path.** Vector flow is `tool → analyzer (compute) →
  analysis_cartography_converter (render to layer) → layer_upsert (store)`. The raster flow mirrors
  it: `tool → rs_service (compute, now keeps array) → raster_cartography_converter (render to PNG +
  layer) → layer_upsert (store)`. A reader who has followed ADR-0007→0008→0009 reads this without
  surprise.
- **Cartography concern stays with cartography.** Colormap selection and legend derivation are
  *cartographic* concerns. The repo's home for them is the converter layer (beside
  `analysis_cartography_converter`); putting colormaps in `rs_service` would couple it up a layer
  into concerns it doesn't own.
- **Reuses ADR-0008 + existing palette infra.** The renderer emits a MapSpec layer whose source
  routes through `mapspec_source` primitives; colormaps come from `CartographyService.COLOR_PALETTES`
  (6 palettes incl. Viridis). No new color logic invented.

The "discard arrays at the boundary" contract is **intentionally relaxed for raster**: arrays now
travel in-process (rs_service → tool → converter, ≤3 calls, by reference — no serialization), are
rendered to a PNG, then dropped. The PNG is what's persisted. This relaxation is explicit, not
accidental.

## The two legend pipelines apply (ADR-0007 insight)

The colormap is baked into the PNG at render time (an `image` source's colors are fixed — MapLibre
raster layers don't data-drive `raster-color` from a source array). The raster layer still carries a
`legend_spec` (continuous: min/max + palette, mirroring the vector continuous contract) so the
live-map `<ThematicLegend>` overlay path can show "what these colors mean." This is the same
two-pipeline split ADR-0007 documented for vector — `legend_spec` for the overlay, paint for the
render — applied to raster. The headless MapSpec Compiler auto-derives its own legend from the
`legend_spec` per its existing contract.

## Out of scope (recorded for the future raster ADR)

- Multi-resolution zoom (XYZ/COG) — deferred; trigger is a real pan/zoom-global-imagery use case.
- The upload-raster `UploadRecord(geometry_type:"raster_analysis")` path — still has no layer
  consumer; orthogonal. This ADR addresses the `rs_service`-computed path only.
- `fetch_dem`'s STAC-HREF-only return (no array) — it does not compute, so it cannot render; either
  it gains a compute step (like `compute_terrain` already has) or it stays a metadata fetch. Not
  addressed here.

## Known follow-ups (gaps in this shipped version, surfaced by code review)

1. **`imageRef` → serving-URL rewrite is not yet wired.** The TS compiler emits the `imageRef`
   cursor verbatim as the `image` source's `url` (e.g. `url: "ref:raster/ndvi_src"`). The compiler
   is session-agnostic by design (ADR: "framework-agnostic, deterministic"); only a caller with
   `session_id` can rewrite `ref:raster/<id>` → `/api/v1/sessions/<sid>/raster/<id>.png`. Neither
   the compile CLI coordinator nor `mapspec_store.compile_mapspec_cli` does this rewrite today. **Net
   effect: raster layers compile to a valid `image` source, but the headless validator/exporter
   cannot yet *fetch* the PNG** (MapLibre would 404 on the `ref:raster/...` URL). Wiring the rewrite
   in `compile_mapspec_cli` (which holds `session_id`) is the immediate next step; this ADR lands
   the pipeline up to that boundary so the rewrite is a focused follow-up, not a redesign.

2. **`validateMapSpec` does not validate raster sources.** A `type:"raster"` source missing
   `bounds`/`imageRef`, or a `raster` layer referencing a geojson source, passes the pre-compile
   validator silently (it never inspects `source.type`). The compiler then emits a malformed `image`
   source. A focused validation addition (raster source requires `imageRef`+`bounds`; raster layer
   requires a raster source) closes this.

### Update — gap 1 wired (#696)

Gap 1 is now wired (PR #696). The compiler remains session-agnostic — it still
emits `imageRef` verbatim as the `image` source `url`. The rewrite lives at the
compile caller that holds `session_id`:

- **Rewrite:** `RuntimeValidator.validate_runtime` (the coordinator chain that
  holds `session_id`) rewrites `ref:raster/<id>` → `__ORIGIN__/raster/<id>.png`
  at mapspec level **before** compilation (strictly before the 1b MVT asset
  assembly block, which it does not touch). The rewritten MapSpec is compiled
  from a staged ephemeral file so the persisted canonical MapSpec retains the
  opaque cursor. `__ORIGIN__` is resolved by the existing html-template
  (`replaceAll("__ORIGIN__", location.origin)`) so no second URL scheme is
  introduced — raster reuses the same `__ORIGIN__` convention as the MVT tiles
  (`#695`/`#697`).
- **Serving:** session PNGs at `.webgis-agent/<sid>/raster/<id>.png` are copied
  into the compiler output's `raster/` subdirectory (mirroring
  `runtime_asset_assembly.assemble_mvt_assets`'s `tiles/` pattern). The
  headless static server serves `dist/` directly, so
  `__ORIGIN__/raster/<id>.png` resolves to `dist/raster/<id>.png` without
  extending the server's mount table or weakening its `..` traversal guard.

The original gap description above is retained for history.
