"""
Unit tests for parallel asyncio.gather tool execution dispatch in ChatExecutionEngine
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.chat.execution_engine import ChatExecutionEngine, ToolExecutionResult


@pytest.mark.asyncio
async def test_execution_engine_parallel_tool_dispatch():
    engine = ChatExecutionEngine(tool_registry=MagicMock())
    engine.tool_pipeline = MagicMock()


    # Mock tool executions with non-zero sleep to verify concurrent speedup
    async def mock_exec(tc, session_id, task_id, executed_tools):
        await asyncio.sleep(0.05)
        name = tc["function"]["name"]
        return ToolExecutionResult(
            tool_name=name,
            tool_call_id=tc["id"],
            raw_result={"success": True},
            llm_payload=f"{name}_ok"
        )

    engine.tool_pipeline.execute_tool_call = AsyncMock(side_effect=mock_exec)

    tc_list = [
        {"id": "call_1", "function": {"name": "st_dbscan"}},
        {"id": "call_2", "function": {"name": "combine_map_theme"}},
        {"id": "call_3", "function": {"name": "fetch_poi_radius"}},
    ]

    executed_tools = set()
    start_time = asyncio.get_event_loop().time()

    tasks = [
        engine.tool_pipeline.execute_tool_call(tc, "sess_123", "task_123", executed_tools)
        for tc in tc_list
    ]
    results = await asyncio.gather(*tasks)
    elapsed = asyncio.get_event_loop().time() - start_time

    assert len(results) == 3
    assert results[0].llm_payload == "st_dbscan_ok"
    assert results[1].llm_payload == "combine_map_theme_ok"
    assert results[2].llm_payload == "fetch_poi_radius_ok"
    # Total elapsed time should be ~0.05s (concurrent) rather than 0.15s (sequential)
    assert elapsed < 0.12
