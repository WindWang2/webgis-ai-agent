import asyncio
import uuid
import json
from app.api.routes.chat import registry
from app.services.session_data import session_data_manager

async def test_deep_enhancement_flow():
    session_id = f"test-session-{uuid.uuid4().hex[:4]}"
    print(f"Starting test with session_id: {session_id}")

    # 1. 模拟获取数据并存储
    mock_data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}, "properties": {"name": "Shanghai", "pop": 24000000}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.40, 39.90]}, "properties": {"name": "Beijing", "pop": 21000000}}
        ]
    }
    ref_id = session_data_manager.store(session_id, mock_data)
    print(f"Stored mock data with ref_id: {ref_id}")

    # 2. 测试 alias_layer
    print("\nTesting alias_layer...")
    alias_res = await registry.dispatch("alias_layer", {"ref_id": ref_id, "alias": "Cities"}, session_id=session_id)
    print(f"Alias result: {alias_res}")

    # 3. 测试 inventory_layers
    print("\nTesting inventory_layers...")
    inv_res = await registry.dispatch("inventory_layers", {}, session_id=session_id)
    print(f"Inventory: {inv_res}")
    assert len(inv_res["layers"]) > 0
    assert inv_res["layers"][0]["alias"] == "Cities"

    # 4. 测试以别名调用工具 (attribute_filter)
    print("\nTesting attribute_filter using ALIAS...")
    filter_res = await registry.dispatch("attribute_filter", {"geojson": "Cities", "query": "pop > 22000000"}, session_id=session_id)
    print(f"Filter result structure: {list(filter_res.keys())}")
    if "error" in filter_res:
         print(f"ERROR OCCURRED: {filter_res['error']}")
    if "stats" in filter_res:
         print(f"Stats content: {filter_res['stats']}")
    
    # print(f"Filtered count: {filter_res.get('stats', {}).get('filtered_count')}")
    assert filter_res["stats"]["filtered_count"] == 1
    assert filter_res["geojson"]["features"][0]["properties"]["name"] == "Shanghai"

    # 5. 测试叠加分析 (overlay_analysis)
    print("\nTesting overlay_analysis (buffer + intersect)...")
    # 先做个 buffer
    from app.services.spatial_tasks import run_buffer_analysis
    # 模拟 Agent 手法: run_buffer(ref:xxx) -> returns ref:yyy
    # 这里我们简化，直接调 service
    from app.services.spatial_analyzer import SpatialAnalyzer
    buf_res = SpatialAnalyzer.buffer(mock_data["features"], distance=1, unit="km")
    ref_buf = session_data_manager.store(session_id, buf_res.data)
    
    # 用 Cities (Point) 与 Buffer (Polygon) 做 intersect
    overlay_res = await registry.dispatch("overlay_analysis", {
        "layer_a": "Cities",
        "layer_b": ref_buf,
        "how": "intersection"
    }, session_id=session_id)
    
    print(f"Overlay result count: {overlay_res.get('stats', {}).get('result_count')}")
    assert overlay_res["stats"]["result_count"] > 0
    print("Overlay test PASSED")

    print("\nALL DEEP ENHANCEMENT TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_deep_enhancement_flow())
