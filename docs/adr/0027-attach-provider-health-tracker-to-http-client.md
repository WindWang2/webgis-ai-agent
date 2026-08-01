# Attach ProviderHealthTracker directly to HTTP Client Execution Seam

**Status:** accepted

We bind `ProviderHealthTracker` to external HTTP client executions via `tracked_provider_get` in `app/services/provider_health.py`.

## Context

Previously, external API providers (Amap, Baidu, Tianditu) in `app/tools/chinese_maps/http.py` manually duplicated 20+ lines of health tracking boilerplate (`ht.record_attempt`, `ht.record_error`, `ht.record_success`, proxy injection, SSL context configuration, and status code checking).

This duplication increased the likelihood of missing circuit breaker tracking or proxy/SSL misconfigurations when adding new providers.

## Decision

1. **`tracked_provider_get` Execution Seam**: Implement `tracked_provider_get(provider, url, params, ...)` in `app/services/provider_health.py` as a high-order async helper.
2. **Automated Lifecycle**: `tracked_provider_get` automatically executes `record_attempt` -> fetches `get_shared_client()` -> configures `get_ssl_context()` & `HTTP_PROXY` -> evaluates HTTP 200 & business status checkers (`check_amap_status`, `check_baidu_status`, `check_tianditu_status`) -> triggers `record_success` / `record_error`.
3. **Thin HTTP Provider Helpers**: Refactor `_amap_get`, `_baidu_get`, and `_tianditu_get` in `app/tools/chinese_maps/http.py` to delegate directly to `tracked_provider_get`.

## Consequences

- **Locality & Reuse**: External HTTP provider requests use a single unified execution seam with guaranteed circuit breaker protection and proxy/SSL enforcement.
- **Maintainability**: `app/tools/chinese_maps/http.py` drops ~80 lines of duplicated try/except and tracking boilerplate.
