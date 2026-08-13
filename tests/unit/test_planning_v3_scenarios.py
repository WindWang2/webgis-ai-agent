"""Deterministic scenario suite for the planning-architecture convergence.

Implements the 10 adversarial scenarios from goal §20 /
``.planning/swarm/G-report.md`` against the integrated implementation
(``app/services/planning/*`` + ``plan_orchestrator`` + ``plan_mode`` +
``execution_engine`` + ``tool_dispatch_service``).

Determinism constraints (see ``.planning/swarm/F-report.md`` §3-4, §6):
- The LLM boundary is mocked at ``app.services.chat.planner.call_llm`` ONLY —
  never ``app.services.chat.llm_client.call_llm`` (planner.py re-exports the
  name; patching the wrong seam silently hits the network).
- A real ``ToolRegistry`` with a small set of inline-registered fake tools
  carrying proper tier/domains metadata (mirrors tests/unit/test_plan_mode.py:21-47).
- In-memory session store (conftest sets ``USE_REDIS=false``) — no network,
  no Redis, no DB.
- No timing assertions: concurrency is proven structurally (a rendezvous
  event) and execution order via call logs / counters.
- The module-level ``plan_orchestrator`` / ``plan_store`` singletons are shared
  across the whole suite: unique ``sess-*`` ids per test + explicit teardown.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.tools.registry import ToolRegistry
from app.services import plan_mode as plan_mode_svc
from app.services.tool_catalog import DOMAIN_KEYWORDS, ToolCatalog
from app.services.chat import planner as planner_mod
from app.services.chat.context_assembler import ChatContextAssembler
from app.services.chat.execution_engine import ChatExecutionEngine
from app.services.chat.llm_client import LLMConfig
from app.services.chat.plan_orchestrator import (
    AgentPlanOrchestrator,
    Plan,
    PlanStep,
    should_plan,
)
from app.services.planning import (
    CanonicalPlan,
    CanonicalStep,
    FollowUpKind,
    PlanStatus,
    StepStatus,
    classify_followup,
)
from app.services.planning.capability import validate_plan_capabilities
from app.services.planning.store import plan_store
from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import ToolDispatchService


# ─── shared fixtures / helpers ─────────────────────────────────────────


@pytest.fixture
def env():
    """Real ToolRegistry with a small set of inline fake tools + call log.

    Tools carry tier/domains metadata (design-v3 §capability derives from it).
    ``calls`` counts dispatches per tool, ``log`` records dispatch order,
    ``received`` records the args each tool actually saw (for ref-resolution
    assertions). All three are fresh per test.
    """
    r = ToolRegistry()
    calls: dict[str, int] = {}
    log: list[str] = []
    received: dict = {}

    def _record(name: str) -> None:
        calls[name] = calls.get(name, 0) + 1
        log.append(name)

    @r.tool(name="fetch_data", description="获取指定关键词的 POI 数据",
            tier=2, domains=["chinese"])
    def fetch_data(keyword: str, count: int = 10) -> dict:
        _record("fetch_data")
        received["fetch_data"] = {"keyword": keyword, "count": count}
        return {"success": True, "data": {"keyword": keyword, "count": count}}

    @r.tool(name="buffer_analysis", description="缓冲分析",
            tier=1, domains=[])
    def buffer_analysis(radius: float, source: dict) -> dict:
        _record("buffer_analysis")
        received["buffer_analysis"] = {"radius": radius, "source": source}
        return {"success": True, "data": {"radius": radius, "source": source}}

    @r.tool(name="hotspot_analysis", description="热点分析",
            tier=2, domains=["statistics"])
    def hotspot_analysis(points: list) -> dict:
        _record("hotspot_analysis")
        received["hotspot_analysis"] = {"points": points}
        return {"success": True, "data": {"hot_count": len(points), "points": points}}

    @r.tool(name="cartography_render", description="制图渲染",
            tier=2, domains=["report"])
    def cartography_render(layers: list) -> dict:
        _record("cartography_render")
        received["cartography_render"] = {"layers": layers}
        return {"success": True, "data": {"rendered": len(layers)}}

    @r.tool(name="compute_ndvi", description="NDVI 计算",
            tier=2, domains=["raster"])
    def compute_ndvi(area: str) -> dict:
        _record("compute_ndvi")
        received["compute_ndvi"] = {"area": area}
        return {"success": True, "data": {"ndvi": 0.42, "area": area}}

    @r.tool(name="plan_route", description="驾车路线规划",
            tier=2, domains=["network"])
    def plan_route(origin: str, destination: str) -> dict:
        _record("plan_route")
        received["plan_route"] = {"origin": origin, "destination": destination}
        return {"success": True, "data": {"origin": origin, "destination": destination}}

    # 样式/图层类工具：与真实 webgis_layer_upsert 一致，不声明任何 domain。
    @r.tool(name="webgis_layer_upsert", description="图层样式/更新",
            tier=1, domains=[])
    def webgis_layer_upsert(layer_ref: str, color: str = "blue") -> dict:
        _record("webgis_layer_upsert")
        received["webgis_layer_upsert"] = {"layer_ref": layer_ref, "color": color}
        return {"success": True, "layer_id": f"lyr-{layer_ref}", "color": color}

    # 视图/相机类工具：与真实 map_view.set_map_view 一致，不声明任何 domain。
    @r.tool(name="set_map_view", description="调整视图",
            tier=1, domains=[])
    def set_map_view(zoom: float = 12) -> dict:
        _record("set_map_view")
        received["set_map_view"] = {"zoom": zoom}
        return {"success": True, "command": "set_map_view", "params": {"zoom": zoom}}

    return SimpleNamespace(
        registry=r,
        catalog=ToolCatalog(r),
        calls=calls,
        log=log,
        received=received,
    )


def _make_engine(env) -> ChatExecutionEngine:
    """Minimal ChatExecutionEngine without deps (F-report §4: __new__ + attrs)."""
    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    engine.base_url = "http://localhost:9999"
    engine.model = "m"
    engine.api_key = "k"
    engine.use_prompt_caching = False
    engine.catalog = env.catalog
    engine.registry = env.registry
    return engine


@pytest.fixture
def cfg():
    return LLMConfig(base_url="http://x", model="m", api_key="k")


def _patch_registry(monkeypatch, env):
    """Route orchestrator capability validation to OUR fake registry so results
    are independent of any globally injected registry (F-report §6)."""
    monkeypatch.setattr(
        "app.services.chat.plan_orchestrator._get_registry", lambda: env.registry
    )


# ─────────────────────────────────────────────────────────────────────────
# 场景 1：单工具简单任务 —— should_plan 不过度规划；无计划时不污染 prompt
# ─────────────────────────────────────────────────────────────────────────


def test_scenario01_short_simple_followup_does_not_overplan():
    """短简单消息 + 活跃计划 → False（承接追问，不重新规划）。"""
    assert should_plan("换个颜色", [], has_active_plan=True) is False
    assert should_plan("放大一点", [], has_active_plan=True) is False
    # 无活跃计划时同样的短消息反而要规划（没有上文可承接）
    assert should_plan("换个颜色", [], has_active_plan=False) is True


@pytest.mark.asyncio
async def test_scenario01_single_tool_task_produces_single_step_plan(env, cfg, monkeypatch):
    """简单请求产出 1 步计划，而非过度拆分。"""
    _patch_registry(monkeypatch, env)

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content":
            '{"intent":"画个热力图","domains":["statistics"],'
            '"steps":[{"n":1,"goal":"热点分析","tool_family":"statistics"}]}'}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)
    sid = "sess-s01-single"
    plan = await planner_mod.make_plan(cfg, sid, "画个热力图", "[环境感知]")
    assert plan is not None
    assert len(plan.steps) == 1  # 不过度规划：简单任务只有 1 步
    assert plan.domains == ["statistics"]
    planner_mod.clear_plan(sid)
    await plan_store.clear(sid)


@pytest.mark.asyncio
async def test_scenario01_no_plan_block_pollutes_prompt(env):
    """无计划时 [执行计划] 块不出现；有计划时才渲染（context_assembler 门控）。"""
    sid = "sess-s01-prompt"
    assembler = ChatContextAssembler()
    messages = [
        {"role": "system", "content": "你是 WebGIS 助手"},
        {"role": "user", "content": "画个热力图"},
    ]

    res = await assembler.assemble(sid, messages)
    joined = "\n".join(m.get("content", "") for m in res.to_messages())
    assert "[执行计划]" not in joined  # 无计划 → 无计划块

    planner_mod.set_plan(sid, Plan(
        intent="画个热力图", domains=["statistics"],
        steps=[PlanStep(n=1, goal="热点分析", tool_family="statistics")],
    ))
    res2 = await assembler.assemble(sid, messages)
    joined2 = "\n".join(m.get("content", "") for m in res2.to_messages())
    assert "[执行计划]" in joined2
    planner_mod.clear_plan(sid)
    await plan_store.clear(sid)


# ─────────────────────────────────────────────────────────────────────────
# 场景 2：多步链（获取数据 → buffer → hotspot → cartography），${stepId} 引用
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario02_multi_step_chain_executes_in_order_with_refs(env):
    sid = "sess-s02-chain"
    plan = plan_mode_svc.PlanProposal(
        title="数据链路",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="fetch_data",
                                   args={"keyword": "医院", "count": 5}),
            plan_mode_svc.PlanStep(id="s2", tool="buffer_analysis",
                                   args={"radius": 500, "source": "${s1.data}"}),
            plan_mode_svc.PlanStep(id="s3", tool="hotspot_analysis",
                                   args={"points": ["${s2.data.source.count}"]}),
            plan_mode_svc.PlanStep(id="s4", tool="cartography_render",
                                   args={"layers": ["${s3.data.hot_count}"]}),
        ],
    )
    # propose 期校验通过（DAG + 静态引用均干净）
    assert plan_mode_svc.validate_plan(plan, set(env.registry.list_tools())) is None
    assert plan_mode_svc.validate_static_refs(plan) == []

    plan_id = await plan_mode_svc.store_plan(sid, plan)
    result = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert result["success"] is True
    assert result["executed"] == ["s1", "s2", "s3", "s4"]
    # 严格串行链 → dispatch 顺序即声明顺序
    assert env.log == ["fetch_data", "buffer_analysis", "hotspot_analysis", "cartography_render"]
    # ${stepId[.path]} 引用逐级解析
    assert env.received["buffer_analysis"]["source"] == {"keyword": "医院", "count": 5}
    assert env.received["hotspot_analysis"]["points"] == [5]      # s2.data.source.count
    assert env.received["cartography_render"]["layers"] == [1]    # s3.data.hot_count
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "completed"


# ─────────────────────────────────────────────────────────────────────────
# 场景 3：两个独立步骤并发，join 步骤收到两者结果
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario03_independent_steps_run_concurrently_then_join(env):
    sid = "sess-s03-concurrent"
    both_entered = asyncio.Event()
    entered = {"n": 0}

    async def _rendezvous(name: str) -> None:
        """两个独立步骤必须同时在飞行中；串行实现会在此处超时失败。"""
        entered["n"] += 1
        if entered["n"] == 2:
            both_entered.set()
        try:
            await asyncio.wait_for(both_entered.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            raise AssertionError(f"step {name} was not dispatched concurrently")

    @env.registry.tool(name="slow_fetch_a", description="独立步骤 A")
    async def slow_fetch_a(tag: str) -> dict:
        await _rendezvous("s1")
        env.log.append("slow_fetch_a")
        return {"success": True, "data": {"tag": tag}}

    @env.registry.tool(name="slow_fetch_b", description="独立步骤 B")
    async def slow_fetch_b(tag: str) -> dict:
        await _rendezvous("s2")
        env.log.append("slow_fetch_b")
        return {"success": True, "data": {"tag": tag}}

    plan = plan_mode_svc.PlanProposal(
        title="并发+join",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="slow_fetch_a", args={"tag": "a"}),
            plan_mode_svc.PlanStep(id="s2", tool="slow_fetch_b", args={"tag": "b"}),
            plan_mode_svc.PlanStep(id="s3", tool="hotspot_analysis",
                                   args={"points": ["${s1.data.tag}", "${s2.data.tag}"]},
                                   depends_on=["s1", "s2"]),
        ],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    result = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert result["success"] is True, f"plan failed: {result}"
    assert result["executed"] == ["s1", "s2", "s3"]
    # join 步骤同时收到 s1、s2 的结果（ref 解析证明）
    assert result["results"]["s3"]["data"]["hot_count"] == 2
    assert sorted(env.log[:2]) == ["slow_fetch_a", "slow_fetch_b"]
    assert env.log[2:] == ["hotspot_analysis"]


# ─────────────────────────────────────────────────────────────────────────
# 场景 4：第 2 步参数校验失败 → validation / correct_args，修正后重试放行
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario04_validation_failure_classified_and_retry_allowed(env):
    sid = "sess-s04-validate"
    service = ToolDispatchService(registry=env.registry)
    executed: set = set()

    bad_tc = {"id": "c1", "function": {"name": "fetch_data",
                                       "arguments": json.dumps({"keyword": "医院", "count": "oops"})}}
    r1 = await service.dispatch(bad_tc, sid, executed)
    assert r1.status == "error"
    assert r1.raw_result.get("code") == "VALIDATION_ERROR"
    failure_class, recovery_action = ChatExecutionEngine._classify_failure(r1)
    assert failure_class == "validation"
    assert recovery_action == "correct_args"

    # dedup 在失败后释放：同 (name, args) 重试绝不会被谎报"已成功执行"
    r2 = await service.dispatch(bad_tc, sid, executed)
    assert r2.status == "error"
    assert "重复调用" not in r2.llm_payload

    # 修正参数后的重试放行并成功
    ok_tc = {"id": "c3", "function": {"name": "fetch_data",
                                      "arguments": json.dumps({"keyword": "医院", "count": 3})}}
    r3 = await service.dispatch(ok_tc, sid, executed)
    assert r3.status == "ok"
    assert env.received["fetch_data"]["count"] == 3


# ─────────────────────────────────────────────────────────────────────────
# 场景 5：工具不可用（未注册）—— 能力校验拦截 + 分类 + 绝不打勾
# ─────────────────────────────────────────────────────────────────────────


def test_scenario05_capability_validation_flags_unregistered_tool(env):
    plan = CanonicalPlan(
        plan_id="p-unknown",
        session_id="sess-s05-cap",
        intent="x",
        steps=[CanonicalStep(id="s1", n=1, goal="g", tool="nonexistent_tool",
                             tool_family="statistics")],
    )
    issues = validate_plan_capabilities(plan, env.registry)
    assert any("unregistered tool 'nonexistent_tool'" in i for i in issues)


@pytest.mark.asyncio
async def test_scenario05_dispatch_unregistered_tool_classifies_unavailable(env):
    service = ToolDispatchService(registry=env.registry)
    tc = {"id": "c1", "function": {"name": "nonexistent_tool_xyz", "arguments": "{}"}}
    r = await service.dispatch(tc, "sess-s05-disp", set())
    assert r.status == "error"
    assert r.raw_result.get("code") == "UNKNOWN_TOOL"
    failure_class, recovery_action = ChatExecutionEngine._classify_failure(r)
    assert failure_class == "tool_unavailable"
    assert recovery_action in ("alternate_tool", "replan_remaining")


def test_scenario05_unregistered_tool_does_not_tick_plan_step(env):
    """R1/R5：幻觉工具绝不打勾计划步骤（metadata 兜底不再伪装成 core 域）。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-s05-tick"
    plan = Plan(
        intent="热点分析", domains=["statistics"],
        steps=[PlanStep(n=1, goal="热点", tool_family="statistics")],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "nonexistent_tool_xyz", env.registry) is None
    assert plan.steps[0].done is False
    orch.clear_plan(sid)


# ─────────────────────────────────────────────────────────────────────────
# 场景 6：悬空引用 ${s1.nonexistent.path} → missing_ref + 修正提示（不静默 None）
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario06_missing_ref_fails_with_hint(env):
    sid = "sess-s06-ref"
    plan = plan_mode_svc.PlanProposal(
        title="badpath",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "医院"}),
            plan_mode_svc.PlanStep(id="s2", tool="buffer_analysis",
                                   args={"radius": 500, "source": "${s1.nonexistent.path}"}),
        ],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    result = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert result["success"] is False
    assert result["failed_step"] == "s2"
    assert result["failure_class"] == "missing_ref"
    assert result["recovery_action"] == "reuse_ref"
    assert "nonexistent.path" in result["error"]   # 修正提示点名坏路径
    assert "s1" in result["error"]                 # 及可用 step keys
    assert "None" not in str(result["results"])    # 没有任何静默 None 落进结果
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "partially_completed"  # s1 已成功 → 部分完成（P2-1）
    assert data["__failure_class__"] == "missing_ref"


# ─────────────────────────────────────────────────────────────────────────
# 场景 7：部分成功 → resume：已完成步骤不重跑、引用复用、剩余步骤完成
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario07_partial_success_resume_reuses_refs(env):
    sid = "sess-s07-resume"
    s2_received: dict = {}

    @env.registry.tool(name="flaky_hotspot", description="第一次失败第二次成功")
    def flaky_hotspot(points: list) -> dict:
        env.calls["flaky_hotspot"] = env.calls.get("flaky_hotspot", 0) + 1
        s2_received["points"] = points
        if env.calls["flaky_hotspot"] == 1:
            # 瞬时网络失败（transient_network）→ 可恢复重试，不触发 livelock guard
            return {"success": False, "code": "TOOL_ERROR", "message": "网络超时 连接失败"}
        return {"success": True, "data": {"hot_count": len(points)}}

    plan = plan_mode_svc.PlanProposal(
        title="resume",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="fetch_data",
                                   args={"keyword": "医院", "count": 5}),
            plan_mode_svc.PlanStep(id="s2", tool="flaky_hotspot",
                                   args={"points": ["${s1.data.count}"]}),
            plan_mode_svc.PlanStep(id="s3", tool="cartography_render",
                                   args={"layers": ["${s2.data.hot_count}"]}),
        ],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)

    r1 = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r1["success"] is False
    assert r1["failed_step"] == "s2"
    assert r1["failure_class"] == "transient_network"
    assert r1["executed"] == ["s1"]
    # 失败也持久化了已完成结果（resume 的数据基础）
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "partially_completed"  # s1 已成功（P2-1）
    assert "s1" in data["__step_results__"]

    r2 = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r2["success"] is True, f"resume failed: {r2}"
    assert r2["executed"] == ["s1", "s2", "s3"]
    assert r2["results"]["s3"]["data"]["rendered"] == 1
    # s1 绝不被重新 dispatch（引用从上次结果复用）
    assert env.calls["fetch_data"] == 1
    assert env.calls["flaky_hotspot"] == 2
    assert env.calls["cartography_render"] == 1
    # resume 用的 s1 结果即上次持久化的结果
    assert s2_received["points"] == [5]
    data2 = await plan_mode_svc.load_plan(sid, plan_id)
    assert data2["__status__"] == "completed"


# ─────────────────────────────────────────────────────────────────────────
# 场景 8：纯样式追问（换成蓝色）→ style_change → 不重规划、不重跑分析、
#         样式工具调用不打勾任何计划步骤（core 通配移除）
# ─────────────────────────────────────────────────────────────────────────


def test_scenario08_style_followup_classified_and_skips_planning(env):
    kind = classify_followup(
        "换成蓝色",
        has_active_plan=True,
        active_domains=["statistics"],
        session_has_refs=True,
        domain_keywords=DOMAIN_KEYWORDS,
    )
    assert kind == FollowUpKind.style_change
    assert should_plan("换成蓝色", [], has_active_plan=True, followup_kind=kind) is False
    # 旧启发式路径（不传 followup_kind）同样不重规划
    assert should_plan("换成蓝色", [], has_active_plan=True) is False


@pytest.mark.asyncio
async def test_scenario08_style_turn_no_reanalysis_and_no_new_plan(env, monkeypatch):
    _patch_registry(monkeypatch, env)
    sid = "sess-s08-style"
    # 已完成分析部分、计划仍活跃（step2 pending → 非终态）+ 会话已有数据 ref
    plan = Plan(
        intent="热点分析", domains=["statistics"],
        steps=[
            PlanStep(n=1, goal="热点", tool_family="statistics", done=True),
            PlanStep(n=2, goal="再热点", tool_family="statistics"),
        ],
    )
    orch = AgentPlanOrchestrator()
    orch.set_plan(sid, plan)
    await orch.flush(sid)
    plan_id = plan_store.peek(sid).plan_id
    await session_data_manager.store(sid, {"geojson": "fc"}, prefix="geojson")

    called = {"n": 0}
    async def fake_call_llm(*_a, **_k):
        called["n"] += 1
        return {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)

    engine = _make_engine(env)
    result = await engine._maybe_plan(sid, "换成蓝色", [])
    assert result is None
    assert called["n"] == 0            # 规划 LLM 绝未被调用（无重分析）
    canon = await plan_store.load_current(sid)
    assert canon is not None and canon.plan_id == plan_id  # 原计划原封未动
    assert canon.steps[1].status == StepStatus.pending
    planner_mod.clear_plan(sid)
    await plan_store.clear(sid)


def test_scenario08_style_tool_call_does_not_tick_plan_step(env):
    """R1：样式/视图类工具（无 domain）不通过 core 通配打勾任何步骤。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-s08-tick"
    plan = Plan(
        intent="热点分析", domains=["statistics"],
        steps=[
            PlanStep(n=1, goal="热点", tool_family="statistics"),
            PlanStep(n=2, goal="再热点", tool_family="statistics"),
        ],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "hotspot_analysis", env.registry) == 1
    # 样式工具调用：无 domain 重叠 → 不打勾，返回 None
    assert orch.advance_step(sid, "webgis_layer_upsert", env.registry) is None
    assert plan.steps[1].done is False
    orch.clear_plan(sid)


# ─────────────────────────────────────────────────────────────────────────
# 场景 9：用户改目标 → new_goal → 强制重规划；旧计划 superseded；sticky 重置
# ─────────────────────────────────────────────────────────────────────────


def test_scenario09_new_goal_classified_and_forces_planning(env):
    # 短消息换目标："换成查路线" —— 含计划域（raster）之外的 network 关键词
    kind = classify_followup(
        "换成查路线",
        has_active_plan=True,
        active_domains=["raster"],
        session_has_refs=True,
        domain_keywords=DOMAIN_KEYWORDS,
    )
    assert kind == FollowUpKind.new_goal
    assert should_plan("换成查路线", [], has_active_plan=True, followup_kind=kind) is True
    # 新领域关键词（路线 → network ∉ active raster）同样强制重规划
    kind2 = classify_followup(
        "再查一下成都到重庆的路线",
        has_active_plan=True,
        active_domains=["raster"],
        session_has_refs=False,
        domain_keywords=DOMAIN_KEYWORDS,
    )
    assert kind2 == FollowUpKind.new_goal


@pytest.mark.asyncio
async def test_scenario09_new_goal_supersedes_and_resets_sticky(env, monkeypatch):
    _patch_registry(monkeypatch, env)
    sid = "sess-s09-goal"
    responses = iter([
        '{"intent":"NDVI 分析","domains":["raster"],"steps":[{"n":1,"goal":"计算 NDVI","tool_family":"raster"}]}',
        '{"intent":"路线规划","domains":["network"],"steps":[{"n":1,"goal":"规划路线","tool_family":"network"}]}',
    ])

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content": next(responses)}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)
    engine = _make_engine(env)

    # 第 1 轮：raster 计划 + raster 领域 sticky
    p1 = await engine._maybe_plan(sid, "分析一下植被覆盖", [])
    assert p1 is not None and p1.domains == ["raster"]
    old_id = (await plan_store.load_current(sid)).plan_id
    env.catalog.select_schemas("NDVI", session_id=sid)  # 模拟 raster 粘性
    assert env.catalog.active_domains(sid) == {"raster"}

    # 第 2 轮：换成查路线 → new_goal → 强制重规划
    p2 = await engine._maybe_plan(sid, "换成查路线", [])
    assert p2 is not None and p2.intent == "路线规划"
    # 旧 canonical 绝不静默覆盖：标记 superseded 并可寻址
    hist = await plan_store.get_by_id(sid, old_id)
    assert hist is not None and hist.status == PlanStatus.superseded
    assert (await plan_store.load_current(sid)).plan_id != old_id
    # design-v3 §5：换目标 → 旧领域 sticky 重置，不再污染工具选择
    assert env.catalog.active_domains(sid) == set()
    planner_mod.clear_plan(sid)
    await plan_store.clear(sid)


# ─────────────────────────────────────────────────────────────────────────
# 场景 10：模拟重启 → 从 store 恢复计划（步骤/状态完整）；终态计划不复活
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario10_restart_restores_plan_with_step_status(env, monkeypatch):
    _patch_registry(monkeypatch, env)
    sid = "sess-s10-restart"
    orch = AgentPlanOrchestrator()
    plan = Plan(
        intent="热点分析", domains=["statistics"],
        steps=[
            PlanStep(n=1, goal="热点", tool_family="statistics"),
            PlanStep(n=2, goal="再热点", tool_family="statistics"),
        ],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "hotspot_analysis", env.registry) == 1
    await orch.flush(sid)  # 只完成 step1 → 计划保持非终态（proposed）
    # 模拟 worker 重启：进程内缓存（orchestrator LRU + plan_store cache）全清
    plan_store.clear_cache()
    orch.clear_plan(sid)

    fresh = AgentPlanOrchestrator()
    restored = await fresh.restore_plan(sid)
    assert restored is not None
    assert restored.intent == "热点分析"
    assert restored.steps[0].done is True          # 打勾进度保留
    assert restored.steps[1].done is False         # 未完成步骤保持未完成

    loaded = await plan_store.load_current(sid)
    assert loaded is not None
    assert loaded.steps[0].status == StepStatus.completed
    assert loaded.steps[1].status == StepStatus.pending
    assert loaded.status == PlanStatus.proposed     # 非终态 → 可继续推进
    await plan_store.clear(sid)


@pytest.mark.asyncio
async def test_scenario10_terminal_plan_not_resurrected_as_active(env):
    sid = "sess-s10-terminal"
    await plan_store.save(CanonicalPlan(
        plan_id="p-terminal",
        session_id=sid,
        intent="已完成的分析",
        status=PlanStatus.completed,
        steps=[CanonicalStep(id="s1", n=1, goal="g", status=StepStatus.completed)],
    ))
    plan_store.clear_cache()  # 模拟重启：store 缓存清空，只留持久化副本
    fresh = AgentPlanOrchestrator()
    assert await fresh.restore_plan(sid) is None   # 终态计划不作为活跃计划恢复
    assert fresh.get_plan(sid) is None
    await plan_store.clear(sid)


# ─────────────────────────────────────────────────────────────────────────
# P1-A：core 限定通配（qualified wildcard）——已注册非展示分析工具打勾 core 步骤；
# 展示类/幻觉工具绝不打勾。fixture 已与生产对齐（tier-1 分析工具 domains=[]）。
# ─────────────────────────────────────────────────────────────────────────


def test_p1a_core_step_ticks_on_registered_no_domain_analysis_tool(env):
    """(a) core 步骤：已注册、无 domain 的分析工具（buffer_analysis）必须打勾——
    生产注册表没有工具声明 core domain，纯 domain 重叠会让 core 步骤永远无法推进。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-p1a-tick"
    plan = Plan(
        intent="缓冲分析", domains=["core"],
        steps=[PlanStep(n=1, goal="做缓冲", tool_family="core")],
    )
    orch.set_plan(sid, plan)
    assert env.registry.metadata("buffer_analysis")["domains"] == []  # 与生产一致
    assert orch.advance_step(sid, "buffer_analysis", env.registry) == 1
    assert plan.steps[0].done is True
    orch.clear_plan(sid)


def test_p1a_core_step_does_not_tick_on_presentation_tool(env):
    """(b) core 步骤：展示类工具（webgis_layer_upsert，样式/图层更新）绝不打勾。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-p1a-presentation"
    plan = Plan(
        intent="缓冲分析", domains=["core"],
        steps=[PlanStep(n=1, goal="做缓冲", tool_family="core")],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "webgis_layer_upsert", env.registry) is None
    assert plan.steps[0].done is False
    # 视图类同样不打勾
    assert orch.advance_step(sid, "set_map_view", env.registry) is None
    assert plan.steps[0].done is False
    orch.clear_plan(sid)


def test_p1a_core_step_does_not_tick_on_unregistered_hallucinated_tool(env):
    """(c) core 步骤：幻觉/未注册工具名绝不打勾。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-p1a-halluc"
    plan = Plan(
        intent="缓冲分析", domains=["core"],
        steps=[PlanStep(n=1, goal="做缓冲", tool_family="core")],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "hallucinated_tool_xyz", env.registry) is None
    assert plan.steps[0].done is False
    orch.clear_plan(sid)


def test_p1a_presentation_tool_normalized_legacy_name_still_excluded(env):
    """展示类工具经 legacy 名规范化后（set_layer_style → webgis_layer_upsert）
    依然在排除集内，core 步骤不打勾。"""
    orch = AgentPlanOrchestrator()
    sid = "sess-p1a-legacy"
    plan = Plan(
        intent="制图", domains=["core"],
        steps=[PlanStep(n=1, goal="加图层", tool_family="core")],
    )
    orch.set_plan(sid, plan)
    assert orch.advance_step(sid, "set_layer_style", env.registry) is None
    assert plan.steps[0].done is False
    orch.clear_plan(sid)


def test_p1a_real_registry_metadata_check():
    """对真实注册表（init_tools）做聚焦元数据检查，防止 fixture/排除集与生产漂移：
    - 没有任何工具声明 domain "core"（0/149）；
    - tier-1 分析工具（buffer_analysis）已注册且 domains=[]；
    - PRESENTATION_TOOLS 里每个名字都是真实注册工具（防打字错误）；"""
    from app.tools import init_tools as _init_tools
    from app.services.chat.plan_orchestrator import PRESENTATION_TOOLS

    r = ToolRegistry()
    _init_tools(r)
    metas = r.all_metadata()
    registered = set(r.list_tools())
    assert "buffer_analysis" in registered
    assert metas["buffer_analysis"]["domains"] == []
    # 生产无任何工具声明 core domain（core 通配必须靠 qualified 规则）
    assert not any("core" in m.get("domains", []) for m in metas.values())
    unknown = PRESENTATION_TOOLS - registered
    assert not unknown, f"PRESENTATION_TOOLS 含未注册工具: {sorted(unknown)}"
    # 展示类工具本身无 domain，靠排除集拦截
    for t in ("webgis_layer_upsert", "set_map_view", "fly_to_location", "webgis_view_set"):
        assert t in registered
        assert metas[t]["domains"] == []


# ─────────────────────────────────────────────────────────────────────────
# P0-1：update_plan_status 在 ref 被逐出（LRU 容量淘汰）后重新 mint + 别名，
# 绝不静默丢写；持有旧 plan_id 的调用方仍能读到最新 payload。
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p0_1_update_status_remints_after_eviction(env):
    """模拟 overwrite() 返回 False（ref 在读写间隙被逐出/后端降级）：update_plan_status
    必须重新 mint + set_alias，load_plan(旧 plan_id) 仍可寻址到最新 payload——绝不静默丢写。"""
    from app.services.session_data import MemorySessionStore
    from app.services import plan_mode as _svc

    class _EvictingOverwriteStore(MemorySessionStore):
        """get 能读到，但 overwrite 一律失败（模拟 ref 在 read→write 间隙被逐出）。"""

        async def overwrite(self, session_id, ref_id, data):
            return False

    tiny = _EvictingOverwriteStore(capacity=100)
    sid = "sess-p01-evict"
    payload = {
        "__kind__": "plan_proposal", "__status__": "pending", "__updated_at__": 1.0,
        "title": "t", "summary": "", "steps": [{"id": "s1", "tool": "x", "args": {}}],
    }
    plan_id = await tiny.store(sid, payload, prefix="plan")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.services.plan_mode.session_data_manager", tiny)
    try:
        await _svc.update_plan_status(sid, plan_id, __status__="completed")
        loaded = await _svc.load_plan(sid, plan_id)
        assert loaded is not None, "update_plan_status 在 overwrite 失败后不得静默丢写"
        assert loaded["__status__"] == "completed"
        assert loaded["__updated_at__"] > 1.0
        # 新 ref 是 ref:plan-*（重新 mint），旧 plan_id 经别名可寻址
        refs = await tiny.list_refs(sid)
        assert any(str(r).startswith("ref:plan-") for r in refs)
        assert any(a == plan_id for a in refs.values())
    finally:
        monkeypatch.undo()


# ─────────────────────────────────────────────────────────────────────────
# P1-C：per-plan 执行锁（TOCTOU 双派发）、stale-running 可恢复、
# 终态写入不覆盖 superseded/cancelled、OperationCancelled → cancelled。
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p1c_concurrent_execute_serialized_per_plan(env):
    """同一计划的两个并发 execute_plan：第一个拿到 per-plan 锁执行；第二个阻塞到
    锁释放后重读，直接返回已完成结果——**绝无双派发**（TOCTOU 修复）。"""
    sid = "sess-p1c-lock"
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    @env.registry.tool(name="slow_lock_tool", description="慢工具")
    async def slow_lock_tool(tag: str) -> dict:
        calls["n"] += 1
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)
        return {"success": True, "data": {"tag": tag}}

    plan = plan_mode_svc.PlanProposal(
        title="lock",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="slow_lock_tool", args={"tag": "a"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)

    async def _run() -> dict:
        return await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)

    t1 = asyncio.create_task(_run())
    await asyncio.wait_for(entered.wait(), timeout=5.0)  # t1 已进入执行（持有锁）
    t2 = asyncio.create_task(_run())  # 并发第二发：阻塞等锁
    release.set()  # 放行 t1
    r1 = await asyncio.wait_for(t1, timeout=5.0)
    r2 = await asyncio.wait_for(t2, timeout=5.0)
    assert r1["success"] is True and r1["status"] == "completed"
    # t2 在锁后重读 → 直接返回已存结果（completed），不重新 dispatch
    assert r2["success"] is True and r2["status"] == "completed"
    assert calls["n"] == 1  # 绝无双派发


@pytest.mark.asyncio
async def test_p3_execute_claims_cross_process_plan_lock(env, monkeypatch):
    """P3 延后项 #2：execute_plan 的 claim+run 关键段必须套在分布式锁
    session_lock(f"plan:{plan_id}") 内——Redis 在时跨进程/跨 pod 原子 claim，
    测试（in-process 兜底）行为不变。此处用记录型 stub 断言锁键与加锁路径。"""
    from app.services import plan_mode as _svc

    acquired: list[str] = []

    class _AioLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeLockRegistry:
        def lock(self, key):
            acquired.append(key)
            return _AioLock()

    monkeypatch.setattr("app.services.plan_mode.session_lock_registry", _FakeLockRegistry())

    sid = "sess-p3-planlock"
    plan = plan_mode_svc.PlanProposal(
        title="lock-claim",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "医院"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    r = await _svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is True
    # 锁键按 plan_id 全局唯一（无需 session 前缀）
    assert acquired == [f"plan:{plan_id}"]
    # 进程内 per-plan 锁仍保留（双保险，单 worker 不退化）
    assert f"{sid}\0{plan_id}" in _svc._PLAN_EXEC_LOCKS


@pytest.mark.asyncio
async def test_p1c_stale_running_is_resumable(env):
    """running 状态超过阈值（或 __updated_at__ 缺失）→ 视为 crashed，允许 resume。"""
    sid = "sess-p1c-stale"
    plan = plan_mode_svc.PlanProposal(
        title="stale",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "医院"}),
        ],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    # 人为写成 running + 陈旧时间戳（crashed 形态）
    import time as _time
    await plan_mode_svc.update_plan_status(
        sid, plan_id, __status__="running", __updated_at__=_time.time() - 1000,
    )
    r = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is True
    assert r["status"] == "completed"
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "completed"


@pytest.mark.asyncio
async def test_p1c_fresh_running_still_blocks(env):
    """running + 新鲜时间戳 → 拒绝执行（在飞）。"""
    sid = "sess-p1c-fresh"
    plan = plan_mode_svc.PlanProposal(
        title="fresh",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "x"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    await plan_mode_svc.update_plan_status(sid, plan_id, __status__="running")
    r = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is False
    assert "已在执行中" in r["error"]


@pytest.mark.asyncio
async def test_p1c_cancelled_plan_refuses_resume(env):
    """cancelled（terminal）计划：resume 必须拒绝，绝不重跑。"""
    sid = "sess-p1c-cancelled"
    plan = plan_mode_svc.PlanProposal(
        title="cancel",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "x"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    await plan_mode_svc.update_plan_status(sid, plan_id, __status__="cancelled")
    r = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is False
    assert r["status"] == "cancelled"
    assert "已取消" in r["error"]
    assert env.calls.get("fetch_data", 0) == 0


@pytest.mark.asyncio
async def test_p1c_operation_cancelled_writes_terminal_cancelled(env):
    """工具抛 OperationCancelled → 写终态 cancelled（不是 failed），返回 status=cancelled。"""
    from app.services.jobs.cancellation import OperationCancelled

    sid = "sess-p1c-opcancel"

    @env.registry.tool(name="cancel_tool", description="抛取消")
    def cancel_tool() -> dict:
        raise OperationCancelled("用户取消")

    plan = plan_mode_svc.PlanProposal(
        title="opcancel",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="cancel_tool", args={})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    r = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is False
    assert r["status"] == "cancelled"
    assert r["failure_class"] == "cancelled"
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "cancelled"  # terminal，不是 failed


@pytest.mark.asyncio
async def test_p1c_terminal_write_does_not_overwrite_superseded(env):
    """执行中途计划被 supersede → 终态写入（completed）不得覆盖 superseded。"""
    sid = "sess-p1c-super-mid"
    entered = asyncio.Event()
    release = asyncio.Event()

    @env.registry.tool(name="slow_super_tool", description="慢工具")
    async def slow_super_tool(tag: str) -> dict:
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)
        return {"success": True, "data": {"tag": tag}}

    plan = plan_mode_svc.PlanProposal(
        title="mid-super",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="slow_super_tool", args={"tag": "a"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)

    async def _run() -> dict:
        return await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)

    t1 = asyncio.create_task(_run())
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    # 运行中被 supersede（模拟：直接改状态，等价 supersede_active_plans 的效果）
    await plan_mode_svc.update_plan_status(sid, plan_id, __status__="superseded")
    release.set()
    await asyncio.wait_for(t1, timeout=5.0)
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "superseded"  # 终态写入未覆盖


# ─────────────────────────────────────────────────────────────────────────
# P2-1：失败状态 partially_completed / failed；确定性失败 livelock guard；
# superseded 且无结果 → success=False。
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p2_1_deterministic_failure_not_rerun_on_resume(env):
    """livelock guard：首次失败为确定性失败（failure_class != transient_network）时，
    resume 直接返回存储的失败，绝不重跑同一工具。"""
    sid = "sess-p2-1-livelock"
    env.calls["deterministic_fail"] = 0

    @env.registry.tool(name="deterministic_fail", description="永远失败")
    def deterministic_fail() -> dict:
        env.calls["deterministic_fail"] += 1
        return {"success": False, "code": "VALIDATION_ERROR", "message": "参数非法"}

    plan = plan_mode_svc.PlanProposal(
        title="livelock",
        steps=[
            plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "医院"}),
            plan_mode_svc.PlanStep(id="s2", tool="deterministic_fail", args={}),
        ],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)

    r1 = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r1["success"] is False
    assert r1["failed_step"] == "s2"
    assert r1["failure_class"] == "validation"
    assert env.calls["deterministic_fail"] == 1

    r2 = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r2["success"] is False
    assert r2["failed_step"] == "s2"
    assert r2["failure_class"] == "validation"
    assert env.calls["deterministic_fail"] == 1  # 绝不被重跑
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "partially_completed"


@pytest.mark.asyncio
async def test_p2_1_superseded_with_empty_results_returns_failure(env):
    """superseded / legacy 计划且无任何已存结果 → success=False + 明确消息
    （不再假装 success=True + executed=[]）。"""
    sid = "sess-p2-1-super-empty"
    plan = plan_mode_svc.PlanProposal(
        title="old",
        steps=[plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={"keyword": "x"})],
    )
    plan_id = await plan_mode_svc.store_plan(sid, plan)
    await plan_mode_svc.update_plan_status(sid, plan_id, __status__="superseded")
    r = await plan_mode_svc.execute_plan_async(sid, plan_id, env.registry)
    assert r["success"] is False
    assert r["status"] == "superseded"
    assert "取代" in r["error"]
    assert env.calls.get("fetch_data", 0) == 0


# ─────────────────────────────────────────────────────────────────────────
# P2-6：session_has_refs 必须排除计划自身 ref（ref:plan-* / plan 别名）
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p2_6_session_has_refs_excludes_plan_refs(env):
    """仅存了计划 ref（ref:plan-* / plan-current 别名）的会话 → session_has_refs
    必须为 False，不得把"刚规划完"误判成 ref_reuse 承接。"""
    from app.services.chat.execution_engine import _has_non_plan_refs
    from app.services.planning.models import CanonicalPlan as _CP

    sid = "sess-p2-6-planrefs"
    # 存一个 plan-mode 计划（ref:plan-* 前缀）+ canonical 计划（plan-current 别名）
    await plan_mode_svc.store_plan(
        sid, plan_mode_svc.PlanProposal(
            title="p", steps=[plan_mode_svc.PlanStep(id="s1", tool="fetch_data", args={})],
        )
    )
    await plan_store.save(_CP(plan_id="p-can", session_id=sid, intent="x"))
    refs = await session_data_manager.list_refs(sid)
    assert _has_non_plan_refs(refs) is False, f"计划 ref 不应算作数据 ref: {refs}"
    # 存一条真实数据 ref → True
    await session_data_manager.store(sid, {"geojson": 1}, prefix="geojson")
    refs2 = await session_data_manager.list_refs(sid)
    assert _has_non_plan_refs(refs2) is True
    await plan_store.clear(sid)
