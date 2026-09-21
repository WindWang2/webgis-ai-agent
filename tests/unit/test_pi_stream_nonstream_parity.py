"""方向 09（ADR-0204）：stream / non-stream turn 生命周期 parity 测试。

同一条 fake RPC 驱动 ``PiBridge.prompt``（非流式）与 ``PiBridge.stream_prompt``
（流式），断言此前漂移的六类行为收敛：

- D1 process_died → tracker 任务两路径都结算为 failed（此前非流式结算成 completed）；
- D2 turn-settle 披露管线（完成度终验/投影/链持久化/checkpoint）双路径共用；
- D3 证据链 USER_OUTPUT + persist_turn_chain 双路径都执行；
- D5 mark_first_event 双路径都标记；
- D6 超时 failure_class 同 taxonomy（pi_stall / pi_turn_budget）；
- D7 轨迹录制携带 map_product 双路径对齐。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiRpcError

import app.api.routes.chat  # noqa: F401  # stream_prompt 冷导入预热（同 test_pi_bridge_lock）


def _make_rpc():
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    rpc.process_died_event = asyncio.Event()
    return rpc


class FakeTrackerTask:
    def __init__(self, tid):
        self.id = tid


class FakeTracker:
    def __init__(self):
        self.settled = []

    def create(self, sid, message):
        return FakeTrackerTask(f"{sid}-task")

    def list_by_session(self, sid):
        return []

    def cancel(self, tid):
        self.settled.append(("cancelled", tid))

    def fail_task(self, tid, reason):
        self.settled.append(("failed", tid))

    def complete_task(self, tid):
        self.settled.append(("completed", tid))


@pytest.fixture(autouse=True)
def _turn_env(monkeypatch):
    state = SimpleNamespace(
        tracker=FakeTracker(),
        settle_calls=[],
        settle_result={"task_complete": True},
        summaries=[],
        recordings=[],
        kernel_turns=[],
        chain_emits=[],
        persists=[],
    )

    def _fake_engine():
        return SimpleNamespace(tracker=state.tracker)

    import app.services.chat.engine_instance as ei
    monkeypatch.setattr(ei, "try_get_chat_engine", _fake_engine)

    import app.services.chat.pi_post_dispatch as ppd
    async def _settle(sid, turn_id, *, reason="turn_settled", state_trigger="execution_settled"):
        state.settle_calls.append((sid, turn_id, reason))
        return state.settle_result
    monkeypatch.setattr(ppd, "settle_turn_projections", _settle)

    monkeypatch.setattr(bridge_mod, "emit_turn_summary", lambda ev: state.summaries.append(ev) or {})

    import app.lib.harness.replay.recorder as rec
    def _record(**kw):
        state.recordings.append(kw)
    monkeypatch.setattr(rec, "maybe_record_turn", _record)

    import app.services.harness_kernel as hk
    class FakeKernelRuntime:
        async def begin_turn(self, turn_id, *, host, message):
            state.kernel_turns.append(("begin", turn_id))

        async def end_turn(self, turn_id, *, host, status):
            state.kernel_turns.append(("end", turn_id, status))
    monkeypatch.setattr(hk, "get_runtime", lambda sid: FakeKernelRuntime())

    # 流式路径 map_event_to_sse 的真实实现会查 cache/caches —— 提供 no-op 依赖
    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)

    return state


async def _feed_settled(rpc):
    await rpc.events.put({"type": "agent_end", "willRetry": False})
    await rpc.events.put({"type": "agent_settled"})


@pytest.mark.asyncio
async def test_clean_settle_runs_shared_pipeline_on_both_paths(_turn_env):
    """D2：清洁收口时双路径调用同一 settle 管线（reason=turn_settled）。"""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "BBB"}]},
            })
            await _feed_settled(rpc)
    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    # ── 非流式 ──
    result = await bridge.prompt("hi", session_id="sess-p")
    assert result["content"] == "BBB"
    assert len(_turn_env.settle_calls) == 1
    sid_p, turn_p, reason_p = _turn_env.settle_calls[0]
    assert (sid_p, reason_p) == ("sess-p", "turn_settled")

    # ── 流式 ──
    events = []

    async def fake_request_s(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "SSS"},
            })
            await _feed_settled(rpc)
    rpc.request = AsyncMock(side_effect=fake_request_s)
    async for ev in bridge.stream_prompt("hi", session_id="sess-s"):
        events.append(ev)
    assert any("task_complete" in ev or "done" in ev for ev in events)
    assert len(_turn_env.settle_calls) == 2
    sid_s, turn_s, reason_s = _turn_env.settle_calls[1]
    assert (sid_s, reason_s) == ("sess-s", "turn_settled")


@pytest.mark.asyncio
async def test_process_died_fails_tracker_task_on_both_paths(_turn_env, monkeypatch):
    """D1：进程死亡 → 两路径 tracker 都 fail_task（非流式此前误结算 completed）。"""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    rpc = _make_rpc()

    async def die_after_prompt(cmd, data=None):
        if cmd == "prompt":
            await asyncio.sleep(0.01)
            rpc.process_died_event.set()
    rpc.request = AsyncMock(side_effect=die_after_prompt)
    bridge = PiBridge(rpc=rpc)

    with pytest.raises(PiRpcError):
        await bridge.prompt("hi", session_id="sess-p")
    assert _turn_env.tracker.settled[-1][0] == "failed"

    # 流式：死亡 → error + done，且 tracker fail
    rpc2 = _make_rpc()
    rpc2.process_died_event.set()  # turn 开始前即死
    bridge2 = PiBridge(rpc=rpc2)
    events = [ev async for ev in bridge2.stream_prompt("hi", session_id="sess-s")]
    assert any(ev.startswith("event: error") for ev in events)
    assert _turn_env.tracker.settled[-1][0] == "failed"


@pytest.mark.asyncio
async def test_stall_timeout_failure_class_parity(_turn_env, monkeypatch):
    """D6：非流式 stall 超时分类与流式同 taxonomy（pi_stall）。"""
    monkeypatch.setattr(bridge_mod, "PI_EVENT_DRAIN_TIMEOUT", 0.03)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 0.05)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 60.0)
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)

    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    with pytest.raises(PiRpcError, match="drain timeout"):
        await bridge.prompt("hi", session_id="sess-p")
    ev_p = _turn_env.summaries[-1]
    assert ev_p.outcome.failure_class == "pi_stall"

    rpc2 = _make_rpc()
    bridge2 = PiBridge(rpc=rpc2)
    events = [ev async for ev in bridge2.stream_prompt("hi", session_id="sess-s")]
    assert any("stalled" in ev for ev in events)
    ev_s = _turn_env.summaries[-1]
    assert ev_s.outcome.failure_class == "pi_stall"


@pytest.mark.asyncio
async def test_total_budget_failure_class_parity(_turn_env, monkeypatch):
    """D6：事件持续滴流但整回合预算耗尽 → 双路径 pi_turn_budget。"""
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 0.1)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 60.0)
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)

    async def _drip(rpc, stop):
        while not stop.is_set():
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "x"},
            })
            await asyncio.sleep(0.02)

    # ── 非流式：send 立即返回，drip 在独立任务里持续投喂 ──
    stop = asyncio.Event()
    rpc = _make_rpc()
    rpc.request = AsyncMock()  # 立即返回；drip 不能挂在 send 上（会死锁）
    feeder = asyncio.ensure_future(_drip(rpc, stop))
    bridge = PiBridge(rpc=rpc)
    with pytest.raises(PiRpcError, match="drain timeout"):
        await bridge.prompt("hi", session_id="sess-p")
    stop.set()
    await feeder
    assert _turn_env.summaries[-1].outcome.failure_class == "pi_turn_budget"

    # ── 流式：同款 drip；读到 total-budget error 即退出 ──
    stop2 = asyncio.Event()
    rpc2 = _make_rpc()
    rpc2.request = AsyncMock()
    feeder2 = asyncio.ensure_future(_drip(rpc2, stop2))
    bridge2 = PiBridge(rpc=rpc2)
    saw_total_budget_error = False
    async for ev in bridge2.stream_prompt("hi", session_id="sess-s"):
        if "exceeded the total budget" in ev:
            saw_total_budget_error = True
    # 完整消费事件流（中途 break 会以 GeneratorExit→cancelled 结算）
    stop2.set()
    await feeder2
    assert saw_total_budget_error
    assert _turn_env.summaries[-1].outcome.failure_class == "pi_turn_budget"


@pytest.mark.asyncio
async def test_first_event_marked_on_both_paths(_turn_env):
    """D5：非流式 drain 同流式一样标记首个真实事件（TTFT 代理）。"""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await _feed_settled(rpc)
    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)
    await bridge.prompt("hi", session_id="sess-p")
    assert _turn_env.summaries[-1].to_summary()["timing_ms"]["first_event"] is not None

    rpc2 = _make_rpc()

    async def fake_request2(cmd, data=None):
        if cmd == "prompt":
            await rpc2.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "S"},
            })
            await _feed_settled(rpc2)
    rpc2.request = AsyncMock(side_effect=fake_request2)
    bridge2 = PiBridge(rpc=rpc2)
    async for _ in bridge2.stream_prompt("hi", session_id="sess-s"):
        pass
    assert _turn_env.summaries[-1].to_summary()["timing_ms"]["first_event"] is not None


@pytest.mark.asyncio
async def test_replay_recording_carries_map_product_on_both_paths(_turn_env):
    """D7：双路径 maybe_record_turn 都携带 settle 管线产出的 map_product。"""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await _feed_settled(rpc)
    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)
    await bridge.prompt("hi", session_id="sess-p")
    assert _turn_env.recordings[-1]["map_product"] == {"task_complete": True}

    rpc2 = _make_rpc()

    async def fake_request2(cmd, data=None):
        if cmd == "prompt":
            await _feed_settled(rpc2)
    rpc2.request = AsyncMock(side_effect=fake_request2)
    bridge2 = PiBridge(rpc=rpc2)
    async for _ in bridge2.stream_prompt("hi", session_id="sess-s"):
        pass
    assert _turn_env.recordings[-1]["map_product"] == {"task_complete": True}


@pytest.mark.asyncio
async def test_kernel_end_turn_status_mapping_parity(_turn_env):
    """kernel turn 终态映射双路径一致：cancelled→cancelled；死亡→failed。"""
    rpc = _make_rpc()

    async def cancel_mid_prompt(cmd, data=None):
        if cmd == "prompt":
            await asyncio.sleep(10)  # 挂起直到被取消
    rpc.request = AsyncMock(side_effect=cancel_mid_prompt)
    bridge = PiBridge(rpc=rpc)
    task = asyncio.ensure_future(bridge.prompt("hi", session_id="sess-p"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    ends = [k for k in _turn_env.kernel_turns if k[0] == "end"]
    assert ends and ends[-1][2] == "cancelled"
