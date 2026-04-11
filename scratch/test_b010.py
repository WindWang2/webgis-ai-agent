import asyncio
import json
from typing import Any
from pydantic import BaseModel, Field

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

async def test_cursor_flow():
    registry = ToolRegistry()
    session_id = "test-session"
    
    # 模拟工具 1: 产生数据
    @tool(registry, name="producer", description="Produces data")
    def producer():
        return {"type": "FeatureCollection", "features": [{"id": 1}], "geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}}

    # 模拟工具 2: 消费数据
    @tool(registry, name="consumer", description="Consumes data")
    def consumer(geojson: Any):
        if isinstance(geojson, dict) and geojson.get("type") == "FeatureCollection":
            return f"Success: Processed {len(geojson['features'])} features"
        return f"Failure: Expected dict, got {type(geojson)}"

    print("Step 1: Producer provides data")
    result = await registry.dispatch("producer", {})
    
    print("Step 2: Store data in SessionDataManager")
    ref_id = session_data_manager.store(session_id, result["geojson"], prefix="geojson")
    print(f"Generated Ref: {ref_id}")
    
    print("\nStep 3: Call Consumer with Ref ID")
    # Dispatch will now automatically resolve the ref if session_id is provided
    final_result = await registry.dispatch("consumer", {"geojson": ref_id}, session_id=session_id)
    print(f"Consumer Result: {final_result}")
    
    expected = "Success: Processed 1 features"
    if final_result == expected:
        print("\n✅ End-to-end cursor dereferencing works correctly!")
    else:
        print(f"\n❌ Failed. Expected '{expected}', but got '{final_result}'")

if __name__ == "__main__":
    asyncio.run(test_cursor_flow())
