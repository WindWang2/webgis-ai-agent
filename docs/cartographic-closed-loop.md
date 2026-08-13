# Cartographic Intelligence Closed Loop

The cartographic closed loop extends the existing MapSpec lifecycle, semantic
cartography checks, map-action ACK protocol, and Pi harness. It does not add a
second agent or map description format.

## Why execution is not map success

The system deliberately keeps these outcomes separate:

| Level | Meaning | Evidence |
|---|---|---|
| L1 | GIS/tool execution validity | tool result and error state |
| L2 | map-state validity | a newer frontend observation and, when applicable, a matching terminal action ACK |
| L3 | cartographic structural validity | deterministic desired-MapSpec checks |
| L4 | cartographic quality | applicable deterministic rules plus actual-state comparison |
| L5 | goal satisfaction | explicit goal or visual evidence; otherwise `not_evaluated` |

A non-error tool result proves only L1. A valid MapSpec proves that its
structure is accepted, not that a user can see the result. A `succeeded` map
action proves command execution, not that it belongs to the current MapSpec or
that runtime state converged. A present legend proves neither semantic
equivalence nor completeness.

## Existing architecture reused

The production flow is:

```text
user intent
  → existing agent/tool planning
  → GIS result / session result ref
  → MapSpecLifecycleEngine candidate
  → deterministic desired-state review
  → bounded AUTO_SAFE presentation repair
  → transactional MapSpec + map_state commit
  → existing step_result/HUD adapter and MapSpec runtime
  → live MapLibre state observation + action ACK (either arrival order)
  → session-scoped PiAgentHarness trusted re-read and re-review
  → cartographic quality gate
```

`legend_spec` remains the authoritative thematic classification. `MapSpec`
remains desired map intent. Session `map_state` remains the actual runtime
observation surface. `CartographyReport`, `MapSpecResult`, `ToolCallEvidence`,
and the existing harness evaluator carry the evidence.

## Evidence model

Each applicable check reports:

- `rule` and `status`: `pass`, `fail`, `warning`, or `not_evaluated`;
- `evidence_class`: `deterministic`, `heuristic`, or `visual`;
- bounded structured `evidence` referencing layer/source/session state;
- `severity`, `repairability`, and an optional typed `suggested_fix`;
- legacy `check`, `evaluated`, and finding fields for compatibility.

An empty findings list is not proof. Positive checks are recorded at the rule
invocation that proved them. A report with no evaluated deterministic rules is
`not_evaluated`. Visual overlap remains explicit visual `not_evaluated` unless
rendered evidence exists.

The lifecycle review is labelled `stage: desired_state`. It can never
self-certify runtime success. The harness result is `stage: actual_runtime` and
is trusted only after the harness reloads state belonging to the evaluated
session and recomputes the desired review.

## Deterministic rules and profiles

The small profile registry selects `general_analysis`, `thematic_map`,
`statistical_map`, `raster_result`, or `network_result` from explicit intent or
layer/source shape. It is typed composition, not a rule DSL.

Rules use metadata already stored on MapSpec sources and layers:

- source/layer binding and result-ref provenance;
- explicit expected-result visibility and finite opacity;
- explicit CRS provenance, finite ordered bounding boxes, and geographic
  coordinate plausibility;
- empty-result and geometry/layer-type checks;
- paint field/type/range checks;
- thematic legend presence where classification requires it;
- classification interval/category/domain integrity;
- legend/style field, color, threshold, palette, and no-data equivalence;
- runtime result presence, visibility, authoritative legend, and camera
  convergence in a newer frontend observation;
- desired-vs-live MapLibre source, layer, paint, and layout convergence.

Ordinary unclassified imagery does not require a thematic legend. An unknown
CRS is never replaced with EPSG:4326. Coordinates without explicit CRS do not
drive an automatic geographic camera.

## Repair policy and termination

The desired-state composer accepts only `AUTO_SAFE` presentation operations:

- normalize a non-finite or out-of-range opacity to the established layer-type
  default;
- restore visibility only when `cartographic_intent.expected_visible` is
  explicit;
- regenerate derived paint from an already-present authoritative
  `legend_spec`.

Missing legends are not synthesized by guessing an inverse from paint.
Classification breaks, fields, categories, analysis parameters, source data,
CRS, and geometry are not silently changed. Those are semantic-risk or
non-repairable failures.

Repairs copy MapSpec presentation branches under the existing per-session
lifecycle lock while sharing immutable source dataset bodies. They do not
invoke a GIS tool or mutate source data. The hard maximum is two applications.
Repeated failure fingerprints,
repeated patch fingerprints, stale generations, or no safe repair terminate as
`repair_exhausted`, `superseded`, `failed_repairable`, or
`failed_unrepairable`; exhaustion never becomes success.

## Runtime convergence and user supersession

Pre-turn frontend context is stored separately and cannot certify a mutation.
After `MapSpecRuntime.reconcileAsync` settles, the frontend compares the locally
derived desired runtime spec with live `getSource`/`getLayer`/paint/layout
state and sends a bounded observation tagged with the backend MapSpec
fingerprint. Dataset bodies and geometries are excluded. Streaming tokens and
ordinary map pans do not trigger review. Identical observations are coalesced.

Each MapSpec mutation records the observation sequence that existed before the
mutation. Final runtime review requires a strictly newer frontend observation
whose fingerprint equals the current desired MapSpec.
The transported MapSpec content fingerprint must equal the freshly recomputed
fingerprint. Any correlated map action must also carry that fingerprint. A
stale ACK, stale review, wrong-session state, cancelled action, or superseded
action cannot pass the current map. User/newer intent therefore wins over an
old autonomous repair.

An ACK remains separate from state comparison. A `store_mounted` ACK proves
only the existing HUD-store action completed; it cannot prove rendering.
Camera ACKs are recomputed from requested versus actual coordinates. The final
runtime rules compare the newer live layer/style observation and camera state,
so command acceptance alone is insufficient. Observation and ACK endpoints
both trigger the same coalesced evaluation, making their arrival order safe.

Harness accumulators, readers, caches, and evaluation locks are scoped by the
real session id and LRU bounded. Both the Pi dispatch path and the legacy chat
tool pipeline feed the same harness seam; no fixed `"production"` evaluator or
cross-session mutable accumulator is used.

## Performance and retention

Review fingerprints use a strict field allowlist and bounded profile summary.
Inline GeoJSON, features, source URLs, arbitrary nested metadata, and other
dataset bodies are excluded. Review never resolves a large ref or downloads
full data. Pure desired reviews are cached by
`(session_id, content_fingerprint)` with a bounded 128-entry cache; live
runtime comparison and ACK evidence are never cached.

Repair history is capped by the hard iteration limit. Slim tool/SSE payloads
retain at most twelve non-pass checks and strip MapSpec source bodies. Evidence
uses existing in-memory/session/harness retention; no database or migration is
introduced.

The headless runtime validator's luminance, blank-canvas, and control-layout
scores are retained as `heuristic_visual_proxies`. They can supplement the
deterministic evidence but cannot produce L4/L5 PASS by themselves.

## Extending the checks

Add deterministic rules to `evaluate_cartography_semantics` only when the
needed structured evidence exists. Record positive and negative results at the
same rule site. If evidence is absent, return `not_evaluated`. A new automatic
fix must be presentation-only, expressible as a typed operation in the bounded
composer, idempotent, and provably free of analysis-semantic changes.

Rendered/vision checks must declare `evidence_class: visual` or `heuristic`,
carry source/confidence metadata, and remain separate from deterministic
acceptance.
