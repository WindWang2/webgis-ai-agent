"""ChatEngine 内部子模块包。

`app.services.chat_engine` 1287 行单文件拆分后的容器（M1）。
- `sse_helpers` — 纯函数：slim/parse/bbox/error 包装 + LRUCache
- `prompt`      — SYSTEM_PROMPT + 自愈消息构造
- `llm_client`  — `call_llm` / `call_llm_stream` 自由函数（不依赖 ChatEngine 实例）

ChatEngine 本身仍住在 chat_engine.py，负责编排：会话、工具调度、SSE 序列化、
持久化、self-healing 闭环。
"""
