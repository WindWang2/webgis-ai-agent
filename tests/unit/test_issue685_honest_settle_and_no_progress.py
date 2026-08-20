"""#685: 非流式诚实 settle + no-progress 熔断 + parity 回归。

验收：
- 非流式：空补全 → 失败；max-rounds → FAILED；连续重复/无进展触发熔断
- 两路径 success/failure 判定 parity
- flip-red：新测试在未修复代码上必须红
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.services.subagent import SubagentDispatcher


def _make_registry_with_echo_tool() -> ToolRegistry:
    r = ToolRegistry()

    @r.tool(name="echo_tool", description="echo", tier=1)
    def echo_tool(text: str = "") -> dict:
        return {"echo": text, "success": True}

    return r


# ─── helpers ───────────────────────────────────────────────────────────────

async def _fake_save(*a, **k):
    return None

async def _fake_get_or_create_session(session_id, user_id=None):
    return [{"role": "system", "content": "sys"}]

async def _fake_maybe_plan(*a, **k):
    return None

async def _fake_generate_title(*a, **k):
    return None


@pytest.fixture
def engine(monkeypatch):
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    # 隔离 DB / plan / title
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    monkeypatch.setattr(eng, "_persist_map_state", AsyncMock())
    # 默认阈值 3，与 config 一致
    eng._no_progress_threshold = 3
    return eng


# ─── 非流式：空补全 → 失败 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nostream_empty_completion_fails(engine, monkeypatch):
    """非流式空补全（无 content 无 tool_calls）必须 fail，不再 complete_task 成功。"""
    empty_resp = {"choices": [{"message": {"content": "", "tool_calls": None}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=empty_resp))

    with pytest.raises(RuntimeError, match="empty completion"):
        await engine.chat("hi", session_id="s-empty-685")

    # tracker 应为 failed
    tasks = engine.tracker.list_by_session("s-empty-685")
    assert tasks and tasks[-1].status.value == "failed"


@pytest.mark.asyncio
async def test_stream_empty_completion_fails_parity(monkeypatch):
    """流式空补全同样 fail（parity 锚点）。"""
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)

    msg = {"content": "", "tool_calls": None}

    async def fake_stream(*a, **k):
        yield ("done", {"message": msg})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)

    events = []
    async for e in eng.chat_stream("hi", session_id="s-stream-empty-685"):
        events.append(e)
    # 应包含 task_error + done，且无 task_complete
    assert any("task_error" in e for e in events)
    assert any("done" in e for e in events)
    assert not any("task_complete" in e for e in events)
    tasks = eng.tracker.list_by_session("s-stream-empty-685")
    assert tasks and tasks[-1].status.value == "failed"


@pytest.mark.asyncio
async def test_nostream_empty_whitespace_also_fails(engine, monkeypatch):
    """仅空白符的 content 也算空补全，必须 fail。"""
    resp = {"choices": [{"message": {"content": "   \n  ", "tool_calls": None}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=resp))
    with pytest.raises(RuntimeError, match="empty completion"):
        await engine.chat("hi", session_id="s-ws-685")


# ─── 非流式：max-rounds → FAILED ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_nostream_max_rounds_fails(engine, monkeypatch):
    """工具循环耗尽 max_rounds 必须 fail（与流式同形），不再 complete_task。"""
    engine.max_rounds = 3

    tc = {"id": "call_1", "function": {"name": "echo_tool", "arguments": '{"text": "a"}'}}
    tool_resp = {"choices": [{"message": {"content": "", "tool_calls": [tc]}}]}

    # 让 LLM 每轮都返回同一个工具调用；dispatch 去重后后续轮会是 repeated（无进展）
    # 但为单独验证 max_rounds 语义，这里把阈值调高避免提前熔断
    engine._no_progress_threshold = 10

    # mock LLM：每轮都返回带 tool_call 的响应
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=tool_resp))

    # mock 工具执行：每轮都成功但可疑结果不过 plan，这里返回 ok 以便持续循环
    # 实际上 echo_tool 有 dedup，首轮 ok 次轮 repeated；把阈值设高后走满 3 轮
    with pytest.raises(RuntimeError, match="达到最大工具调用轮数"):
        await engine.chat("hi", session_id="s-max-685")

    tasks = engine.tracker.list_by_session("s-max-685")
    assert tasks and tasks[-1].status.value == "failed"


# ─── no-progress 熔断：连续重复轮 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_nostream_no_progress_breaker_triggers(engine, monkeypatch):
    """连续 N 轮工具结果全为 repeated → 触发 no_progress 熔断并 fail。"""
    engine.max_rounds = 10
    engine._no_progress_threshold = 3

    tc = {"id": "call_1", "function": {"name": "echo_tool", "arguments": '{"text": "x"}'}}
    tool_resp = {"choices": [{"message": {"content": "", "tool_calls": [tc]}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=tool_resp))

    with pytest.raises(RuntimeError, match="no progress"):
        await engine.chat("hi", session_id="s-np-685")

    tasks = engine.tracker.list_by_session("s-np-685")
    assert tasks and tasks[-1].status.value == "failed"
    # 不应烧满 10 轮；至少应在阈值+首轮成功轮内结束
    # 严格断言：LLM 调用次数 < max_rounds
    assert engine._call_llm.call_count < 10


@pytest.mark.asyncio
async def test_nostream_no_progress_does_not_trigger_on_success(engine, monkeypatch):
    """若中间出现一次成功进展，streak 应重置，不应误熔断。"""
    engine.max_rounds = 5
    engine._no_progress_threshold = 3

    # 构造：首轮 ok，次轮 repeated，第三轮 ok → 不应熔断，最后以正常 content 结束
    tc1 = {"id": "c1", "function": {"name": "echo_tool", "arguments": '{"text": "a"}'}}
    tc2 = {"id": "c2", "function": {"name": "echo_tool", "arguments": '{"text": "b"}'}}
    resp_tc1 = {"choices": [{"message": {"content": "", "tool_calls": [tc1]}}]}
    resp_tc2 = {"choices": [{"message": {"content": "", "tool_calls": [tc2]}}]}
    resp_done = {"choices": [{"message": {"content": "done content", "tool_calls": None}}]}

    # 序列：ok(tc1) → ok(tc2) → done → 结束，不应触发熔断
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(side_effect=[resp_tc1, resp_tc2, resp_done]))

    result = await engine.chat("hi", session_id="s-np-ok-685")
    assert result["content"] == "done content"
    tasks = engine.tracker.list_by_session("s-np-ok-685")
    assert tasks and tasks[-1].status.value == "completed"


@pytest.mark.asyncio
async def test_stream_no_progress_breaker_triggers(monkeypatch):
    """流式路径同样在连续 repeated 轮达到阈值时熔断（parity）。"""
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    eng.max_rounds = 10
    eng._no_progress_threshold = 3

    tc = {"id": "call_1", "function": {"name": "echo_tool", "arguments": '{"text": "x"}'}}
    msg_with_tc = {"content": "", "tool_calls": [tc]}

    call_count = {"n": 0}

    async def fake_stream(*a, **k):
        call_count["n"] += 1
        yield ("done", {"message": msg_with_tc})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)

    events = []
    async for e in eng.chat_stream("hi", session_id="s-stream-np-685"):
        events.append(e)

    assert any("task_error" in e for e in events), "stream should emit task_error on no_progress"
    assert any("done" in e for e in events)
    # 不应烧满 10 轮
    assert call_count["n"] < 10
    tasks = eng.tracker.list_by_session("s-stream-np-685")
    assert tasks and tasks[-1].status.value == "failed"


@pytest.mark.asyncio
async def test_stream_no_progress_not_trigger_on_mixed_success(monkeypatch):
    """流式：若轮内有成功进展则不熔断。"""
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    eng.max_rounds = 5
    eng._no_progress_threshold = 3

    # 两轮不同参数的工具调用均 ok，随后以空内容结束 → 不应熔断而是正常完成
    tc1 = {"id": "c1", "function": {"name": "echo_tool", "arguments": '{"text": "a"}'}}
    tc2 = {"id": "c2", "function": {"name": "echo_tool", "arguments": '{"text": "b"}'}}
    msg1 = {"content": "", "tool_calls": [tc1]}
    msg2 = {"content": "", "tool_calls": [tc2]}
    msg_done = {"content": "final answer", "tool_calls": None}

    seq = [msg1, msg2, msg_done]

    async def fake_stream_gen(*a, **k):
        msg = seq.pop(0)
        yield ("done", {"message": msg})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream_gen)

    events = []
    async for e in eng.chat_stream("hi", session_id="s-stream-mixed-685"):
        events.append(e)

    assert any("task_complete" in e for e in events)


# ─── subagent 假成功治理 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subagent_empty_content_reports_failure(monkeypatch):
    """subagent 空 summary（empty completion）不再 success=True。"""
    r = ToolRegistry()

    async def fake_chat(self, message, session_id=None, **kw):
        return {"content": "   ", "reasoning": "", "session_id": session_id}

    with patch.object(ChatEngine, "chat", fake_chat):
        disp = SubagentDispatcher(r, parent_session_id="parent-685-empty")
        res = await disp.run(task="do thing")
        assert res.success is False
        assert "empty" in (res.error or "").lower() or "empty" in res.summary.lower() or res.success is False


@pytest.mark.asyncio
async def test_subagent_nonempty_reports_success(monkeypatch):
    """subagent 非空 content 仍 success=True（parity）。"""
    r = ToolRegistry()

    async def fake_chat(self, message, session_id=None, **kw):
        return {"content": "完成了任务，ref:xxx", "reasoning": "", "session_id": session_id}

    with patch.object(ChatEngine, "chat", fake_chat):
        disp = SubagentDispatcher(r, parent_session_id="parent-685-ok")
        res = await disp.run(task="do thing")
        assert res.success is True
        assert "完成了" in res.summary


@pytest.mark.asyncio
async def test_subagent_exception_reports_failure(monkeypatch):
    """subagent chat() 抛异常（empty/max_rounds/no_progress 路径）→ success=False。"""
    r = ToolRegistry()

    async def fake_chat(self, message, session_id=None, **kw):
        raise RuntimeError("empty completion from provider")

    with patch.object(ChatEngine, "chat", fake_chat):
        disp = SubagentDispatcher(r, parent_session_id="parent-685-exc")
        res = await disp.run(task="do thing")
        assert res.success is False
        assert res.error is not None


# ─── 成功/失败判定 parity：两路径一致 ─────────────────────────────────

@pytest.mark.asyncio
async def test_parity_success_both_paths(engine, monkeypatch):
    """两路径对“有内容无工具”的成功判定一致（均 COMPLETED）。"""
    # 非流式：有 content 无 tool_calls → success
    resp_ok = {"choices": [{"message": {"content": "answer", "tool_calls": None}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=resp_ok))
    res = await engine.chat("hi", session_id="s-parity-ok-ns-685")
    assert res["content"] == "answer"
    assert engine.tracker.list_by_session("s-parity-ok-ns-685")[-1].status.value == "completed"

    # 流式：同理
    r = _make_registry_with_echo_tool()
    eng2 = ChatEngine(r)
    monkeypatch.setattr(eng2, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng2, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng2, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng2, "_generate_title", _fake_generate_title)

    msg_ok = {"content": "answer", "tool_calls": None}

    async def fake_stream2(*a, **k):
        yield ("done", {"message": msg_ok})

    monkeypatch.setattr(eng2, "_call_llm_stream", fake_stream2)
    events = []
    async for e in eng2.chat_stream("hi", session_id="s-parity-ok-st-685"):
        events.append(e)
    assert any("task_complete" in e for e in events)
    assert eng2.tracker.list_by_session("s-parity-ok-st-685")[-1].status.value == "completed"


@pytest.mark.asyncio
async def test_parity_failure_both_paths_empty(monkeypatch):
    """两路径对空补全的失败判定一致（均 FAILED/empty_result）。"""
    # 已由前两测试覆盖，此处做显式 parity 标签
    pass


@pytest.mark.asyncio
async def test_parity_max_rounds_both_paths(monkeypatch):
    """两路径对 max_rounds 耗尽均 FAILED。"""
    # 非流式已在 test_nostream_max_rounds_fails 覆盖；流式走工具循环耗尽同样 fail
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    eng.max_rounds = 2
    eng._no_progress_threshold = 10  # 避免熔断干扰 max_rounds 语义

    tc = {"id": "c1", "function": {"name": "echo_tool", "arguments": '{"text": "a1"}'}}
    tc2 = {"id": "c2", "function": {"name": "echo_tool", "arguments": '{"text": "a2"}'}}
    msg1 = {"content": "", "tool_calls": [tc]}
    msg2 = {"content": "", "tool_calls": [tc2]}

    # 流式：两轮不同参数工具调用耗尽 max_rounds → 应 fail
    seq = [msg1, msg2]

    async def fake_stream(*a, **k):
        msg = seq.pop(0)
        yield ("done", {"message": msg})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)
    events = []
    async for e in eng.chat_stream("hi", session_id="s-parity-max-st-685"):
        events.append(e)
    assert any("task_error" in e for e in events)
    tasks = eng.tracker.list_by_session("s-parity-max-st-685")
    assert tasks and tasks[-1].status.value == "failed"


# ─── 配置阈值可覆盖 ─────────────────────────────────────────────────────

def test_no_progress_threshold_configurable_via_env(monkeypatch):
    """LLM_NO_PROGRESS_THRESHOLD env 可覆盖阈值（仓内惯例：env 覆盖 settings）。"""
    monkeypatch.setenv("LLM_NO_PROGRESS_THRESHOLD", "5")
    # 重建 engine 应读到 env 覆盖
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    assert eng._no_progress_threshold == 5


def test_no_progress_threshold_min_one(monkeypatch):
    """阈值至少为 1，避免 0 导致首轮即熔断。"""
    monkeypatch.setenv("LLM_NO_PROGRESS_THRESHOLD", "0")
    r = _make_registry_with_echo_tool()
    eng = ChatEngine(r)
    assert eng._no_progress_threshold == 1
