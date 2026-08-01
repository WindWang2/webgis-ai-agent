# Delete the SpatialAnalyzer dynamic dispatch seam

**Status:** accepted

We will **not** keep a dynamic name-dispatch seam over `SpatialAnalyzer`'s concrete
operators. The `execute()` classmethod, the `execute_analysis()` top-level function,
`OPERATOR_MAP`, and its `ANALYSIS_OPERATORS` alias are deleted. The concrete classmethods
(`buffer`, `clip`, `overlay`, `statistics`, `cluster`, `aggregate`, `nearest`,
`path_analysis`, `attribute_filter`, `central_feature`, `recognize_vector_data`) are the
module's only interface.

## Context

Candidate #3 of the architecture-review pass (commit `29eb61f`) added a dynamic operator
dispatch seam to `SpatialAnalyzer`: an `execute(op_name, input_data, parameters, callback)`
classmethod that looked up an operator name in `OPERATOR_MAP` and re-dispatched to the
concrete method, plus a top-level `execute_analysis(task_type, parameters, input_data, callback)`
shim. The intent was a "dynamic execute seam."

A follow-up review found the seam had two problems:

1. **Zero production callers.** Every spatial tool calls the concrete methods directly —
   `SpatialAnalyzer.buffer(...)`, `.overlay(...)`, `.statistics(...)` (see
   `app/tools/spatial.py`, `app/tools/advanced_spatial.py`, `app/tools/spatial_stats.py`).
   The only consumers of `execute` / `execute_analysis` / `OPERATOR_MAP` /
   `ANALYSIS_OPERATORS` were two unit tests. The seam fronted nothing.

2. **A swapped-argument footgun.** `SpatialAnalyzer.execute(cls, op_name, input_data,
   parameters, callback)` placed `input_data` before `parameters`; the `execute_analysis`
   shim placed `parameters` before `input_data`. The shim re-ordered them on the call, but
   any future caller passing positional args to one and reading the signature of the other
   would silently corrupt arguments — a latent bug behind a seam that earns its keep from
   no caller.

## Decision

Delete all four symbols (`execute`, `execute_analysis`, `OPERATOR_MAP`, `ANALYSIS_OPERATORS`).
The concrete classmethods are the interface. `AnalysisResult` (the
`GeoAnalysisResult` type alias, ADR-0009) and `_to_feature_collection` (the GeoJSON
normalization seam, used internally by the concrete methods) are untouched.

This applies the same bar ADR-0007, ADR-0008, and ADR-0009 established: a seam earns its
keep with a real consumer. One consumer (here, two tests only) is the "one adapter =
hypothetical seam" pattern. The deletion test passed: removing the seam concentrates
nothing — it deletes a layer nothing routes through and eliminates the swapped-args
footgun. The concrete methods were always the real interface; the tests were the only
thing the seam fronted.

## What we are not doing

- No `dispatch(op_name, ...)` replacement. If a future caller genuinely needs name-keyed
  dispatch (e.g. an LLM tool that takes an operator name string), that caller can build a
  thin local `name → method` map at that point — the concrete methods are all classmethods
  and trivially addressable. We do not pre-build the indirection for a consumer that does
  not exist.
- No change to `AnalysisResult` / `_to_feature_collection` / the concrete operators.

## Trigger to revisit

Reopen when a **real second consumer of name-keyed dispatch** appears — e.g. an
LLM-facing tool whose schema takes an operator name string and forwards to the matching
method, or a task queue that routes by operator name. At that point a dispatch seam has
multiple callers and earns its keep; the design sketch above (the concrete method map)
becomes the body of that seam, with the argument order fixed to a single contract.
