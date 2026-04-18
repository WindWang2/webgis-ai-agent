import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="npx",
        args=["-y", "@z_ai/mcp-server"],
        env=os.environ.copy()
    )
    print("Connecting...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.list_tools()
            for t in res.tools:
                print(f"Tool: {t.name}")

if __name__ == "__main__":
    asyncio.run(main())
