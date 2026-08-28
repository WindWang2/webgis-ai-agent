"""audit4 harness-semantics fixes: #982 / #992 / #993 / #994.

- #982: Pi stream_prompt gains a whole-turn wall-clock budget — a drip-feed
  event stream (each event resetting the silence budget) can no longer keep a
  dead-looped turn alive indefinitely.
- #992: format_layer_lines caps the rendered inventory (newest 20 refs + one
  "另有 N 层未列出" summary line); hidden refs skip schema inference too.
- #993: webgis_map_intent is tier 1 (visible for keyword-less requests like
  『看看成都的大学』); a late Pi tool callback whose turn is no longer active
  must NOT record its step onto the newest task of another turn.
- #994: a harness-synth PlanStep with tool_binding only ticks when the
  completed tool name is in the binding; unrelated analysis tools no longer
  advance bound steps.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiToolRequest, dispatch_tool
from app.services.chat.context import layer_schema as ls
from app.services.chat.plan_orchestrator import (
    AgentPlanOrchestrator,
    Plan,
    PlanStep,
)
from app.services.tool_catalog import ToolCatalog
from app.services.tool_dispatch_service import ToolDispatchResult
from app.tools import init_tools
from app.tools.registry import ToolRegistry
from app.services.planning import StepStatus
from app.services.planning.models import CanonicalPlan, CanonicalStep


# ─── #982: Pi 流式整轮总预算 ────────────────────────────────────────────────


def _make_rpc() -> MagicMock:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


def _drip_event(delta: str) -> dict:
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": []},
        "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": delta},
    }


async def test_stream_prompt_total_budget_cuts_drip_feed(monkeypatch):
    """A stream that keeps producing events (silence budget never trips) must
    still be cut by the whole-turn budget: error + done + abort RPC, with the
    total-budget message (not the stall message)."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 60.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 0.15)

    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    abort_mock = AsyncMock()
    bridge._abort_on_disconnect = abort_mock

    stop = asyncio.Event()

    async def feeder():
        # Drip-feed: one event every 0.03s — far under the (patched) 60s stall
        # budget, so only the total budget can end the turn.
        while not stop.is_set():
            await rpc.events.put(_drip_event("x"))
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.03)
            except asyncio.TimeoutError:
                pass

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            asyncio.ensure_future(feeder())
        return {}

    rpc.request = AsyncMock(side_effect=fake_request)

    events = []
    async for ev in bridge.stream_prompt("看看成都的大学", session_id="sess-budget"):
        events.append(ev)
    stop.set()

    error_events = [e for e in events if e.startswith("event: error")]
    assert error_events, "total-budget exhaustion must yield an error event"
    assert "total budget" in error_events[0], (
        f"error must name the total budget (not the stall): {error_events[0]!r}"
    )
    assert any(e.startswith("event: done") for e in events), "stream must still emit done"
    abort_mock.assert_awaited(), (
        "total-budget timeout must go through the abort RPC cleanup path, "
        "otherwise Pi keeps executing tools after the client saw the error"
    )


async def test_stream_prompt_completes_within_total_budget(monkeypatch):
    """A healthy turn settling inside the total budget must complete normally —
    the new check may not false-trip on the happy path."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 60.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 5.0)

    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put(_drip_event("ok"))
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})
        return {}

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)
    abort_mock = AsyncMock()
    bridge._abort_on_disconnect = abort_mock

    events = []
    async for ev in bridge.stream_prompt("hi", session_id="sess-ok"):
        events.append(ev)

    assert not [e for e in events if e.startswith("event: error")], (
        "a turn settling within budget must not error"
    )
    assert any(e.startswith("event: done") for e in events)
    abort_mock.assert_not_awaited()


# ─── #992: 图层清单 20 条上限 ───────────────────────────────────────────────


async def test_format_layer_lines_caps_inventory_and_keeps_newest():
    """33 refs → exactly LAYER_INVENTORY_MAX_LINES layer lines + 1 summary line;
    the kept refs are the NEWEST (dict tail — dispatch appends on store)."""
    inventory = {f"ref:geojson-{i:03d}": f"图层{i}" for i in range(33)}
    lines = await ls.format_layer_lines(inventory, active_layers=[], session_id=None)

    assert len(lines) == ls.LAYER_INVENTORY_MAX_LINES + 1, (
        f"expected 20 kept lines + 1 summary line, got {len(lines)}"
    )
    assert lines[0].startswith("ref:geojson-013"), "oldest refs must be dropped first"
    assert "ref:geojson-032" in lines[-2], "newest ref (tail) must be kept"
    assert "另有 13 层未列出" in lines[-1]
    rendered = "\n".join(lines[:-1])
    for i in range(13):
        assert f"ref:geojson-{i:03d}" not in rendered, f"hidden ref {i:03d} leaked"


async def test_format_layer_lines_no_summary_when_within_cap():
    """At or under the cap: no truncation, no summary line."""
    inventory = {f"ref:geojson-{i:03d}": None for i in range(ls.LAYER_INVENTORY_MAX_LINES)}
    lines = await ls.format_layer_lines(inventory, active_layers=[], session_id=None)
    assert len(lines) == ls.LAYER_INVENTORY_MAX_LINES
    assert not any("另有" in ln for ln in lines)


async def test_format_layer_lines_skips_schema_for_hidden_refs(monkeypatch):
    """Truncation happens BEFORE the parallel schema gather — dropped refs must
    not trigger schema inference (a full data fetch per unseen ref)."""
    seen: list[str] = []

    async def fake_build(session_id, ref_id, sample_size=5):
        seen.append(ref_id)
        return None

    monkeypatch.setattr(ls, "build_layer_schema", fake_build)
    inventory = {f"ref:geojson-{i:03d}": None for i in range(30)}
    await ls.format_layer_lines(inventory, active_layers=[], session_id="s-cap")

    assert sorted(seen) == [f"ref:geojson-{i:03d}" for i in range(10, 30)], (
        "schema inference must run only for the kept (newest 20) refs"
    )


# ─── #993①: webgis_map_intent tier 对齐 docstring 承诺 ─────────────────────


def test_webgis_map_intent_tier1_and_visible_without_intent_domains():
    """『看看成都的大学』hits chinese/osm keywords but none of webgis_map_intent's
    old gating domains (statistics/report/network/temporal) — under tier=2 the
    intent tool was invisible exactly there, breaking SYSTEM_PROMPT's 意图先行."""
    r = ToolRegistry()
    init_tools(r)

    assert int(r.metadata("webgis_map_intent").get("tier", 1)) == 1
    # webgis_map_product stays tier=2: it runs only after data tools returned.
    assert int(r.metadata("webgis_map_product").get("tier", 2)) == 2

    catalog = ToolCatalog(r, sticky_ttl=0)
    for query in ("看看成都的大学", "你好"):
        names = {s["function"]["name"] for s in catalog.select_schemas(query, session_id="s-993")}
        assert "webgis_map_intent" in names, (
            f"webgis_map_intent must be always-visible (tier 1); query={query!r} "
            f"got {len(names)} schemas without it"
        )


# ─── #993③: 迟到工具回调不记到新 turn 的 task ──────────────────────────────


def _fake_registry_for_dispatch() -> MagicMock:
    reg = MagicMock()
    reg.list_tools = MagicMock(return_value=["buffer_analysis"])
    reg.metadata = MagicMock(return_value={"tier": 1})
    return reg


def _fake_engine_with_task():
    task = SimpleNamespace(id="task-new-turn", status=SimpleNamespace(value="running"))
    step = SimpleNamespace(id="step-1")
    tracker = MagicMock()
    tracker.list_by_session = MagicMock(return_value=[task])
    tracker.start_step = MagicMock(return_value=step)
    tracker.complete_step = MagicMock()
    tracker.fail_step = MagicMock()
    return SimpleNamespace(tracker=tracker), tracker


async def test_late_tool_callback_step_not_misattributed(monkeypatch, caplog):
    """A callback whose verified turn is no longer the active turn (previous
    turn cancelled, new turn already created its task) must drop the step with
    a warning — never record it onto the newest task."""
    fake_service = MagicMock()
    fake_service.dispatch = AsyncMock(return_value=ToolDispatchResult(
        status="ok", llm_payload="done", slim_event={}, geojson_ref=None,
        raw_result={}, error_msg=None, map_actions=[],
    ))
    monkeypatch.setattr(bridge_mod, "get_tool_registry", _fake_registry_for_dispatch)
    monkeypatch.setattr(bridge_mod, "ToolDispatchService", lambda **kw: fake_service)
    engine, tracker = _fake_engine_with_task()
    # AH-P1-2：bridge 经 engine_instance 读取 ChatEngine（不再反向 import 路由层）
    from app.services.chat import engine_instance
    monkeypatch.setattr(engine_instance, "_engine", engine)

    # New turn is active; the callback belongs to the cancelled previous turn.
    monkeypatch.setattr(bridge_mod, "_active_turn_turn_id", "turn-new")
    monkeypatch.setattr(bridge_mod, "_active_turn_run_id", "run-new")
    monkeypatch.setattr(bridge_mod, "_active_turn_session_id", "s-late")

    with caplog.at_level(logging.WARNING, logger="app.agent_pi_bridge"):
        resp = await dispatch_tool(PiToolRequest(
            toolCallId="tc-late", name="webgis_execute", arguments={"toolName": "buffer_analysis", "arguments": {}},
            sessionId="s-late", verifiedTurnId="turn-old",
        ))
    assert resp.isError is False
    tracker.start_step.assert_not_called(), (
        "late callback's step must not be recorded onto the newest turn's task"
    )
    assert any("drop late tool step" in rec.message for rec in caplog.records)

    # Same callback, verified as the ACTIVE turn → step recorded as before.
    resp = await dispatch_tool(PiToolRequest(
        toolCallId="tc-live", name="webgis_execute", arguments={"toolName": "buffer_analysis", "arguments": {}},
        sessionId="s-late", verifiedTurnId="turn-new",
    ))
    assert resp.isError is False
    tracker.start_step.assert_called_once_with("task-new-turn", "buffer_analysis", {})
    tracker.complete_step.assert_called_once()

    # Unverified direct in-process calls (verifiedTurnId=None) keep legacy behavior.
    resp = await dispatch_tool(PiToolRequest(
        toolCallId="tc-direct", name="webgis_execute", arguments={"toolName": "buffer_analysis", "arguments": {}},
        sessionId="s-late",
    ))
    assert resp.isError is False
    assert tracker.start_step.call_count == 2


# ─── #994: 合成计划步骤的工具绑定打勾语义 ──────────────────────────────────


class _Registry994:
    """buffer_analysis / search_poi registered; search_poi declares chinese."""

    def metadata(self, name):
        return {"domains": ["chinese"]} if name == "search_poi" else {"domains": []}

    def list_tools(self):
        return ["buffer_analysis", "search_poi"]


def test_advance_step_bound_step_ignores_unrelated_tool():
    """A bound step must NOT be ticked by an unrelated registered analysis tool
    (the old core-wildcard let 3 unrelated calls 'complete' a 3-step plan)."""
    orch = AgentPlanOrchestrator()
    sid = "s-994-bind"
    plan = Plan(
        intent="成都的大学分布",
        domains=["chinese"],
        steps=[
            PlanStep(n=1, goal="搜索大学 POI", tool_family="core",
                     tool_binding=["search_poi"]),
            PlanStep(n=2, goal="缓冲分析", tool_family="core"),
        ],
    )
    orch.set_plan(sid, plan)
    reg = _Registry994()

    # buffer_analysis is registered and non-presentation — the OLD wildcard
    # would have ticked step 1; the binding must refuse it. The call may still
    # tick the next UNBOUND core step (advance_step walks undone steps in order).
    assert orch.advance_step(sid, "buffer_analysis", reg) == 2
    assert plan.steps[0].done is False, "unrelated tool must not tick a bound step"
    assert plan.steps[1].done is True

    # The bound tool ticks the bound step.
    assert orch.advance_step(sid, "search_poi", reg) == 1
    assert plan.steps[0].done is True


def test_advance_step_unbound_steps_keep_wildcard_semantics():
    """Steps without tool_binding fall back to the existing core wildcard /
    domain-overlap paths (LLM-planned steps carry no binding)."""
    orch = AgentPlanOrchestrator()
    sid = "s-994-unbound"
    plan = Plan(
        intent="热点分析",
        domains=["statistics"],
        steps=[PlanStep(n=1, goal="计算热点", tool_family="core")],
    )
    orch.set_plan(sid, plan)
    assert plan.steps[0].tool_binding is None
    assert orch.advance_step(sid, "buffer_analysis", _Registry994()) == 1


def test_binding_survives_projection_round_trip():
    """tool_binding must survive canonical ↔ projection conversion, otherwise a
    reloaded plan silently degrades back to wildcard matching."""
    orch = AgentPlanOrchestrator()
    canon = CanonicalPlan(
        plan_id="p1", session_id="s", intent="i", domains=["statistics"],
        steps=[
            CanonicalStep(id="s1", n=1, goal="g1", tool_family="core",
                          status=StepStatus.pending, tool_binding=["search_poi"]),
            CanonicalStep(id="s2", n=2, goal="g2", tool_family="core",
                          status=StepStatus.pending),
        ],
    )
    proj = orch._to_projection(canon)
    assert proj.steps[0].tool_binding == ["search_poi"]
    assert proj.steps[1].tool_binding is None

    back = orch._canonical_from_projection(proj, "s")
    assert back.steps[0].tool_binding == ["search_poi"]
    assert back.steps[1].tool_binding is None
