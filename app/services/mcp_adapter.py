"""MCP 客户端适配器 - 将 MCP server 工具注册到 ToolRegistry

支持两种传输方式：
  - stdio：本地进程（如 npx @modelcontextprotocol/server-filesystem）
  - sse：远程 HTTP/SSE MCP server

配置文件格式（mcp_servers.json，与 Claude Desktop 兼容）：
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "my-remote": {
      "transport": "sse",
      "url": "http://localhost:3001/sse"
    }
  }
}
"""
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPAdapter:
    """MCP 客户端适配器，管理多个 MCP server 连接并将工具注册到 ToolRegistry"""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._sessions: dict[str, Any] = {}
        self._exit_stack = AsyncExitStack()

    async def connect_stdio(self, name: str, command: str, args: list[str],
                            env: dict[str, str] | None = None) -> None:
        """连接 stdio 类型的 MCP server"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        merged_env = {**os.environ, **(env or {})}
        params = StdioServerParameters(command=command, args=args, env=merged_env)
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._sessions[name] = session
        count = await self._register_tools(name, session)
        logger.info(f"[MCP] '{name}' (stdio) connected, registered {count} tools")

    async def connect_sse(self, name: str, url: str,
                          headers: dict[str, str] | None = None) -> None:
        """连接 SSE/HTTP 类型的 MCP server"""
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        read, write = await self._exit_stack.enter_async_context(
            sse_client(url, headers=headers or {})
        )
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._sessions[name] = session
        count = await self._register_tools(name, session)
        logger.info(f"[MCP] '{name}' (sse) connected, registered {count} tools")

    async def _register_tools(self, server_name: str, session: Any) -> int:
        """将 MCP server 的工具注册到 ToolRegistry，返回注册数量"""
        tools_result = await session.list_tools()
        count = 0
        for tool_def in tools_result.tools:
            self._register_one_tool(server_name, tool_def, session)
            count += 1
        return count

    def _register_one_tool(self, server_name: str, tool_def: Any, session: Any) -> None:
        """注册单个 MCP 工具"""
        tool_name = tool_def.name
        # 若与已有工具名冲突，加前缀
        if tool_name in self.registry._tools:
            tool_name = f"{server_name}__{tool_def.name}"

        description = (tool_def.description or tool_name).strip()
        input_schema = tool_def.inputSchema or {}
        properties: dict = input_schema.get("properties", {})
        required: list = input_schema.get("required", [])

        # 闭包捕获 session 和原始工具名
        _original_name = tool_def.name
        _session = session

        async def call_mcp_tool(**kwargs: Any) -> Any:
            try:
                result = await _session.call_tool(_original_name, kwargs)
                if not result.content:
                    return {"result": None}
                texts = [c.text for c in result.content if hasattr(c, "text") and c.text]
                if len(texts) == 1:
                    try:
                        return json.loads(texts[0])
                    except (json.JSONDecodeError, ValueError):
                        return {"result": texts[0]}
                return {"results": texts}
            except Exception as e:
                logger.error(f"[MCP] tool '{_original_name}' error: {e}")
                return {"error": str(e)}

        # 直接写入 registry 内部结构（绕过 inspect，因为 kwargs 是动态的）
        self.registry._tools[tool_name] = call_mcp_tool
        self.registry._schemas.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
        logger.info(f"  [MCP] registered tool: {tool_name}")

    async def close(self) -> None:
        """断开所有 MCP server 连接"""
        await self._exit_stack.aclose()
        self._sessions.clear()
        logger.info("[MCP] all connections closed")

    @staticmethod
    def load_config(path: str) -> dict:
        """加载 mcp_servers.json 配置文件"""
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            # 扩展系统环境变量和 .env 变量
            content = os.path.expandvars(content)
            return json.loads(content)

    @classmethod
    async def from_config(cls, config: dict, registry: ToolRegistry) -> "MCPAdapter":
        """根据配置文件创建并连接所有 MCP server"""
        adapter = cls(registry)
        servers: dict = config.get("mcpServers", {})
        for name, cfg in servers.items():
            transport = cfg.get("transport", "stdio")
            try:
                if transport == "stdio":
                    await adapter.connect_stdio(
                        name=name,
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=cfg.get("env"),
                    )
                elif transport == "sse":
                    await adapter.connect_sse(
                        name=name,
                        url=cfg["url"],
                        headers=cfg.get("headers"),
                    )
                else:
                    logger.warning(f"[MCP] unknown transport '{transport}' for server '{name}', skipped")
            except Exception as e:
                logger.error(f"[MCP] failed to connect server '{name}': {e}")
        return adapter
