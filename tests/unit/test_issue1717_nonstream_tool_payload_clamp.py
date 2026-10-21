"""Issue #1717 回归测试：非流式 chat() 的工具载荷必须与 DB 同钳 100K 后才
驻进 ``_sessions`` LRU 会话历史（audit4 #980 流式修复的非流式 parity）。

修复前（``_chat_locked`` 主结果循环，原 execution_engine.py:1773-1779）：未
截断的 ``llm_payload`` 直接 append 进 ``messages``（``_get_or_create_session``
返回的 ``_sessions[session_id]`` LRU 列表），轮内重组时全量重发给 provider；
而 ``_save_msg_async`` 对 DB 写入有 100K 钳制 —— 内存驻全量、DB 存截断版，
两侧不一致。#817 异常 unwind 收获路径（finally 块）有同样缺陷。

修复：``_clamp_tool_payload`` 单一辅助，内存副本与 DB 同钳
``_TOOL_PAYLOAD_MAX_CHARS``（与流式 ``db_save_content`` 逐字节同义）。
"""
import json

import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat.execution_engine import (
    _TOOL_PAYLOAD_MAX_CHARS,
    _clamp_tool_payload,
)
from app.services.chat.tool_pipeline import ToolExecutionResult
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

_HUGE_PAYLOAD = json.dumps({"data": "X" * 150_000}, ensure_ascii=False)


def _tool_call_resp() -> dict:
    return {"choices": [{"message": {
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "big_tool", "arguments": "{}"},
        }],
    }}]}


def _final_resp() -> dict:
    return {"choices": [{"message": {"content": "done"}}]}


def _big_exec_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name="big_tool",
        tool_call_id="call_1",
        raw_result={"data": "X" * 150_000},
        llm_payload=_HUGE_PAYLOAD,
    )


async def _fake_load(session_id, user_id=None):
    # 本环境无可用 DB —— 显式 stub 加载成功，否则会话不会进 _sessions 缓存
    return [{"role": "system", "content": "sys"}]


def _tool_save(mock_save):
    """所有 role='tool' 的 _save_msg_async 调用。"""
    return [c for c in mock_save.call_args_list if len(c.args) > 1 and c.args[1] == "tool"]


# ─── 钳制辅助语义 ───────────────────────────────────────────────────────────


def test_clamp_tool_payload_boundary():
    """辅助本身：低于上限原样返回，恰好等于上限不截断，超限取前 100K。"""
    assert _clamp_tool_payload("short") == "short"
    at_cap = "y" * _TOOL_PAYLOAD_MAX_CHARS
    assert _clamp_tool_payload(at_cap) == at_cap
    clamped = _clamp_tool_payload("z" * 150_000)
    assert len(clamped) == _TOOL_PAYLOAD_MAX_CHARS
    assert clamped == "z" * _TOOL_PAYLOAD_MAX_CHARS


# ─── 非流式主结果循环 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nonstream_tool_payload_clamped_in_session_memory():
    """#1717：非流式标准工具轮 —— 内存副本与 DB 写入同钳 100K。

    断言两侧收到同一个截断串：内存侧（_sessions LRU 的 tool 消息 content）
    与 DB 侧（_save_msg_async 的 tool_result 实参）都是
    ``llm_payload[:100000]``，而非原始 150K 全量。
    """
    engine = ChatEngine(ToolRegistry())

    with patch.object(engine, "_load_session_from_db", side_effect=_fake_load), \
         patch.object(engine, "_call_llm", new_callable=AsyncMock,
                      side_effect=[_tool_call_resp(), _final_resp()]), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save, \
         patch.object(engine.tool_pipeline, "execute_tool_call",
                      new_callable=AsyncMock, return_value=_big_exec_result()):
        result = await engine.chat("跑一个大工具", session_id="sess-1717-main")

    assert result["content"] == "done"

    # 内存副本：_sessions LRU 里的 tool 消息被钳制
    session_msgs = engine._sessions["sess-1717-main"]
    tool_msgs = [m for m in session_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == _HUGE_PAYLOAD[:_TOOL_PAYLOAD_MAX_CHARS]
    assert len(tool_msgs[0]["content"]) == _TOOL_PAYLOAD_MAX_CHARS

    # DB 侧：与内存副本同一个截断串（此前 DB 收截断版、内存驻全量）
    saves = _tool_save(mock_save)
    assert len(saves) == 1
    assert saves[0].args[4] == tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_nonstream_short_tool_payload_not_clamped():
    """边界：未超限的工具载荷原样进出（钳制不得改变正常流量）。"""
    engine = ChatEngine(ToolRegistry())
    small = json.dumps({"data": "ok"})

    exec_res = ToolExecutionResult(
        tool_name="small_tool", tool_call_id="call_1",
        raw_result={"data": "ok"}, llm_payload=small,
    )

    with patch.object(engine, "_load_session_from_db", side_effect=_fake_load), \
         patch.object(engine, "_call_llm", new_callable=AsyncMock,
                      side_effect=[_tool_call_resp(), _final_resp()]), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save, \
         patch.object(engine.tool_pipeline, "execute_tool_call",
                      new_callable=AsyncMock, return_value=exec_res):
        await engine.chat("跑一个小工具", session_id="sess-1717-small")

    session_msgs = engine._sessions["sess-1717-small"]
    tool_msgs = [m for m in session_msgs if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == small
    assert _tool_save(mock_save)[0].args[4] == small


# ─── #817 异常 unwind 收获路径 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nonstream_unwind_path_clamps_tool_payload():
    """#1717：异常 unwind（#817 收获路径）同样把内存副本与 DB 同钳 100K。

    模拟 wave 等待被外部 abort（``_cancel_and_await`` 收到 set 形参的
    未完成任务集时抛错 → ``_wave_exc`` → finally 收获已完成工具）：
    收获落库/驻内存的 tool 消息必须是截断版。
    """
    engine = ChatEngine(ToolRegistry())
    task_id_box: dict = {}

    async def cancelling_execute(tc, session_id, task_id, *args, **kwargs):
        # 工具完成前点燃取消令牌 —— 走 F28 抢占式取消分支，再由爆炸版
        # _cancel_and_await 把 wave 等待变成异常 unwind。
        task_id_box["id"] = task_id
        engine.tracker.cancel(task_id)
        return _big_exec_result()

    real_cancel_and_await = engine._cancel_and_await

    async def exploding_cancel_and_await(tasks, timeout=None):
        if isinstance(tasks, set):
            raise RuntimeError("wave abort")
        return await real_cancel_and_await(tasks, timeout)

    with patch.object(engine, "_load_session_from_db", side_effect=_fake_load), \
         patch.object(engine, "_call_llm", new_callable=AsyncMock,
                      side_effect=[_tool_call_resp(), _final_resp()]), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save, \
         patch.object(engine.tool_pipeline, "execute_tool_call",
                      new_callable=AsyncMock, side_effect=cancelling_execute), \
         patch.object(engine, "_cancel_and_await", exploding_cancel_and_await):
        with pytest.raises(RuntimeError, match="wave abort"):
            await engine.chat("跑一个大工具后 abort", session_id="sess-1717-unwind")

    # unwind 收获路径确实落过库（否则本测试空跑失去意义）
    saves = _tool_save(mock_save)
    assert len(saves) == 1
    assert saves[0].args[4] == _HUGE_PAYLOAD[:_TOOL_PAYLOAD_MAX_CHARS]

    session_msgs = engine._sessions["sess-1717-unwind"]
    tool_msgs = [m for m in session_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == _HUGE_PAYLOAD[:_TOOL_PAYLOAD_MAX_CHARS]
