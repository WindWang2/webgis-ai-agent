# 03 — Fix clear_session Pi path

**What to build:** The `USE_NEW_AGENT` feature flag behavior is honest — either Pi state is actually cleared, or the branch is removed.

**Blocked by:** 02 — Surface prompt() errors to HTTP layer

**Status:** ready-for-agent

- [ ] Either implement `clear_session` via `pi_bridge.abort()` + state reset, or remove the `if USE_NEW_AGENT` branch with an explicit comment
- [ ] Add test verifying the chosen behavior
