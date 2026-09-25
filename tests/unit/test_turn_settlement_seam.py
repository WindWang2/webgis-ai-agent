"""F03（ADR-0204-f03 D1/D3）：单结算 seam 的终点 × 终态语义矩阵。

同一 fake RPC 驱动 ``PiBridge.prompt`` / ``PiBridge.stream_prompt``，断言
每个 turn 终点（clean / 用户取消 / system 中止 / policy 中止 / stall /
未分类异常）产出唯一的诚实终态：

- kernel end_turn 的 TurnStatus；
- tracker 结算动作（cancel / fail / complete）；
- settle 管线的 TurnSettleOutcome.settle_class（clean vs reduced）；
- cleanup abort（finally 的 abort-on-disconnect）绝不抢占失败族终态。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiRpcError

import app.api.routes.chat  # noqa: F401  # stream_prompt 冷导入预热


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
        self.settled.append(("failed", tid, reason))

    def complete_task(self, tid):
        self.settled.append(("completed", tid))


@pytest.fixture(autouse=True)
def _turn_env(monkeypatch):
    state = SimpleNamespace(
        tracker=FakeTracker(),
        settle_calls=[],
        kernel_ends=[],
        summaries=[],
        refusal_resolver=None,
    )

    def _fake_engine():
        return SimpleNamespace(tracker=state.tracker)

    import app.services.chat.engine_instance as ei
    monkeypatch.setattr(ei, "try_get_chat_engine", _fake_engine)

    import app.services.chat.pi_post_dispatch as ppd

    async def _settle(sid, turn_id, *, outcome=None, reason="turn_settled",
                      state_trigger="execution_settled"):
        state.settle_calls.append((sid, turn_id, getattr(outcome, "settle_class", "clean")))
        return {"task_complete": True}

    monkeypatch.setattr(ppd, "settle_turn_projections", _settle)

    async def _refusal(sid, turn_id):
        if state.refusal_resolver is not None:
            return await state.refusal_resolver(sid, turn_id)
        return None

    monkeypatch.setattr(ppd, "resolve_turn_refusal", _refusal)

    monkeypatch.setattr(
        bridge_mod, "emit_turn_summary", lambda ev: state.summaries.append(ev) or {}
    )

    import app.services.harness_kernel as hk

    class FakeKernelRuntime:
        async def begin_turn(self, turn_id, *, host, message):
            pass

        async def end_turn(self, turn_id, *, host, status):
            state.kernel_ends.append((turn_id, status))

    monkeypatch.setattr(hk, "get_runtime", lambda sid: FakeKernelRuntime())

    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
    return state


async def _feed_settled(rpc):
    await rpc.events.put({"type": "agent_end", "willRetry": False})
    await rpc.events.put({"type": "agent_settled"})


def _kernel_end(state, sid):
    """最近一次 kernel end_turn 终态（bridge 使用自生成 turn id，
    每个用例一个新 bridge + 唯一会话 → 取末条即本会话 turn）。"""
    ends = state.kernel_ends
    return ends[-1][1] if ends else None


def _settle_class(state, sid):
    classes = [c for s, _t, c in state.settle_calls if s == sid]
    return classes[-1] if classes else None


def _tracker_action(state, sid):
    for entry in reversed(state.tracker.settled):
        if entry[1] == f"{sid}-task":
            return entry[0]
    return None


# ── abort 来源 → 终态（vendor 以 clean agent_settled 应答 abort）──────────


@pytest.mark.parametrize(("source", "expected", "tracker_expected"), [
    ("user", "cancelled", "cancelled"),
    ("system", "aborted", "failed"),
    ("policy", "aborted", "failed"),
])
@pytest.mark.asyncio
async def test_abort_source_sets_honest_terminal(_turn_env, source, expected, tracker_expected):
    """abort 后 vendor 正常 agent_settled：kernel 记 cancelled/aborted，
    不再误结算 completed（P1 修复的回归面）。走真实 ``bridge.abort`` 路径
    （来源台账由 abort 以 snapshot turn id 记录）。"""
    sid = f"sess-abort-{source}"
    rpc = _make_rpc()
    release = asyncio.Event()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await release.wait()  # 挂起直到测试放行（模拟 abort 后 vendor 收尾）
            await _feed_settled(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    task = asyncio.ensure_future(bridge.prompt("hi", session_id=sid))
    await asyncio.sleep(0.05)  # active-turn 注册完成
    await bridge.abort(session_id=sid, source=source)
    release.set()
    await task
    assert _kernel_end(_turn_env, sid) == expected
    assert _settle_class(_turn_env, sid) == expected
    assert _tracker_action(_turn_env, sid) == tracker_expected


@pytest.mark.asyncio
async def test_cleanup_abort_does_not_override_failure_family(_turn_env, monkeypatch):
    """finally 的 abort-on-disconnect（cleanup）不得把 stall turn 记成
    cancelled/aborted —— 终态成因归属：超时归超时。"""
    monkeypatch.setattr(bridge_mod, "PI_EVENT_DRAIN_TIMEOUT", 0.03)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 0.05)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 60.0)
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)

    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    with pytest.raises(PiRpcError, match="drain timeout"):
        await bridge.prompt("hi", session_id="sess-stall")
    assert _kernel_end(_turn_env, "sess-stall") == "failed"
    # 诊断面同帧：pi_stall（first-wins，cleanup abort 未抢结算）
    assert _turn_env.summaries[-1].outcome.failure_class == "pi_stall"


# ── 未分类异常 → failed（P1 修复：错误不伪装成 completed）─────────────────


@pytest.mark.asyncio
async def test_unclassified_exception_fails_nonstream(_turn_env, monkeypatch):
    import app.services.chat.pi_turn_context as ptc

    async def boom(*a, **kw):
        raise RuntimeError("context assembly exploded")

    monkeypatch.setattr(ptc, "bind_turn_prompt", boom)
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    # 非流式路径将异常上抛（既有契约），但结算已完成：kernel/tracker 诚实 failed
    with pytest.raises(RuntimeError, match="context assembly exploded"):
        await bridge.prompt("hi", session_id="sess-err")
    assert _kernel_end(_turn_env, "sess-err") == "failed"
    assert _tracker_action(_turn_env, "sess-err") == "failed"
    # reduced settle：错误分支也收口投影（outcome=failed）
    assert _settle_class(_turn_env, "sess-err") == "failed"


@pytest.mark.asyncio
async def test_unclassified_exception_fails_stream(_turn_env, monkeypatch):
    """stream 路径此前没有 generic except —— 崩溃 turn 被结算成 completed。"""

    def boom(*a, **kw):
        raise ValueError("mapper exploded")

    monkeypatch.setattr(bridge_mod, "map_event_to_sse", boom)
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "S"},
            })
            await _feed_settled(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    with pytest.raises(ValueError, match="mapper exploded"):
        async for _ in bridge.stream_prompt("hi", session_id="sess-serr"):
            pass
    assert _kernel_end(_turn_env, "sess-serr") == "failed"
    assert _settle_class(_turn_env, "sess-serr") == "failed"


# ── clean settle + 拒答候选 → refused 降级 ────────────────────────────────


@pytest.mark.asyncio
async def test_clean_settle_downgrades_to_refused(_turn_env):
    """零执行活动 + 未解决澄清：clean settle 降级 refused（不是 completed）。"""

    async def _refusal(sid, turn_id):
        return {"reason_code": "clarification_required", "question": "哪个区域？"}

    _turn_env.refusal_resolver = _refusal
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await _feed_settled(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    await bridge.prompt("hi", session_id="sess-ref")
    assert _kernel_end(_turn_env, "sess-ref") == "refused"
    # 拒答是 clean 结算（无所失物）：投影管线仍走 clean 语义（终验幂等门
    # 会如实评估），终态本身记录 refused。
    assert _settle_class(_turn_env, "sess-ref") == "clean"
    # 拒答不是失败也不是取消：tracker 正常 complete（无需重试/无需用户介入）
    assert _tracker_action(_turn_env, "sess-ref") == "completed"


@pytest.mark.asyncio
async def test_client_cancel_beats_abort_source(_turn_env):
    """显式 CancelledError（客户端断开）优先于 abort 来源：保持 cancelled。"""
    rpc = _make_rpc()

    async def hang(cmd, data=None):
        if cmd == "prompt":
            await asyncio.sleep(10)

    rpc.request = AsyncMock(side_effect=hang)
    bridge = PiBridge(rpc=rpc)
    task = asyncio.ensure_future(bridge.prompt("hi", session_id="sess-to"))
    await asyncio.sleep(0.05)
    bridge_mod.record_turn_abort_source("sess-to", "policy")  # 中途 policy 中止
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert _kernel_end(_turn_env, "sess-to") == "cancelled"


@pytest.mark.asyncio
async def test_policy_abort_parity_stream_path(_turn_env):
    """policy 中止在流式路径同样结算 aborted（双路径语义等价）。"""
    sid = "sess-pol-s"
    rpc = _make_rpc()
    release = asyncio.Event()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await release.wait()
            await _feed_settled(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)

    async def consume():
        events = [ev async for ev in bridge.stream_prompt("hi", session_id=sid)]
        return events

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.05)
    await bridge.abort(session_id=sid, source="policy")
    release.set()
    events = await task
    assert any(ev.startswith("event: done") for ev in events)
    assert _kernel_end(_turn_env, sid) == "aborted"
    assert _settle_class(_turn_env, sid) == "aborted"
