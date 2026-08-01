# ADR-0031: Split `agent_pi_bridge.py` into Deep Modules

- **Status**: Accepted
- **Date**: 2026-08-01
- **Deciders**: Antigravity AI Team, Lead Architect
- **Technical Story**: Candidate F3 (`agent_pi_bridge.py` refactoring into single-responsibility modules)

---

## Context

`app/agent_pi_bridge.py` originally held 600+ lines of code mixing:
1. Subprocess lifecycle management (`Popen`, stdio streams, JSON-RPC counter/futures).
2. SSE event translation (`AgentSessionEvent` -> SSE strings, text/thinking token formatting, compaction handling).
3. ADR-0022 rendezvous dispatch cache (`_dispatch_result_cache`, `_session_executed_sets`, `get_cached_dispatch_result`).
4. HTTP tool dispatch models (`PiToolRequest`, `PiToolResponse`, `dispatch_tool`).

This created tight coupling, making unit testing of RPC multiplexing or event mapping require spawning/mocking full bridge instances and subprocesses.

---

## Decision

We split `agent_pi_bridge.py` into focused, single-responsibility modules while retaining the ADR-0022 cache boundary in `agent_pi_bridge.py`:

1. **`app/services/chat/pi_rpc_client.py` (`PiRpcClient`)**:
   - Manages subprocess lifecycle (`Popen`, stdio streams, `start()`, `stop()`).
   - Handles async JSON-RPC request-response multiplexing (`request(cmd, data)`) and event queue management (`events`).
   - Exposes clean properties (`events`, `process_died`).

2. **`app/services/chat/pi_event_mapper.py` (`map_event_to_sse`)**:
   - Pure function mapping Pi `AgentSessionEvent` dicts to SSE string format.
   - Takes `cache_lookup` callable parameter `(session_id, tool_call_id) -> Optional[ToolDispatchResult]` injected by `PiBridge.stream_prompt`.
   - Zero internal state, zero knowledge of cache internals.

3. **`app/agent_pi_bridge.py` (`PiBridge`)**:
   - High-level coordinator delegating RPC multiplexing to `self._rpc: PiRpcClient`.
   - Delegates event mapping to `map_event_to_sse(event, self._session_id, cache_lookup=get_cached_dispatch_result)`.
   - Preserves ADR-0022 rendezvous cache boundary (`_dispatch_result_cache`, `_session_executed_sets`, `dispatch_tool`, `get_cached_dispatch_result`).

---

## Consequences

- **Testability**: Unit tests for RPC multiplexing (`test_pi_rpc_client.py`) and event mapping (`test_pi_event_mapper.py`) run in isolation without subprocess dependencies.
- **Maintainability**: Clear separation between transport (RPC client), presentation (SSE mapper), and coordination/caching (`PiBridge`).
- **Backward Compatibility**: `PiBridge` interface remains identical; all existing callers work without changes.
