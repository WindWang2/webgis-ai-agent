import httpx
import asyncio
import os
from dotenv import load_dotenv

async def test_deepseek():
    # Load environment variables
    load_dotenv(override=True)
    
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    
    print(f"Testing DeepSeek API...")
    print(f"Base URL: {base_url}")
    print(f"Model: {model}")
    print(f"API Key: {api_key[:5]}...{api_key[-5:] if api_key else ''}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, can you hear me?"}
        ],
        "max_tokens": 50
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            
            print(f"\nStatus Code: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                print(f"Response: {content}")
                print("\n[SUCCESS] DeepSeek API is working correctly!")
            else:
                print(f"Error Response: {response.text}")
                print("\n[FAILED] DeepSeek API returned an error.")
                
    except Exception as e:
        print(f"\n[ERROR] An exception occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_deepseek())
