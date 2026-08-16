"""#436: subagent context isolation on the INPUT side (sibling of #407).

#407 fixed the output side: a subagent engine never persists its transcript
into the parent conversation. But the input side leaked: the sub engine
reuses the parent's session_id, and

  - ``_get_or_create_session`` → ``_load_session_from_db`` has no subagent
    bypass, so the parent's persisted history (budget-truncated to ~6000
    tokens) is loaded into the subagent's message list;
  - ``ChatContextAssembler.assemble`` injects
    ``render_plan_block(get_plan(session_id))`` — the PARENT's active plan,
    including "unfinished steps" admonitions and tool_family hints for
    tools the subagent may not even have.

The subagent tool contract states the subagent sees only its task. Tests
below spawn a subagent against a parent session that has history + an
active plan and assert the assembled LLM prompt contains neither.

Deterministic: LLM call, history DB read and saves are faked; no network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.chat import planner as planner_mod
from app.services.subagent import SubagentDispatcher
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

_PARENT_MARKER = "PARENT-HISTORY-MARKER-XYZ"


@pytest.fixture
def parent_plan():
    plan = planner_mod.Plan(
        intent="PARENT-PLAN-INTENT-MARKER 分析全国医院分布",
        domains=["core"],
        steps=[
            planner_mod.PlanStep(n=1, goal="PARENT-STEP load hospitals", tool_family="core"),
            planner_mod.PlanStep(n=2, goal="PARENT-STEP buffer analysis", tool_family="core"),
        ],
    )
    planner_mod.set_plan("sess-parent-436", plan)
    yield plan
    planner_mod.clear_plan("sess-parent-436")


@pytest.fixture
def captured_llm_prompts(monkeypatch):
    """Capture the composed message list the sub engine sends to the LLM."""
    captured: list[list[dict]] = []

    async def fake_call_llm(self, messages, tools):  # noqa: ANN001
        captured.append(messages)
        return {"choices": [{"message": {"content": "子任务完成：已处理。", "reasoning": ""}}]}

    monkeypatch.setattr(ChatEngine, "_call_llm", fake_call_llm)
    return captured


# ─── unit: _get_or_create_session bypasses the DB history load ───────────────


@pytest.mark.asyncio
async def test_subengine_does_not_load_parent_history(monkeypatch):
    engine = ChatEngine(ToolRegistry(), is_subagent_engine=True)

    async def spy_load(session_id, user_id=None):
        spy_load.called = True
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _PARENT_MARKER},
            {"role": "assistant", "content": "parent answer"},
        ]

    spy_load.called = False
    monkeypatch.setattr(engine, "_load_session_from_db", spy_load)

    messages = await engine._get_or_create_session("sess-parent-436")

    assert not spy_load.called, "subagent engine loaded the parent's DB history"
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert _PARENT_MARKER not in str(messages)


@pytest.mark.asyncio
async def test_main_engine_still_loads_parent_history(monkeypatch):
    """The bypass must be scoped to subagent engines only — a main engine on
    the same session still hydrates from the DB."""
    engine = ChatEngine(ToolRegistry())

    async def fake_load(session_id, user_id=None):
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _PARENT_MARKER},
        ]

    monkeypatch.setattr(engine, "_load_session_from_db", fake_load)
    messages = await engine._get_or_create_session("sess-parent-436-main")
    assert any(_PARENT_MARKER in str(m) for m in messages)


# ─── unit: assembler plan-block gate ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_assembler_plan_block_gate(parent_plan):
    from app.services.chat.context_assembler import ChatContextAssembler

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
    ]
    with_block = await ChatContextAssembler().assemble(
        "sess-parent-436", list(messages)
    )
    assert any("执行计划" in str(m.get("content", "")) for m in with_block.to_messages())

    without_block = await ChatContextAssembler().assemble(
        "sess-parent-436", list(messages), include_plan_block=False
    )
    rendered = "\n".join(str(m.get("content", "")) for m in without_block.to_messages())
    assert "执行计划" not in rendered
    assert "PARENT-PLAN-INTENT-MARKER" not in rendered


# ─── integration: dispatcher.run end-to-end prompt isolation ─────────────────


@pytest.mark.asyncio
async def test_spawned_subagent_prompt_excludes_parent_history_and_plan(
    parent_plan, captured_llm_prompts, monkeypatch
):
    monkeypatch.setattr(
        ChatEngine, "_save_msg_async", AsyncMock()
    )  # defensive: no DB writes during test
    dispatcher = SubagentDispatcher(ToolRegistry(), "sess-parent-436")

    # Parent's DB history would be loaded here if the bypass were missing.
    async def fake_load(self, session_id, user_id=None):  # noqa: ANN001
        return [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": _PARENT_MARKER},
            {"role": "assistant", "content": "parent 的旧回答"},
            {"role": "user", "content": f"{_PARENT_MARKER} 第二轮"},
        ]

    monkeypatch.setattr(ChatEngine, "_load_session_from_db", fake_load)

    result = await dispatcher.run(
        task="为海淀区每个医院找最近的地铁站",
        domains=["network"],
    )

    assert result.success, result.error
    assert captured_llm_prompts, "LLM was never called"
    prompt_text = "\n".join(
        str(m.get("content", "")) for m in captured_llm_prompts[0]
    )

    # Task itself must be present (wrapped subagent prompt).
    assert "为海淀区每个医院找最近的地铁站" in prompt_text
    # Parent's persisted history must be absent.
    assert _PARENT_MARKER not in prompt_text, (
        "subagent context contains the parent's persisted history"
    )
    # Parent's active plan block must be absent.
    assert "执行计划" not in prompt_text, (
        "subagent context contains the parent's active plan block"
    )
    assert "PARENT-PLAN-INTENT-MARKER" not in prompt_text
    assert "PARENT-STEP" not in prompt_text


@pytest.mark.asyncio
async def test_subagent_context_size_independent_of_parent_history(
    captured_llm_prompts, monkeypatch
):
    """Acceptance: assembled context length is independent of N parent turns."""
    monkeypatch.setattr(ChatEngine, "_save_msg_async", AsyncMock())

    async def make_parent_history(n_turns: int):
        async def fake_load(self, session_id, user_id=None):  # noqa: ANN001
            msgs = [{"role": "system", "content": self._build_system_prompt()}]
            for i in range(n_turns):
                msgs.append({"role": "user", "content": f"{_PARENT_MARKER} turn {i}"})
                msgs.append({"role": "assistant", "content": f"answer {i}"})
            return msgs

        return fake_load

    sizes = []
    for n_turns in (2, 40):
        captured_llm_prompts.clear()
        monkeypatch.setattr(
            ChatEngine, "_load_session_from_db", await make_parent_history(n_turns)
        )
        dispatcher = SubagentDispatcher(ToolRegistry(), "sess-parent-436")
        res = await dispatcher.run(task="子任务", domains=["core"])
        assert res.success
        sizes.append(len(str(captured_llm_prompts[0])))

    assert sizes[0] == sizes[1], (
        f"subagent context size depends on parent history length: {sizes}"
    )
