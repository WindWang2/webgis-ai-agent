# 05 — Pi readiness signal + timeout constants

**What to build:** Pi bridge doesn't race on startup, and timeout values are tunable without code changes.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Replace `await asyncio.sleep(1)` with `get_state` readiness check after startup, with timeout
- [x] Extract `PI_STARTUP_READY_TIMEOUT`, `PI_RPC_TIMEOUT`, `PI_EVENT_DRAIN_TIMEOUT`, `PI_EVENT_STREAM_TIMEOUT` as named constants
- [x] Document that `get_pi_bridge(extension_paths)` only honors paths on first call
