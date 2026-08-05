# 03 — Fix clear_session Pi path

**What to build:** The `USE_NEW_AGENT` feature flag behavior is honest — either Pi state is actually cleared, or the branch is removed.

**Blocked by:** 02 — Surface prompt() errors to HTTP layer

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Either implement `clear_session` via `pi_bridge.abort()` + state reset, or remove the `if USE_NEW_AGENT` branch with an explicit comment
- [x] Add test verifying the chosen behavior
