# ADR-001: Fetch-on-Demand / ref_id Cursor Pattern

## Context

Spatial analysis produces large GeoJSON and raster results (often 10–100 MB). The LLM agent
needs to reference these results in subsequent turns, but:
1. LLM context windows cannot hold 50 MB+ GeoJSON payloads.
2. Storing large blobs in Postgres rows is expensive and slow.
3. The frontend needs efficient streaming of large datasets without blocking the SSE gateway.

## Decision

Large data objects are stored in `SessionDataManager` (in-memory LRU, optional Redis backend)
and referenced by opaque `ref_id` cursors (e.g., `ref:geojson-abc123...`). The LLM only sees
the cursor; the frontend fetches the full payload via `/layers/data/{ref_id}`.

The `ref_id` serves as a capability token: possession grants access to the data within the
same `session_id` scope.

## Consequences

**Positive:**
- LLM context stays lean; only metadata flows through the agent chain.
- Heavy data transfer is isolated from the SSE gateway.
- LRU eviction naturally bounds memory usage.
- Redis backend enables cross-worker sharing in production.

**Negative:**
- Adds lifecycle complexity (ref creation, alias resolution, LRU eviction, ownership checks).
- Data is volatile unless persisted as `UploadRecord` or `Layer`.
- Frontend must make additional fetch requests, adding latency.
- Cross-session ref sharing is impossible by design (security boundary).
