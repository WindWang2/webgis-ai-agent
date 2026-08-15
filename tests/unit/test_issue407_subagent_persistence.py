"""Issue #407 回归测试：子代理内部 transcript 绝不持久化进父会话 DB 历史。

修复前：SubagentDispatcher 用 parent_session_id 跑子引擎，子代理每条内部
消息（任务 prompt、assistant 消息、tool_call/tool 响应）都经 _save_msg_async
写进父会话 DB 行 —— 重载 / LRU 逐出后父上下文包含完整子代理 transcript。

修复（两层）：
- 子代理引擎（is_subagent_engine=True）的 _save_msg_async 整体抑制持久化；
- 「clearing」标记提升为注册表级（class 级共享）：任一引擎实例 clear_session
  后，其他实例（含 in-flight 子引擎）对该会话的 DB 写同样被抑制。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register("buffer_analysis", "buf", func=lambda **_: {}, tier=1)
    return r


def _recording_history(saved: list):
    """记录 save_message 的 AsyncHistoryService 替身（load_context 返回空历史）。"""
    from app.services.history_store_protocol import HistoryContext

    class _RecordingHistory:
        def __init__(self, db=None):
            self.db = db

        async def load_context(self, **kwargs):
            return HistoryContext(
                session_id=kwargs.get("session_id", ""),
                owner_token=None,
                user_id=None,
                llm_messages=[],
                raw_conversation=None,
            )

        async def save_message(
            self,
            session_id,
            role,
            content,
            tool_calls=None,
            tool_result=None,
            tool_call_id=None,
            reasoning_content=None,
        ):
            saved.append((session_id, role, content))

    return _RecordingHistory


# ─── 子代理不写父会话 DB ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_turn_does_not_persist_to_parent_db_history(registry):
    """真实子引擎跑一轮完整工具回合：父会话 DB 历史零写入。"""
    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-407-sub"
    saved: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )
    assert sub_engine.is_subagent_engine is True

    resp1 = {"choices": [{"message": {"content": "调用工具", "tool_calls": [
        {"id": "call_1", "function": {"name": "buffer_analysis", "arguments": '{"radius": 10}'}},
    ]}}]}
    resp2 = {"choices": [{"message": {"content": "子任务完成，生成 ref:data-abc。"}}]}

    with patch.object(sub_engine, "_call_llm", new_callable=AsyncMock, side_effect=[resp1, resp2]):
        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
            result = await sub_engine.chat("子任务：批量统计", session_id=parent_sid)

    assert "子任务完成" in result["content"]  # 子代理正常跑完
    assert saved == []  # 子代理内部消息（user/assistant/tool）从不写进父会话 DB


@pytest.mark.asyncio
async def test_subagent_repair_orphaned_tool_calls_does_not_persist(registry):
    """取消/断连路径的孤儿 repair（_repair_orphaned_tool_calls 的 DB 写）同样被抑制。

    内存 repair 照常（消息配对补齐，子引擎自己的上下文保持合法），但绝不写库。
    """
    import asyncio

    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-407-repair"
    saved: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )

    messages = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_orphan_1", "function": {"name": "buffer_analysis", "arguments": "{}"}},
        ]},
    ]
    with patch(
        "app.services.chat.execution_engine.AsyncHistoryService",
        _recording_history(saved),
    ):
        await sub_engine._repair_orphaned_tool_calls(parent_sid, messages)

    # 内存 repair 照常补齐（子引擎上下文合法），DB 零写入
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call_orphan_1"
    assert saved == []


# ─── clearing 标记注册表级共享 ────────────────────────────────────


@pytest.mark.asyncio
async def test_clearing_marker_is_registry_wide(registry):
    """任一实例标记 clearing 后，其他实例（如 in-flight 子引擎）写库被抑制。"""
    from app.services.chat.execution_engine import SessionClearingError
    from app.services.chat_engine import ChatEngine

    e1 = ChatEngine(registry)
    e2 = ChatEngine(registry)
    sid = "sess-407-clear"
    saved: list = []

    try:
        e1._clearing_sessions.add(sid)
        assert sid in e2._clearing_sessions  # 注册表级共享（同一集合）

        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
            await e2._save_msg_async(sid, "user", "hello")  # 其他实例的写被抑制
            await e2._save_msg_async("sess-407-other", "user", "hi")  # 非 clearing 会话照常

        assert saved == [("sess-407-other", "user", "hi")]

        with pytest.raises(SessionClearingError):
            e2._reject_if_clearing(sid)  # 新 turn 拒绝（跨实例生效）
    finally:
        e1._clearing_sessions.discard(sid)  # 清掉共享标记，避免泄漏到其他测试
