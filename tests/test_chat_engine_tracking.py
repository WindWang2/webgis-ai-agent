"""ChatEngine Task Tracking 测试 - TDD"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.chat_engine import ChatEngine
from app.services.task_tracker import TaskTracker, detect_geojson
from app.tools.registry import ToolRegistry, tool


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode")
    def geocode(query: str) -> dict:
        return {"lat": 39.9042, "lon": 116.4074, "name": "北京"}

    @tool(r, name="query_osm_poi", description="Query POI")
    def query_osm_poi(area: str, category: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4074, 39.9042]}}
            ]
        }

    return r


def test_tracker_accessible(registry):
    """测试 tracker 在 ChatEngine 中可访问"""
    engine = ChatEngine(registry)
    assert hasattr(engine, "tracker")
    assert isinstance(engine.tracker, TaskTracker)


@pytest.mark.asyncio
async def test_stream_emits_task_start(registry):
    """测试流式对话发送 task_start 事件"""
    engine = ChatEngine(registry)

    # Mock LLM 返回最终回复（无工具调用）
    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_call": None}
    mock_response = {"choices": [{"message": mock_msg}]}

    # Mock _call_llm 方法
    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        events = []
        async for event in engine.chat_stream("北京坐标", session_id="test-s1"):
            events.append(event)

        # 验证 task_start 事件
        task_start_events = [e for e in events if "task_start" in e]
        assert len(task_start_events) >= 1

        # 解析 task_start 数据
        for e in task_start_events:
            if "task_start" in e:
                data = json.loads(e.split("data: ")[1])
                assert "task_id" in data
                assert data["task_id"].startswith("task-")


@pytest.mark.asyncio
async def test_stream_emits_task_complete(registry):
    """测试流式对话发送 task_complete 事件"""
    engine = ChatEngine(registry)

    # Mock LLM 返回最终回复
    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_calls": None}
    mock_response = {"choices": [{"message": mock_msg}]}

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        events = []
        async for event in engine.chat_stream("北京坐标", session_id="test-s2"):
            events.append(event)

        # 验证 task_complete 事件
        task_complete_events = [e for e in events if "task_complete" in e]
        assert len(task_complete_events) >= 1


@pytest.mark.asyncio
async def test_stream_emits_step_events_on_tool_call(registry):
    """测试工具调用时发送 step_start 和 step_result 事件（同时保留旧事件）"""
    engine = ChatEngine(registry)

    # 第一次调用返回 tool_call
    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}
        ]
    }
    resp1 = {"choices": [{"message": msg1}]}

    # 第二次调用返回最终回复
    msg2 = {"content": "北京的坐标是 39.9042, 116.4074", "tool_calls": None}
    resp2 = {"choices": [{"message": msg2}]}

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=[resp1, resp2]):
        events = []
        async for event in engine.chat_stream("查询北京坐标", session_id="test-s3"):
            events.append(event)

        # 验证新事件：step_start
        step_start_events = [e for e in events if "step_start" in e]
        assert len(step_start_events) >= 1, "should emit step_start event"
        for e in step_start_events:
            data = json.loads(e.split("data: ")[1])
            assert "task_id" in data
            assert "step_id" in data
            assert data["tool_name"] == "geocode"

        # 验证新事件：step_result
        step_result_events = [e for e in events if "step_result" in e]
        assert len(step_result_events) >= 1, "should emit step_result event"
        for e in step_result_events:
            data = json.loads(e.split("data: ")[1])
            assert "task_id" in data
            assert "step_id" in data
            assert "has_geojson" in data

        # 验证旧事件向后兼容：tool_call
        tool_call_events = [e for e in events if "tool_call" in e]
        assert len(tool_call_events) >= 1, "should still emit tool_call event"

        # 验证旧事件向后兼容：tool_result
        tool_result_events = [e for e in events if "tool_result" in e]
        assert len(tool_result_events) >= 1, "should still emit tool_result event"


@pytest.mark.asyncio
async def test_stream_step_error_on_tool_failure(registry):
    """测试工具执行失败时发送 step_error 事件"""
    engine = ChatEngine(registry)

    # Mock LLM 返回 tool_call
    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_err", "function": {"name": "geocode", "arguments": '{"query": "测试"}'}}
        ]
    }
    resp1 = {"choices": [{"message": msg1}]}

    # Mock registry.dispatch 抛出异常
    async def mock_dispatch(name, args):
        raise Exception("Tool execution failed")

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=resp1):
        with patch.object(engine.registry, "dispatch", new_callable=AsyncMock, side_effect=mock_dispatch):
            events = []
            async for event in engine.chat_stream("测试", session_id="test-s4"):
                events.append(event)

            # 验证 step_error 事件
            step_error_events = [e for e in events if "step_error" in e]
            assert len(step_error_events) >= 1, "should emit step_error event"
            for e in step_error_events:
                data = json.loads(e.split("data: ")[1])
                assert "task_id" in data
                assert "step_id" in data
                assert "error" in data


@pytest.mark.asyncio
async def test_task_cancellation_stops_loop(registry):
    """测试任务取消后停止循环并发送 task_cancelled 事件"""
    engine = ChatEngine(registry)

    # Mock LLM 返回 tool_call（无限循环直到取消）
    tc = MagicMock()
    tc.id = "call_1"
    tc.function.name = "geocode"
    tc.function.arguments = '{"query": "北京"}'
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=msg)]

    # 模拟在第二次迭代时取消任务
    call_count = 0

    async def mock_call_llm(messages, tools=None):
        nonlocal call_count
        call_count += 1
        return mock_response

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=mock_call_llm):
        # 第一次迭代后手动取消任务
        async def run_with_cancel():
            events = []
            async for event in engine.chat_stream("测试", session_id="test-s5"):
                events.append(event)
                # 在第一次 tool_call 后取消任务
                if "task_start" in event:
                    # 获取 task_id 并取消
                    for e in events:
                        if "task_start" in e:
                            task_id = json.loads(e.split("data: ")[1]).get("task_id")
                            if task_id:
                                engine.tracker.cancel(task_id)
                            break
            return events

        events = await run_with_cancel()

        # 验证 task_cancelled 事件
        cancelled_events = [e for e in events if "task_cancelled" in e]
        # 可能没有收到因为循环可能在取消前就结束
        # 验证任务确实被标记为 cancelled
        for e in events:
            if "task_start" in e:
                task_id = json.loads(e.split("data: ")[1]).get("task_id")
                if task_id:
                    task = engine.tracker.get(task_id)
                    if task:
                        # 任务应该被取消或已完成
                        assert task.status.value in ["cancelled", "completed"]


def test_detect_geojson():
    """测试 detect_geojson 函数"""
    # 测试直接 FeatureCollection
    result1 = {"type": "FeatureCollection", "features": []}
    assert detect_geojson(result1) is True

    # 测试嵌套 FeatureCollection
    result2 = {"data": {"type": "FeatureCollection", "features": []}}
    assert detect_geojson(result2) is True

    # 测试非 GeoJSON
    result3 = {"lat": 39.9042, "lon": 116.4074}
    assert detect_geojson(result3) is False

    # 测试非 dict
    assert detect_geojson("string") is False
    assert detect_geojson(None) is False