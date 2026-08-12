# Data Fabric V3 — Reliability, Truthfulness & Offline Resilience

Hardens the V1/V2 Data Fabric (ADR-0050) into a connectivity layer whose results
are **truthful, bounded, tenant-isolated, and verifiable offline**. This ADR is
the single consolidated record for the V3 reliability contracts; it supersedes
the ad-hoc notes that preceded it and consolidates the changes landed in the
Data Fabric V3 PR.

## Context & Problem

The V1 architecture was correct in shape but several contracts were unsound in
practice (see `.scratch/data-fabric-v3/findings.md`, swarm audit round 1):

- **Fake materialization success.** A session-store failure was masked: a random
  `ref:…` was minted and `status:"success"` returned for a ref that resolved to
  no payload.
- **Synthetic data as real.** Five adapters (STAC, GeoParquet, FlatGeobuf,
  PMTiles, S3) and the `GenericDataSourceAdapter` fallback served fabricated
  features as if they were remote results on any error.
- **SSRF redirect bypass.** `validate_url` ran once; `requests` then followed
  redirects to `169.254.169.254` with no re-validation. Cross-tenant catalog
  reads had no guard.
- **Fabricated metadata.** Undeclared CRS became `EPSG:4326`; unknown counts
  became exact `0/100/10000`; tile pyramids were mislabeled `vector`.
- **Unbounded results / OOM.** The `limit` param capped the request, not the
  accepted payload.
- **Six divergent source-type registries**; no retries; blocking sync I/O inside
  async materialization; no health cache; no circuit breaker.

## Decisions

### 1. Truthful materialization (P0)
A ref exists **iff** its payload is retrievable. Store failure (exception OR the
Redis-unavailability sentinel — a deliberate cross-cutting resilience seam kept
for chat-dispatch stability) is reported as a typed `MATERIALIZATION_FAILED`
result with `ref_id=None`, `success=False`. Audit-row writes are atomic with the
store: a commit failure rolls back and reports failure (no orphan success). The
REST route honors the flag (503 store-down vs 200 success vs 413 oversized).

### 2. Canonical adapter registry (P1)
`AdapterRegistry` (`registry.py`) is the single source of truth for
`source_type → adapter`. All 10 adapters are reachable from both the REST
manager and the tool connection-manager. Unknown types raise
`UnsupportedSourceError` — the factory **never** falls back to mock data.
`GenericDataSourceAdapter` is an explicit opt-in demo adapter
(canonical `generic`, aliases `geojson/mock/sample`). Each spec declares
capability flags for pushdown negotiation.

### 3. SSRF re-validation per hop (P0)
`SSRFSafeHTTPAdapter` re-runs `validate_url` inside `send()`. Because `requests`
re-invokes the mounted adapter for every redirect hop, each redirect target is
re-validated (closing the 302→metadata bypass) and the DNS-rebinding TOCTOU
window is shrunk (re-resolve immediately before connect). All HTTP adapters use
`make_safe_session()`. Residual: full connect-time IP pinning is a documented
follow-up.

### 4. Tenant isolation on the catalog (P0)
`preview`/`query`/`materialize` routes now resolve the item's `DataSource` and
apply `_require_tenant_owned` (404, not 403, to avoid existence leaks). Anonymous
callers reach only truly global sources. `endpoint_url` is redacted of userinfo
on egress.

### 5. Bounded resource guards (P0)
`limits.py` centralizes hard bounds (`DATA_FABRIC_MAX_FEATURES / MAX_RESPONSE_BYTES
/ MAX_PAGES / QUERY_TIMEOUT`), clamped to non-zero floors so protection cannot be
disabled via config. `enforce_result_bounds` runs at both materialization choke
points before store; oversized results raise `RESULT_TOO_LARGE` (HTTP 413) with an
actionable shrink hint. Does not trust the remote `limit`.

### 6. Reliability controls (P1)
- `reliability.py`: transient-only retry (`is_transient`) with bounded exp
  backoff + full jitter and an injectable sleep/clock seam (tests never wait).
  4xx / validation / security errors are never retried; the conservative POST
  rule retries HTTPError-class transients only when idempotent.
- `circuit_breaker.py`: per-source closed/open/half-open breaker with injectable
  clock and bounded LRU registry; open ⇒ fail-fast `SourceUnreachableError`.
- `health.py`: truthful semantics — a validated URL reports `valid_profile`, not
  `healthy`; only an adapter probe reports `healthy`. TTL health cache (healthy
  longer than failure) and breaker-gated fast-fail.

### 7. Metadata truthfulness (P0)
`metadata.py` normalizes at the persistence boundary: undeclared CRS → `None`
(never fakes `EPSG:4326`); OGC CRS URIs canonicalized; geometry types recognized
across `esri*` codes and raster/tile (tile pyramids no longer `vector`);
feature count `None` for unknown (`0` reserved for genuine zero).

### 8. Structured errors (P1)
`errors.py` defines the canonical taxonomy
(`UNSUPPORTED_SOURCE`, `SOURCE_UNREACHABLE`, `SOURCE_TIMEOUT`, `SOURCE_AUTH_FAILED`,
`SOURCE_RATE_LIMITED`, `SOURCE_BAD_RESPONSE`, `RESULT_TOO_LARGE`,
`MATERIALIZATION_FAILED`, `CANCELLED`, `SECURITY_BLOCKED`, …) and
`classify_http_status`, the single retry/circuit-breaker classifier.

### 9. Offline fault-injection (testability)
`FakeFabricAdapter` (subclass of `SSRFSafeHTTPAdapter`) returns canned responses
per route with no sockets, while still enforcing per-hop SSRF validation — so
the real `requests` redirect machinery exercises redirect-SSRF, retry,
pagination, auth and oversized paths fully offline and deterministically.

## Deferred / follow-up

The V3 follow-up slices landed in this PR (P2 set):

- **File-format adapters** (GeoParquet/FlatGeobuf/PMTiles/S3) no longer serve
  synthetic fixtures as real data — a configured endpoint that fails returns a
  typed error; synthetic is demo-mode only and labeled `source="synthetic-demo"`.
  STAC (b3a98c4) was the reference pattern, now applied to all four (c3211b0).
- **Local-file path guards** (Section 44): `resolve_safe_local_path` blocks
  traversal / symlink escape / sensitive-system-dir / oversize reads (355f99a).
- **Catalog sync efficiency** (Section 30/31): bounded-concurrency describe,
  batch DB lookup (no N+1), incremental fingerprint skip (5f360b0).
- **Async / cancellation** (Section 13/17): blocking adapter I/O offloaded via
  `asyncio.to_thread`; cooperative `CancellationToken` aborts before store
  (0f33b2c).
- **Safe TTL metadata cache** (Section 37): tenant-scoped `SafeTTLCache` /
  `cached_describe`; the scope is a mandatory key part (no cross-tenant leak) (aa53cbc).

Still deferred (P3):

- **Connect-time IP pinning** for full DNS-rebinding defense (socket-level
  transport pin). The per-send revalidation in `SSRFSafeHTTPAdapter` already
  shrinks the practical TOCTOU window to the resolve→connect gap inside one
  `send()`; pinning the connection to a pre-validated IP would close the
  residual gap but risks breaking legitimate dual-stack / CDN connections, so
  it is deferred unless a deployment requires it.
- **HTTP conditional requests** (ETag / If-None-Match) and a **query-result**
  cache: needs adapter response-header plumbing this PR does not introduce;
  metadata caching is already in place.
- **Removed-dataset marking**: cohesive write+read change (status convention +
  catalog-list filtering), no migration-free win; tracked separately.

## Consequences

- Error semantics changed from fake-success to typed failure for store/health
  failures. This is an allowed correctness fix (ADR-0050 §egress); clients that
  treated `success:true` on every 200 must now read the `success`/`error_type`
  fields. The REST route preserves a structured body on the failure status codes.
- The `GenericDataSourceAdapter` is no longer an implicit fallback; integrations
  that relied on `source_type="geojson"` continue to work (it is a registered
  demo adapter), but arbitrary unknown types now raise.
- No DB migration required (the `crs` column is nullable; `None` is stored for
  unknown CRS).
