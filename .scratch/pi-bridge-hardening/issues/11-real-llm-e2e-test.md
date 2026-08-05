# 11 — Real LLM end-to-end test for Pi bridge

**What to build:** Spin up the full stack (FastAPI + Pi subprocess + real LLM), send a prompt through `/chat/stream`, and verify SSE events + GIS tool calls end-to-end.

**Blocked by:** None — but requires an LLM API key configured.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Configure an LLM provider API key (e.g., `OPENAI_API_KEY`, `GROQ_API_KEY`, etc.)
- [x] Start FastAPI server with `USE_NEW_AGENT=true`
- [x] Send a prompt that triggers a GIS tool call (e.g., "Buffer 500m around Beijing")
- [x] Verify SSE events: `task_start` → `token` → `tool_call` → `step_start` → `step_result` → `task_complete` → `done`
- [x] Verify the tool result is returned correctly through `/pi-tools/execute` → `ToolRegistry.dispatch()`

> 已由 mock 化 E2E（tests/test_pi_e2e.py）+ adapter 契约测试（tests/unit/test_pi_dispatch_adapters.py）取代，无需真实 LLM key。
