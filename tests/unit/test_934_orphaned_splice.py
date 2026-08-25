"""Regression for #934: _repair_orphaned_tool_calls must splice after caller."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.chat.execution_engine import ChatExecutionEngine


def _validate_order(msgs: list[dict]) -> None:
    pending: set[str] = set()
    for idx, msg in enumerate(msgs):
        role = msg.get("role")
        if pending:
            assert role == "tool" and msg.get("tool_call_id") in pending, (
                f"violation at {idx}: expected tool in {pending}, got {msg}"
            )
            pending.remove(msg.get("tool_call_id"))
        elif role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls"):
                pending.add(tc.get("id"))


@pytest.mark.asyncio
async def test_single_orphan_mid_conversation_spliced_after_caller():
    engine = ChatExecutionEngine(MagicMock(), MagicMock())
    engine._save_msg_async = AsyncMock()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "user", "content": "q2"},
    ]
    await engine._repair_orphaned_tool_calls("s", messages)
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "tool" and messages[3]["tool_call_id"] == "call_1"
    assert messages[4]["role"] == "user"
    _validate_order(messages)
    assert engine._save_msg_async.call_count == 1


@pytest.mark.asyncio
async def test_multiple_orphans_preserve_relative_order():
    engine = ChatExecutionEngine(MagicMock(), MagicMock())
    engine._save_msg_async = AsyncMock()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "tool_calls": [{"id": "A"}]},
        {"role": "assistant", "tool_calls": [{"id": "B"}]},
        {"role": "user", "content": "next"},
    ]
    await engine._repair_orphaned_tool_calls("s", messages)
    # expected: sys, A, tool A, B, tool B, user
    assert [m.get("tool_call_id") or (m.get("tool_calls") or [{}])[0].get("id") for m in messages if m["role"] in ("assistant","tool")] == ["A", "A", "B", "B"]
    _validate_order(messages)


@pytest.mark.asyncio
async def test_multiple_ids_per_assistant_insert_contiguously_in_order():
    engine = ChatExecutionEngine(MagicMock(), MagicMock())
    engine._save_msg_async = AsyncMock()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "tool_calls": [{"id": "X"}, {"id": "Y"}]},
        {"role": "user", "content": "q"},
    ]
    await engine._repair_orphaned_tool_calls("s", messages)
    assert messages[1]["role"] == "assistant"
    assert messages[2]["tool_call_id"] == "X"
    assert messages[3]["tool_call_id"] == "Y"
    assert messages[4]["role"] == "user"
    _validate_order(messages)


@pytest.mark.asyncio
async def test_orphan_already_at_tail_appends_correctly():
    engine = ChatExecutionEngine(MagicMock(), MagicMock())
    engine._save_msg_async = AsyncMock()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"id": "Z"}]},
    ]
    await engine._repair_orphaned_tool_calls("s", messages)
    assert messages[-1]["role"] == "tool" and messages[-1]["tool_call_id"] == "Z"
    _validate_order(messages)


@pytest.mark.asyncio
async def test_already_answered_not_duplicated():
    engine = ChatExecutionEngine(MagicMock(), MagicMock())
    engine._save_msg_async = AsyncMock()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "tool_calls": [{"id": "OK"}]},
        {"role": "tool", "tool_call_id": "OK", "content": "done"},
        {"role": "user", "content": "next"},
    ]
    await engine._repair_orphaned_tool_calls("s", messages)
    assert len(messages) == 4
    _validate_order(messages)
    engine._save_msg_async.assert_not_called()
