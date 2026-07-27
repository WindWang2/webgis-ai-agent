# ADR-006: Unified Tool Dispatch

## Context

The system had two live paths that performed tool dispatch — the legacy ChatEngine path
(production default, `USE_NEW_AGENT` off) and the Pi bridge path (opt-in, `USE_NEW_AGENT` on).
They shared a name and a domain concept but almost nothing else. The legacy
`app/services/chat/dispatcher.py::dispatch_tool` was genuinely deep: behind one signature it
owned validate, run, store-large-GeoJSON-to-`ref_id`, slim-for-frontend, intercept-repeated-calls,
and wrap-errors-as-self-healing-hints. The Pi path's `agent_pi_bridge.dispatch_tool` owned only
the first two and silently dropped the rest.

This produced a concrete, silent regression: under `USE_NEW_AGENT`, GIS tools ran and the LLM saw
their output, but produced layers never appeared on the map. The Pi path never stored the GeoJSON,
so no `ref_id` cursor was created; the SSE handler then slimmed the result and stripped the
`geojson` field entirely; the frontend's layer-mount logic keyed off the absent cursor and found
nothing to mount. No error, no log. No test covered it, because each path was tested in isolation
and no test exercised the full dispatch→frontend contract through the Pi path.

The deeper friction: the two implementations would keep drifting with every future tool-behaviour
change, because nothing structurally forced them to agree.

## Decision

Introduce a single in-process `ToolDispatchService` (`app/services/tool_dispatch_service.py`) that
owns the entire dispatch chain. Both the legacy ChatEngine path and the Pi bridge path route through
it. The service returns a **discriminated-result value** rather than the legacy flat 8-field dict:

```python
@dataclass
class ToolDispatchResult:
    status: Literal["ok", "repeated", "error"]
    llm_payload: str
    slim_event: dict
    geojson_ref: str | None
    raw_result: Any
    error_msg: str | None
```

Callers branch on `status` (the single discriminant) rather than reassembling a conclusion from a
bag of parallel booleans. `has_geojson` is gone — derivable as `geojson_ref is not None`.

Four design choices, each decided against a named alternative:

1. **In-process module, not a port.** `ToolDispatchService` is a plain module both callers import,
   not a formal port with HTTP/in-memory adapters. Dispatch is never deployed across a network
   boundary, so a port would be indirection with one adapter (the "one adapter = hypothetical seam"
   rule). Its one non-pure dependency — session data — already has a `SessionDataProtocol` port and
   an in-memory stand-in (ADR-004).

2. **Discriminated-result dataclass, not an outcome object with methods.** A deeper alternative
   would have given the result methods like `sse_event()` and `complete_step()` that own the SSE
   shaping and task-tracker interaction. Rejected: it would couple dispatch to the presentation and
   execution-tracking layers, breaking locality. Dispatch owns "what happened"; the caller owns
   "how to tell the world about it."

3. **Dispatch-once with a session-keyed result cache, two adapters — not two service entry points.**
   The Pi path dispatches once (in the HTTP tool-callback) and caches the `ToolDispatchResult` by
   `(session_id, toolCallId)`. The HTTP-callback adapter translates `llm_payload` → `PiToolResponse`;
   the streaming SSE adapter reads the cached result and emits `step_result` carrying `geojson_ref`.
   A two-entry-point alternative (dispatch for HTTP + shape-for-SSE) was rejected because it splits
   "dispatch" and reintroduces the shallowness this effort removes.

4. **Expand–contract migration, Pi path first — not big-bang or legacy-first.** The service was
   added beside the old dispatcher (expand); the Pi path migrated first because it is opt-in and is
   the path with the bug, so migrating it *is* the fix and it serves as a canary; the legacy path
   migrated last because it is the working production default; finally the old module was deleted
   (contract). Each step kept CI green because the service and old code coexisted until the final
   delete.

The pure helper `is_suspicious_result` (empty/error-result detection for self-heal hints) collapsed
to a single definition inside the service module.

## Consequences

**Positive:**
- The silent layer-mount regression under `USE_NEW_AGENT` is fixed and locked by a regression test
  (`test_sse_adapter_round_trips_geojson_ref`): a tool returning a FeatureCollection yields a
  `geojson_ref` that round-trips into the SSE `step_result` payload.
- Tool-behaviour changes (ref-storage, repetition, self-heal, slimming, broadcast) now apply to both
  agent paths from one module; the structural divergence cannot recur.
- The interface shrank: callers read one discriminant + typed fields instead of an 8-field bag of
  parallel booleans that could be combined into nonsense (e.g. `repeated=True AND is_error=True`).
- Dispatch is testable through one interface; the contract failures (missing ref, dropped error
  hints) are caught at the service seam regardless of which path runs.

**Negative:**
- The Pi path carries a module-level session-keyed result cache plus a per-session executed-set.
  This assumes the HTTP callback and the SSE stream hit the same process — true today (single
  uvicorn, no `--workers`, Pi subprocess owned by that process) but would break under multi-worker
  deployment. A latent stale-state bug also exists: `prompt`/`stream_prompt` clear state via
  `self._session_id`, which is only reassigned on a truthy `session_id`, so a `session_id=None`
  request clears the previous session's state. Both are tracked as follow-ups.
- The `status` branch cascade now appears in two consumers (`chat_engine.py` and
  `agent_pi_bridge.py`). Drift risk is bounded (both share the single service as the source of
  `status` assignment, and the two sites produce different outputs), but converging them into a
  typed SSE event catalogue remains a follow-up (architecture candidate #3).
- Tier-3 tool authorization is still enforced inline at the Pi boundary (`dispatch_tool`), not in
  the service — it is Pi-path-specific and was explicitly out of scope (architecture candidate #5).

## References

- Spec: `.scratch/unified-tool-dispatch/spec.md`
- Tickets: `.scratch/unified-tool-dispatch/issues/01`–`04`
- Supersedes the dispatch logic in the deleted `app/services/chat/dispatcher.py`
- Respects ADR-001 (Fetch-on-Demand / `ref_id`), ADR-002 (agent-centric), ADR-004
  (redis-or-memory session data)
