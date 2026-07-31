# No `analysis_result` interface until a second consumer appears

**Status:** accepted

We will **not** introduce a type discriminant on the Analysis Result (e.g. a `kind:
"analysis_result"` field stamped at `GeoAnalysisResult.to_llm_response()`) at this time. The
architecture review's Candidate #2 framed this as a "Strong" deepening ("give the Analysis Result
a real interface"), arguing that `to_llm_response()` flattens the dataclass to an unmarked dict
and the consumer (`is_analysis_result`) re-derives identity by sniffing marker keys. **A code
investigation contradicted the report's premise.** We defer the candidate, and record why.

## What the investigation found

The report assumed the Analysis Result dataclass is the source of analysis identity, and that
identity gets erased at `to_llm_response()`. Neither holds:

1. **The dataclass is not the marker source.** `GeoAnalysisResult.to_llm_response()` emits only
   `{success, summary, data, error_type?, correction_hint?, stats?}` — it never carries
   `legend_spec`/`algorithm`/`analysis_type`/`source_ref`. Those markers are attached by **inline
   emitters at the tool layer**, *after* the dataclass has been flattened. The dataclass cannot
   stamp an identity it doesn't own.

2. **There is exactly one case-1 emitter.** Of the ~28 `to_llm_response()` call sites in `app/tools/`,
   precise per-`@tool` analysis found:
   - **1** emitter returns a to_llm_response dict *and* attaches `legend_spec` (`h3_binning`).
   - **5** emitters attach `legend_spec` to a raw dict that never went through the dataclass
     (`heatmap_data`, `kde_contours`, `create_thematic_map`, `apply_template`, plus the
     `_build_legend_spec` helper).
   - **22** emitters return a plain analysis result with no legend (buffer/clip/dissolve/overlay/etc.).

3. **The marker location genuinely diverges** — but across the 5 *legend-only* emitters, not across
   analysis results: some attach `legend_spec` at the top level (`h3_binning`, `create_thematic_map`);
   others attach it *inside* the GeoJSON `data` (`heatmap_data`, `kde_contours`). The converter's
   double-lookup (`legend_spec` at top level OR inside `data`) is a real workaround for this
   divergence, not defensive over-engineering.

4. **The "0 collision tests" finding is stale.** `test_is_analysis_result_detection:62-74` already
   covers the collision case (a GeoJSON FeatureCollection carrying a stray `algorithm` key is
   correctly treated as GeoJSON, not an analysis result). The sniff was hardened in the converter's
   v1 work.

## Why defer

The candidate fails the seam test this repo has now applied three times (ADR-0007's rejection of
Candidate #1; ADR-0008's rejection of a `has_data` primitive):

- **The `kind:"analysis_result"` fix targets case-1, which has one emitter.** A single-producer
  discriminant is Speculative Generality — it ships a field with one writer and one reader
  (`is_analysis_result`), exactly the "one consumer = hypothetical seam" pattern. The actual
  duplication (5 legend-only emitters with divergent marker location) is *untouched* by an
  analysis-result identity field.
- **The real friction** (legend_spec location divergence across 5 emitters) is small and *already
  worked around* by the converter's tested double-lookup. It is not the "re-derived a fifth time on
  the next edit" urgency the report implied.
- **ADR-0007's standard applies symmetrically.** We rejected Candidate #1 (unified cartography
  module) because its seam was hypothetical until a second consumer lands. The same bar rejects
  Candidate #2.

## Trigger to revisit

Reopen when **any** of these is true:

1. **A second consumer of analysis-result identity appears** — e.g. the eval-evidence pipeline or a
   provenance/replay reader that needs to distinguish "this dict is an analysis result" without
   re-sniffing. At that point the discriminant earns multiple readers and the seam is real.
2. **The legend_spec location divergence grows** — if a 6th/7th emitter appears and the converter's
   double-lookup keeps widening, *that* (not analysis-result identity) becomes the candidate: a
   single `attach_legend_spec(payload, spec)` emit-point helper that all legend-bearing emitters
   route through, standardising where the marker lives. The investigation above is the design
   sketch for that future ADR.

## What we are not doing

- No `kind` field on `to_llm_response()`.
- No `emit_analysis_payload` helper at the tool layer.
- No simplification of `is_analysis_result` (it stays as the working, tested sniff).
- No change to the 5 legend-only emitters' marker locations.

The converter's `is_analysis_result` / double-lookup is accepted as the present boundary; the
divergence it papers over is documented here so it is not rediscovered as a surprise.
