# 10 — Wire get_messages() to a route or remove it

**What to build:** `PiBridge.get_messages()` is a public API method that's never called from any route. Either wire it to a frontend-facing endpoint or remove it to avoid dead surface area.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Either add `/api/v1/chat/sessions/{session_id}/messages` route that calls `pi_bridge.get_messages()` when Pi is active
- [x] Or remove `get_messages()` from `PiBridge` if the legacy route already covers this need
