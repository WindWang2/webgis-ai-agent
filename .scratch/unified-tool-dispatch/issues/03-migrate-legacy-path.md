# 03 — Migrate legacy path: route chat_engine through ToolDispatchService

**What to build:** Point the legacy ChatEngine path at the new service. This is the production-default
path (`USE_NEW_AGENT` off), so it moves last — after the Pi canary has validated the service in the
wild. Once migrated, both agent paths share one dispatch module and the structural divergence that
caused the regression cannot recur. The stale shallow tests that asserted on the legacy dispatcher's
flat raw field bag are deleted; the pure `is_suspicious_result` helper and its tests are retained.

**Blocked by:** 02 — Migrate Pi path (serial ordering; Pi is the canary per the spec's Q6 decision).

**Status:** done — verified 2026-08-05 (code + tests)

- [x] ChatEngine calls `ToolDispatchService.dispatch()` instead of the legacy `dispatch_tool`
- [x] ChatEngine branches on the discriminated result's `status` (ok / repeated / error) rather than
      reassembling a bag of raw booleans/strings — the shallow caller logic is replaced by a clean
      `match` on the outcome
- [x] `has_geojson` is no longer read anywhere — callers check `geojson_ref is not None`
- [x] The legacy `dispatcher.dispatch_tool` body is removed (its concerns now live in the service);
      the file may still temporarily hold `is_suspicious_result` until the contract step
- [x] Stale shallow field-assertion tests deleted (the deepening rule: old unit tests on the
      superseded shape are waste once interface-level tests exist)
- [x] `is_suspicious_result` tests retained — that pure helper stays
- [x] Full suite green; no caller references the old `dispatch_tool` shape
