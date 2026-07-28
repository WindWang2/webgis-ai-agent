# Spec: Unified Tool Dispatch

**Source:** Synthesizes the `/improve-codebase-architecture` deepening of candidate #1
(unify tool dispatch), grilled to a shared understanding over six decisions.
**Status:** `ready-for-agent` — decisions settled; this spec drives the build via `/to-tickets`.

---

## Problem Statement

The system has two live paths that perform tool dispatch — the legacy ChatEngine path
(production default, `USE_NEW_AGENT` off) and the Pi bridge path (opt-in, `USE_NEW_AGENT` on).
They share a name and a domain concept but almost nothing else. The legacy path is genuinely
deep: behind one signature it owns validate, tier-authorize, run, store-large-GeoJSON-to-ref_id,
slim-for-frontend, intercept-repeated-calls, and wrap-errors-as-self-healing-hints. The Pi path
owns only the first three and silently drops the rest.

From the user's perspective this produces a concrete, silent regression: under `USE_NEW_AGENT`,
GIS tools run and the LLM sees their output, but produced layers never appear on the map. The Pi
path never stores the GeoJSON, so no `ref_id` cursor is created; the frontend's layer-mount logic
keys off that cursor and finds nothing to mount. No error, no log — the feature simply looks
broken. No test covers this, because the two paths are each tested in isolation and no test
exercises the full dispatch→frontend contract through the Pi path.

The deeper friction: the two implementations will keep drifting with every future tool-behaviour
change, because nothing structurally forces them to agree.

## Solution

Introduce a single in-process `ToolDispatchService` that owns the entire dispatch chain — both
adapters (legacy ChatEngine and Pi bridge) call into it. The service is a plain in-process module
(no network port — dispatch is never deployed across a boundary); its only non-pure dependency is
session data, which already has an in-memory test stand-in. The service returns a
discriminated-result value, so callers branch on the *outcome* rather than reassembling a bag of
raw fields. Migrate the two paths onto it in an expand–contract sequence, Pi path first (it's the
buggy, opt-in canary), legacy path last (it's the working production default).

## User Stories

1. As a spatial analyst running under the new agent, I want a GIS tool that produces a layer to
   actually show that layer on my map, so that I can see the result of my analysis.
2. As a spatial analyst, I want the layer I produced to be referenceable later in the conversation,
   so that I can ask follow-up questions about "the layer from the last tool" without re-running it.
3. As a spatial analyst, I want repeated identical tool calls to be intercepted, so that an agent
   loop doesn't re-run an expensive analysis I already have.
4. As a spatial analyst, I want a tool that errors to come back as a corrective hint, so that the
   agent can adjust its parameters and retry rather than surfacing a raw crash.
5. As a developer maintaining tool behaviour, I want one place that owns how a tool call becomes a
   result, so that a change to result-shaping, ref-storage, or repetition handling applies to both
   agent paths and cannot silently diverge again.
6. As a developer, I want the dispatch contract tested through a single interface, so that a
   regression like the missing layer mount is caught before merge regardless of which path runs.
7. As a developer, I want the production-default path to keep working throughout the migration, so
   that users on the legacy agent are unaffected while the new service is validated.
8. As a developer, I want the new agent's HTTP-callback shape and the streaming SSE shape to both be
   served by one dispatch, so that there is no second code path that can drop behaviour again.

## Implementation Decisions

- **Scope — unify and redesign.** Both paths route through one new service. This is not a thin
  refactor that only points the Pi path at the existing deep dispatcher; the dispatcher's interface
  is also reshaped, because its current return shape (a flat bag of parallel booleans and strings)
  is shallow and forces callers to reassemble the outcome themselves.

- **Seam — in-process module, local-substitutable.** `ToolDispatchService` is a plain module both
  callers import. It is *not* a formal port with HTTP/in-memory adapters — dispatch is never
  deployed across a network boundary, so a port would be indirection with one adapter. The module's
  dependencies are injected (the tool registry and a broadcast callback already are today); its one
  non-pure dependency is session data, which has a `SessionDataProtocol` port and an in-memory
  stand-in. The module is tested with that stand-in running in the suite.

- **Interface — discriminated-result value.** The service returns a result carrying one status
  discriminant plus the typed fields each caller needs. This collapses the previous flat return:
  `has_geojson` disappears (derivable as "a ref was produced"), and callers no longer read a
  boolean bag. The result encodes the decision precisely (shape agreed during grilling, not from a
  prototype):

  ```python
  @dataclass
  class ToolDispatchResult:
      status: Literal["ok", "repeated", "error"]   # the 3 branches callers actually make
      llm_payload: str                               # what the LLM sees (always present)
      slim_event: dict                               # what the frontend SSE sees (always present)
      geojson_ref: str | None                        # set only when status=="ok" and a layer was produced
      raw_result: Any                                # for step-completion tracking
      error_msg: str | None                          # set only when status=="error"
  ```

  Dispatch owns "what happened"; the caller owns "how to tell the world about it." The result does
  *not* carry methods that emit SSE or touch the task tracker — that would couple dispatch to the
  presentation/execution-tracking layer and break locality. This deliberately rejects the deeper
  alternative (an outcome object that also owns SSE/tracker interaction) for that coupling reason.

- **Pi path consumption — dispatch-once, session-keyed result cache, two adapters.** The Pi path
  dispatches once (in the HTTP tool-callback), and the result is cached keyed by session + tool
  call. The HTTP-callback adapter translates `llm_payload` into the Pi wire response. When Pi
  streams the tool-execution-end event, the SSE adapter reads the *cached* result and emits the
  frontend event from its `slim_event`/`geojson_ref`. One service entry point, two adapters. The
  cache is justified because Pi echoes the result, but the frontend needs the *server's* view
  (slimmed and ref-bearing), which the server already computed — re-deriving it would split
  dispatch into two entry points and reintroduce the shallowness this effort removes. The cache is
  session-scoped, short-lived (one turn), and an internal seam of the service — not exposed in the
  interface.

- **Migration — expand–contract, Pi path first.** The service is added beside the existing
  dispatcher (expand); the Pi path is migrated first because it is opt-in and it is the path with
  the bug, so migrating it *is* the fix and it serves as a canary; the legacy path is migrated last
  because it is the working production default; finally the old dispatcher module is removed
  (contract). Each step keeps CI green because the service and old code coexist until the final
  delete.

## Testing Decisions

The interface is the test surface. A good test asserts an observable outcome through the service's
interface, not internal state — and survives internal refactors.

- **Interface tests on `dispatch()`.** Test the service directly with a mock tool registry and the
  real in-memory `session_data_manager` (both already used by the existing dispatcher test, so no
  new harness). Cover the three status branches (ok / repeated / error). The load-bearing test that
  locks the regression this whole effort fixes: *a tool returning a FeatureCollection yields
  `geojson_ref is not None`* — the exact behaviour the Pi path drops today.
- **Delete the stale shallow tests.** The existing dispatcher tests that assert on the flat raw
  field bag become waste once interface-level tests exist (the deepening guidance is explicit: old
  unit tests on the superseded shape are deleted, not layered on). Tests of the pure
  `is_suspicious_result` helper are retained — that function stays.
- **Pi-adapter contract tests.** Test the two adapters off the cached result: the HTTP-callback
  translation (`dispatch()` → Pi wire response content) and the SSE translation (cached result →
  frontend event, asserting `geojson_ref` round-trips into the emitted payload). This closes the
  intent of the open "real LLM E2E" ticket without requiring a real LLM — an adapter contract test
  is the right seam, because the regression was a contract failure, not an LLM-behaviour failure.

## Out of Scope

- **The AgentRuntime seam (architecture candidate #2).** Unifying the route-level `if _use_pi_bridge`
  branches behind a shared `AgentRuntime` interface is a natural sequel, but it is a separate
  deepening with its own decision tree. This spec is dispatch only.
- **The SSE event catalogue (architecture candidate #3).** A single typed event vocabulary shared
  between Python and the frontend becomes much cheaper to extract *after* the dispatch sites have
  converged via this work; it is explicitly deferred and tracked as a follow-up.
- **The tier-3 authorization module (architecture candidate #5).** Folding tier authorization into
  the registry or a dedicated module folds naturally into the dispatch service, but it is not the
  goal of this spec; if it falls out cleanly during the expand step it may be included, otherwise
  it stays as-is.
- **Re-litigating ADRs 0001–0005.** Fetch-on-Demand / ref_id, agent-centric, hybrid async compute,
  redis-or-memory fallback, and the tiered tool catalog are all settled and respected.

## Further Notes

- **Decisions were grilled, not assumed.** The six load-bearing decisions above (scope, seam,
  interface, Pi-consumption, tests, migration) were each put to the maintainer one at a time and
  resolved with a recommended answer; the discriminated-result interface and the dispatch-once +
  cache shape were chosen against named alternatives (outcome-object-with-methods; two service
  entry points).
- **The regression is verified, not theoretical.** The Pi path's dispatch never calls the session
  data store, so no `ref_id` is produced; the slim helper then strips the `geojson` field; the
  frontend's layer-mount logic keys off the absent cursor and mounts nothing. Confirmed at the line
  level during grilling.
- **An ADR is intended.** This work is hard to reverse (once both paths route through the service,
  the dispatcher's structure is baked in), surprising without context (the session-keyed cache and
  the two-adapter shape raise "why not just dispatch in the SSE handler?"), and the result of a real
  trade-off (discriminated result over the deeper outcome-object, to keep dispatch decoupled from
  presentation). It will be recorded as ADR-0006 alongside the build, referencing this spec.
- **The `Tool dispatch` domain term is settled.** It has been added to `CONTEXT.md` — "dispatch"
  names the whole validate→store→slim→self-heal chain, not just the `registry.dispatch()` call
  inside it. This sharpens the glossary regardless of whether the build proceeds immediately.
- **Branch hygiene.** This work is backend and unrelated to the open frontend branch
  (`fix/remaining-type-safety-and-state`, 3 commits ahead of master, verified, not yet pushed). The
  build should start on a fresh branch off `master`, not pile onto the frontend branch.
- **Tracker convention.** Follows the project's local tracker: one issue file per unit under
  `.scratch/<feature>/issues/NN-*.md`, `ready-for-agent` status, numbered in dependency order
  (blockers first). The expand–contract migration maps directly to four tickets: expand, migrate Pi,
  migrate legacy, contract — each blocked by its predecessor as the Q6 sequencing requires.
