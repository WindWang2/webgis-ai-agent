import asyncio
import traceback
from app.services.mcp_adapter import MCPAdapter
from app.api.routes.chat import registry

async def test_url(url, headers=None):
    from mcp.client.sse import sse_client
    from contextlib import AsyncExitStack
    print(f"\n--- Testing URL: {url} ---")
    async with AsyncExitStack() as stack:
        try:
            read, write = await stack.enter_async_context(sse_client(url, headers=headers or {}))
            print("Successfully connected!")
        except Exception as e:
            print("Failed:")
            print(e)
            if hasattr(e, "response"):
                print("Response text:", e.response.text)

async def main():
    api_key = "7cbaa1c7338742f496ce72b0ce10fc81.dgv75CpFP04zp5mN"
    
    # Test 1: web_search_prime/sse
    await test_url(
        "https://open.bigmodel.cn/api/mcp/web_search_prime/sse",
        {"Authorization": f"Bearer {api_key}"}
    )
    # Test 2: web_search/sse
    await test_url(
        "https://open.bigmodel.cn/api/mcp/web_search/sse",
        {"Authorization": f"Bearer {api_key}"}
    )
    # Test 3: web_search_prime/mcp (original)
    await test_url(
        "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
        {"Authorization": f"Bearer {api_key}"}
    )

if __name__ == "__main__":
    asyncio.run(main())
