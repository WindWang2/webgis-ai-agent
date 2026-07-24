# 08 — Fix hardcoded timeout message in stream_prompt error SSE

**What to build:** The `stream_prompt` timeout error SSE hardcodes "30s" instead of referencing the `PI_EVENT_STREAM_TIMEOUT` constant, so the message goes stale when the constant is tuned.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Replace hardcoded "30s" in `stream_prompt` error SSE with `PI_EVENT_STREAM_TIMEOUT`
- [ ] Add test verifying the error message matches the constant value
