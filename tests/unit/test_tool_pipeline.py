"""Unit tests for ToolExecutionPipeline (ADR-0024)."""
import pytest
import json
from app.tools.registry import ToolRegistry, tool
from app.services.task_tracker import TaskTracker
from app.services.chat.tool_pipeline import ToolExecutionPipeline, ToolExecutionResult


def _build_test_registry():
    r = ToolRegistry()

    @tool(r, name="echo_tool", description="Echo input")
    def echo_tool(msg: str) -> dict:
        return {"echo": msg}

    @tool(r, name="error_tool", description="Throw error")
    def error_tool() -> dict:
        raise ValueError("Tool failure test")

    return r


@pytest.mark.asyncio
async def test_tool_pipeline_successful_execution():
    registry = _build_test_registry()
    tracker = TaskTracker()
    pipeline = ToolExecutionPipeline(registry, tracker)

    task = tracker.create("session_1", "Test message")
    tc = {
        "id": "call_123",
        "function": {
            "name": "echo_tool",
            "arguments": json.dumps({"msg": "hello world"}),
        },
    }

    res = await pipeline.execute_tool_call(tc, session_id="session_1", task_id=task.id)

    assert isinstance(res, ToolExecutionResult)
    assert res.tool_name == "echo_tool"
    assert res.tool_call_id == "call_123"
    assert res.raw_result == {"echo": "hello world"}
    assert not res.is_error
    assert "echo" in res.llm_payload

    # Verify task tracker steps
    updated_task = tracker.get(task.id)
    assert len(updated_task.steps) == 1
    assert updated_task.steps[0].status == "completed"


@pytest.mark.asyncio
async def test_tool_pipeline_duplicate_sentinel_blocking():
    registry = _build_test_registry()
    pipeline = ToolExecutionPipeline(registry)

    tc = {
        "id": "call_1",
        "function": {
            "name": "echo_tool",
            "arguments": json.dumps({"msg": "same_arg"}),
        },
    }

    executed_tools = set()

    # First execution should succeed
    res1 = await pipeline.execute_tool_call(tc, session_id="session_1", executed_tools=executed_tools)
    assert not res1.is_error

    # Second execution with identical args should be blocked by sentinel
    res2 = await pipeline.execute_tool_call(tc, session_id="session_1", executed_tools=executed_tools)
    assert "[重复调用拦截]" in res2.llm_payload


@pytest.mark.asyncio
async def test_tool_pipeline_error_handling():
    registry = _build_test_registry()
    tracker = TaskTracker()
    pipeline = ToolExecutionPipeline(registry, tracker)

    task = tracker.create("session_1", "Test error task")
    tc = {
        "id": "call_err",
        "function": {
            "name": "error_tool",
            "arguments": "{}",
        },
    }

    res = await pipeline.execute_tool_call(tc, session_id="session_1", task_id=task.id)
    assert res.is_error
    assert "Tool failure test" in res.llm_payload

    updated_task = tracker.get(task.id)
    assert len(updated_task.steps) == 1
    assert updated_task.steps[0].status == "failed"
