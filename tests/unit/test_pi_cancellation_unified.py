"""ADR-0100 Pi Runtime V6 — unified cancellation + wave fairness regression.

锁定：
1. abort_active_pi_turn 是唯一桥接 abort 原语（owner-worker 解析、
   CONC-F7 预算、绝不抛出）；
2. cancel_agent_task_and_turn 级联 = tracker + durable + registry + abort
   （task/job 两条路由共用同一实现）；
3. _SessionWaveGate 按会话公平（会话达上限时等待者不持全局槽，
   其它会话照常通过）；
4. subagent 取消：父令牌取消 → 子代理诚实返回 cancelled（非字符串猜测）。
"""
from __future__ import annotations

import asyncio

import pytest


# ── abort_active_pi_turn ─────────────────────────────────────────────────────


class _FakeBridge:
    def __init__(self, *, hang=False, raise_error=False):
        self.aborted = []
        self.hang = hang
        self.raise_error = raise_error

    async def abort(self, session_id=None):
        if self.raise_error:
            raise RuntimeError("rpc dead")
        if self.hang:
            await asyncio.sleep(30)
        self.aborted.append(session_id)
        return {"status": "aborted", "session": session_id}


class _Entry:
    def __init__(self, bridge):
        self.bridge = bridge


@pytest.fixture()
def _seam(monkeypatch):
    import app.agent_pi_bridge as bridge_mod
    import app.api.routes.chat as chat_routes
    from app.services.chat import session_cancellation as seam

    saved_entry = bridge_mod.get_active_turn_entry
    saved_pi = getattr(chat_routes, "pi_bridge", None)
    yield seam, monkeypatch
    bridge_mod.get_active_turn_entry = saved_entry
    chat_routes.pi_bridge = saved_pi


def test_abort_routes_to_owning_worker(_seam):
    seam, monkeypatch = _seam
    bridge = _FakeBridge()
    import app.agent_pi_bridge as bridge_mod

    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry",
        lambda sid: _Entry(bridge) if sid == "s1" else None)
    result = asyncio.run(seam.abort_active_pi_turn("s1"))
    assert result["aborted"] is True
    assert bridge.aborted == ["s1"]


def test_abort_no_active_turn_is_noop(_seam):
    seam, monkeypatch = _seam
    import app.agent_pi_bridge as bridge_mod
    import app.api.routes.chat as chat_routes

    monkeypatch.setattr(bridge_mod, "get_active_turn_entry", lambda sid: None)
    chat_routes.pi_bridge = _FakeBridge()
    result = asyncio.run(seam.abort_active_pi_turn("s-none"))
    assert result["aborted"] is False
    assert chat_routes.pi_bridge.aborted == []


def test_abort_never_raises_on_rpc_error_or_timeout(_seam):
    seam, monkeypatch = _seam
    import app.agent_pi_bridge as bridge_mod

    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry",
        lambda sid: _Entry(_FakeBridge(raise_error=True)))
    result = asyncio.run(seam.abort_active_pi_turn("s-err"))
    assert result["aborted"] is False

    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry",
        lambda sid: _Entry(_FakeBridge(hang=True)))
    result2 = asyncio.run(seam.abort_active_pi_turn("s-hang", timeout=0.05))
    assert result2["aborted"] is False
    assert result2["detail"] == "abort timeout"


# ── cancel_agent_task_and_turn cascade ──────────────────────────────────────


def test_cascade_cancels_tracker_durable_and_bridge(monkeypatch):
    from app.services.chat import session_cancellation as seam

    class _Tracker:
        def __init__(self):
            self.info = type("I", (), {"background_job_ids": ["dj-1"],
                                       "session_id": "s-casc"})()
            self.cancelled = []

        def get(self, tid):
            return self.info

        def cancel(self, tid):
            self.cancelled.append(tid)
            return True

    class _Engine:
        tracker = None

    durable_calls = []
    registry_cancels = []
    aborts = []

    import app.api.routes.chat as chat_routes
    import app.services.jobs.store as store_mod
    from app.lib import cancellation as cancels

    engine_holder = _Engine()
    engine_holder.tracker = _Tracker()
    monkeypatch.setattr(chat_routes, "get_engine", lambda: engine_holder)
    async def _fake_request_cancel(db, jid):
        durable_calls.append(jid)
        return (True, "cancelling")

    monkeypatch.setattr(
        store_mod.DurableJobStore, "request_cancel",
        staticmethod(_fake_request_cancel),
    )
    monkeypatch.setattr(
        cancels, "registry",
        type("R", (), {"cancel": staticmethod(
            lambda jid, reason="": registry_cancels.append((jid, reason)))})(),
    )
    monkeypatch.setattr(
        seam, "abort_active_pi_turn",
        lambda sid, **kw: aborts.append(sid) or asyncio.sleep(0, result={
            "aborted": True, "detail": "x"}),
    )

    async def run():
        return await seam.cancel_agent_task_and_turn(None, "task-9")

    result = asyncio.run(run())
    assert result["cancelled"] is True
    assert durable_calls == ["dj-1"]
    assert registry_cancels and registry_cancels[0][0] == "dj-1"
    assert aborts == ["s-casc"]


# ── session wave gate fairness ───────────────────────────────────────────────


def test_session_gate_caps_per_session_without_blocking_others():
    from app.services.tool_dispatch_service import _SessionWaveGate

    gate = _SessionWaveGate(cap=2)
    order: list = []

    async def worker(session, name, delay):
        await gate.acquire(session)
        try:
            order.append(f"{name}:start")
            await asyncio.sleep(delay)
            order.append(f"{name}:end")
        finally:
            await gate.release(session)

    async def scenario():
        # 会话 A 打满自己的上限（2），会话 B 的工具必须仍能进入
        tasks = [
            asyncio.create_task(worker("A", "a1", 0.10)),
            asyncio.create_task(worker("A", "a2", 0.10)),
            asyncio.create_task(worker("A", "a3", 0.10)),  # A 的第三个排队
            asyncio.create_task(worker("B", "b1", 0.02)),
        ]
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    # B 在 A 的第三个工具之前完成（公平性：B 不因 A 打满会话上限而饿等）
    assert order.index("b1:end") < order.index("a3:start")
    assert gate.held_sessions == 0  # 全部释放后表为空


def test_session_gate_release_cleans_table():
    from app.services.tool_dispatch_service import _SessionWaveGate

    gate = _SessionWaveGate(cap=1)

    async def go():
        await gate.acquire("s")
        assert gate.held_sessions == 1
        await gate.release("s")
        assert gate.held_sessions == 0

    asyncio.run(go())


# ── subagent cancellation ────────────────────────────────────────────────────


def test_subagent_parent_cancel_returns_honest_cancelled(monkeypatch):
    from app.lib.cancellation import CancellationToken, use_token
    from app.services.subagent import SubagentDispatcher

    class _Reg:
        def all_metadata(self):
            return {}

        def get_schemas_subset(self, names):
            return []

    started = asyncio.Event()

    class _SubEngine:
        def __init__(self):
            self.cancelled = False

        async def chat(self, message, session_id):
            started.set()
            # 模拟一个长跑子代理循环：直到任务被取消才返回
            while True:
                await asyncio.sleep(0.01)

    dispatcher = SubagentDispatcher(_Reg(), "sess-sub")
    dispatcher._build_sub_engine = lambda subset, rounds: _SubEngine()

    async def run():
        parent = CancellationToken()
        loop = asyncio.get_running_loop()
        # use_token 必须先于 task 创建 —— ContextVar 在 create_task 时拷贝，
        # 之后才进入的话 dispatcher.run() 里读不到父令牌（无链接 → 挂起）。
        with use_token(parent):
            task = asyncio.create_task(dispatcher.run(
                task="长任务", domains=None, max_rounds=3))
            await started.wait()
            loop.call_later(0.05, parent.cancel, "user stop")
            return await task

    result = asyncio.run(run())
    assert result.success is False
    assert result.error == "cancelled"
    assert "取消" in result.summary


# ── truthful skill surface refresh ───────────────────────────────────────────


def test_refresh_skill_surface_reports_both_layers_truthfully():
    import asyncio

    from app.tools.registry import ToolRegistry
    from app.tools import init_tools
    from app.tools.skill_surface_refresh import register_skill_surface_refresh

    reg = ToolRegistry()
    init_tools(reg)
    register_skill_surface_refresh(reg)

    from app.tools.registry import confirm_tier3

    async def call():
        with confirm_tier3():
            return await reg.dispatch("refresh_skill_surface", {"reason": "test"})

    result = asyncio.run(call())
    layers = result.get("layers") or {}
    # 注册表层：立即刷新，经 webgis_execute 可达
    assert layers["registry_layer"]["refreshed"] is True
    assert layers["registry_layer"]["reachable_via"] == "webgis_execute"
    # native schema 面：如实报告冻结 —— 不假装刷新成功
    assert layers["native_schema_layer"]["refreshed"] is False
    assert "respawn" in layers["native_schema_layer"]["requires"]
