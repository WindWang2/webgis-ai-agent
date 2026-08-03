"""ChatEngine 内部子模块包。

`app.services.chat_engine` 1287 行单文件拆分后的容器（M1）。
- `prompt`      — SYSTEM_PROMPT + 自愈消息构造
- `llm_client`  — `call_llm` / `call_llm_stream` 自由函数（不依赖 ChatEngine 实例）

slim/bbox/error 包装纯函数原住 `sse_helpers`，已直接收拢到它们的归属模块
（`tool_dispatch_service`）——废弃的 re-export shim 已删除（Candidate #7）。

ChatEngine 本身住在 chat_engine.py 继承 ChatExecutionEngine，负责编排：会话、工具调度、SSE 序列化、
持久化、self-healing 闭环。
"""

from app.services.chat.execution_engine import ChatExecutionEngine

__all__ = ["ChatExecutionEngine"]

