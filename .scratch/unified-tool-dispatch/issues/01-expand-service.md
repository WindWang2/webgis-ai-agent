# 01 — Expand: add ToolDispatchService beside the legacy dispatcher

**What to build:** Introduce the single in-process module that owns the entire tool-dispatch chain
(validate → tier-authorize → run via registry → store large GeoJSON to a `ref_id` cursor → slim for
the frontend → intercept repeated calls → wrap errors as self-healing hints). It lives beside the
existing legacy dispatcher; nothing calls it yet. This is the expand half of an expand–contract
migration — the new service coexists with the old code, so CI stays green.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] New in-process module for tool dispatch exists beside the legacy dispatcher (not replacing it
      yet)
- [x] Returns a discriminated-result value (status: ok / repeated / error) carrying `llm_payload`,
      `slim_event`, `geojson_ref`, `raw_result`, `error_msg` — `has_geojson` is derivable as
      "a ref was produced" and is *not* a separate field
- [x] Dependencies injected (tool registry, broadcast callback) — consistent with how the legacy
      dispatcher already accepts them; the one non-pure dependency is session data, used via the
      existing `SessionDataProtocol` port
- [x] Behaviour absorbed from the legacy dispatcher: GeoJSON stored to a `ref_id` cursor; repeated
      identical calls intercepted; errors wrapped as self-healing hints; result slimmed for the
      frontend
- [x] Interface tests at `dispatch()` cover the three status branches (ok / repeated / error), using
      a mock tool registry and the real in-memory `session_data_manager` (no new harness)
- [x] **Load-bearing regression-lock test:** a tool returning a FeatureCollection yields
      `geojson_ref is not None` — the exact behaviour the Pi path drops today
- [x] Legacy path untouched and still production-default; `tsc`/lint/test equivalents green (the new
      service has callers only in tests)
