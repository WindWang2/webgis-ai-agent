"""Regression tests for #554 — Pi bridge turn-lock/abort/dispatch-service defects.

  1. Lock wait before the stream's first real event had ZERO bytes on the
     wire: a concurrent second stream's SSE headers had flushed, but no
     keepalive flowed while it waited for the previous turn's lock
     (held across send + drain + cleanup, up to PI_RPC_TIMEOUT=300s), so
     idle-timeout proxies dropped the queued user's connection.

  2. prompt() (non-streaming) drained with a 2s timeout and returned a 200
     with truncated content WITHOUT sending the abort RPC — Pi kept
     generating tokens / executing tools (up to the 300s RPC timeout) while
     the client believed the turn succeeded.

  3. dispatch_tool constructed a fresh ToolDispatchService per HTTP callback,
     so the instance-level _completed_keys set never accumulated: a repeat
     call within the same turn always got the "still in flight" message even
     after the first call had completed (post-success dedup semantics dead).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiRpcError, PiToolRequest, dispatch_tool

# stream_prompt lazily imports app.api.routes.chat inside the turn path; on a
# cold process that module-level import costs seconds INSIDE the generator, so
# the first turn stalls with zero bytes and blows this file's sub-2s lock-wait
# budgets (test_stream_prompt_emits_keepalive_during_lock_wait is order-sensitive
# otherwise). Warm it at collection time — in production it is always imported
# at app startup before any turn runs.
import app.api.routes.chat  # noqa: F401


def _make_rpc() -> MagicMock:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


def _rpc_commands(rpc: MagicMock) -> list[str]:
    return [c.args[0] for c in rpc.request.call_args_list if c.args]


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    saved = (bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry)
    bridge_mod._dispatch_service = None
    bridge_mod._dispatch_service_registry = None
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry = saved


# ─── Defect 1: lock-wait zero bytes → keepalive during the wait ───────────


@pytest.mark.asyncio
async def test_stream_prompt_emits_keepalive_during_lock_wait(monkeypatch):
    """A concurrent second stream must emit bytes (keepalive comment) while
    blocked on the turn lock, WITHOUT sending its prompt RPC — and only send
    the prompt once the first turn has released the lock."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 60.0)

    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)

    a_drained_first_event = asyncio.Event()
    a_release = asyncio.Event()
    b_prompt_sent = asyncio.Event()

    async def fake_request_a(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "AAA"},
            })

    async def fake_request_b(cmd, data=None):
        if cmd == "prompt":
            b_prompt_sent.set()
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "BBB"},
            })
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    async def run_a():
        rpc.request = AsyncMock(side_effect=fake_request_a)
        async for ev in bridge.stream_prompt("A", session_id="sess-A"):
            if "AAA" in ev:
                a_drained_first_event.set()

    async def run_b():
        rpc.request = AsyncMock(side_effect=fake_request_b)
        events = []
        async for ev in bridge.stream_prompt("B", session_id="sess-B"):
            events.append(ev)
        return events

    async def feed_a_agent_end():
        await a_release.wait()
        await rpc.events.put({"type": "agent_end", "willRetry": False})
        await rpc.events.put({"type": "agent_settled"})

    feeder = asyncio.ensure_future(feed_a_agent_end())
    task_a = asyncio.ensure_future(run_a())
    # Turn A holds the lock and is parked mid-drain waiting for agent_settled.
    await asyncio.wait_for(a_drained_first_event.wait(), timeout=2.0)
    await asyncio.sleep(0.02)

    # Start turn B: it must block on the lock, emit keepalives, and NOT send
    # its prompt yet.
    task_b = asyncio.ensure_future(run_b())
    await asyncio.sleep(0.15)  # a few heartbeat intervals elapse
    # Keepalive comments are SSE comment frames (``: keepalive``), not events.
    # Use task_b's yielded frames: poke the first yielded chunk via a shared list.
    assert not b_prompt_sent.is_set(), (
        "turn B sent its prompt while turn A still held the lock — the lock "
        "wait must not send the prompt"
    )
    # task_b yields inside the lock-wait loop; its first frames are keepalive
    # comments. Grab whatever it has yielded so far through the task's own list.
    # We can't iterate task_b's generator from here, so assert via the shared
    # event that turn B is (a) still alive and (b) not done — i.e. parked on
    # the lock loop rather than finished early.
    assert not task_b.done(), "turn B finished while turn A held the lock"

    # Release A; B must then acquire, send its prompt, and complete.
    a_release.set()
    await asyncio.wait_for(asyncio.gather(task_a, task_b, feeder), timeout=5.0)
    assert b_prompt_sent.is_set(), "turn B never sent its prompt after A released"
    b_events = task_b.result()
    assert any(e.startswith(": keepalive") for e in b_events), (
        "turn B produced no keepalive bytes while waiting on the turn lock — "
        "the connection would have sat at zero bytes until the lock freed"
    )


# ─── Defect 2: prompt() drain timeout must abort + raise, not fake 200 ────


@pytest.mark.asyncio
async def test_prompt_drain_timeout_raises_and_sends_abort(monkeypatch):
    """A drain that times out without agent_settled must raise (not return a 200
    with truncated content) AND send the abort RPC so Pi stops executing.

    #786: the failure condition is now CONTINUOUS silence reaching the stream
    path's stall budget (PI_EVENT_STREAM_TIMEOUT), not a single inter-event
    gap — so the stall budget is patched small here instead of only
    PI_EVENT_DRAIN_TIMEOUT (which is now just the per-event wait granularity)."""
    monkeypatch.setattr(bridge_mod, "PI_EVENT_DRAIN_TIMEOUT", 0.05)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 0.05)

    rpc = _make_rpc()  # request() sends the prompt, feeds NO events
    bridge = PiBridge(rpc=rpc)

    with pytest.raises(PiRpcError, match="drain timeout"):
        await bridge.prompt("hi", session_id="sess-drain")

    assert "abort" in _rpc_commands(rpc), (
        "drain-timeout turn must send the abort RPC so Pi stops generating "
        f"tokens / executing tools; calls={_rpc_commands(rpc)}"
    )


@pytest.mark.asyncio
async def test_prompt_survives_silent_gap_longer_than_drain_timeout(monkeypatch):
    """#786: a silent window between tool_execution_start and tool_execution_end
    (or a slow first token) longer than PI_EVENT_DRAIN_TIMEOUT must NOT kill the
    non-streaming turn — the drain keeps waiting as long as the CONTINUOUS
    silence stays under the stream stall budget. A 0.2s gap with a 0.05s
    per-event granularity and events arriving every 0.02s must complete."""
    monkeypatch.setattr(bridge_mod, "PI_EVENT_DRAIN_TIMEOUT", 0.05)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 60.0)

    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            async def emit(kind: str, payload: dict | None = None):
                await asyncio.sleep(0.02)
                await rpc.events.put(payload or {"type": kind})

            # tool_execution_start ... [silent 0.2s tool execution] ... tool_end
            await emit("tool_execution_start", {
                "type": "tool_execution_start", "toolCallId": "tc-1",
                "tool": "webgis_execute", "arguments": {},
            })
            await asyncio.sleep(0.2)
            await emit("tool_execution_end", {
                "type": "tool_execution_end", "toolCallId": "tc-1",
                "content": [{"type": "text", "text": "tool done"}],
            })
            await emit("agent_end", {"type": "agent_end", "willRetry": False})
            await emit("agent_settled")

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    result = await asyncio.wait_for(
        bridge.prompt("hi", session_id="sess-gap"), timeout=5.0
    )
    assert result["content"] == ""
    assert "abort" not in _rpc_commands(rpc), (
        "a turn whose silent gap stayed under the stall budget must NOT be "
        f"aborted; calls={_rpc_commands(rpc)}"
    )
    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_prompt_send_failure_sends_abort():
    """#790 (B-6 parity with stream_prompt): when request("prompt") itself
    raises PiRpcError, the finally must still send the abort RPC — Pi may
    already have started the turn and its tools, so without the abort a retry
    duplicates side effects."""
    rpc = _make_rpc()

    async def failing_request(cmd, data=None):
        if cmd == "prompt":
            raise PiRpcError("Pi pipe error")
        return {"ok": True}

    rpc.request = AsyncMock(side_effect=failing_request)
    bridge = PiBridge(rpc=rpc)

    with pytest.raises(PiRpcError, match="Pi pipe error"):
        await bridge.prompt("hi", session_id="sess-sendfail")

    assert "abort" in _rpc_commands(rpc), (
        "a failed prompt RPC must send the abort RPC (send_failed), mirroring "
        f"stream_prompt's B-6 handling; calls={_rpc_commands(rpc)}"
    )
    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_prompt_clean_agent_end_still_succeeds():
    """The happy path is unchanged: agent_settled within the budget returns the
    content and sends NO abort."""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            })
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    result = await bridge.prompt("hi", session_id="sess-ok")
    assert result["content"] == "ok"
    assert "abort" not in _rpc_commands(rpc)


# ─── Defect 3: shared ToolDispatchService → _completed_keys accumulates ──


class _StubRegistry:
    def __init__(self):
        self.call_count = 0

    def list_tools(self):
        return ["query_map_features"]

    def metadata(self, name):
        return {"tier": 1}

    async def dispatch(self, name, args, session_id=None):
        self.call_count += 1
        return {"success": True, "data": {"summary": "ok"}, "feature_count": 1}


def _tool_request(tc_id: str, session_id: str = "sess-d") -> PiToolRequest:
    # 扩展真实路径：非 native 工具经 webgis_execute 代理调用
    return PiToolRequest(
        name="webgis_execute",
        toolCallId=tc_id,
        arguments={"toolName": "query_map_features", "arguments": {"query": "医院"}},
        sessionId=session_id,
    )


@pytest.mark.asyncio
async def test_dispatch_reuses_service_and_marks_completed(monkeypatch):
    """(a) Two concurrent identical callbacks execute ONCE (second = repeated);
    (b) a repeat AFTER completion gets the post-success message — only possible
    when _completed_keys accumulates across callbacks, i.e. the Service is
    shared instead of rebuilt per callback."""
    stub = _StubRegistry()
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: stub)

    in_flight = asyncio.Event()
    started = asyncio.Event()
    orig_dispatch = stub.dispatch

    async def _gated_dispatch(name, args, session_id=None):
        started.set()
        await in_flight.wait()
        return await orig_dispatch(name, args, session_id=session_id)

    stub.dispatch = _gated_dispatch

    first = asyncio.ensure_future(dispatch_tool(_tool_request("tc-1")))
    await asyncio.wait_for(started.wait(), timeout=2.0)  # first is in flight

    second = asyncio.ensure_future(dispatch_tool(_tool_request("tc-2")))
    r2 = await asyncio.wait_for(second, timeout=2.0)
    assert r2.isError is False
    assert "重复调用拦截" in r2.content[0]["text"]
    assert "仍在执行中" in r2.content[0]["text"], (
        "first call is still gated, so the dedup message must be the in-flight "
        "variant (never a fabricated success)"
    )
    assert stub.call_count == 0, "no real execution may happen for the deduped call"

    in_flight.set()
    r1 = await first
    assert r1.isError is False
    assert stub.call_count == 1, "identical concurrent callbacks must execute once"

    # Post-success repeat: the shared service's _completed_keys now holds the
    # key, so the message must be the POST-SUCCESS variant — not "仍在执行中".
    # audit4 #984 后的诚实契约：post-success 措辞为「以相同参数执行过」+
    # 「本次未重新执行」（不再声称"成功执行/结果已生效"）。
    r3 = await dispatch_tool(_tool_request("tc-3"))
    assert "已在本任务中以相同参数执行过" in r3.content[0]["text"], (
        "post-success dedup message missing — _completed_keys never persists, "
        "i.e. ToolDispatchService is still rebuilt per callback"
    )
    assert "未重新执行" in r3.content[0]["text"]


# ─── #789: real tool name in the harness trail (no layer_upsert relabel) ──


@pytest.mark.asyncio
async def test_dispatch_keeps_real_tool_name_and_structural_mutation(monkeypatch):
    """#789 (F-A-2): a fingerprint-carrying ``webgis_view_set`` dispatch must
    be recorded in the harness trail under its REAL name (audit trail +
    ToolChoiceAccuracy) while still entering the mutation ledger — the ledger
    classification is structural (result carries mapspec_fingerprint), not the
    fake "webgis_layer_upsert" relabel."""
    from app.services.tool_dispatch_service import ToolDispatchResult

    sid = "sess-789-viewset"
    result = ToolDispatchResult(
        status="ok",
        llm_payload="view updated",
        slim_event={},
        geojson_ref=None,
        raw_result={
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": "carto-sha256:fp-789",
            "mutation_revision": 1,
        },
        error_msg=None,
        map_actions=[],
    )
    fake_service = MagicMock()
    fake_service.dispatch = AsyncMock(return_value=result)
    monkeypatch.setattr(bridge_mod, "ToolDispatchService", lambda **kw: fake_service)

    fake_registry = MagicMock()
    fake_registry.list_tools = MagicMock(return_value=["webgis_view_set"])
    fake_registry.metadata = MagicMock(return_value={"tier": 1})
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: fake_registry)

    async def _fake_persist(session_id, event, actions):
        return True

    async def _fake_eval(session_id, **kwargs):
        return {}

    monkeypatch.setattr(bridge_mod, "_persist_cartographic_harness_context", _fake_persist)
    monkeypatch.setattr(bridge_mod, "evaluate_cartographic_session", _fake_eval)

    try:
        resp = await dispatch_tool(PiToolRequest(
            toolCallId="tc-789",
            name="webgis_execute",
            arguments={"toolName": "webgis_view_set", "arguments": {"zoom": 10}},
            sessionId=sid,
        ))
        assert resp.isError is False

        harness = bridge_mod.get_harness(sid)
        assert harness is not None
        assert harness.tool_calls[-1]["name"] == "webgis_view_set", (
            "#789: dispatch must record the REAL tool name, not the "
            f"webgis_layer_upsert relabel (got {harness.tool_calls[-1]['name']!r})"
        )
        mutations = [
            m for m in harness.mapspec_mutations
            if m.get("session_id") == sid
        ]
        assert [m["tool_name"] for m in mutations] == ["webgis_view_set"]
        assert mutations[0]["is_valid"] is True, (
            "the structural fingerprint signal must still feed the mutation "
            "ledger / validity ladder"
        )
    finally:
        bridge_mod._discard_session_harness(sid)