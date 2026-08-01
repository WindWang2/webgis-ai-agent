# No `attach_legend_spec` helper — all legend emitters already attach at top level

**Status:** accepted

We will **not** introduce a single `attach_legend_spec(payload, spec)` emit-point
helper, despite ADR-0009 naming it as the anticipated candidate when legend_spec
location divergence grew. An investigation found the divergence the helper would
standardize does not exist: all five legend-bearing emitters already attach
`legend_spec` at the top level. The converter's `data`-inner fallback was dead code.

## What ADR-0009 anticipated

ADR-0009 (trigger #2) said: *"if a 6th/7th emitter appears and the converter's
double-lookup keeps widening, that becomes the candidate: a single
`attach_legend_spec(payload, spec)` emit-point helper that all legend-bearing
emitters route through, standardising where the marker lives."*

## What the investigation found

The review (Candidate #5) claimed 5 emitters across 2 attachment sites — 4 top-level
and 1 (`heatmap_data`) nested inside `data` — making the converter's double-lookup a
load-bearing branch. Tracing each emitter's actual payload shape and path through
`is_analysis_result` contradicted this:

| Emitter | Payload shape | `legend_spec` site | Reaches converter? |
|---------|---------------|--------------------|--------------------|
| `h3_binning` | `to_llm_response()` dict (non-GeoJSON) | top-level (`:382`) | yes — top-level lookup hits |
| `kde_contours` | FeatureCollection | top-level (`:328`) | **no** — GeoJSON shape, `is_analysis_result` False |
| `heatmap_data` | FeatureCollection | top-level (`:265`,`:296`) | **no** — GeoJSON shape, `is_analysis_result` False |
| `apply_template` | return dict (non-GeoJSON) | top-level (`:265`) | yes — top-level lookup hits |
| `create_thematic_map` | return dict (non-GeoJSON) | top-level (`:128`) | yes — top-level lookup hits |

The apparent divergence was an illusion. `heatmap_data`'s `data["legend_spec"]` looks
"nested in data" only because `data` *is* the returned FeatureCollection — from the
consumer's view, `result["legend_spec"]` is top-level for every emitter
(`tests/test_heatmap_native.py:63-64` reads `result["legend_spec"]` directly).

The converter's `data`-inner fallback (`analysis_cartography_converter.py:367-368`,
the `if not legend_spec and isinstance(...data...dict)` branch) was **unreachable**.
Its only logical target (`heatmap_data`) outputs a FeatureCollection shape, and
`is_analysis_result` explicitly rules "GeoJSON wins" — returning False for any
GeoJSON-shaped dict *before* this converter runs. The three emitters that do reach
the converter (`h3_binning`, `apply_template`, `create_thematic_map`) all attach at
top level, so the top-level lookup always hits and the `data`-inner branch never fires.

## Decision

Delete the converter's dead `data`-inner fallback. Do **not** introduce an
`attach_legend_spec` helper — there is no divergence to standardize (all emitters are
already top-level), so a helper would be speculative generality: one more symbol
routing five sites that already agree, with the "standardize the site" decision
duplicated nowhere.

This closes ADR-0009's trigger #2 not by widening the double-lookup (the anticipated
growth signal) but by showing the double-lookup's `data`-inner half was never reachable.
ADR-0009's trigger #1 (a second consumer of analysis-result identity) remains open and
unaffected.

## What we are not doing

- No `attach_legend_spec` helper.
- No change to any emitter's attachment site (they already agree).
- No change to `is_analysis_result` or the analysis-result identity contract
  (ADR-0009 trigger #1 still governs that).

## Trigger to revisit

Reopen if **a real divergence appears** — a new emitter that (a) attaches `legend_spec`
somewhere other than top level *and* (b) reaches this converter (i.e. its output is
non-GeoJSON-shaped, so `is_analysis_result` returns True). At that point the
double-lookup would re-earn its keep, and the `attach_legend_spec` helper ADR-0009
anticipated becomes the right move. Until then, the single top-level read is correct.
