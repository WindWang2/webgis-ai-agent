# 02 — Migrate Pi path: route agent_pi_bridge through ToolDispatchService

**What to build:** Point the Pi bridge's tool dispatch at the new service, with dispatch-once
semantics and a session-keyed result cache so two adapters share one dispatch. This is the
bug-fixing canary: under `USE_NEW_AGENT`, GIS tools will now actually store their GeoJSON, produce a
`ref_id`, and the produced layers will mount on the map — the silent regression the spec exists to
fix. Pi is opt-in, so if this goes wrong only `USE_NEW_AGENT=true` users are affected, and they were
already getting the bug.

**Blocked by:** 01 — Expand: add ToolDispatchService beside the legacy dispatcher.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Pi bridge's tool-callback (the `/pi_tools/execute` route) calls `ToolDispatchService.dispatch()`
      once and translates the result's `llm_payload` into the Pi wire response (`PiToolResponse.content`)
- [x] Session-keyed result cache added: keyed by session + tool call, short-lived (one turn), an
      internal seam of the service — not exposed in the interface
- [x] The streaming `_handle_tool_execution_end` reads the *cached* result and emits the frontend
      event from its `slim_event`/`geojson_ref` (the SSE adapter) — no longer re-deriving slim/ref
      from Pi's echoed result
- [x] The Pi path's standalone dispatch logic (the current `dispatch_tool` body that only
      validate/tier/run/normalize) is removed — it now delegates to the service
- [x] **Pi-adapter contract tests:** the HTTP-callback translation (`dispatch()` → Pi response
      content) and the SSE translation (cached result → frontend event) both round-trip `geojson_ref`
      into the emitted payload — the test that *would have caught* the current regression
- [x] This closes the intent of the open "real LLM E2E" ticket without requiring a real LLM — an
      adapter contract test is the right seam (the regression was a contract failure, not an
      LLM-behaviour failure)
- [x] Legacy path still untouched; full suite green
