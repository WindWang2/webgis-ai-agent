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
  → existing dispatch seam authors displayable vector results into MapSpec
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

Displayable GeoJSON produced by existing GIS tools now enters the same MapSpec
lifecycle automatically at `ToolDispatchService`; tools do not need to invent a
second agent step merely to become reviewable. The dispatcher stores the result
once behind the existing session ref, converts only presentation metadata, and
removes the inline feature body before SSE/harness persistence. Rendered
`heatmap_raster` results and spatial-decision simulation layers use the same
desired-state review; they are never advertised as empty GeoJSON mounts.
If authoring is unavailable, L1 execution remains truthful but cartography is
explicitly `not_evaluated` and no runtime generation or map action is fabricated.
A first MapSpec mutation that fails after save discards the residual candidate
instead of inventing last-known-good state.

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

- source/layer binding, source addressability, and exact result-ref provenance;
- explicit expected-result visibility and finite opacity;
- explicit CRS provenance, independently validated vector/raster bounds, and
  geographic coordinate plausibility;
- empty-result and geometry/layer-type checks;
- paint field/type/range checks;
- thematic legend presence where classification requires it;
- classification interval/category/domain integrity;
- legend/style field, color, threshold, palette, and no-data equivalence;
- runtime result presence, visibility, authoritative legend, and camera
  convergence in a newer frontend observation;
- desired-vs-live MapLibre source, layer, paint, and layout convergence.

Native MapLibre expression arrays, geometry collections, and mixed-geometry
sources that require runtime sublayer fan-out are surfaced as `not_evaluated`
when the structured checker cannot prove their semantics.
Descriptor-backed sources whose fields are unknown likewise remain
`not_evaluated`; they do not fail by pretending an absent field list is
complete. A fatal/page execution error from the existing headless runtime is a
deterministic runtime failure, while canvas appearance remains heuristic.

Desired visibility omitted from MapSpec is attributed to MapLibre's documented
`layout.visibility = visible` default and explicitly labelled as desired-state
contract evidence. It does not certify live visibility: L4 still requires the
newer frontend observation. Analysis-originated layers without a distinct
`provenance.result_ref` remain `not_evaluated`; an input `source_ref` can never
stand in for the displayed result identity.

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
  `legend_spec` only when the current style already uses the same field and
  classification method (pure color/break projection drift).

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

After live-state review, a second bounded planner can issue the existing map
action command `cartographic_runtime_repair`. It can only restore explicit
visibility, reapply a finite desired opacity/style projection, or refresh the
runtime legend from the authoritative MapSpec. The action carries the session,
MapSpec fingerprint, preceding observation sequence, and patch fingerprint.
It is applied by the existing frontend command arbiter, ACKed through the
existing action channel, and must be followed by a newer observation bound to
that exact repair action before re-review can pass. Runtime repair also has a
hard maximum of two attempts; an identical patch, rejected ACK, user
cancel/supersession, or stale generation terminates explicitly.

## Runtime convergence and user supersession

Pre-turn frontend context is stored separately and cannot certify a mutation.
After `MapSpecRuntime.reconcileAsync` settles, the frontend compares the locally
derived desired runtime spec with live `getSource`/`getLayer`/paint/layout
state and the runtime's exact applied source generation, then sends a bounded
observation tagged with the backend MapSpec fingerprint. Same-id source updates
replace the live source and rebuild dependent layers; source existence alone is
not convergence. Dataset bodies and geometries are excluded. Streaming tokens
and ordinary map pans do not trigger review. Identical observations are
coalesced. Each POST carries a client-monotonic generation, and the backend
rejects a delayed older arrival without replaying any repair action.

Style readiness uses a hard-bounded retry budget. If a slow basemap exceeds
that budget, the runtime reports `style_load_timeout` and arms one event-driven
reconciliation; a later real `styledata` readiness event reapplies the newest
pending MapSpec and schedules a fresh observation without polling.

Each MapSpec mutation records the observation sequence that existed before the
mutation. Final runtime review requires a strictly newer frontend observation
whose fingerprint equals the current desired MapSpec.
The transported MapSpec content fingerprint must equal the freshly recomputed
fingerprint. Any correlated map action must also carry that fingerprint. A
stale ACK, stale review, wrong-session state, cancelled action, or superseded
action cannot pass the current map. User/newer intent therefore wins over an
old autonomous repair.

The lifecycle also advances a session-scoped `mutation_revision` under the
existing distributed lock. Durable harness context accepts only the revision
that still equals authoritative MapSpec state; a late completion from an older
revision cannot replace newer provenance or seed a process-local evaluation.
Correctness-critical Redis reads invalidate the replica-local L1 cache after
lock acquisition so a lock holder cannot validate against its own stale copy.

An ACK remains separate from state comparison. A `store_mounted` ACK proves
only the existing HUD-store action completed; it cannot prove rendering.
Camera ACKs are recomputed from requested versus actual coordinates. Missing
viewport evidence is `not_evaluated`, while an observed mismatching camera is a
failure. The final runtime rules compare the newer live layer/style observation
and camera state, so command acceptance alone is insufficient. Observation and ACK endpoints
both trigger the same coalesced evaluation, making their arrival order safe.

Harness accumulators, readers, caches, and evaluation locks are scoped by the
real session id and LRU bounded. Both the Pi dispatch path and the legacy chat
tool pipeline feed the same harness seam; no fixed `"production"` evaluator or
cross-session mutable accumulator is used.

Pi callbacks are routed by a short-lived HMAC turn capability embedded in the
agent turn and verified by the backend. The route assigns and persists a real
session before the first prompt. A correctly signed token is accepted only
while its exact `(session_id, turn_id)` owns the live Pi prompt; completion,
cancellation, and supersession retire it immediately. Caller-supplied routing
fields are ignored, and dispatch-result caches are keyed by the verified
session plus tool-call ID. The frontend similarly retains anonymous owner
tokens per session, and an ACK resolves its token from the correlated session
when queued so a later workspace switch cannot retag the request.

## Performance and retention

Review fingerprints use a strict field allowlist and bounded profile summary.
Inline GeoJSON, features, source URLs, arbitrary nested metadata, and other
dataset bodies are excluded. Source/layer/profile collections are capped and
carry omission counts plus stable digests, so a large MapSpec cannot create an
unbounded evidence payload. Review never resolves a large ref or downloads full
data. Pure desired reviews are cached by
`(session_id, content_fingerprint)` with a bounded 128-entry cache. Completed
runtime evaluations are cached only by session, runtime fingerprint,
observation sequence, and a hash of terminal action/tool evidence; a new ACK,
observation, tool result, or MapSpec generation invalidates the key.

Automatic mutation checkpoints snapshot presentation state without
materializing immutable session refs. Explicit named archival checkpoints
remain self-contained and materialize their refs through the existing bounded
checkpoint path. This prevents a style-only repair from downloading or copying
a large dataset.

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
