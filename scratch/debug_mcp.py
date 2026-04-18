import asyncio
import os
import traceback
from dotenv import load_dotenv

load_dotenv()

from app.api.routes.chat import registry as tool_registry
from app.services.mcp_adapter import MCPAdapter

async def test_mcp():
    mcp_config = MCPAdapter.load_config("mcp_servers.json")
    adapter = MCPAdapter(tool_registry)
    servers = mcp_config.get("mcpServers", {})
    print("mcp_config loaded:", mcp_config)
    
    import logging
    logging.basicConfig(level=logging.DEBUG)
    
    for name, cfg in servers.items():
        if name != "web-search-prime":
            continue
        try:
            print(f"Connecting to {name}...")
            await adapter.connect_sse(
                name=name,
                url=cfg["url"],
                headers=cfg.get("headers"),
            )
            print(f"Connected to {name}!")
        except Exception as e:
            print(f"Error connecting to {name}:")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_mcp())
