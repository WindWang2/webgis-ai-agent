"""H-1..H-9（#856-#864）harness 优化回归测试。

覆盖:
- H-1: 规划确定性短路(寒暄最简门 + 高置信 harness 合成 0 次 LLM 调用)
- H-2: legacy 回合总时长预算(流式/非流式, failure_class=turn_timeout)
- H-5: 规划等待期抢占式取消
- H-6: 流式空白补全不算成功
- H-8: harness 前门工具对 proximity/network 类请求可见
- H-9: 产品阶段 evidence 与真实选择参数同源
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.chat_engine import ChatEngine
from app.services.chat import planner as planner_mod
from app.services.chat.execution_engine import TurnTimeoutError
from app.services.chat.plan_orchestrator import plan_orchestrator
from app.services.chat import plan_orchestrator as plan_orchestrator_module
from app.services.gis_harness.tools import register_gis_harness_tools
from app.services.tool_catalog import ToolCatalog
from app.tools.registry import ToolRegistry


# ─── 公共 fixture ─────────────────────────────────────────────────────────

def _make_registry_with_echo_tool() -> ToolRegistry:
    r = ToolRegistry()

    @r.tool(name="echo_tool", description="echo", tier=1)
    def echo_tool(text: str = "") -> dict:
        return {"echo": text, "success": True}

    return r


async def _fake_save(*a, **k):
    return None


async def _fake_get_or_create_session(session_id, user_id=None):
    return [{"role": "system", "content": "sys"}]


async def _fake_maybe_plan(*a, **k):
    return None


async def _fake_generate_title(*a, **k):
    return None


def _tool_call_event(name: str = "echo_tool", args: str = '{"text": "a"}'):
    return {
        "id": "call_1",
        "function": {"name": name, "arguments": args},
    }


# ─── H-1: 规划确定性短路 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h1_chitchat_skips_planning_llm(monkeypatch):
    """寒暄消息（短 + 无域关键词 + unclear）→ 不规划、不调 LLM。"""
    calls = []

    async def fake_make_plan(*a, **k):
        calls.append(1)
        return None

    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    result = await plan_orchestrator.orchestrate_plan(
        object(), "sess-h1-chitchat", "你好", [], "env"
    )
    assert result is None
    assert calls == []  # 0 次 LLM 规划调用


@pytest.mark.asyncio
async def test_h1_high_confidence_synthesizes_without_llm(monkeypatch):
    """「成都小学分布情况」类高置信请求 → harness 确定性合成计划，0 次 LLM。"""
    calls = []

    async def fake_make_plan(*a, **k):
        calls.append(1)
        return None

    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    # 全量套件污染隔离：更早的测试可能经 agent_pi_bridge 注入过受限的 stub
    # registry（缺 poi_query 系工具）——合成会把能力标 unavailable 而回落
    # LLM。钉 None（available 未知 → 不标 unavailable）与本用例单元语义一致。
    monkeypatch.setattr(plan_orchestrator_module, "_get_registry", lambda: None)
    result = await plan_orchestrator.orchestrate_plan(
        object(), "sess-h1-synth", "成都小学分布情况", [], "env"
    )
    assert result is not None
    assert calls == []  # 确定性合成，不付规划 LLM 调用
    assert result.steps, "合成计划必须有可执行步骤"
    assert result.recipe_id, "harness 附着 recipe_id"
    assert isinstance(result.gis_intent, dict), "harness 附着 gis_intent"
    assert "statistics" in result.domains or result.domains
    # 步骤 goal 均来自 purpose_map（非空可读文案）
    assert all(s.goal for s in result.steps)
    plan_orchestrator.clear_plan("sess-h1-synth")


@pytest.mark.asyncio
async def test_h1_low_confidence_falls_back_to_llm(monkeypatch):
    """无任务规则命中（fallback）的长消息 → 回落 LLM 规划。"""
    calls = []

    async def fake_make_plan(cfg, session_id, message, env):
        calls.append(message)
        return None

    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    vague = "请帮我处理一下这些内容，给出有价值的建议和结论"
    await plan_orchestrator.orchestrate_plan(
        object(), "sess-h1-llm", vague, [], "env"
    )
    assert calls == [vague]  # LLM 路径被触发


# ─── H-2: 回合总时长预算 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h2_stream_turn_total_timeout(monkeypatch):
    """流式回合超总预算 → turn_timeout 诚实收尾（task_error + done）。"""
    eng = ChatEngine(_make_registry_with_echo_tool())
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    eng._turn_total_timeout_s = 0.2

    async def fake_stream(*a, **k):
        await asyncio.sleep(0.13)  # 两轮即越过 0.2s 预算
        yield ("done", {"message": {"content": "", "tool_calls": [_tool_call_event()]}})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)

    events = []
    async for e in eng.chat_stream("查询", session_id="s-h2-stream"):
        events.append(e)
    assert any("task_error" in e for e in events)
    assert not any("task_complete" in e for e in events)
    assert any("总预算" in e or "总时长" in e for e in events if "task_error" in e)
    tasks = eng.tracker.list_by_session("s-h2-stream")
    assert tasks and tasks[-1].status.value == "failed"


@pytest.mark.asyncio
async def test_h2_nostream_turn_total_timeout(monkeypatch):
    """非流式回合超总预算 → TurnTimeoutError(failure_class=turn_timeout)。"""
    eng = ChatEngine(_make_registry_with_echo_tool())
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)
    eng._turn_total_timeout_s = 0.1

    async def slow_call_llm(*a, **k):
        await asyncio.sleep(0.08)
        return {"choices": [{"message": {"content": "", "tool_calls": [_tool_call_event()]}}]}

    monkeypatch.setattr(eng, "_call_llm", slow_call_llm)

    with pytest.raises(TurnTimeoutError):
        await eng.chat("查询", session_id="s-h2-nostream")
    tasks = eng.tracker.list_by_session("s-h2-nostream")
    assert tasks and tasks[-1].status.value == "failed"


# ─── H-5: 规划等待期抢占式取消 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_h5_cancel_during_planning_takes_effect_fast(monkeypatch):
    """规划等待期取消 → 秒级生效（不等 120s planner 返回）。"""
    eng = ChatEngine(_make_registry_with_echo_tool())
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)

    async def slow_plan(*a, **k):
        await asyncio.sleep(5.0)
        return None

    monkeypatch.setattr(eng, "_maybe_plan", slow_plan)

    started = time.monotonic()
    gen = eng.chat_stream("查询", session_id="s-h5-cancel")
    cancelled_seen = False
    async for e in gen:
        if "task_start" in e:
            tasks = eng.tracker.list_by_session("s-h5-cancel")
            assert tasks, "task_start 后 tracker 必须已有任务"
            eng.tracker.cancel(tasks[-1].id)
        if "task_cancelled" in e:
            cancelled_seen = True
            break
    elapsed = time.monotonic() - started
    await gen.aclose()
    assert cancelled_seen, "必须以 task_cancelled 收尾"
    assert elapsed < 2.5, f"取消应在规划期内立即生效（实际 {elapsed:.1f}s）"


# ─── H-6: 流式空白补全 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h6_stream_whitespace_only_completion_fails(monkeypatch):
    """纯空白补全（"  \\n"）在流式路径同样算空补全 → task_error。"""
    eng = ChatEngine(_make_registry_with_echo_tool())
    monkeypatch.setattr(eng, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", _fake_save)
    monkeypatch.setattr(eng, "_maybe_plan", _fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", _fake_generate_title)

    async def fake_stream(*a, **k):
        yield ("done", {"message": {"content": "  \n", "tool_calls": None}})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)
    events = []
    async for e in eng.chat_stream("hi", session_id="s-h6-ws"):
        events.append(e)
    assert any("task_error" in e for e in events), "空白补全必须诚实失败"
    assert not any("task_complete" in e for e in events)


# ─── H-8: 前门工具域覆盖 ─────────────────────────────────────────────────

def test_h8_harness_frontdoor_domains_cover_task_family():
    """webgis_map_intent/product 的 domains 必须覆盖 proximity(network) /
    change_detection(temporal) 任务族——此前只标 statistics/report。"""
    r = ToolRegistry()
    register_gis_harness_tools(r)
    meta = r.all_metadata()
    for name in ("webgis_map_intent", "webgis_map_product"):
        domains = set(meta[name]["domains"])
        assert "network" in domains, f"{name} 缺 network 域（proximity 任务族不可见）"
        assert "temporal" in domains, f"{name} 缺 temporal 域（change_detection 任务族不可见）"


def test_h8_frontdoor_selected_for_proximity_query():
    """「距离学校500米以内的地铁站」只激活 network 域 → 前门工具必须可见。"""
    r = ToolRegistry()
    register_gis_harness_tools(r)
    catalog = ToolCatalog(r)
    schemas = catalog.select_schemas("距离学校500米以内的地铁站有哪些")
    names = {s["function"]["name"] for s in schemas}
    assert "webgis_map_intent" in names, f"proximity 类请求必须看到意图前门（实际 {sorted(names)}）"


# ─── H-9: 产品阶段 evidence 与真实选择同源 ────────────────────────────────

@pytest.mark.asyncio
async def test_h9_product_stage_reports_unavailable_consistently():
    """注册表缺真实数据工具时，产品阶段能力状态与意图阶段一致（unavailable）。"""
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    r = ToolRegistry()
    register_gis_harness_tools(r)  # 只有 3 个 harness 工具，无 poi_query 工具
    available = set(r.list_tools())

    intent = resolve_map_request_intent("成都小学分布情况")
    planner = MapProductPlanner()
    plan = planner.plan_from_intent(
        intent, available_tools=available or None
    )
    poi_req = next(
        (d for d in plan.data_requirements if d.capability == "poi_query"), None
    )
    assert poi_req is not None
    assert poi_req.status == "unavailable", (
        "注册表无 poi_query 工具时必须诚实报告 unavailable（而非 pending）"
    )


def test_h9_evidence_candidates_share_selection_source():
    """evidence 候选序与真实选择同源（源码级回归锚点）：
    - plan_from_intent 收到注册表可见工具（available_tools=...）；
    - 候选选择与 evidence 记录共用同一份带 project_verified 的取数。"""
    from app.services.gis_harness import tools as harness_tools

    src = open(harness_tools.__file__, encoding="utf-8").read()
    assert "available_tools=available or None" in src, (
        "webgis_map_product 必须把注册表可见工具传给 plan_from_intent（H-9）"
    )
    assert "project_verified=_verified" in src, (
        "候选选择必须带 project_verified（H-9）"
    )
    assert "c.id for c in _candidates" in src, (
        "evidence 候选必须来自与决策同源的一次取数（H-9）"
    )


# ─── 2026-08-25 会话回归：product-* 直写图层必须带 name ──────────────────

def test_product_layers_carry_display_names():
    """webgis_map_product 补层（heatmap/points）必须给图层 stamp 名称。

    无名的 product-* 层在前端面板只能显示 id 后缀（product-930ab45f-
    points），用户认不出哪行是 POI 查询结果；且 spec 无 name 时前端镜像
    行的命名链退到算法名。源码级回归锚点（与 H-9 同模式）。"""
    from app.services.gis_harness import tools as harness_tools

    src = open(harness_tools.__file__, encoding="utf-8").read()
    assert 'converted["name"] = f"{title}·密度热力图" if title else "密度热力图"' in src, (
        "product heatmap 层必须带语义名（title 前缀可选）"
    )
    assert 'converted["name"] = f"{title}·点位分布" if title else "点位分布图"' in src, (
        "product points 层必须带语义名（title 前缀可选）"
    )
