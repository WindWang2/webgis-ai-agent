import asyncio
import aiohttp
import sys
import os

# 将项目根目录添加到 python 路径
sys.path.append(os.getcwd())

from app.core.network import get_ssl_context, get_base_headers
from app.core.config import settings

async def test_geocoding_connection():
    urls = [
        "https://nominatim.openstreetmap.org/search",
        "https://nominatim.openstreetmap.fr/search"
    ]
    params = {
        "q": "Beijing",
        "format": "json",
        "limit": 1
    }
    
    headers = get_base_headers()
    ssl_ctx = get_ssl_context()
    proxy = settings.HTTPS_PROXY or settings.HTTP_PROXY
    
    for url in urls:
        print(f"\nTesting connection to {url}")
        print(f"Proxy: {proxy}")
        print(f"SSL Context: {ssl_ctx}")
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params, ssl=ssl_ctx, proxy=proxy, timeout=10) as resp:
                    print(f"Status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"Success! Found: {data[0].get('display_name') if data else 'No results'}")
                    else:
                        text = await resp.text()
                        print(f"Error: {text[:200]}")
        except Exception as e:
            print(f"Failed with exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_geocoding_connection())
