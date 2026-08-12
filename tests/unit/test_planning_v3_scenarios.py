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
            tier=1, domains=["core"])
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
    assert data["__status__"] == "failed"
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
            return {"success": False, "code": "TOOL_ERROR", "message": "第一次失败"}
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
    assert r1["executed"] == ["s1"]
    # 失败也持久化了已完成结果（resume 的数据基础）
    data = await plan_mode_svc.load_plan(sid, plan_id)
    assert data["__status__"] == "failed"
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
