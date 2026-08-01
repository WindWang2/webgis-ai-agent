# Keep non-agent dispatch paths on registry.dispatch — ToolDispatchService is agent-loop only

**Status:** accepted

We will **not** force `plan_mode` and the `/tools/execute` admin route through
`ToolDispatchService`. They call `registry.dispatch` directly, and that is correct.

## Context

Architecture-review Candidate #6 framed the two `registry.dispatch` callers outside
`ToolDispatchService` — `app/services/plan_mode.py:287` and
`app/api/routes/chat.py:359` (`/tools/execute`) — as a "dispatch path bypass" and an
"ADR-0006 contract leak," arguing they recreate the silent-drift regression class ADR-0006
was built to prevent (no `ref_id`, no WS broadcast, no `event_log`, no self-heal).

A code investigation contradicted the premise.

## What the investigation found

`ToolDispatchService.dispatch` is an **agent-loop contract**, not a general-purpose dispatch
primitive. Its signature takes what only an agent loop has:

- `tc: dict` — an OpenAI-style tool_call (`{id, function:{name, arguments}}`).
  Neither caller has this structure; they hold `(tool_name, args_dict)`.
- `executed_tools: set` — a per-task set for **repetition-interception**. `plan_mode` executes
  plan steps sequentially by design (no loop to intercept); `/tools/execute` is one-shot.

And the service layers **six** cross-cutting concerns that only the agent loop needs:

1. Repetition-intercept (loop guard) — agent-loop only.
2. Registry execution — shared (both callers get this from `registry.dispatch` directly).
3. Error path: `is_error_dict` routing + `tool_failed` event + self-heal wrapping —
   agent-loop UI (the event drives the `[环境感知]` context block). Both callers handle
   errors themselves: `plan_mode` checks `result.get("success") is False`; `/tools/execute`
   returns the raw dict to the HTTP client. The **error shape** itself (`std_error_response`
   with `code` + `correction_hint`) is already unified inside `registry.dispatch` (Candidate
   #5, ADR-0013-adjacent), so both callers receive the same standardized error dict regardless.
4. Large-GeoJSON → `ref_id` (Fetch-on-Demand) — **neither caller wants this.** `plan_mode`
   stores the raw result inline in `step_results` (it needs the payload, not a cursor);
   `/tools/execute` returns the raw result to the HTTP client.
5. Slim-for-LLM payload + suspicious-result self-heal tail — agent-loop LLM shaping only.
6. `event_log` write + WS broadcast — agent-loop live-UI only. `plan_mode` is headless
   plan execution; `/tools/execute` is admin diagnostics.

Of the six, only #2 (execution) and the error-*shape* half of #3 are shared — and both are
already owned by `registry.dispatch`. Forcing these two callers through `ToolDispatchService`
would require either a second entry point on the service (dissolving the "single dispatch"
contract ADR-0006 established) or adapters that discard 4 of the 6 concerns they'd be routed
through. The indirection would carry no benefit the callers want.

## Why this is not an ADR-0006 leak

ADR-0006 unified **the two agent dispatch paths** — the legacy ChatEngine path and the Pi
bridge path — because both genuinely needed the full six-concern chain (ref-storage,
broadcast, self-heal, slimming, repetition) and were silently drifting. That unification
holds: both agent paths still route through `ToolDispatchService`, and that is the contract
the ADR locked.

`plan_mode` and `/tools/execute` are not agent dispatch paths. They are a headless plan
executor and an admin diagnostic endpoint, with different contracts (inline results, no
Fetch-on-Demand, no live-UI broadcast). "Dispatch" in ADR-0006 names the agent-loop act
end-to-end; conflating it with "any tool execution" over-generalizes the ADR's scope. The
registry's public `dispatch()` being callable by these paths is not a standing invitation to
re-drift — it is the correct seam for callers that need execution without the agent-loop
cross-cutting chain.

## Decision

Keep `plan_mode` and `/tools/execute` on `registry.dispatch`. Do not narrow
`registry.dispatch` to internal, and do not add a second `ToolDispatchService` entry point
to accommodate them. The error *shape* is already unified at the registry (Candidate #5);
the agent-loop *cross-cutting chain* stays on the service where its two real consumers live.

## What we are not doing

- No change to `plan_mode`'s or `/tools/execute`'s dispatch call.
- No narrowing of `registry.dispatch` to `_dispatch_impl`-only.
- No second `ToolDispatchService` entry point.

## Trigger to revisit

Reopen when a **non-agent caller needs one of the agent-loop cross-cutting concerns**
(#1, #4, #5, or #6) — e.g. if `plan_mode` grows to need `ref_id` Fetch-on-Demand for very
large plan outputs, or if `/tools/execute` needs to fire WS broadcasts. At that point the
caller has a real need for part of the chain, and the right move is to extract the shared
concern(s) behind a narrower seam (not to force the whole agent-loop contract onto them).
