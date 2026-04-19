import asyncio
import aiohttp
import ssl
import certifi

urls = [
    "https://nominatim.openstreetmap.org/search?q=Beijing&format=json",
    "https://nominatim.openstreetmap.fr/search?q=Beijing&format=json",
    "https://overpass-api.de/api/interpreter?data=[out:json];node(50.746,7.154,50.748,7.157);out;",
]

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    # Try with verification disabled as a last resort diagnostic
    ctx_no_verify = ssl.create_default_context()
    ctx_no_verify.check_hostname = False
    ctx_no_verify.verify_mode = ssl.CERT_NONE
    return ctx, ctx_no_verify

async def test_url(session, url, ssl_ctx, label):
    try:
        async with session.get(url, ssl=ssl_ctx, timeout=10) as resp:
            print(f"[{label}] {url} -> Status: {resp.status}")
            return resp.status == 200
    except Exception as e:
        print(f"[{label}] {url} -> ERROR: {e}")
        return False

async def main():
    ctx, ctx_no_verify = get_ssl_context()
    
    headers = {"User-Agent": "WebGIS-AI-Agent-Diagnostic/1.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        print("--- Testing with SSL Verification ---")
        for url in urls:
            await test_url(session, url, ctx, "VERIFIED")
            
        print("\n--- Testing WITHOUT SSL Verification ---")
        for url in urls:
            await test_url(session, url, ctx_no_verify, "UNVERIFIED")

if __name__ == "__main__":
    asyncio.run(main())
