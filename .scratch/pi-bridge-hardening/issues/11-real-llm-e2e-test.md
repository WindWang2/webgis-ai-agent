# 11 — Real LLM end-to-end test for Pi bridge

**What to build:** Spin up the full stack (FastAPI + Pi subprocess + real LLM), send a prompt through `/chat/stream`, and verify SSE events + GIS tool calls end-to-end.

**Blocked by:** None — but requires an LLM API key configured.

**Status:** ready-for-agent

- [ ] Configure an LLM provider API key (e.g., `OPENAI_API_KEY`, `GROQ_API_KEY`, etc.)
- [ ] Start FastAPI server with `USE_NEW_AGENT=true`
- [ ] Send a prompt that triggers a GIS tool call (e.g., "Buffer 500m around Beijing")
- [ ] Verify SSE events: `task_start` → `token` → `tool_call` → `step_start` → `step_result` → `task_complete` → `done`
- [ ] Verify the tool result is returned correctly through `/pi-tools/execute` → `ToolRegistry.dispatch()`
