import httpx
import asyncio
import json

async def test_stream():
    url = "http://localhost:8001/api/v1/chat/stream"
    payload = {
        "message": "分析成都锦江区公园热度分析",
        "session_id": "test_session_reset"
    }
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload) as response:
                print(f"Status: {response.status_code}")
                async for line in response.aiter_lines():
                    if line.strip():
                        print(f"Data: {line[:100]}...")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_stream())
