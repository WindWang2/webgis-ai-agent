# 35. Unified SessionStore Protocol Parity & Active Provider Seam

Date: 2026-08-02

## Status

Accepted

## Context

Prior to this decision, `SessionDataManager` (in-memory) and `RedisSessionDataManager` (Redis) relied on duck typing rather than a formal runtime-checkable protocol seam with a unified contract test suite. Subtle behavioral divergences in map state mutations, ref alias resolution, and session metadata structures risked contract drift over time.

## Decisions

1. **Protocol Implementation Seam**: Defined `MemorySessionStore` (`app/services/session_data.py`) and `RedisSessionStore` (`app/services/session_data_redis.py`) implementing `SessionStoreProtocol`.
2. **Backward Compatibility Aliases**: Retained `SessionDataManager` and `RedisSessionDataManager` as aliases for legacy imports.
3. **Active Provider Factory**: Added `get_session_store() -> SessionStoreProtocol` in `app/services/session_data_protocol.py` to return the active store instance (Redis if enabled, else Memory fallback).
4. **Shared Contract Test Suite**: Added `tests/unit/test_session_store_contract.py` enforcing a 16-method contract test matrix across both store adapters.

## Consequences

- **Leverage**: Identical storage semantics guaranteed regardless of whether Redis or in-memory fallback is active.
- **Locality**: TTL rules, LRU eviction, alias resolution, and concurrency guarantees are bound strictly to the protocol contract test.
- **Testability**: Fast in-memory store and production Redis store share 100% of contract tests.
