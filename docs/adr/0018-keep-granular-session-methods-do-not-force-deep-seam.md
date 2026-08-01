# Keep the granular session methods — do not force a 4-method deep seam

**Status:** accepted

We will **not** refactor all call sites to use only `load_context` and `commit_dispatch`,
nor deprecate the granular session methods. The granular methods serve genuine non-dispatch
concerns (frontend perception, ref/alias resolution, lifecycle) that the 4 deep methods
cannot express.

## Context

Architecture-review Candidate #3 (2026-08-01 report) claimed callers "bypass
`SessionContext`" and invoke 14 shallow granular methods directly, and proposed refactoring
all call sites to use `load_context` / `commit_dispatch`, deprecating the granular methods
to enforce a strict 4-method deep seam.

A code investigation contradicted both the diagnosis and the fix, while surfacing a real
(but different) sub-problem.

## What the investigation found

### 1. There is no bypassed `SessionContext` facade

`SessionContext` (`session_data_protocol.py:13-22`) is a plain dataclass — the return type
of `load_context`, not a service class callers route through. Nobody bypasses it. Callers
correctly use the `session_data_manager` singleton backend directly, which ADR-0004
mandates ("the fallback is transparent to the rest of the codebase"). The "bypass" framing
misreads a dataclass as a service.

### 2. The granular methods serve concerns `commit_dispatch` cannot express

`commit_dispatch` (`session_data.py:199-212`) is structurally a single-event emitter — its
body only does `append_event("tool_executed", payload)`. It has no parameters for viewport,
layer opacity, alias, or lifecycle. The granular methods break into groups, each serving a
non-dispatch concern:

- **Map-state** (`set_map_state`, `update_layer_in_state`, `remove_layer_from_state`,
  `get_map_state`): driven by `ws_service.py` for **frontend perception events** (pan/zoom,
  layer toggle, base-layer change). There is no dispatch cycle for a user dragging the map.
  `commit_dispatch` cannot serve these — it has no `viewport`/`layer_id`/`opacity` params.
- **Ref/alias** (`set_alias`, `resolve_alias`, `list_refs`): `resolve_alias` has **10
  callers** (layer_manager, registry, map_view) — alias→ref translation is a read-time
  concern baked into tool-argument resolution. `commit_dispatch` does not create aliases
  (the `alias_layer` tool does, via `set_alias`).
- **Event log** (`append_event`, `get_event_log`): `append_event` has **7 callers** in
  `ws_service.py` emitting non-tool events (`layer_toggled`, `base_layer_changed`,
  `upload_completed`). These are independent audit streams, not dispatch results.
- **Lifecycle** (`get_started_at`, `get_session_metadata`, `clear_session`): orthogonal to
  dispatch. `get_session_metadata` is the *actual* deep read used by `context_builder.py`;
  `load_context` is unused (see below).

Forcing everything through 4 methods would require either bloating `commit_dispatch` to
accept viewport/layer/opacity/alias params (destroying its "dispatch" meaning) or losing
frontend and lifecycle features.

### 3. The real sub-problem: the deep layer is an unfulfilled promise, not a bypassed truth

The investigation surfaced that the deep methods are themselves anemic or unadopted — the
*opposite* of the report's "callers should use the deep methods" framing:

- `load_context` has **zero external callers** — dead code. (`get_session_metadata` is what
  `context_builder` actually calls for a deep read.)
- `commit_dispatch` has **one** caller (`tool_dispatch_service.py:392`) and its body is a
  thin wrapper around `append_event("tool_executed", ...)`. The protocol docstring promises
  "atomic commit of dispatch result AND state," but the implementation stores no ref,
  mutates no map state, and touches no alias — it appends one event.
- The protocol's own comment labels the 14 methods `# 兼容过渡 API` ("compatibility
  transition API"), meaning the deep layer was aspirational and was never enforced because
  it never grew the capabilities the granular methods provide.

## Decision

Keep the granular session methods. Do not force call sites through `load_context` /
`commit_dispatch`, and do not deprecate the granular API. The dual surface is not a leak —
the granular methods own concerns the deep methods were never built to handle.

## Recorded sub-problem (not acted on now)

The deep-method layer is an unfulfilled promise: `load_context` is dead code and
`commit_dispatch` is an anemic single-event wrapper that does not deliver the "atomic
dispatch commit" its docstring claims. This is recorded here so a future change can address
it deliberately — see "Trigger to revisit."

## What we are not doing

- No deprecation of the 14 granular methods.
- No forced migration of call sites to the 4 deep methods.
- No deletion of `load_context` / `commit_dispatch` in this round (they stay; their fate is
  a separate decision under the trigger below).

## Trigger to revisit

Two distinct triggers:

1. **Strengthen the deep layer**: if a real atomic-unit-of-work need appears (e.g. a
   dispatch must atomically store-ref + update-map-state + append-event under one lock to
   avoid a torn-read race), then *grow* `commit_dispatch` to deliver the docstring's
   promise — do not shrink the call sites onto an anemic method.
2. **Remove the dead deep layer**: if `load_context` stays at zero callers and
   `commit_dispatch` stays a 1-caller anemic wrapper with no atomicity benefit, a cleanup
   pass may delete `load_context` and inline `commit_dispatch`'s single caller to
   `append_event`. This ADR records the anemia so that future cleanup is not blocked by an
   assumption that the deep layer is load-bearing.

A re-suggestion to "force everything through 4 deep methods" does not meet either trigger
unless it first shows the deep methods can actually express the concerns (frontend,
ref/alias, lifecycle) the granular methods own.
