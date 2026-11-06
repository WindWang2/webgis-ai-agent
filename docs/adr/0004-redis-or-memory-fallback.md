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

## Supported Redis topology (2026-11 review)

Session storage requires a standalone Redis endpoint (a primary with replicas is
compatible). Redis Cluster sharding is unsupported: the persisted multi-key
transactions do not use a common hash tag. The asynchronous session-store health
check detects `cluster_enabled=1` and rejects that configuration explicitly. Do
not point `REDIS_URL` at a Cluster endpoint; a Cluster migration requires a
versioned key-layout migration and coordinated reader/writer rollout.
