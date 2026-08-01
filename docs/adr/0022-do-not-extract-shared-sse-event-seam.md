# Do not extract a shared SseEventEmitter seam between the two agent loops

**Status:** accepted

We will **not** extract a shared `SseEventEmitter` module that both agent loops
(`ChatEngine` and `PiBridge`) drive. The two paths keep their independent SSE
payload construction. The module-global dispatch-result cache in
`agent_pi_bridge.py` stays as-is.

## Context

Architecture-review batch 3 (Candidate #4, 2026-08-01 report) claimed the two
agent paths "emit the same SSE events but build payloads independently and have
already drifted" — citing Pi hardcoding `step_index=0` and aliasing
`task_id := session_id` — and proposed a shared emitter to "kill the drift" and
"dissolve the module-global dispatch-result cache."

A code + frontend investigation found the friction is substantially weaker than
reported: the drift is latent, the frontend consumer that would surface it is
dead code, and the cache is deliberate architecture rather than a workaround.

## What the investigation found

### 1. The `step_index` drift is latent, not functional

The report's headline drift is real at the wire: ChatEngine emits
`"step_index": len(task.steps)` (`chat_engine.py:706`); PiBridge emits
`"step_index": 0` with a `TODO: derive from actual step metadata` comment
(`agent_pi_bridge.py:596`).

But the frontend never reads it. The task-progress UI renders the step list
with its own array index:

```tsx
// frontend/components/chat/task-progress.tsx:135
{task.steps.map((step, idx) => (
  <div key={step.id} ...>
    ... [{step.tool}] ...   // renders step.tool, step.status, step.error
```

`grep` for `stepIndex` / `step_index` across the frontend returns the store
action signature (`taskSlice.ts:16 stepStart(taskId, stepId, stepIndex, tool)`)
and the type field (`hud-types.ts:17 stepIndex: number`) — i.e. the field is
**stored** on the step object but **never read** by any renderer or logic. The
Pi-vs-CGE divergence in `step_index` therefore produces no observable
difference today. It is a latent footgun, not an active bug.

### 2. The frontend step tracker is dead code

This is the deeper finding. The store actions that would consume these SSE
events — `taskStart`, `stepStart`, `stepResult`, `stepError`
(`taskSlice.ts:13-86`) — are **never dispatched in production**:

- `grep` for `taskStart(` / `stepStart(` outside the slice definition and test
  files returns **zero** hits in `app/` or non-test `frontend/`.
- The real SSE handler (`frontend/lib/hooks/use-sse-stream.ts`) handles `token`,
  `content`, `step_result`, `plan_ready`, `plan_step_done`, `plan_finalized`,
  `step_error`, `task_error`, and `explorer_progress` — but **never calls**
  `taskStart` / `stepStart` / `stepResult`. The only `useHudStore` dispatches
  from the SSE handler are `addLayer`, `updateLayer`, `setCartographyTitle`,
  and `updateExplorerTask` (a different slice).
- Consequently `currentTask` is **always `null`** in production, the
  `currentTask && <TaskProgress>` guard (`chat-panel.tsx:70`) is always false,
  and `TaskProgress` never renders.
- The only callers of these actions are `slices.test.ts` (unit tests) and
  `test-utils.tsx` (a mock).

So the entire `step_index` / `step_id` / `task_id` payload contract — for both
paths — is dead on the receiving end. The `step_result` event *is* consumed,
but only for `geojson_ref` / `result.command` / layer-mounting
(`use-sse-stream.ts:125-200`); its `task_id`/`step_id`/`step_index` fields are
not read there either.

### 3. The dispatch-result cache is documented architecture, not a workaround

The report framed `_dispatch_result_cache` (`agent_pi_bridge.py:109-128`) as a
"workaround for the lack of a shared turn-context object." The code shows the
opposite: it is a deliberate, commented design (unified-tool-dispatch ticket 02)
with read-after-clear semantics, per-session cleanup
(`_clear_session_dispatch_cache`), and an explicit fallback path on cache miss.
It exists because the Pi path genuinely has **two non-shareable adapters**: an
HTTP callback (`dispatch_tool`, the `/pi-tools/execute` route) and an SSE event
stream (`_handle_tool_execution_end`), which run at different times and cannot
share state via a function call. The cache *is* the shared abstraction between
those two adapters; an `SseEventEmitter` would not dissolve it, because the
emitter runs in the SSE adapter while the dispatch result is produced in the
HTTP-callback adapter — the rendezvous problem is unchanged.

### 4. `task_id` aliasing is correct, not drift

The report flagged Pi's `task_id := session_id` (`_base_step_payload`,
`agent_pi_bridge.py:671`) as drift against ChatEngine's real task id. But the
Pi path has no `TaskTracker` and emits no separate task identity — the session
id *is* the task identity on that path by design (the `task_start` event at
`agent_pi_bridge.py:507` sets `"task_id": session_id`). There is no second id
being aliased; the field is honestly populated. Since the frontend doesn't
dispatch `taskStart` anyway (see #2), the value is moot in production.

## Decision

Do not extract a shared `SseEventEmitter`. Keep the two paths' independent
payload construction and the module-global dispatch cache.

This applies the bar ADR-0014 and ADR-0018 established: an architecture-review
candidate whose friction is disproven by investigation is recorded as rejected
rather than acted on. The SSE *vocabulary* is already shared (`sse_event()` in
`app/utils/sse.py`, same event names on both paths); what the candidate proposed
to share — the per-event payload *construction* — front-ends onto a dead
consumer.

## Recorded sub-problem (not acted on now)

The frontend task tracker is dead code: `taskStart` / `stepStart` /
`stepResult` / `stepError` in `taskSlice.ts`, the `TaskProgress` component, and
the `currentTask` state are all unreached in production (the SSE handler never
dispatches them). This is a real cleanup opportunity — deleting them, or
*wiring* them to the SSE handler to revive the step-progress UI — is independent
of C4 and can proceed anytime as frontend work. It is recorded here because the
C4 investigation surfaced it, not because the SSE seam would address it.

A separate finding: the `step_index` divergence is a latent footgun. If the
frontend tracker is ever revived, PiBridge must derive `step_index` from real
metadata (its current `TODO`), not hardcode `0`. That fix belongs to whatever
change revives the tracker, not to a backend seam extraction.

## What we are not doing

- No `SseEventEmitter` / shared payload-construction module.
- No removal or relocation of `_dispatch_result_cache` — it is the deliberate
  rendezvous between the Pi HTTP-callback and SSE adapters.
- No change to the `step_index` value either path emits (latent; revisit if the
  frontend tracker is revived).
- No deletion of the dead frontend tracker in this round (recorded above as a
  separate, optional cleanup).

## Trigger to revisit

Reopen only if **a real consumer of the step-progress payload contract
appears** — i.e. the frontend tracker is wired to the SSE handler (reviving
`TaskProgress`), or a third transport is added that must emit the same
`step_*` / `task_*` payloads. At that point (a) the `step_index` divergence
becomes functional and must be fixed at the Pi source, and (b) a shared emitter
earns its keep with ≥2 real consumers driving it.

A re-suggestion framed as "the two paths duplicate SSE payload construction"
does not meet this bar unless it first shows the constructed payloads are
actually consumed downstream. Two builders of an unread contract is not drift
worth a seam.
