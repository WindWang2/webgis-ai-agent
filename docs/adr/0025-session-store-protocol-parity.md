# SessionStoreProtocol 100% Interface Parity across Backends

**Status:** accepted

We guarantee 100% interface parity between `SessionDataManager` (in-memory) and `RedisSessionDataManager` (Redis) behind the `@runtime_checkable` `SessionStoreProtocol` interface (ADR-0018).

## Context

`SessionStoreProtocol` (`app/services/session_data_protocol.py`) originally declared 14 fine-grained methods. Over time, both `SessionDataManager` and `RedisSessionDataManager` introduced `overwrite` (used by Plan Mode for in-place data key updates) and `cleanup_idle_sessions` (for LRU eviction of idle session stores).

Without explicit inclusion of these methods in `SessionStoreProtocol`, static type checkers could not guarantee complete substitutability, and runtime protocol checks via `isinstance` were not available.

## Decision

1. **Decorator & Parity**: Mark `SessionStoreProtocol` with `@runtime_checkable` and include all 16 fine-grained methods (`get`, `store`, `overwrite`, `set_alias`, `list_refs`, `resolve_alias`, `get_map_state`, `set_map_state`, `update_layer_in_state`, `remove_layer_from_state`, `get_event_log`, `append_event`, `get_started_at`, `get_session_metadata`, `clear_session`, `cleanup_idle_sessions`).
2. **Explicit Type Annotations**: Harmonize method signatures across `SessionDataManager` and `RedisSessionDataManager` with explicit return type hints (`-> str`, `-> bool`, `-> None`, `-> Optional[Any]`).
3. **Parametrized Compliance Tests**: Create `tests/unit/test_session_store_protocol.py` that executes a shared test suite against both backends to verify identical behavior, including `overwrite` key mutation semantics and `isinstance(..., SessionStoreProtocol)` checks.

## Consequences

- **Substitutability**: In-memory `SessionDataManager` can be used in test environments with 100% confidence that its behavior matches `RedisSessionDataManager`.
- **Static & Runtime Verification**: Both mypy/pyright static checks and runtime `isinstance(mgr, SessionStoreProtocol)` pass for all session store instances.
