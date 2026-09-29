"""chat_stream 事件序列不变量的引擎级集成测试（H03，sse_contracts INV-1..5）。

与 tests/unit/test_sse_envelope_property.py（纯 serializer property）互补：
这里用真实 ChatExecutionEngine（stub LLM/planner/DB）驱动完整阶段链，
断言 wire 上的**全序不变量**：

- INV-2 恰好一个终态且为最后一帧；
- INV-3 plan_finalized 先于终态；
- INV-4 task_start 先于一切业务事件；
- INV-1 scope 内 id 严格单调；
- 工具波内 step_start → tool_call → step_result/tool_result 顺序；
- error 事件携带统一 error_class（error_taxonomy 接线）；
- 协作取消：task_cancelled 终态 + tracker 收尾 + 会话锁释放（无泄漏）。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat_engine import ChatEngine
from app.services.distributed_lock import session_lock
from app.services.task_tracker import TaskStatus
from app.tools.registry import ToolRegistry, tool
from app.utils.sse import (
    TERMINAL_EVENTS,
    sse_event_id,
    sse_event_id_scope,
    sse_event_type,
)


def _frames(events: list[str]) -> list[dict]:
    """把（可能合批的）wire chunk 流拆成事件帧并解析 type/id/data。"""
    out = []
    for chunk in events:
        for block in chunk.split("\n\n"):
            if not block:
                continue
            etype = sse_event_type(block)
            data = {}
            for line in block.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[len("data: "):])
            out.append({"type": etype, "id": sse_event_id(block), "data": data})
    return out


def _fake_llm_stream(rounds: list[dict]):
    """按轮次回放的假 LLM 流（每轮消耗一个响应）。"""
    iterator = iter(rounds)

    async def stream(*args, **kwargs):
        try:
            msg = next(iterator)
        except StopIteration:  # pragma: no cover - 防御
            msg = {"content": "done", "tool_calls": None}
        yield ("done", {"message": msg, "usage": {"total_tokens": 1}})

    return stream


def _tool_call_msg(name: str, arguments: str) -> dict:
    return {
        "content": "",
        "tool_calls": [
            {"id": f"call-{name}", "type": "function",
             "function": {"name": name, "arguments": arguments}}
        ],
    }


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode")
    def geocode(query: str) -> dict:
        return {"lat": 39.9042, "lon": 116.4074, "name": "北京"}

    @tool(r, name="slow_tool", description="Slow")
    def slow_tool(query: str) -> dict:
        import time

        time.sleep(0.05)
        return {"ok": True}

    return r


@pytest.fixture
def engine(registry, monkeypatch):
    """真实 ChatEngine，stub DB/planner/title 副作用（同
    test_chat_engine_tracking 的 fixture 模式）。"""
    eng = ChatEngine(registry)

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    async def fake_generate_title(*a, **kw):
        return None

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", fake_generate_title)
    return eng


def _assert_common_invariants(frames: list[dict]):
    """INV-2/4 + id 单调：所有场景共享的序列不变量。

    终态契约（与 app.utils.sse.TERMINAL_EVENTS 一致）：一个 turn 恰好一个
    **结果**事件（task_complete / task_error / task_cancelled，互斥）；
    ``done`` 是流关闭帧（结果事件之后，至多一次；协作取消的 wave 分支
    以 task_cancelled 直接收尾，无 done）。
    """
    types = [f["type"] for f in frames]
    business = [t for t in types if t]
    # INV-4: task_start 先于一切业务事件（首个非空类型）
    assert business[0] == "task_start", f"首帧必须是 task_start，实际 {business[:3]}"
    # INV-2: 恰好一个结果事件；done 至多一次且在结果事件之后
    outcomes = [t for t in business if t in ("task_complete", "task_error", "task_cancelled")]
    assert len(outcomes) == 1, f"恰好一个结果事件，实际 {outcomes}"
    assert types.count("done") <= 1, "done 收尾帧至多一次"
    assert business[-1] in TERMINAL_EVENTS, "最后一帧必须是终态"
    # INV-1: scope 内 id 严格 +1（keep_alive 等事件都占 id，注释帧不占）
    ids = [f["id"] for f in frames if f["id"] is not None]
    assert ids == list(range(1, len(ids) + 1)), f"id 非严格单调: {ids[:10]}"


@pytest.mark.asyncio
async def test_happy_path_ordering_and_single_terminal(engine):
    """纯文本轮：task_start → token* → content(streaming_done) →
    task_complete → done。"""
    async def stream(*args, **kwargs):
        yield ("token", {"content": "北京", "is_reasoning": False})
        yield ("token", {"content": "坐标是 39.9", "is_reasoning": False})
        yield ("done", {"message": {"content": "北京坐标是 39.9", "tool_calls": None},
                        "usage": {"total_tokens": 1}})

    engine._call_llm_stream = stream
    raw: list[str] = []
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        with sse_event_id_scope():
            async for event in engine.chat_stream("北京坐标", session_id="ord-1"):
                raw.append(event)
    frames = _frames(raw)
    _assert_common_invariants(frames)
    types = [f["type"] for f in frames]
    assert types == [
        "task_start", "token", "token", "content", "task_complete", "done",
    ]
    # token 携带 turn_id（emitter additive 关联键）
    assert frames[1]["data"]["turn_id"]
    # session_id 盖章
    assert frames[1]["data"]["session_id"] == "ord-1"


@pytest.mark.asyncio
async def test_tool_wave_ordering(engine):
    """工具轮：step_start 先于 tool_call，随后 step_result/tool_result，
    终态最后。"""
    engine._call_llm_stream = _fake_llm_stream([
        _tool_call_msg("geocode", '{"query": "北京"}'),
        {"content": "北京在 39.9N", "tool_calls": None},
    ])
    raw: list[str] = []
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        with sse_event_id_scope():
            async for event in engine.chat_stream("北京在哪", session_id="ord-2"):
                raw.append(event)
    frames = _frames(raw)
    _assert_common_invariants(frames)
    types = [f["type"] for f in frames]
    # 第一轮工具波 + 第二轮终答
    assert types == [
        "task_start",
        "step_start", "tool_call", "step_result", "tool_result",
        "content", "task_complete", "done",
    ]
    # step_result 主成功分支 geojson_ref 恒在场（键稳定性，可为 null）
    step_result = next(f for f in frames if f["type"] == "step_result")
    assert "geojson_ref" in step_result["data"]


@pytest.mark.asyncio
async def test_plan_finalized_precedes_terminal(engine, monkeypatch):
    """INV-3：本轮产生计划时 plan_ready/plan_finalized 先于终态。"""
    plan = type("P", (), {})()
    plan.intent = "查坐标"
    plan.domains = ["geocode"]
    plan.steps = [
        type("S", (), {"n": 1, "goal": "geocode 北京", "tool_family": "core", "done": False})()
    ]

    async def fake_plan(*a, **kw):
        return plan

    # plan_finalized 读 canonical 计划存储（_maybe_plan 在真实链路里注册）
    from app.services.chat import planner as planner_mod

    monkeypatch.setattr(planner_mod, "get_plan", lambda sid: plan)
    engine._maybe_plan = fake_plan
    engine._call_llm_stream = _fake_llm_stream([
        {"content": "完成", "tool_calls": None},
    ])
    raw: list[str] = []
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        with sse_event_id_scope():
            async for event in engine.chat_stream("计划轮", session_id="ord-3"):
                raw.append(event)
    frames = _frames(raw)
    _assert_common_invariants(frames)
    types = [f["type"] for f in frames]
    assert "plan_ready" in types and "plan_finalized" in types
    assert types.index("plan_finalized") < len(types) - 1  # 先于终态
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_empty_completion_carries_error_class(engine):
    """空补全 → task_error 且 error_class=empty_result（D3 接线）。"""
    engine._call_llm_stream = _fake_llm_stream([
        {"content": "   ", "tool_calls": None},
    ])
    raw: list[str] = []
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        async for event in engine.chat_stream("空轮", session_id="ord-4"):
            raw.append(event)
    frames = _frames(raw)
    _assert_common_invariants(frames)
    err = next(f for f in frames if f["type"] == "task_error")
    assert err["data"]["error_class"] == "empty_result"
    assert frames[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_max_rounds_carries_error_class(registry, monkeypatch):
    """max_rounds 耗尽 → task_error 且 error_class=max_rounds。"""
    eng = ChatEngine(registry)
    eng.max_rounds = 1

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    eng._call_llm_stream = _fake_llm_stream([
        _tool_call_msg("geocode", '{"query": "x"}'),
    ])
    raw: list[str] = []
    with patch.object(eng, "_save_msg_async", new_callable=AsyncMock):
        async for event in eng.chat_stream("循环", session_id="ord-5"):
            raw.append(event)
    frames = _frames(raw)
    _assert_common_invariants(frames)
    err = next(f for f in frames if f["type"] == "task_error")
    assert err["data"]["error_class"] == "max_rounds"


@pytest.mark.asyncio
async def test_cooperative_cancel_terminates_turn_and_releases_lock(engine, registry):
    """协作取消：cancel token 点燃 → task_cancelled 唯一终态，tracker 收尾，
    会话锁释放（取消后无会话/锁泄漏）。"""
    engine._call_llm_stream = _fake_llm_stream([
        _tool_call_msg("slow_tool", '{"query": "x"}'),
        {"content": "不该到达", "tool_calls": None},
    ])
    raw: list[str] = []
    sid = "ord-cancel"
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        gen = engine.chat_stream("慢工具", session_id=sid)
        first = await gen.__anext__()
        task_id = json.loads(first.split("data: ", 1)[1])["task_id"]
        # 在工具波等待期间点燃取消 token

        async def cancel_during_wave():
            await asyncio.sleep(0.01)
            engine.tracker.cancel(task_id)

        canceller = asyncio.create_task(cancel_during_wave())
        try:
            async for event in gen:
                raw.append(event)
        finally:
            canceller.cancel()

    frames = _frames(raw)
    types = [f["type"] for f in frames]
    assert types.count("task_cancelled") == 1
    assert types[-1] == "task_cancelled"
    assert "step_cancelled" in types
    # tracker 收尾（F7）：任务不再 running
    task_info = engine.tracker.get(task_id)
    assert task_info is None or task_info.status != TaskStatus.running
    # 锁已释放：同 session 立即重新可锁（RUN-03 全 turn 持锁的释放面）
    lock = session_lock(sid)
    await asyncio.wait_for(lock.acquire(), timeout=1.0)
    lock.release()


@pytest.mark.asyncio
async def test_events_share_single_envelope_fields(engine):
    """所有结构事件共享统一信封字段：session_id 恒在场（INV-5 关联面）。"""
    engine._call_llm_stream = _fake_llm_stream([
        _tool_call_msg("geocode", '{"query": "北京"}'),
        {"content": "完成", "tool_calls": None},
    ])
    raw: list[str] = []
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        async for event in engine.chat_stream("信封", session_id="ord-6"):
            raw.append(event)
    for f in _frames(raw):
        if f["type"] == "keep_alive":
            continue  # 心跳保持极简 ping 形状
        assert f["data"].get("session_id") == "ord-6", f"{f['type']} 缺 session_id"
