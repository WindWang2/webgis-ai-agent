import sys
import os
import asyncio
import json

# Adjust python path
sys.path.append(os.getcwd())

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.services.chat_engine import _slim_tool_result

async def run_integration_test():
    registry = ToolRegistry()
    register_spatial_tools(registry)
    
    # 模拟一个会话和大型数据集
    session_id = "test-session-123"
    
    print("--- 步骤 1: 执行 heatmap_data (Grid 模式) ---")
    # 构造 2000 个点，确保触发 slimming
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4 + i*0.001, 39.9 + i*0.001]}} 
        for i in range(2000)
    ]
    geojson_input = {"type": "FeatureCollection", "features": features}
    
    # 执行工具
    result = await registry.dispatch("heatmap_data", {
        "geojson": geojson_input,
        "cell_size": 100,
        "render_type": "grid"
    }, session_id=session_id)
    
    result_str = json.dumps(result, ensure_ascii=False)
    print(f"原始结果长度: {len(result_str)}")
    
    print("\n--- 步骤 2: 执行 Slimming 逻辑 ---")
    # 模拟已经存储了 ref (由于我们没启动 celery 和 redis，这里简单传个 mock ref)
    geojson_ref = "ref:heatmap-123"
    
    slimmed = _slim_tool_result(result, result_str, geojson_ref)
    slimmed_dict = json.loads(slimmed)
    
    print(f"精简后长度: {len(slimmed)}")
    print(f"包含类型: {slimmed_dict.get('type')}")
    print(f"包含元数据: {slimmed_dict.get('metadata')}")
    print(f"包含摘要: {'geojson_summary' in slimmed_dict}")
    
    # 验证关键点
    assert slimmed_dict.get("type") == "FeatureCollection"
    assert "metadata" in slimmed_dict
    assert "features" not in slimmed_dict
    assert "available_properties" in slimmed_dict["geojson_summary"]
    assert "weight" in slimmed_dict["geojson_summary"]["available_properties"]
    
    print("\n--- 集成测试通过! 部署环境可用性检查完成 ---")

if __name__ == "__main__":
    asyncio.run(run_integration_test())
