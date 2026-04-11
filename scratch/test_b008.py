import asyncio
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool

async def test_validation():
    registry = ToolRegistry()
    
    class TestArgs(BaseModel):
        val: int = Field(..., ge=1, le=10)
    
    @tool(registry, name="test_tool", description="A test tool", args_model=TestArgs)
    def test_tool(val: int):
        return f"Result: {val}"
    
    print("Testing valid call...")
    res = await registry.dispatch("test_tool", {"val": 5})
    print(res)
    
    print("\nTesting invalid call (out of range)...")
    try:
        await registry.dispatch("test_tool", {"val": 15})
    except ValueError as e:
        print(f"Caught expected error:\n{e}")
        
    print("\nTesting invalid call (wrong type)...")
    try:
        await registry.dispatch("test_tool", {"val": "hello"})
    except ValueError as e:
        print(f"Caught expected error:\n{e}")

if __name__ == "__main__":
    asyncio.run(test_validation())
