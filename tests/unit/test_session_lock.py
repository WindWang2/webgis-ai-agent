"""B2 修复回归：_get_or_create_session 在并发下不应双加载。"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_concurrent_get_or_create_loads_db_once():
    engine = ChatEngine(ToolRegistry())
    load_count = 0

    async def fake_load(session_id):
        nonlocal load_count
        load_count += 1
        await asyncio.sleep(0.02)  # 模拟 DB I/O
        return [{"role": "system", "content": "x"}]

    engine._load_session_from_db = fake_load  # type: ignore[assignment]

    # 10 个并发同 session_id 请求
    results = await asyncio.gather(*[
        engine._get_or_create_session("same-sid") for _ in range(10)
    ])

    assert load_count == 1, f"应只触发 1 次 DB 加载，实际 {load_count}"
    # 所有 coroutine 应当拿到同一个 list 对象
    first = results[0]
    assert all(r is first for r in results)


@pytest.mark.asyncio
async def test_different_sessions_load_independently():
    engine = ChatEngine(ToolRegistry())
    load_count = 0

    async def fake_load(session_id):
        nonlocal load_count
        load_count += 1
        return [{"role": "system", "content": session_id}]

    engine._load_session_from_db = fake_load  # type: ignore[assignment]

    await asyncio.gather(
        engine._get_or_create_session("A"),
        engine._get_or_create_session("B"),
        engine._get_or_create_session("C"),
    )
    assert load_count == 3
