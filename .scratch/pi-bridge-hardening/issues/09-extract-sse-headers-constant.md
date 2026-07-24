# 09 — Extract SSE headers constant in chat.py

**What to build:** The SSE `StreamingResponse` headers dict is copy-pasted for both the Pi path and the legacy path. Extract a shared constant.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Extract `SSE_HEADERS` dict constant at module level in `chat.py`
- [ ] Replace both copies of the headers dict with the constant
