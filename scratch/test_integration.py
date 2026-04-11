import sys
import asyncio
import json
import uuid
from typing import Any, Optional
from unittest.mock import MagicMock, AsyncMock, patch

# Mock problematic modules before they are imported by ChatEngine/Tools
sys.modules["app.core.database"] = MagicMock()
sys.modules["app.services.history_service"] = MagicMock()
mock_task_queue = MagicMock()
sys.modules["app.services.task_queue"] = mock_task_queue

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.services.session_data import session_data_manager

# 模拟 OSM 工具返回的数据
MOCK_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"name": "Test Point"}, "geometry": {"type": "Point", "coordinates": [104, 30]}}
    ]
}

async def test_integrated_flow():
    """综合集成测试：验证自愈、游标、解引用及异步调用链路"""
    registry = ToolRegistry()
    
    # 1. 注册工具 (我们需要实际注册以验证 Pydantic)
    from app.tools.spatial import register_spatial_tools
    from app.tools.osm import register_osm_tools
    register_spatial_tools(registry)
    register_osm_tools(registry)
    
    engine = ChatEngine(registry)
    session_id = f"test-{uuid.uuid4().hex[:4]}"
    
    print(f"--- 开启集成测试 (Session: {session_id}) ---")

    # 模拟 LLM 响应生成器
    mock_responses = [
        # 第一轮：LLM 决定调用 query_osm_poi
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "正在为您查询餐厅...",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "query_osm_poi", "arguments": json.dumps({"area": "成都", "category": "restaurant"})}
                    }]
                }
            }]
        },
        # 第二轮：LLM 拿到游标后，决定调用 buffer_analysis
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "查询到餐厅，正在计算缓冲区...",
                    "tool_calls": [{
                        "id": "call_2",
                        "function": {"name": "buffer_analysis", "arguments": '{"geojson": "@REPLACE_ME@", "distance": 1000}'}
                    }]
                }
            }]
        },
        # 第三轮：最终总结
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "分析完成，已为您在地图展示 1000 米缓冲区。"
                }
            }]
        }
    ]

    response_idx = 0

    async def mock_call_llm(messages, tools=None):
        nonlocal response_idx
        res = mock_responses[response_idx]
        
        # 针对第二轮，动态填入游标
        if response_idx == 1:
            # 从缓存获取最新的游标
            cache = session_data_manager._store.get(session_id, {})
            if cache:
                ref_id = list(cache.keys())[-1]
                print(f"[LLM] 检测到游标: {ref_id}，准备填入参数")
                res["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = \
                    res["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"].replace("@REPLACE_ME@", ref_id)
        
        response_idx += 1
        return res

    # 模拟 OSM API 和 Celery
    with patch("app.tools.osm._query_overpass", return_value=MOCK_GEOJSON), \
         patch("app.services.task_queue.celery_app.send_task") as mock_celery:
        
        # 模拟 Celery 任务结果
        mock_task = MagicMock()
        mock_task.get.return_value = {"success": True, "data": MOCK_GEOJSON, "stats": {"area": 100}}
        mock_celery.return_value = mock_task

        # 注入 Mock LLM
        engine._call_llm = mock_call_llm

        print("\n[Action] 用户：查询成都餐厅并做 1000 米缓冲区")
        # 我们使用 chat_stream 来模拟真实流式环境（这里简化为收集结果）
        results = []
        async for event in engine.chat_stream("查询成都餐厅并做 1000 米缓冲区", session_id):
            if "event:" in event:
                results.append(event)
        
        print("\n[Verify] 流程检查:")
        
        # 检查是否生成了游标
        cache = session_data_manager._store.get(session_id, {})
        has_cursor = any("ref:geojson" in k for k in cache.keys())
        print(f"- 游标缓存生成: {'✅' if has_cursor else '❌'} ({list(cache.keys())})")
        
        # 检查 Celery 是否被调用
        celery_called = mock_celery.called
        print(f"- 异步任务发送: {'✅' if celery_called else '❌'}")
        if celery_called:
            print(f"  └─ 任务名称: {mock_celery.call_args[0][0]}")
            
        # 检查参数解析（解引用）
        # 如果 mock_celery 被调用时，第一个参数是 MOCK_GEOJSON 而不是 'ref:xxx'，说明解引用成功
        if celery_called:
            passed_geojson = mock_celery.call_args[1].get('args', [None])[0]
            if isinstance(passed_geojson, dict) or (isinstance(passed_geojson, list) and len(passed_geojson) > 0):
                print(f"- 参数解引用还原: ✅ (收到 Dict/List 数据)")
            else:
                print(f"- 参数解引用还原: ❌ (收到了 {type(passed_geojson)})")

    print("\n--- 集成测试结束 ---")

if __name__ == "__main__":
    asyncio.run(test_integrated_flow())
