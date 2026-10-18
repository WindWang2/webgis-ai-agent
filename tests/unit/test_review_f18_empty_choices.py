"""Review F18: ``choices: []`` must not crash with IndexError; it takes the
honest empty-completion path."""
import pytest

import app.services.chat.execution_engine as ee
from app.services.chat.turn_recovery import EmptyCompletionError
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_empty_choices_takes_honest_empty_completion_path(monkeypatch):
    async def _fake_llm(self, messages, tools=None):
        return {"choices": [], "usage": {}}

    monkeypatch.setattr(ee.ChatExecutionEngine, "_call_llm", _fake_llm)
    from app.services.chat_engine import ChatEngine

    engine = ChatEngine(ToolRegistry())
    # 诚实路径的定义（execution_engine._chat_locked 空补全分支）：
    # tracker.fail_task + 抛 EmptyCompletionError（failure_class=empty_result），
    # 绝不保存空 assistant 消息、也绝不静默吞成空响应。
    with pytest.raises(EmptyCompletionError, match="empty completion from provider"):
        await engine.chat(message="hi", session_id="sess-review-f18")
