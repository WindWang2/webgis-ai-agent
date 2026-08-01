# 33. Deep ChatContextAssembler & ContextAssemblyResult Seam

Date: 2026-08-02

## Status

Accepted

## Context

Prior to this decision, prompt context assembly was managed via `app/services/chat/context_builder.py`, which acted as a shallow re-export facade exposing 20+ internal utility functions (`_xml_fence`, `_untrusted`, `truncate_history_by_budget`, `build_layer_schema`, etc.) across multiple sub-modules (`history_compression.py`, `layer_schema.py`, `formatters.py`, `session_overview.py`).

Callers like `ChatEngine` were forced to orchestrate low-level details (token budget calculations, history truncation, and XML security fencing) directly, reducing caller leverage and creating tight coupling to module-internal functions.

## Decisions

1. **Deep Module `ChatContextAssembler`**: Created `app/services/chat/context_assembler.py` defining a single primary interface method:
   ```python
   class ChatContextAssembler:
       def __init__(self, store: SessionStoreProtocol | None = None): ...
       async def assemble(self, session_id: str, messages: list[dict]) -> ContextAssemblyResult: ...
   ```
2. **Structured Observability Value Object**: Created `ContextAssemblyResult` dataclass (`messages`, `estimated_tokens`, `history_turns_included`, `layer_count`, `.to_messages()`) exposing context metadata without requiring callers to re-parse assembled prompt strings.
3. **SessionStore Dependency Injection**: `ChatContextAssembler` accepts an optional `store: SessionStoreProtocol | None = None` parameter (defaulting to the `session_data_manager` singleton), allowing fast unit testing with mock or in-memory session stores.
4. **Backward Compatibility Adapter**: `app/services/chat/context_builder.py` delegates `compose_request_messages` to `ChatContextAssembler` as a thin deprecation shim while maintaining legacy symbol exports for existing test suites.

## Consequences

- **Leverage**: `ChatEngine` calls a single `assemble(...)` method rather than coordinating multiple prompt formatting helpers.
- **Locality**: History truncation, token estimation, and security fencing are concentrated inside the context module.
- **Testability**: `tests/unit/test_chat_context_assembler.py` verifies context assembly directly through the primary `assemble(...)` seam.
