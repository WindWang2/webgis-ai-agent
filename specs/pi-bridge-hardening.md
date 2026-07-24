# Spec: Pi Bridge Hardening — Post-Integration Review Fixes

## Problem Statement

The Pi submodule integration is functional and all 1182 tests pass, but a code review (d49858d...HEAD, two-axis review) identified 13 findings across Standards and Spec axes. The most impactful issues are:

1. **Deprecated `asyncio.get_event_loop()`** — crashes on Python 3.12+ (removed in 3.12)
2. **`prompt()` errors swallowed silently** — when Pi returns an error, the HTTP response shows empty content with no failure indication to the user
3. **`clear_session` Pi path is a dead stub** — silently falls through to legacy engine, giving false confidence that the feature flag is honored
4. **Repeated `USE_NEW_AGENT` guard** — three copies of the same conditional, any new Pi-backed route must duplicate it
5. **`asyncio.sleep(1)` as only readiness signal** — if Pi takes >1s to start, first RPC request fails or hangs
6. **`bufsize=0` with `text=True`** — invalid combination on some Python builds

These are not architectural concerns — the integration design is sound. They are hardening issues that block production readiness of the Pi bridge path.

## Solution

Apply targeted fixes to the Pi bridge and its consumers, grouped into three priority tiers. No architectural changes; all fixes are surgical corrections to existing code.

## User Stories

1. As a Python developer, I want the Pi bridge to use `asyncio.get_running_loop()` instead of `get_event_loop()`, so that it runs on Python 3.12+ without deprecation warnings or crashes.
2. As a backend engineer, I want `prompt()` errors to surface as HTTP error responses, so that users see a meaningful error message instead of an empty response when Pi fails.
3. As a backend engineer, I want `clear_session` to either properly clear Pi state or explicitly skip the Pi path, so that the feature flag behavior is honest and predictable.
4. As a Python developer, I want the `USE_NEW_AGENT` guard extracted into one helper, so that adding new Pi-backed routes doesn't require duplicating the same conditional three times.
5. As a backend engineer, I want the Pi bridge to wait for actual readiness (not a fixed sleep), so that the first RPC request doesn't fail due to a race condition.
6. As a Python developer, I want the subprocess pipes configured correctly, so that `ValueError` doesn't crash startup on some Python builds.
7. As a maintainer, I want magic timeouts (`1s`, `2s`, `30s`, `300s`) extracted into named constants, so that tuning doesn't require hunting through the bridge code.
8. As a maintainer, I want `get_pi_bridge(extension_paths)` to either accept new paths on subsequent calls or raise a clear error, so that the API contract is honest.
9. As a backend engineer, I want `stream_prompt` to yield explicit `task_error` and `error` SSE events on failure, so that the frontend can display meaningful error states instead of silently stopping.
10. As a maintainer, I want compaction event strings (`[压缩上下文...]`) either localized or removed from the bridge layer, so that the RPC layer doesn't embed presentation strings.
11. As a maintainer, I want `slim_event_result` imported lazily inside `_map_event_to_sse` (it already is), so that the bridge doesn't couple to the chat SSE layer at import time.
12. As a backend engineer, I want `get_messages()` removed or wired to a route, so that dead public API surface doesn't accumulate.
13. As a CI maintainer, I want all 1182 tests to continue passing after fixes, so that no regression is introduced.

## Implementation Decisions

- **`asyncio.get_running_loop()`**: Replace both occurrences of `asyncio.get_event_loop()` in `_read_responses` and `_send_request`. This is a direct substitution — `get_running_loop()` returns the same loop object when called inside a coroutine.

- **`prompt()` error surfacing**: The `prompt()` method currently returns `{"content": "", "error": "..."}` on `PiRpcError`. The consumer (`chat.py` `chat_completions`) drops the `error` field. Fix at the bridge layer: raise `PiRpcError` after logging, and let the route layer catch it and return an `HTTPException(502, ...)` or include the error in the `ChatResponse` content.

- **`clear_session` Pi path**: Two options — (a) implement via `pi_bridge.abort()` + state reset, or (b) remove the `if USE_NEW_AGENT` branch until a real implementation exists, falling through to legacy with an explicit comment. Given the spec's "honest feature flag" principle, option (b) is preferred until `abort()` semantics are verified.

- **`USE_NEW_AGENT` guard extraction**: Create a single helper `def _use_pi_bridge() -> bool` that returns `USE_NEW_AGENT and pi_bridge is not None`. Replace three copies in `chat.py`. This is a 3-line addition and 3-line reduction.

- **Pi readiness**: Replace `await asyncio.sleep(1)` with a readiness check: send a lightweight `get_state` RPC command after startup and wait for the response, with a timeout. If Pi is already running, this returns immediately. If not, it blocks until ready or fails fast.

- **`bufsize=0` with `text=True`**: Change to `bufsize=1` (line-buffered) or remove the parameter (default is line-buffered in text mode). This is a one-word fix.

- **Magic timeouts → named constants**: Extract `PI_STARTUP_READY_TIMEOUT`, `PI_RPC_TIMEOUT`, `PI_EVENT_DRAIN_TIMEOUT`, `PI_EVENT_STREAM_TIMEOUT` as module-level constants in `agent_pi_bridge.py`. Each maps to one of the existing hard-coded values.

- **`get_pi_bridge(extension_paths)` contract**: Document that `extension_paths` is only honored on first call. If re-initialization is needed, the caller must call `shutdown_pi_bridge()` first. No behavior change — just make the contract explicit.

- **Compaction strings**: Move `[压缩上下文...]` and `[上下文压缩完成]` to a constants block at the top of `_map_event_to_sse`, with a comment that they should be replaced with i18n-aware strings when the frontend supports localized SSE events.

- **`get_messages()`**: Mark as `# TODO: wire to /api/v1/chat/messages route` or remove it. Minimal surface area is preferred.

## Testing Decisions

- **What makes a good test**: Test external behavior — the HTTP response a route returns, the SSE events `stream_prompt` yields, the error surfaced to the user. Do not test internal implementation details like queue state or future management.
- **Which modules will be tested**: `app/agent_pi_bridge.py` (event mapping, error handling, readiness), `app/api/routes/chat.py` (Pi path branching, error surfacing), `app/api/routes/pi_tools.py` (tool dispatch, session propagation).
- **Prior art**: The existing `tests/test_pi_integration.py` (28 tests) already covers event mapping, subprocess flow, and tool dispatch. New tests should follow the same patterns: mocked subprocess, sync readline via `run_in_executor`, SSE parsing helper.
- **Regression gate**: All 1182 existing tests must continue passing. No exceptions.

## Out of Scope

- Architectural redesign of the Pi bridge (already settled)
- Migrating more routes to Pi (only the three existing: `/completions`, `/stream`, `clear_session`)
- Adding new Pi RPC commands beyond what's already implemented
- Frontend changes to handle new SSE event types
- Production deployment configuration (Docker, process management, etc.)
- Real LLM end-to-end test (requires API keys and network — deferred)

## Further Notes

- The fixes are ordered by impact: P0 (crash/silent-failure) → P1 (dead stubs) → P2 (cleanup).
- No changes to the extension (`app/extensions/webgis-tools/index.ts`) or the Pi submodule itself.
- The `clear_session` decision should be confirmed with the team: either implement it or remove the branch. Don't leave it as a silent pass.
