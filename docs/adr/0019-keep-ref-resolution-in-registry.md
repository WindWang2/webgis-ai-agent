# Keep ref-resolution in registry.dispatch — input-deref is the shared three-way seam

**Status:** accepted

We will **not** move reference resolution (`ref:xxx` dereferencing) out of
`ToolRegistry.dispatch` into `ToolDispatchService`. Input dereferencing stays in the
registry where all three callers reach it; output offloading stays in the dispatch service
where only the agent loop wants it.

## Context

Architecture-review Candidate #4 (2026-08-01 report) claimed `ToolRegistry` performs stateful
I/O (dereferencing `ref:xxx` cursors) while `ToolDispatchService` performs payload offloading,
framing this as "reference lifecycle logic split across two places." The proposed fix: move
reference resolution INTO `ToolDispatchService` so `ToolRegistry` becomes "completely pure and
stateless."

A code investigation found the framing misidentifies two distinct phases as one lifecycle,
and the proposed move would break two accepted (ADR-0014) non-agent dispatch paths.

## What the investigation found

### 1. The "split lifecycle" is two different phases of one round-trip

These are the two ends of the ADR-0001 Fetch-on-Demand round-trip, not one lifecycle torn in
half:

- **ref-RESOLUTION** (`registry.py:185-199`, calling `_resolve_references` L269-312) =
  **INPUT** dereferencing: `ref:xxx` cursor → GeoJSON payload, *before* the tool runs.
- **payload-OFFLOADING** (`tool_dispatch_service.py:343-344`) = **OUTPUT** storage: large
  GeoJSON result → new `ref:geojson-xxx` cursor, *after* the tool runs.

Input-deref belongs with execution; output-store belongs with the agent-loop slimming chain.
They are correctly co-located with their respective phase.

### 2. Input-deref is shared infrastructure for all three callers

`_resolve_references` fires for **every** caller of `registry.dispatch` that passes a
`session_id` (L185: `if session_id and isinstance(arguments, dict):`). The trigger is any
string arg matching `ref:` or a registered alias, with skip-keys
`{"ref_id", "layer_ref", "layer_id", "plan_id", "before_ref"}` exempted. All three callers
pass a `session_id`:

- The agent loop (via `ToolDispatchService`, which calls `registry.dispatch` internally).
- `plan_mode.py:287` — `await registry.dispatch(step.tool, resolved_args, session_id=...)`.
  An LLM-authored plan step can carry a literal `ref:geojson-xxx` in its args.
- `/tools/execute` (`chat.py:359`) — `await registry.dispatch(tool_name, args,
  session_id=...)`. The admin client can submit `ref:xxx` arguments.

### 3. Moving ref-resolution to ToolDispatchService would BREAK the non-agent paths

This is the hard blocker. `plan_mode` and `/tools/execute` call `registry.dispatch` **directly**,
bypassing `ToolDispatchService` — and ADR-0014 (accepted) says that is correct, because they
are not agent-loop paths and do not want the six agent-loop cross-cutting concerns (repetition
guard, slimming, broadcast, etc.).

If ref-resolution moved into `ToolDispatchService`, those two callers would receive
**unresolved `ref:xxx` strings** — their GeoJSON arguments would never be dereferenced. They
would have to either route through the service (dissolving ADR-0014) or re-implement deref
themselves, re-creating the exact drift ADR-0006 was built to prevent.

### 4. "Pure, stateless registry" is not a real win

The I/O does not disappear — it relocates one layer up into the dispatch service, which must
then take raw `ref:xxx` args and deref them before calling the registry. The registry becomes
"pure" only by making the service impure, and the non-agent callers lose a capability they
currently get for free. This is relocating complexity, not removing it.

## Decision

Keep reference resolution (`_resolve_references`) in `registry.dispatch`. Keep payload
offloading (large-result → `ref:xxx`) in `ToolDispatchService`. Do not move input-deref into
the service, and do not pursue a "pure, stateless registry."

## Relationship to ADR-0014

ADR-0014 established that `plan_mode` and `/tools/execute` correctly call `registry.dispatch`
directly. This ADR extends that: ref-resolution must therefore *also* live in
`registry.dispatch`, because it is shared infrastructure all three callers need. ADR-0014's
"Trigger to revisit" names the *inverse* case (a non-agent caller needing an agent-loop
concern like output-offloading); this candidate proposes the opposite direction and hits the
same blocker from the other side.

## What we are not doing

- No relocation of `_resolve_references` into `ToolDispatchService`.
- No split of `registry.dispatch` into a "with-deref" / "without-deref" entry pair (it would
  re-create the two-path drift ADR-0006 closed, just inside the registry).
- No change to where output-offloading happens (stays in the dispatch service, agent-loop only).

## Trigger to revisit

Reopen only if **the input-deref concern and the output-offload concern genuinely merge into
one unit of work** — e.g. if a single caller must atomically deref-input + execute + offload-output
under one lock to avoid a torn state. At that point the right move is a narrower transaction
seam, not uprooting input-deref from the shared registry. A re-suggestion framed as "registry
should be pure" does not meet this bar unless it first shows the non-agent callers no longer
need `ref:xxx` dereferencing.
