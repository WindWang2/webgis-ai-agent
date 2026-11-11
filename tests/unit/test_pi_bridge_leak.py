"""Regression: Pi-bridge per-turn state must not leak across long-lived sessions.

Simulates 50 long chat sessions, each with tool dispatches (the hardened
callback contract requires the verified turn sessionId — SEC fix — so every
dispatch lands in that session's bucket of _session_executed_sets) and a
client that disconnects mid-stream. Asserts the
module-level dedup sets and dispatch-result cache return to baseline after
every turn, and that gc/tracemalloc don't accumulate retained tool results.

RED on the pre-fix code: neither the session execution bucket nor
_dispatch_result_cache is cleaned at turn end (only at the *next* turn start,
keyed by the real session id), so both grow monotonically across sessions.
"""
import asyncio
import gc
import tracemalloc
import weakref
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app import agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiToolRequest, dispatch_tool
from app.services.tool_dispatch_service import ToolDispatchResult
from tests.fixtures.pi_mocks import make_token_event


class _QueryMapFeaturesArgs(BaseModel):
    location: list[float]
    buffer_m: float = 10


class _StubRegistry:
    def list_tools(self):
        return ["query_map_features"]

    def metadata(self, name):
        return {"tier": 1}

    def args_model(self, name):
        # Match the real query tool's parameter seam. An incomplete stub here
        # raises inside PiInputGate; DEBUG exc_info capture then owns traceback
        # frames (and their dispatch results), which is unrelated to cache GC.
        return _QueryMapFeaturesArgs if name == "query_map_features" else None


def _make_event_rpc(events):
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False

    async def _seed(cmd, data=None):
        for ev in events:
            await rpc.events.put(ev)

    rpc.request = AsyncMock(side_effect=_seed)
    # G22-P1：abort 控制面改走 issue（写相位）/collect（响应相位）拆分；
    # 没有 issue 的 mock 会让 abort-on-disconnect 早退（token 不再 cancel
    # → 泄漏守卫如实变红）。写相位同 _seed 语义：丢弃事件播种仅对 prompt
    # 类调用有意义，控制命令直接返回已解 future。
    async def _issue(cmd, data=None):
        future = asyncio.get_running_loop().create_future()
        future.set_result({})
        return SimpleNamespace(id=f"mock-{cmd}", command=cmd, future=future)

    async def _collect(issued, command=None):
        return await issued.future

    rpc.issue = AsyncMock(side_effect=_issue)
    rpc.collect = AsyncMock(side_effect=_collect)
    return rpc


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # A production bridge has an initialized tracker. Exception-log capture
    # retains traceback frames by design and is not the memory owner measured
    # here; observability failures have a dedicated behavior test.
    import app.services.chat.engine_instance as engines
    tracker = SimpleNamespace(
        create=lambda sid, message: SimpleNamespace(id=f"task-{sid}"),
        list_by_session=lambda sid: [], cancel=lambda tid: None,
        fail_task=lambda tid, reason: None, complete_task=lambda tid: None,
    )
    monkeypatch.setattr(engines, "try_get_chat_engine", lambda: SimpleNamespace(tracker=tracker))
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()


@pytest.fixture(params=[False, True], ids=["automatic-gc", "deferred-gc"])
def _gc_schedule(request):
    """Exercise retained-memory measurement independently of cyclic-GC timing."""
    was_enabled = gc.isenabled()
    if request.param:
        gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


async def _fake_dispatch(self, tc, session_id, executed):
    """Stand-in for ToolDispatchService.dispatch: adds to the dedup set, returns ok."""
    executed.add((tc["function"]["name"], str(tc["function"]["arguments"])))
    return ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"type": "step_result"},
        geojson_ref=None,
        raw_result={"data": "x" * 1024},  # MB-scale in the real world
        error_msg=None,
    )


@pytest.fixture
def _dispatch_results(monkeypatch):
    """Observe only results created by this workload without retaining them."""
    observed = []

    async def dispatch(self, tc, session_id, executed):
        result = await _fake_dispatch(self, tc, session_id, executed)
        observed.append(weakref.ref(result))
        return result

    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", dispatch)
    return observed


def _assert_results_released(observed):
    retained = sum(reference() is not None for reference in observed)
    assert retained <= 5, f"{retained} newly created ToolDispatchResult objects retained after sessions"


async def _run_one_session(i, monkeypatch):
    """One chat session: two Pi tool dispatches + a stream the client abandons."""
    rpc = _make_event_rpc([make_token_event("hello")])
    bridge = PiBridge(rpc=rpc)
    agen = bridge.stream_prompt(f"msg {i}", session_id=f"session-{i}")
    try:
        ev = await agen.__anext__()
        assert ev.startswith("event: task_start")
        # Dispatch inside the active turn so only disconnect cleanup, not
        # the next turn's startup cleanup, can remove these cached results.
        for j in range(2):
            response = await dispatch_tool(PiToolRequest(
                name="webgis_execute",
                toolCallId=f"tc-{i}-{j}",
                arguments={
                    "toolName": "query_map_features",
                    "arguments": {"location": [116.0, 39.0], "buffer_m": 10},
                },
                # Long-tail tools use the proxy; a rejected bare name would
                # never call _fake_dispatch and make this leak guard vacuous.
                sessionId=f"session-{i}",
            ))
            assert not response.isError, response
            assert (f"session-{i}", f"tc-{i}-{j}") in bridge_mod._dispatch_result_cache
        assert bridge_mod._session_executed_sets[f"session-{i}"]
    finally:
        await agen.aclose()  # client disconnect -> GeneratorExit -> finally


@pytest.mark.asyncio
async def test_turn_state_returns_to_baseline_across_many_sessions(monkeypatch):
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())
    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    for i in range(50):
        await _run_one_session(i, monkeypatch)
        assert bridge_mod._session_executed_sets == {}, (
            f"turn {i}: executed sets leaked {len(bridge_mod._session_executed_sets)} "
            f"bucket(s) ("" bucket size={len(bridge_mod._session_executed_sets.get('', ()))})"
        )
        assert bridge_mod._dispatch_result_cache == {}, (
            f"turn {i}: dispatch cache leaked {len(bridge_mod._dispatch_result_cache)} entries"
        )


@pytest.mark.asyncio
async def test_no_retained_objects_or_memory_growth(monkeypatch, _gc_schedule, _dispatch_results, caplog):
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())

    # 预热：会话循环内的首次惰性导入（scipy/networkx/子模块可达数十 MB）
    # 不是泄漏 —— tracemalloc 必须在导入完成**之后**启动，测的才是保留
    # 增长而非冷启动导入成本（CI 边缘抖动的根因；两分支同一修复）。
    for i in range(3):
        await _run_one_session(i, monkeypatch)
    gc.collect()
    tracemalloc.start()
    try:
        for i in range(50):
            await _run_one_session(3 + i, monkeypatch)
        # Measure retained allocations, just like the object guard below.
        # RPC MagicMock/AsyncMock cycles can leave several MB of unreachable
        # garbage until generation 2 runs, depending on earlier suite activity.
        # Collection does not remove reachable caches or leaked tool results.
        gc.collect()
        current, _peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Count the actual 106 results of this workload, including warmup. Live
    # results owned by earlier tests/caches cannot inflate this measurement;
    # weak references also avoid keeping the measured objects alive ourselves.
    assert len(_dispatch_results) == 106
    _assert_results_released(_dispatch_results)
    assert not any(record.exc_info and record.name == "app.services.chat.pi_input_gate"
                   for record in caplog.records), "the registry fixture must not trigger captured gate tracebacks"
    # 4.5MB（2026-10-17 二次校准）：澄清闭环 envelope + settle coordinator
    # 收据台账 + representation 绑定投影并入后，50 会话合法常驻足迹实测
    # ~3.95MB（CI 3.12，2026-10-17 run：current=3948367，retained ≤5 守卫绿）。
    # 校准史：2.5MB（初始）→ 3.0MB（2026-09-14，ADR-0180-0184）→ 4.5MB。
    # 真正的泄漏守卫是上方 retained ≤5 断言 —— 字节上限只锁"足迹量级不漂移"。
    assert current < 4_500_000, f"tracemalloc current={current} bytes after 50 sessions"


@pytest.mark.asyncio
async def test_result_probe_excludes_old_objects_but_rejects_six_new_cached_results(_dispatch_results):
    # Existing reachable results from another workload are legitimate baseline.
    unrelated = [ToolDispatchResult("ok", "old", {}, None, {}, None) for _ in range(10)]
    service = bridge_mod.ToolDispatchService(registry=_StubRegistry())
    for index in range(6):
        result = await service.dispatch({"function": {
            "name": "query_map_features", "arguments": {"location": [116.0, 39.0]},
        }}, "leak-probe", set())
        bridge_mod.cache_dispatch_result(f"new-{index}", result, "leak-probe")
    del result
    assert len(_dispatch_results) == 6
    with pytest.raises(AssertionError, match="6 newly created ToolDispatchResult"):
        _assert_results_released(_dispatch_results)
    bridge_mod._clear_dispatch_cache("leak-probe")
    gc.collect()
    _assert_results_released(_dispatch_results)
    assert len(unrelated) == 10  # Still reachable; excluded rather than cleared.
