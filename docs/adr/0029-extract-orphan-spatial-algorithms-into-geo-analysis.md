# Pull orphan spatial algorithms into lib/geo_analysis/

**Status:** accepted

Record architecture-review **Batch 6 Candidate F2** as implemented. This ADR
records the decision so future reviews do not re-suggest keeping spatial
algorithm logic in the tool adapter layer.

## Context

The Batch 6 architecture survey (`/tmp/architecture-review-20260801-222351.html`)
identified that `app/tools/spatial_stats.py` (557 LOC) carried **two distinct
kinds of code**:

1. **Thin tool adapters** (the `@tool` wrappers with LLM-facing validation and
   descriptions) - correctly in the tool layer.
2. **Deep math/geometry algorithms** (~390 LOC) - `kde_surface`,
   `kde_contours`, `voronoi_polygons`, `convex_hull`, `multi_ring_buffer` -
   doing exactly the same kind of work as their statistical siblings
   (`moran_i_narrated`, `hotspot_narrated`, `cluster_narrated`) that already
   lived correctly in `app/lib/geo_analysis/`. Same kind of math, two homes.

Additionally, two sibling tools (`hotspot_analysis`, `h3_lisa`) **bypassed**
`SpatialAnalyzer` by importing `lib.geo_analysis.statistics` directly, breaking
the "all geo math through SpatialAnalyzer" invariant that the other 5 stats
tools followed.

A verified live latent crash made this urgent: `SpatialAnalyzer.path_analysis`
imported `shortest_path` from `network.py`, which never existed. The
`path_analysis` tool (registered in `advanced_spatial.py`) called it, so every
LLM invocation raised `ImportError`. Zero test coverage.

## Decision

Extract the 5 orphan algorithms into two new deep-math modules, route all 7
stats tools through `SpatialAnalyzer`, and delete the broken `path_analysis`.
In three commits (`edaa0d9` -> `7ea3301`).

### Module home: density.py + geometry_ops.py

- **`app/lib/geo_analysis/density.py`** (new): `kde_surface` + `kde_contours`.
  Both return `GeoAnalysisResult`, unifying the return contract with
  `statistics.py`. The `kde_contours` `legend_spec` attaches as a top-level
  key on the FC in `data` (read by the cartography converters as an analysis
  marker).
- **`app/lib/geo_analysis/geometry_ops.py`** (new): `voronoi_polygons` +
  `convex_hull` + `multi_ring_buffer`. All return `GeoAnalysisResult`.
- **`statistics.py` unchanged** (stays at 608 LOC - the 5 orphans are not all
  statistics; voronoi/convex_hull/multi_ring are geometry, not statistics).

### Return contract: GeoAnalysisResult

All 5 extracted functions adopt `GeoAnalysisResult` (matching their siblings).
`data` = the GeoJSON FC with algorithm-specific envelope keys
(`grid_size`/`bandwidth_m`/`levels_count`/`legend_spec` as extra FC keys);
`summary` = a short string; `error_type`/`correction_hint` for failures.

**Key design nuance** (discovered during implementation): `kde_contours`' tool
wrapper returns `res.data` directly (not `to_llm_response()`), because the
dispatch layer matches `type=="FeatureCollection"` at the top level and the
cartography converters read `legend_spec` as a top-level analysis marker on
the FC dict. `to_llm_response()` would bury both inside a `{success, data}`
envelope, breaking that path. `kde_surface` uses `to_llm_response()` like the
other stats tools.

### SpatialAnalyzer operators

7 new `@classmethod` operators added: `kde_surface`, `kde_contours`,
`voronoi_polygons`, `convex_hull`, `multi_ring_buffer`, `hotspot`, `lisa`.
Each is a thin delegator doing `_to_feature_collection` -> delegate to the lib
function. Tool wrappers route through `SpatialAnalyzer`.

### Bypass fix

`hotspot_analysis` and `h3_lisa` rewired from direct `lib.geo_analysis.statistics`
imports to `SpatialAnalyzer.hotspot()` / `.lisa()` operators. The "all geo
math through SpatialAnalyzer" invariant is now uniformly true (7 of 7 stats
tools route through the seam).

### path_analysis deletion

`SpatialAnalyzer.path_analysis` (the dangling `shortest_path` import) + the
`path_analysis` tool in `advanced_spatial.py` + the `PathAnalysisArgs` schema
are deleted. It was a live `ImportError` crash (vaporware, not a stub).
Re-implement fresh with tests if real network routing is needed - do not
resurrect the broken shell.

## Consequences

- **depth**: the 5 orphans leave the shallow adapter and join the deep math
  layer. `spatial_stats.py` shrinks from 557 to 183 LOC (pure thin adapters).
- **locality**: one kind of math, one home (`lib/geo_analysis/`). The "all geo
  math through SpatialAnalyzer" invariant is uniformly true.
- **interface**: `SpatialAnalyzer` is the single seam; one return contract
  (`GeoAnalysisResult`) across all `geo_analysis` functions.
- **Bug fixed**: the `path_analysis` `ImportError` crash is eliminated by
  deletion.
- **Test coverage**: 27 new pure-math tests (13 density + 14 geometry_ops) +
  7 operator-parity tests. The orphans had zero behavioral coverage before.

## What we are not doing

- **`raster_ops.py` folding** - out of scope (Q6). Has a real caller + test;
  folding mixes unrelated concerns.
- **`interpolation.py` bypass** - `idw_interpolation` in `advanced_spatial.py`
  also imports `lib.geo_analysis` directly (same class of inconsistency as
  hotspot/h3_lisa). Flagged for a future pass; not touched here to keep F2
  focused on `spatial_stats.py`'s cluster.
- **Raster return-contract mismatch** - `raster_math.py` returns plain `dict`
  while vector ops return `GeoAnalysisResult`. Separate concern, noted in the
  survey; not addressed here.
- **Implementing `shortest_path`** - Q5 chose deletion. Re-implement fresh
  with tests if real routing is needed.

## Related

- Batch 6 / F1 (ADR-0028): the same pattern (deepen shallow adapter into a deep
  module behind one interface) applied to the Chinese-maps provider cluster.
- Batch 5 / E3 (ADR-0026): `SpatialAnalyzer` was deepened as the unified
  spatial+raster domain engine. F2 extends its operator surface.
- The `CONTEXT.md` "SpatialAnalyzer Domain Engine" entry is updated to note
  the new operators + the invariant.
