# 08 — Fix hardcoded timeout message in stream_prompt error SSE

**What to build:** The `stream_prompt` timeout error SSE hardcodes "30s" instead of referencing the `PI_EVENT_STREAM_TIMEOUT` constant, so the message goes stale when the constant is tuned.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Replace hardcoded "30s" in `stream_prompt` error SSE with `PI_EVENT_STREAM_TIMEOUT`
- [x] Add test verifying the error message matches the constant value

> 核实补缺 (2026-08-05): 超时错误消息已引用常量；本次补充了断言消息文本与`PI_EVENT_STREAM_TIMEOUT` 一致的测试（tests/test_pi_integration.py）。
