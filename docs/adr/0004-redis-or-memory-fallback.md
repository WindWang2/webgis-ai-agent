# ADR-004: Redis-or-Memory Fallback for Session Data

## Context

`SessionDataManager` stores session-scoped large objects (GeoJSON, raster blobs, map state).
In production, Redis is the natural backend for shared session data across multiple workers.
In development or single-process deployments, Redis may not be available.

## Decision

`create_session_data_manager()` tries Redis first; if unavailable, falls back to in-memory
`OrderedDict` LRU. The fallback is transparent to the rest of the codebase.

## Consequences

**Positive:**
- Service stays operational without Redis (development, single-process).
- No hard dependency on Redis for basic functionality.
- Graceful degradation: features that need Redis (cross-worker sharing, persistence) work in
  production but degrade to single-process in development.

**Negative:**
- In-memory mode is not shared across workers. Data is lost on restart.
- LRU eviction is more aggressive than Redis-with-TTL (no TTL, only size-based eviction).
- Production deployments without Redis will silently lose data, which may confuse operators.
