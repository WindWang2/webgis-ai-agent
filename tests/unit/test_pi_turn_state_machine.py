"""PI-01（ADR-0286 D1）：TurnPhase 显式状态机 + settlement exactly-once。

覆盖 M0 审计缺口 G3（状态机碎片化——6 布尔旗标 × 两条路径拼凑、
tool_wait/settling 不可表达）与 G4（exactly-once settlement 无直接测试、
seam else→complete_task 开放分支无未知词表防御）。

词表（任务契约）：created/starting/running/tool_wait/cancelling/settling/settled/failed。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge

import app.api.routes.chat  # noqa: F401  # stream_prompt 冷导入预热


def _make_rpc():
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    rpc.process_died_event = asyncio.Event()
    return rpc


# ── 纯相位机 ────────────────────────────────────────────────────────────────

def test_legal_lifecycle_transitions():
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    assert t.phase is TurnPhase.CREATED
    assert t.transition(TurnPhase.STARTING, reason="flags-init")
    assert t.transition(TurnPhase.RUNNING, reason="prompt-accepted")
    assert t.transition(TurnPhase.TOOL_WAIT, reason="tool_execution_start")
    assert t.transition(TurnPhase.RUNNING, reason="tool_execution_end")
    assert t.transition(TurnPhase.TOOL_WAIT, reason="tool_execution_start")
    assert t.transition(TurnPhase.RUNNING, reason="tool_execution_end")
    assert t.transition(TurnPhase.SETTLING, reason="seam")
    assert t.transition(TurnPhase.SETTLED, reason="completed")
    assert t.to_dict()["phase"] == "settled"
    assert t.to_dict()["transitions"]  # 轨迹非空且有界


def test_illegal_transition_rejected_and_counted():
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    # CREATED 不可直达 RUNNING（必须经 STARTING）；SETTLED 只能从 SETTLING 进。
    assert t.transition(TurnPhase.RUNNING) is False
    assert t.transition(TurnPhase.STARTING) is True
    assert t.transition(TurnPhase.RUNNING) is True
    assert t.transition(TurnPhase.SETTLED) is False  # 必须经 SETTLING
    assert t.transition(TurnPhase.TOOL_WAIT) is True  # RUNNING→TOOL_WAIT 合法
    assert t.transition(TurnPhase.RUNNING) is True
    assert t.transition(TurnPhase.STARTING) is False  # RUNNING→STARTING 非法
    d = t.to_dict()
    assert d["illegal_transitions"] == 3  # CREATED→RUNNING / RUNNING→SETTLED / RUNNING→STARTING
    assert t.phase is TurnPhase.RUNNING  # 非法转移不改相位


def test_terminal_phase_locked():
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    t.transition(TurnPhase.STARTING)
    t.transition(TurnPhase.SETTLING)
    t.transition(TurnPhase.FAILED)
    for target in TurnPhase:
        assert t.transition(target) is False
    assert t.phase is TurnPhase.FAILED


def test_settlement_latch_exactly_once():
    from app.services.chat.pi_turn_state import TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    assert t.begin_settlement() is True
    assert t.begin_settlement() is False
    assert t.begin_settlement() is False
    assert t.to_dict()["settlement_attempts"] == 3


@pytest.mark.parametrize(("status", "expected_phase"), [
    ("completed", "settled"),
    ("refused", "settled"),
    ("cancelled", "failed"),
    ("failed", "failed"),
    ("aborted", "failed"),
    ("interrupted", "failed"),
    ("something_new", "failed"),  # 未知词表 fail-closed
])
def test_terminal_status_mapping(status, expected_phase):
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    t.transition(TurnPhase.STARTING)
    t.transition(TurnPhase.RUNNING)
    t.transition(TurnPhase.SETTLING)
    assert t.record_terminal_status(status) is True
    assert t.phase is TurnPhase(expected_phase)
    assert t.last_settlement_status == status


def test_terminal_status_only_from_settling():
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    t = TurnPhaseTracker("turn-x")
    t.transition(TurnPhase.STARTING)
    # 未进入 SETTLING 就报终态：防御性接受并记非法（真实控制流不应出现）
    assert t.record_terminal_status("completed") is True
    assert t.to_dict()["illegal_transitions"] >= 1


# ── seam 集成（bridge 边界 exactly-once + 未知词表防御）────────────────────

@pytest.fixture
def _seam_env(monkeypatch):
    state = SimpleNamespace(tracker_settled=[], kernel_ends=[], settle_calls=[])

    class FakeTrackerTask:
        def __init__(self, tid):
            self.id = tid

    class FakeTracker:
        def create(self, sid, message):
            return FakeTrackerTask(f"{sid}-task")

        def list_by_session(self, sid):
            return []

        def cancel(self, tid):
            state.tracker_settled.append(("cancelled", tid))

        def fail_task(self, tid, reason):
            state.tracker_settled.append(("failed", tid, reason))

        def complete_task(self, tid):
            state.tracker_settled.append(("completed", tid))

    import app.services.chat.engine_instance as ei
    monkeypatch.setattr(ei, "try_get_chat_engine", lambda: SimpleNamespace(tracker=FakeTracker()))

    import app.services.harness_kernel.settle as hks
    from app.services.harness_kernel.settle_claim import SettlementClaim
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.acquire_settlement_claim",
        AsyncMock(return_value=SettlementClaim("claimed", "fixture-claim")),
    )
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.complete_settlement",
        AsyncMock(return_value=True),
    )

    async def _settle_effects(sid, turn_id, settle_class, *, reason="turn_settled",
                              state_trigger="execution_settled"):
        state.settle_calls.append((sid, turn_id, settle_class))
        return [], {"task_complete": True}

    monkeypatch.setattr(hks, "run_settle_effects", _settle_effects)
    monkeypatch.setattr(hks, "resolve_turn_refusal", AsyncMock(return_value=None))

    import app.services.harness_kernel as hk
    import app.services.harness_kernel.runtime as hk_runtime

    class FakeKernelRuntime:
        async def begin_turn(self, turn_id, *, host, message, lock=None):
            pass

        async def end_turn(self, turn_id, *, host, status, checkpoint=True, lock=None, settle_token=""):
            state.kernel_ends.append((turn_id, status))
            return []

        async def lifecycle_parity_snapshot(self, turn_id):
            return {"turn_id": turn_id, "phase": "verifying", "status": "running"}

        async def turn_refusal_candidate(self, turn_id):
            return None

    monkeypatch.setattr(hk, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(hk_runtime, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
    hks.reset_for_tests()
    yield state
    hks.reset_for_tests()


async def _seam_kwargs(**overrides):
    from app.services.chat.pi_turn_state import TurnPhase, TurnPhaseTracker

    tracker = TurnPhaseTracker("turn-seam")
    tracker.transition(TurnPhase.STARTING)
    tracker.transition(TurnPhase.RUNNING)
    kw = dict(
        session_id="sess-seam",
        turn_id="turn-seam",
        tracker_task_id="sess-seam-task",
        cancelled=False,
        timed_out=False,
        send_failed=False,
        process_died=False,
        error=False,
        error_detail="",
        timeout_reason="",
        projections_settled=False,  # 让 coordinator 真跑效果序（settle_calls 可观测）
        phase=tracker,
    )
    kw.update(overrides)
    return kw


@pytest.mark.asyncio
async def test_seam_double_settle_is_noop(_seam_env, monkeypatch):
    """第二次 seam 调用被相位门闩拦截：不再产生 kernel end / settle 效果，
    返回首次记录的 status。"""
    kw = await _seam_kwargs()
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    status1 = await bridge._settle_turn_outcome(**kw)
    status2 = await bridge._settle_turn_outcome(**kw)
    assert status1 == status2 == "completed"
    assert len(_seam_env.kernel_ends) == 1
    assert len(_seam_env.settle_calls) == 1
    assert kw["phase"].to_dict()["settlement_attempts"] == 2


@pytest.mark.asyncio
async def test_unknown_kernel_status_fails_tracker(_seam_env, monkeypatch):
    """coordinator 返回未知 kernel status → tracker fail_task（此前 else 分支
    会把未知词表误结算成 completed）。"""
    import app.services.harness_kernel.settle as hks

    async def _weird_settle(inp):
        return SimpleNamespace(kernel_status="something_new", refusal=None)

    monkeypatch.setattr(hks, "settle_turn", _weird_settle)
    kw = await _seam_kwargs()
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    status = await bridge._settle_turn_outcome(**kw)
    assert status == "something_new"
    actions = [a for a in _seam_env.tracker_settled if a[1] == "sess-seam-task"]
    assert actions and actions[0][0] == "failed"
    assert "something_new" in actions[0][2]
    assert kw["phase"].phase.value == "failed"


@pytest.mark.asyncio
async def test_seam_without_phase_backcompat(_seam_env):
    """不传 phase 的旧调用路径保持原语义（coordinator 幂等兜底）。"""
    kw = await _seam_kwargs()
    kw.pop("phase")
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    status = await bridge._settle_turn_outcome(**kw)
    assert status == "completed"
    assert len(_seam_env.kernel_ends) == 1


# ── bridge 全路径相位观测 ──────────────────────────────────────────────────

@pytest.fixture
def _full_env(monkeypatch):
    """复用 seam_env 的桩 + 摘要捕获。"""
    import app.services.chat.engine_instance as ei

    summaries = []

    class FakeTrackerTask:
        def __init__(self, tid):
            self.id = tid

    class FakeTracker:
        def create(self, sid, message):
            return FakeTrackerTask(f"{sid}-task")

        def list_by_session(self, sid):
            return []

        def cancel(self, tid):
            pass

        def fail_task(self, tid, reason):
            pass

        def complete_task(self, tid):
            pass

    monkeypatch.setattr(ei, "try_get_chat_engine", lambda: SimpleNamespace(tracker=FakeTracker()))

    import app.services.harness_kernel.settle as hks
    from app.services.harness_kernel.settle_claim import SettlementClaim
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.acquire_settlement_claim",
        AsyncMock(return_value=SettlementClaim("claimed", "fixture-claim")),
    )
    monkeypatch.setattr(
        "app.services.harness_kernel.settle_claim.complete_settlement",
        AsyncMock(return_value=True),
    )

    async def _settle_effects(sid, turn_id, settle_class, *, reason="turn_settled",
                              state_trigger="execution_settled"):
        return [], {"task_complete": True}

    monkeypatch.setattr(hks, "run_settle_effects", _settle_effects)
    monkeypatch.setattr(hks, "resolve_turn_refusal", AsyncMock(return_value=None))

    import app.services.harness_kernel as hk
    import app.services.harness_kernel.runtime as hk_runtime

    class FakeKernelRuntime:
        async def begin_turn(self, turn_id, *, host, message, lock=None):
            pass

        async def end_turn(self, turn_id, *, host, status, checkpoint=True, lock=None, settle_token=""):
            return []

        async def lifecycle_parity_snapshot(self, turn_id):
            return {"turn_id": turn_id, "phase": "verifying", "status": "running"}

        async def turn_refusal_candidate(self, turn_id):
            return None

    monkeypatch.setattr(hk, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(hk_runtime, "get_runtime", lambda sid: FakeKernelRuntime())
    monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
    monkeypatch.setattr(bridge_mod, "emit_turn_summary", lambda ev: summaries.append(ev) or {})
    hks.reset_for_tests()
    yield summaries
    hks.reset_for_tests()


async def _feed_tool_turn(rpc):
    """一条带工具往返的完整事件序列（tool_start/end + settled）。"""
    await rpc.events.put({"type": "agent_start"})
    await rpc.events.put({"type": "tool_execution_start", "toolCallId": "call-1"})
    await rpc.events.put({"type": "tool_execution_end", "toolCallId": "call-1"})
    await rpc.events.put({"type": "agent_end", "willRetry": False})
    await rpc.events.put({"type": "agent_settled"})


@pytest.mark.asyncio
async def test_stream_turn_records_tool_wait_phase(_full_env):
    """流式 turn：tool_execution_start/end 驱动 TOOL_WAIT⇀RUNNING，
    相位轨迹进 TurnEvidence summary。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await _feed_tool_turn(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    events = [ev async for ev in bridge.stream_prompt("hi", session_id="sess-phase")]
    assert any(ev.startswith("event: task_complete") for ev in events)
    summary = _full_env[-1].to_summary()
    tp = summary["turn_phase"]
    assert tp["phase"] == "settled"
    phases = [t["to"] for t in tp["transitions"]]
    assert "tool_wait" in phases and "running" in phases and "settling" in phases
    assert tp["illegal_transitions"] == 0
    assert tp["settlement_attempts"] == 1


@pytest.mark.asyncio
async def test_cancelled_turn_observes_cancelling_phase(_full_env):
    """客户端取消：CANCELLING 相位被观测；终相位 FAILED；outcome CANCELLED。"""
    rpc = _make_rpc()
    release = asyncio.Event()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await release.wait()
            await _feed_tool_turn(rpc)

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)

    async def consume():
        return [ev async for ev in bridge.stream_prompt("hi", session_id="sess-cancel")]

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    release.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    summary = _full_env[-1].to_summary()
    tp = summary["turn_phase"]
    phases = [t["to"] for t in tp["transitions"]]
    assert "cancelling" in phases
    assert tp["phase"] == "failed"
    assert summary["outcome"]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_nonstream_turn_phase_in_summary(_full_env):
    """非流式路径同样挂载相位机（两路径同构）。"""
    rpc = _make_rpc()

    async def scripted(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=scripted)
    bridge = PiBridge(rpc=rpc)
    await bridge.prompt("hi", session_id="sess-ns")
    summary = _full_env[-1].to_summary()
    assert summary["turn_phase"]["phase"] == "settled"
    assert summary["turn_phase"]["settlement_attempts"] == 1
