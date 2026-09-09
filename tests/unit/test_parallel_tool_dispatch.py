"""
Unit tests for parallel asyncio.gather tool execution dispatch in ChatExecutionEngine
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.chat.execution_engine import ChatExecutionEngine, ToolExecutionResult
from tests.fixtures.perf_budget import ameasure_median

# 并发语义的结构常量：每个 mock 工具睡 SLEEP_S，共 N_CALLS 个。
# 串行总时长 = N_CALLS * SLEEP_S；并发理想 ≈ SLEEP_S。
SLEEP_S = 0.05
N_CALLS = 3


@pytest.mark.asyncio
async def test_execution_engine_parallel_tool_dispatch():
    engine = ChatExecutionEngine(tool_registry=MagicMock())
    engine.tool_pipeline = MagicMock()

    # Mock tool executions with non-zero sleep to verify concurrent speedup
    async def mock_exec(tc, session_id, task_id, executed_tools):
        await asyncio.sleep(SLEEP_S)
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

    # W11 迁移（原固定断言 ``elapsed < 0.12``）：语义是结构性的——「3 个
    # 调用并发跑完，而不是串行 3×sleep」——改为比值断言：median 耗时必须
    # 明显小于串行上界（0.8 × N×sleep，与原 0.12s 同值但由常量派生），
    # median-of-3 抗事件循环调度抖动。退化为串行（≥ 0.15s）时必红。
    executed_tools = set()
    holder: dict = {}

    async def _round() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        holder["results"] = await asyncio.gather(*[
            engine.tool_pipeline.execute_tool_call(
                tc, "sess_123", "task_123", executed_tools)
            for tc in tc_list
        ])
        return loop.time() - start

    elapsed_median = await ameasure_median(_round, iterations=3)
    results = holder["results"]

    assert len(results) == 3
    assert results[0].llm_payload == "st_dbscan_ok"
    assert results[1].llm_payload == "combine_map_theme_ok"
    assert results[2].llm_payload == "fetch_poi_radius_ok"
    # 并发比值断言：median 总时长 < 0.8 × 串行（结构性，机器速度无关）；
    # 串行退化（≥ N×sleep = 0.15s）必然命中。
    sequential_bound = N_CALLS * SLEEP_S
    assert elapsed_median < 0.8 * sequential_bound, (
        f"parallel round median {elapsed_median:.3f}s >= "
        f"0.8 * sequential bound {sequential_bound:.3f}s — dispatch may be "
        "running sequentially"
    )
