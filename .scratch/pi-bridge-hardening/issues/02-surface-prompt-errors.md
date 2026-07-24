# 02 — Surface prompt() errors to HTTP layer

**What to build:** When Pi fails, the user sees a meaningful error message instead of an empty response.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `prompt()` raises `PiRpcError` after logging (instead of returning `{"content": "", "error": "..."}`)
- [ ] `chat.py` `chat_completions` catches `PiRpcError` and returns `HTTPException(502, ...)` or includes error in response content
- [ ] `stream_prompt` error path verified to yield `task_error` SSE (already implemented in working tree)
