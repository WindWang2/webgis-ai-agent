"""ChatEngine Task Tracking 测试 - TDD"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat_engine import ChatEngine
from app.services.task_tracker import TaskStatus, TaskTracker, detect_geojson
from app.tools.registry import ToolRegistry, tool


def _fake_stream(response_msg):
    """Create a fake _call_llm_stream that yields a single 'done' event."""
    async def stream(*args, **kwargs):
        yield ("done", {"message": response_msg})
    return stream


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode")
    def geocode(query: str) -> dict:
        return {"lat": 39.9042, "lon": 116.4074, "name": "北京"}

    @tool(r, name="query_osm_poi", description="Query POI")
    def query_osm_poi(area: str, category: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4074, 39.9074]}}
            ]
        }

    return r


@pytest.fixture
def engine(registry, monkeypatch):
    """A ChatEngine whose session/plan/title side effects are stubbed out so
    chat_stream does not touch the DB / Redis / a real LLM planner (which
    would hang the test). Mirrors the fixture pattern in
    test_chat_engine_planning.py."""
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


def test_tracker_accessible(registry):
    """测试 tracker 在 ChatEngine 中可访问"""
    engine = ChatEngine(registry)
    assert hasattr(engine, "tracker")
    assert isinstance(engine.tracker, TaskTracker)


@pytest.mark.asyncio
async def test_stream_emits_task_start(engine):
    """测试流式对话发送 task_start 事件"""
    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(mock_msg)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("北京坐标", session_id="test-s1"):
                events.append(event)

        task_start_events = [e for e in events if "task_start" in e]
        assert len(task_start_events) >= 1
        data = json.loads(task_start_events[0].split("data: ")[1])
        assert "task_id" in data
        assert data["task_id"].startswith("task-")


@pytest.mark.asyncio
async def test_stream_emits_task_complete(engine):
    """测试流式对话发送 task_complete 事件"""
    mock_msg = {"content": "北京坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(mock_msg)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("北京坐标", session_id="test-s2"):
                events.append(event)

        task_complete_events = [e for e in events if "task_complete" in e]
        assert len(task_complete_events) >= 1


@pytest.mark.asyncio
async def test_stream_emits_step_events_on_tool_call(engine):
    """测试工具调用时发送 step_start 和 step_result 事件"""
    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}
        ]
    }
    msg2 = {"content": "北京的坐标是 39.9042, 116.4074", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("查询北京坐标", session_id="test-s3"):
                events.append(event)

        step_start_events = [e for e in events if "step_start" in e]
        assert len(step_start_events) >= 1, "should emit step_start event"
        data = json.loads(step_start_events[0].split("data: ")[1])
        assert "task_id" in data
        assert "step_id" in data
        assert data["tool"] == "geocode"

        step_result_events = [e for e in events if "step_result" in e]
        assert len(step_result_events) >= 1, "should emit step_result event"

        tool_call_events = [e for e in events if "tool_call" in e]
        assert len(tool_call_events) >= 1, "should still emit tool_call event"

        tool_result_events = [e for e in events if "tool_result" in e]
        assert len(tool_result_events) >= 1, "should still emit tool_result event"


@pytest.mark.asyncio
async def test_stream_step_error_on_tool_failure(engine):
    """测试工具执行失败时发送 step_error 事件"""
    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_err", "function": {"name": "geocode", "arguments": '{"query": "测试"}'}}
        ]
    }

    async def mock_dispatch(name, args):
        raise Exception("Tool execution failed")

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg1)()):
        with patch.object(engine.registry, "dispatch", new_callable=AsyncMock, side_effect=mock_dispatch):
            with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
                events = []
                async for event in engine.chat_stream("测试", session_id="test-s4"):
                    events.append(event)

                step_error_events = [e for e in events if "step_error" in e]
                assert len(step_error_events) >= 1, "should emit step_error event"
                data = json.loads(step_error_events[0].split("data: ")[1])
                assert "task_id" in data
                assert "step_id" in data
                assert "error" in data


@pytest.mark.asyncio
async def test_task_cancellation_stops_loop(engine):
    """测试任务取消后停止循环并发送 task_cancelled 事件"""
    msg_with_tool = {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}
        ]
    }

    with patch.object(engine, "_call_llm_stream", return_value=_fake_stream(msg_with_tool)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("测试", session_id="test-s5"):
                events.append(event)
                if "task_start" in event:
                    task_id = json.loads(event.split("data: ")[1]).get("task_id")
                    if task_id:
                        engine.tracker.cancel(task_id)

        cancelled_events = [e for e in events if "task_cancelled" in e]
        assert len(cancelled_events) >= 1, "should emit task_cancelled event"


def test_detect_geojson():
    """测试 detect_geojson 函数"""
    result1 = {"type": "FeatureCollection", "features": []}
    assert detect_geojson(result1) is True

    result2 = {"data": {"type": "FeatureCollection", "features": []}}
    assert detect_geojson(result2) is True

    result3 = {"lat": 39.9042, "lon": 116.4074}
    assert detect_geojson(result3) is False

    assert detect_geojson("string") is False
    assert detect_geojson(None) is False


# ─── Phase 8: chat_stream 并行工具分发 ───────────────────────────────


@pytest.mark.asyncio
async def test_stream_parallel_dispatch_runs_tools_concurrently(engine, registry, monkeypatch):
    """多个工具调用必须并发执行：总耗时 < 串行之和（性能不变量）。

    LLM 一轮返回 3 个互不依赖的工具调用；每个工具 sleep 0.2s。
    串行需要 ~0.6s；并行需要 ~0.2s。阈值 0.45s（< 2× 串行）即证明并发。
    """
    import asyncio
    import time as _t

    @tool(registry, name="slow_echo", description="sleep 后返回")
    async def slow_echo(tag: str, delay: float = 0.2) -> dict:
        await asyncio.sleep(delay)
        return {"success": True, "data": {"tag": tag}}

    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_a", "function": {"name": "slow_echo", "arguments": '{"tag": "a", "delay": 0.2}'}},
            {"id": "call_b", "function": {"name": "slow_echo", "arguments": '{"tag": "b", "delay": 0.2}'}},
            {"id": "call_c", "function": {"name": "slow_echo", "arguments": '{"tag": "c", "delay": 0.2}'}},
        ],
    }
    msg2 = {"content": "done", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            t0 = _t.perf_counter()
            events = []
            async for event in engine.chat_stream("并行测试", session_id="test-parallel"):
                events.append(event)
            elapsed = _t.perf_counter() - t0

    assert any("step_result" in e for e in events), "should emit step_result"
    assert elapsed < 0.45, f"未并发执行：3 个 0.2s 工具耗时 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_stream_parallel_keeps_per_tool_event_order(engine, registry):
    """每个工具的 step_start 必须先于其 step_result 出现（前端顺序不变量）。"""
    import asyncio

    @tool(registry, name="slow_echo2", description="sleep 后返回")
    async def slow_echo2(tag: str, delay: float = 0.1) -> dict:
        await asyncio.sleep(delay)
        return {"success": True, "data": {"tag": tag}}

    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_x", "function": {"name": "slow_echo2", "arguments": '{"tag": "x", "delay": 0.1}'}},
            {"id": "call_y", "function": {"name": "slow_echo2", "arguments": '{"tag": "y", "delay": 0.1}'}},
        ],
    }
    msg2 = {"content": "done", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("顺序测试", session_id="test-order"):
                events.append(event)

    # 解析所有 step_start / step_result 的 step_id
    starts: dict[str, int] = {}   # step_id -> index
    results: dict[str, int] = {}  # step_id -> index
    for i, e in enumerate(events):
        if "step_start" in e:
            data = json.loads(e.split("data: ", 1)[1])
            starts[data["step_id"]] = i
        elif "step_result" in e:
            data = json.loads(e.split("data: ", 1)[1])
            results[data["step_id"]] = i

    assert len(starts) == 2, "should emit 2 step_start"
    assert len(results) == 2, "should emit 2 step_result"
    for sid in starts:
        assert starts[sid] < results[sid], (
            f"step {sid}: step_start 必须早于 step_result"
        )


@pytest.mark.asyncio
async def test_stream_parallel_messages_in_original_order(engine, registry):
    """LLM 上下文消息必须按原始 tool_call 顺序追加（tool_call_id 对齐）。"""
    import asyncio

    @tool(registry, name="slow_echo3", description="sleep 后返回")
    async def slow_echo3(tag: str, delay: float = 0.05) -> dict:
        await asyncio.sleep(delay)
        return {"success": True, "data": {"tag": tag}}

    # 故意把 call_y 的延迟设得比 call_x 短：完成顺序 y→x，
    # 但 messages 追加顺序必须仍是 x→y（原始声明顺序）。
    msg1 = {
        "content": None,
        "tool_calls": [
            {"id": "call_x", "function": {"name": "slow_echo3", "arguments": '{"tag": "x", "delay": 0.1}'}},
            {"id": "call_y", "function": {"name": "slow_echo3", "arguments": '{"tag": "y", "delay": 0.01}'}},
        ],
    }
    msg2 = {"content": "done", "tool_calls": None}

    captured: list[dict] = []

    async def fake_save_msg_async(session_id, role, content, tool_calls=None,
                                  tool_result=None, tool_call_id=None, reasoning_content=None):
        if role == "tool":
            captured.append({"tool_call_id": tool_call_id, "content": tool_result})

    with patch.object(engine, "_call_llm_stream", side_effect=[_fake_stream(msg1)(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock, side_effect=fake_save_msg_async):
            events = []
            async for event in engine.chat_stream("对齐测试", session_id="test-align"):
                events.append(event)

    ids = [c["tool_call_id"] for c in captured]
    assert ids == ["call_x", "call_y"], f"messages 必须按原始声明顺序追加，got {ids}"


# ─── Phase 8: chat_stream token SSE 批处理 ────────────────────────────


def _fake_token_stream(tokens, final_msg):
    """A fake _call_llm_stream yielding N token events then one done."""
    async def stream(*args, **kwargs):
        for t in tokens:
            yield ("token", {"content": t})
        yield ("done", {"message": final_msg})
    return stream


@pytest.mark.asyncio
async def test_stream_token_batching_coalesces_events(engine):
    """高频 token 事件必须被批处理合并为更少更大的 write。

    40 个 token 超过 max_events=32 → 至少一个 yield 携带多条 token 事件；
    全部 token 内容必须完整到达（合并不影响完整性）；done 仍逐条到达。
    """
    tokens = [f"tok{i}" for i in range(40)]
    msg2 = {"content": "".join(tokens), "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", return_value=_fake_token_stream(tokens, msg2)()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("批处理测试", session_id="test-batch"):
                events.append(event)

    multi = [e for e in events if e.count("event: token") > 1]
    assert len(multi) >= 1, "token 事件应被合并为多事件批次"
    joined = "".join(events)
    for i in range(40):
        assert f"tok{i}" in joined, f"token tok{i} 应完整到达"
    assert any("event: done" in e for e in events)


@pytest.mark.asyncio
async def test_stream_token_batching_preserves_tokens_before_tool_call(engine, registry):
    """token 批处理不得影响工具调用路径：token 后跟 tool_call 时，
    step_start/tool_call 事件仍逐条出现且顺序正确。"""
    @tool(registry, name="quick_tool", description="快速工具")
    async def quick_tool(x: int) -> dict:
        return {"success": True, "data": {"x": x}}

    async def stream(*args, **kwargs):
        for i in range(40):
            yield ("token", {"content": f"t{i}"})
        yield ("done", {"message": {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "quick_tool", "arguments": '{"x": 1}'}}],
        }})

    msg2 = {"content": "done", "tool_calls": None}

    with patch.object(engine, "_call_llm_stream", side_effect=[stream(), _fake_stream(msg2)()]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("批处理+工具测试", session_id="test-batch-tool"):
                events.append(event)

    step_start_idx = next(i for i, e in enumerate(events) if "step_start" in e)
    tool_call_idx = next(i for i, e in enumerate(events) if "tool_call" in e)
    step_result_idx = next(i for i, e in enumerate(events) if "step_result" in e)
    # 顺序不变量：step_start → tool_call → step_result
    assert step_start_idx < tool_call_idx < step_result_idx
    # 所有 token 完整到达
    joined = "".join(events)
    assert all(f"t{i}" in joined for i in range(40))


# ─── B-P2-17: 断连 / 取消时 tracker 任务必须终止 ────────────────────────


@pytest.mark.asyncio
async def test_stream_disconnect_fails_tracker_task(engine):
    """B-P2-17: 流式对话中途断连（生成器被 aclose）后，tracker 任务不得停留在
    running。

    fix 前 chat_stream 没有 GeneratorExit/CancelledError 处理：客户端断连
    （Starlette 对 StreamingResponse 调用 aclose）会让任务一直 running，
    直到 MAX_TOTAL_TASKS 上限才被逐出。fix 后任务会终止；F8 之后终态从
    failed 改为 cancelled —— 断连/取消必须与真正的失败区分开。
    """
    with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
        gen = engine.chat_stream("断连测试", session_id="test-disc")
        first = await gen.__anext__()
        assert "task_start" in first
        task_id = json.loads(first.split("data: ", 1)[1])["task_id"]

        # 模拟客户端断连：turn 尚未结束就关闭生成器（GeneratorExit）
        await gen.aclose()

        task = engine.tracker.get(task_id)
        assert task is not None, "任务应仍存在于 tracker"
        assert task.status == TaskStatus.cancelled, (
            f"断连后任务应为 cancelled（F8），实际 {task.status}"
        )


@pytest.mark.asyncio
async def test_stream_cancelled_mid_turn_fails_tracker_task(engine):
    """B-P2-17 (CancelledError 分支)：流式对话中途被取消时任务也必须终止。

    StreamingResponse 在客户端断连时对生成器投递的正是 CancelledError（不是
    GeneratorExit），因此两个分支都要终止任务。fix 前任务停留在 running（泄漏）。
    F8 之后终态从 failed 改为 cancelled —— 取消必须与失败可区分。
    """
    token_consumed = asyncio.Event()
    parked = asyncio.Event()

    async def slow_stream(*args, **kwargs):
        yield ("token", {"content": "partial"})
        token_consumed.set()
        await parked.wait()  # 保持 turn 打开，等待取消

    with patch.object(engine, "_call_llm_stream", return_value=slow_stream()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            gen = engine.chat_stream("取消测试", session_id="test-cancel")
            first = await gen.__anext__()
            assert "task_start" in first
            task_id = json.loads(first.split("data: ", 1)[1])["task_id"]

            # 消费 token 后生成器停在 _call_llm_stream 内部
            pull = asyncio.ensure_future(gen.__anext__())
            await asyncio.wait_for(token_consumed.wait(), timeout=2.0)

            # 模拟断连：取消正在驱动生成器的读取任务
            pull.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pull

            task = engine.tracker.get(task_id)
            assert task is not None, "任务应仍存在于 tracker"
            assert task.status == TaskStatus.cancelled, (
                f"取消后任务应为 cancelled（F8），实际 {task.status}"
            )


@pytest.mark.asyncio
async def test_chat_cooperative_cancel_settles_cancelled_not_success(engine, monkeypatch):
    """P1-2 integration: legacy non-stream chat() cooperative cancel must settle
    the turn outcome CANCELLED, NOT SUCCEEDED. This is the exact "error counted
    as success" failure the observability PR prevents — the round loop returns a
    normal "任务已取消" dict on cancel, so without _settle_cancel the outer turn
    would record SUCCEEDED."""
    import app.services.chat.execution_engine as ee
    captured = {}

    def fake_emit(ev):
        captured["outcome"] = ev.outcome.outcome.value if ev.outcome.outcome else None

    monkeypatch.setattr(ee, "emit_turn_summary", fake_emit)
    monkeypatch.setattr(engine.tracker, "is_cancelled", lambda task_id: True)
    monkeypatch.setattr(engine, "_save_msg_async", AsyncMock())
    result = await engine.chat("cancel me", session_id="s-coop-cancel")
    assert "取消" in result["content"]
    assert captured.get("outcome") == "cancelled", (
        f"cooperative cancel must settle CANCELLED, got {captured.get('outcome')!r}"
    )


@pytest.mark.asyncio
async def test_chat_stream_cooperative_cancel_settles_cancelled(engine, monkeypatch):
    """P2-1 integration: legacy stream chat_stream cooperative cancel (round-top
    is_cancelled) must settle CANCELLED, not leave outcome None/SUCCEEDED."""
    import app.services.chat.execution_engine as ee
    captured = {}

    def fake_emit(ev):
        captured["outcome"] = ev.outcome.outcome.value if ev.outcome.outcome else None

    monkeypatch.setattr(ee, "emit_turn_summary", fake_emit)
    monkeypatch.setattr(engine.tracker, "is_cancelled", lambda task_id: True)
    monkeypatch.setattr(engine, "_save_msg_async", AsyncMock())
    events = []
    async for ev in engine.chat_stream("cancel me", session_id="s-coop-cancel-stream"):
        events.append(ev)
    assert any("task_cancelled" in e for e in events)
    assert captured.get("outcome") == "cancelled", (
        f"stream cooperative cancel must settle CANCELLED, got {captured.get('outcome')!r}"
    )

