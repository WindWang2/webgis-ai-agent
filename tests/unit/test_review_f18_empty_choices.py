"""Review F18: ``choices: []`` must not crash with IndexError; it takes the
honest empty-completion path."""
import pytest

import app.services.chat.execution_engine as ee
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_empty_choices_is_not_index_error(monkeypatch):
    async def _fake_llm(self, messages, tools=None):
        return {"choices": [], "usage": {}}

    monkeypatch.setattr(ee.ChatExecutionEngine, "_call_llm", _fake_llm)
    from app.services.chat_engine import ChatEngine

    engine = ChatEngine(ToolRegistry())
    try:
        await engine.chat(message="hi", session_id="sess-review-f18")
    except IndexError as e:  # pragma: no cover - the regression
        pytest.fail(f"choices: [] crashed the parse: {e!r}")
    except Exception:
        pass  # honest empty-completion failure is acceptable
