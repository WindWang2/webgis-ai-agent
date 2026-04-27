"""ChatEngine Task Tracking 测试 - TDD"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.chat_engine import ChatEngine
from app.services.task_tracker import TaskTracker, detect_geojson
from app.tools.registry import ToolRegistry, tool


def _fake_stream(response_msg):
    """Create a fake _call_llm_stream that yields a single 'done' event."""
    async def stream(*args, **kwargs):
        yield ("done", {"message": response_msg})
    return stream


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

    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(mock_msg)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("北京坐标", session_id="test-s1"):
                events.append(event)

        task_start_events = [e for e in events if "task_start" in e]
        assert len(task_start_events) >= 1
        data = json.loads(task_start_events[0].split("data: ")[1])
        assert "task_id" in data
        assert data["task_id"].startswith("task-")


@pytest.mark.asyncio
async def test_stream_emits_task_complete(registry):
    """测试流式对话发送 task_complete 事件"""
    engine = ChatEngine(registry)

    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(mock_msg)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("北京坐标", session_id="test-s2"):
                events.append(event)

        task_complete_events = [e for e in events if "task_complete" in e]
        assert len(task_complete_events) >= 1


@pytest.mark.asyncio
async def test_stream_emits_step_events_on_tool_call(registry):
    """测试工具调用时发送 step_start 和 step_result 事件"""
    engine = ChatEngine(registry)

    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}
        ]
    }
    msg2 = {"content": "北京的坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("查询北京坐标", session_id="test-s3"):
                events.append(event)

        step_start_events = [e for e in events if "step_start" in e]
        assert len(step_start_events) >= 1, "should emit step_start event"
        data = json.loads(step_start_events[0].split("data: ")[1])
        assert "task_id" in data
        assert "step_id" in data
        assert data["tool"] == "geocode"

        step_result_events = [e for e in events if "step_result" in e]
        assert len(step_result_events) >= 1, "should emit step_result event"

        tool_call_events = [e for e in events if "tool_call" in e]
        assert len(tool_call_events) >= 1, "should still emit tool_call event"

        tool_result_events = [e for e in events if "tool_result" in e]
        assert len(tool_result_events) >= 1, "should still emit tool_result event"


@pytest.mark.asyncio
async def test_stream_step_error_on_tool_failure(registry):
    """测试工具执行失败时发送 step_error 事件"""
    engine = ChatEngine(registry)

    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_err", "function": {"name": "geocode", "arguments": '{"query": "测试"}'}}
        ]
    }

    async def mock_dispatch(name, args):
        raise Exception("Tool execution failed")

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg1)()):
        with patch.object(engine.registry, "dispatch", new_callable=AsyncMock, side_effect=mock_dispatch):
            with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
                events = []
                async for event in engine.chat_stream("测试", session_id="test-s4"):
                    events.append(event)

                step_error_events = [e for e in events if "step_error" in e]
                assert len(step_error_events) >= 1, "should emit step_error event"
                data = json.loads(step_error_events[0].split("data: ")[1])
                assert "task_id" in data
                assert "step_id" in data
                assert "error" in data


@pytest.mark.asyncio
async def test_task_cancellation_stops_loop(registry):
    """测试任务取消后停止循环并发送 task_cancelled 事件"""
    engine = ChatEngine(registry)

    msg_with_tool = {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}
        ]
    }

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg_with_tool)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("测试", session_id="test-s5"):
                events.append(event)
                if "task_start" in event:
                    task_id = json.loads(event.split("data: ")[1]).get("task_id")
                    if task_id:
                        engine.tracker.cancel(task_id)

        cancelled_events = [e for e in events if "task_cancelled" in e]
        assert len(cancelled_events) >= 1, "should emit task_cancelled event"


def test_detect_geojson():
    """测试 detect_geojson 函数"""
    result1 = {"type": "FeatureCollection", "features": []}
    assert detect_geojson(result1) is True

    result2 = {"data": {"type": "FeatureCollection", "features": []}}
    assert detect_geojson(result2) is True

    result3 = {"lat": 39.9042, "lon": 116.4074}
    assert detect_geojson(result3) is False

    assert detect_geojson("string") is False
    assert detect_geojson(None) is False
