import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app.api.routes.chat import registry as tool_registry
from app.services.mcp_adapter import MCPAdapter

async def test_mcp():
    print(f"Env API Key loaded: {bool(os.environ.get('Z_AI_API_KEY'))}")
    mcp_config = MCPAdapter.load_config("mcp_servers.json")
    print("Config loaded!")
    adapter = await MCPAdapter.from_config(mcp_config, tool_registry)
    tools = tool_registry._tools.keys()
    print("Adapter initialized tools:", len(tools))
    for t in tools:
        print(" -", t)
    await adapter.close()

if __name__ == "__main__":
    asyncio.run(test_mcp())
