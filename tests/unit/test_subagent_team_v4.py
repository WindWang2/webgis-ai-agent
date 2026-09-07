"""ADR-0104 决策 7（Wave 6）：专家子代理团队运行时测试。

覆盖：
- 角色注册表完备性（9 专家角色，含既有等价名 reconcile，不重复）；
- SubagentRole 新字段 expected_outputs / failure_behavior（有界 + 词汇表校验）
  与注册表重复/身份校验；
- role∩caller 收紧不变量（域交集、预算取更严者、无 role 时行为不变）；
- spawn_subagent 工具 role 参数 fail-closed（未知角色诚实拒绝，默认路径不变）；
- 并行委派：信号量有界、父预算 roll-up、失败隔离（一子失败不炸兄弟）、
  诚实 settle（completed/partial/failed）、kill switch、递归深度护栏、
  子代理之间的协作式取消检查点（当前令牌 + 既有任务注册表 is_cancelled）。

确定性：全部用 stub 子引擎（不打 LLM、不导入 chat_engine —— 该导入链
依赖 model_runtime 包初始化，测试探针按文件路径独立加载 roles.py）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from app.services.subagent import (
    SUBAGENT_MAX_PARALLEL_CHILDREN,
    SUBAGENT_PARALLEL_CONCURRENCY,
    SubagentDispatcher,
    SubagentTaskSpec,
)
from app.services.subagent_roles import (
    ROLE_FAILURE_BEHAVIORS,
    SUBAGENT_ROLES,
    BudgetExceeded,
    SubagentBudget,
    SubagentRole,
    get_subagent_role,
    validate_subagent_role_registry,
)
from app.tools.registry import ToolRegistry

#: ADR-0104 决策 7 新增的五角色（+ planner/corpus_worker 细化）
NEW_V4_ROLES = (
    "spatial_scientist",
    "algorithm_reviewer",
    "map_observer",
    "result_verifier",
    "doc_crosschecker",
    "planner",
    "corpus_worker",
)

#: 9 专家角色 ↔ 注册表名 reconcile（审计口径：既有等价名可接受，不重复造）
V4_SPECIALIST_ACCEPTED_NAMES = {
    "planner": {"planner"},
    "data_inspector": {"gis_inspector"},
    "cartography_reviewer": {"cartography_reviewer"},
    "corpus_worker": {"corpus_worker"},
    "spatial_scientist": {"spatial_scientist"},
    "algorithm_reviewer": {"algorithm_reviewer"},
    "map_observer": {"map_observer"},
    "result_verifier": {"result_verifier", "tool_result_verifier"},
    "doc_crosschecker": {"doc_crosschecker"},
}


def _load_model_roles_standalone():
    """按文件路径独立加载 model_runtime/roles.py（绕开包 __init__ 的
    routing↔llm_client 导入环 —— roles.py 本身只依赖标准库）。"""
    path = Path(__file__).resolve().parents[2] / "app" / "services" / "chat" / "model_runtime" / "roles.py"
    spec = importlib.util.spec_from_file_location("_v4_roles_probe", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 处理需要 cls.__module__ 可解析
    spec.loader.exec_module(mod)
    return mod


# ─── fixtures ───────────────────────────────────────────────


@pytest.fixture()
def reg():
    """真实 ToolRegistry（描述符 side_effect 分类齐全，供 fail-closed 过滤）。"""
    r = ToolRegistry()
    r.register(
        name="search_poi", description="查询 POI",
        func=lambda q: {"success": True}, tier=1, side_effect="cacheable_read",
    )
    r.register(
        name="stats_analyze", description="统计",
        func=lambda d: {"success": True}, tier=2, domains=["statistics"],
        side_effect="deterministic_compute",
    )
    r.register(
        name="raster_calc", description="栅格",
        func=lambda d: {"success": True}, tier=2, domains=["raster"],
        side_effect="deterministic_compute",
    )
    r.register(
        name="webgis_layer_remove", description="删层（突变）",
        func=lambda i: {"success": True}, tier=2, domains=["mapspec"],
        side_effect="state_mutation",
    )
    r.register(
        name="webgis_cartography_status", description="地图观察（纯读）",
        func=lambda i: {"success": True}, tier=2, domains=["report", "mapspec"],
        side_effect="pure",
    )
    return r


class _StubEngine:
    """模拟子引擎：chat() 先做 n_calls 次 dispatch 再返回 content。

    dispatch 内无 await —— 子代理执行无让渡，运行顺序确定性。
    """

    def __init__(self, n_calls=0, content="done", fail=None, gate: asyncio.Event | None = None):
        self.max_rounds = 10
        self.calls = {"n": 0}
        self.last_message = None
        outer = self

        class _Svc:
            async def dispatch(self, tc, session_id, executed_tools=None):
                outer.calls["n"] += 1
                if outer._fail_at is not None and outer.calls["n"] >= outer._fail_at:
                    raise outer._fail_exc
                return {"status": "ok"}

        self.dispatch_service = _Svc()
        self._n_calls = n_calls
        self._content = content
        self._fail_at = None if fail is None else fail[0]
        self._fail_exc = None if fail is None else fail[1]
        self._gate = gate

    async def chat(self, message, session_id):
        self.last_message = message
        if self._gate is not None:
            await self._gate.wait()
        for _ in range(self._n_calls):
            await self.dispatch_service.dispatch(
                {"function": {"name": "search_poi", "arguments": "{}"}}, session_id,
            )
        return {"content": self._content, "reasoning": ""}


def _engine_factory(behaviors: list[dict]):
    """每次 _build_sub_engine 产出一个新 stub；built 记录已构建的引擎。"""
    built: list[_StubEngine] = []

    def factory(subset, rounds):
        eng = _StubEngine(**behaviors[min(len(built), len(behaviors) - 1)])
        built.append(eng)
        return eng

    return factory, built


# ─── 角色注册表完备性 ────────────────────────────────────────


def test_v4_specialist_registry_complete():
    """9 专家角色齐备（既有等价名 reconcile，无重复定义）。"""
    for specialist, accepted in V4_SPECIALIST_ACCEPTED_NAMES.items():
        assert accepted & set(SUBAGENT_ROLES), f"缺少专家角色: {specialist}"
    # 既有六角色不被破坏
    for legacy in ("data_researcher", "gis_inspector", "scientific_reviewer",
                   "cartography_reviewer", "tool_result_verifier", "cheap_summarizer"):
        assert legacy in SUBAGENT_ROLES
    validate_subagent_role_registry()


def test_new_roles_declare_full_contract():
    """每个新角色显式声明：预算、突变策略、模型角色、期望输出、失败行为。"""
    roles_mod = _load_model_roles_standalone()
    for name in NEW_V4_ROLES:
        role = get_subagent_role(name)
        assert role.max_rounds > 0
        assert role.max_tool_calls > 0
        assert role.max_wall_time_s > 0
        assert role.allow_mutation is False, name
        assert 0 < len(role.expected_outputs) <= 8, name
        assert role.failure_behavior in ROLE_FAILURE_BEHAVIORS, name
        # 模型角色必须命中既有 model_runtime 角色表（routing 经既有 profile）
        assert role.model_role in roles_mod.DEFAULT_ROLE_PROFILES, name


def test_spatial_scientist_scope_excludes_map_mutation():
    role = get_subagent_role("spatial_scientist")
    assert "mapspec" not in role.allowed_domains          # 无 style/map 突变面
    assert role.allow_mutation is False                    # fail-closed 突变过滤


def test_map_observer_scope_observation_only():
    role = get_subagent_role("map_observer")
    assert role.allow_mutation is False
    assert "webgis_layer_remove" in role.tool_blacklist
    assert "webgis_map_intent" in role.tool_blacklist


def test_role_new_fields_validated_fail_closed():
    base = dict(name="probe", title="探针", model_role="subagent_worker")
    # 默认值：旧构造方式保持合法
    r = SubagentRole(**base)
    assert r.expected_outputs == ()
    assert r.failure_behavior == "honest_failure_disclosed"
    # failure_behavior 词汇表外 → ValueError
    with pytest.raises(ValueError, match="failure_behavior"):
        SubagentRole(**base, failure_behavior="yolo")
    # expected_outputs：非 tuple 可协同为 tuple；超量/非字符串/超长 → ValueError
    r2 = SubagentRole(**base, expected_outputs=["a", "b"])
    assert r2.expected_outputs == ("a", "b")
    with pytest.raises(ValueError, match="上限"):
        SubagentRole(**base, expected_outputs=tuple(str(i) for i in range(9)))
    with pytest.raises(ValueError, match="非空字符串"):
        SubagentRole(**base, expected_outputs=("ok", 3))
    with pytest.raises(ValueError, match="上限"):
        SubagentRole(**base, expected_outputs=("x" * 121,))


def test_registry_validation_rejects_identity_drift_and_duplicates():
    role_a = SubagentRole(name="role_a", title="A", model_role="subagent_worker")
    role_b = SubagentRole(name="role_b", title="B", model_role="subagent_worker")
    # key 与 role.name 漂移 → ValueError
    with pytest.raises(ValueError, match="身份不一致"):
        validate_subagent_role_registry({"role_a": role_b})
    # 不同 key、除 name 外逐字段相同（重复定义）→ ValueError
    role_a2 = SubagentRole(name="role_a2", title="A", model_role="subagent_worker")
    with pytest.raises(ValueError, match="重复定义"):
        validate_subagent_role_registry({"role_a": role_a, "role_a2": role_a2})
    # 正常注册表通过
    validate_subagent_role_registry({"role_a": role_a, "role_b": role_b})


# ─── role∩caller 收紧不变量 ─────────────────────────────────


@pytest.mark.asyncio
async def test_role_narrows_caller_domains_and_rounds(reg):
    """角色只收紧：域取交集（不允许经参数越权放宽）、轮次取更严者。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-narrow")
    captured = {}

    def fake_build(subset, rounds):
        captured["names"] = {s["function"]["name"] for s in subset}
        captured["rounds"] = rounds
        return _StubEngine(content="ok")

    dispatcher._build_sub_engine = fake_build
    result = await dispatcher.run(
        task="分析", domains=["mapspec", "statistics"],
        max_rounds=50, role="spatial_scientist",
    )
    assert result.success is True
    assert "stats_analyze" in captured["names"]           # 交集命中
    assert "webgis_layer_remove" not in captured["names"] # 域交集 + 突变过滤双保险
    assert "raster_calc" not in captured["names"]         # 调用方未给的域不会放开
    assert captured["rounds"] == 10                       # 角色 10 < 调用方 50


@pytest.mark.asyncio
async def test_no_role_keeps_legacy_behavior(reg):
    """kill switch/兼容：不传 role → 无域收紧、无轮次收紧（旧路径不变）。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-legacy")
    captured = {}

    def fake_build(subset, rounds):
        captured["names"] = {s["function"]["name"] for s in subset}
        captured["rounds"] = rounds
        return _StubEngine(content="ok")

    dispatcher._build_sub_engine = fake_build
    result = await dispatcher.run(task="x", domains=["mapspec"], max_rounds=50)
    assert result.success is True
    assert captured["rounds"] == 50
    assert "webgis_layer_remove" in captured["names"]     # 无角色 → 突变面照旧


@pytest.mark.asyncio
async def test_expected_outputs_injected_into_task_header(reg):
    """expected_outputs 注入子代理任务头；旧角色/无角色任务文本不含该头。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-outputs")
    built_holder = {}

    def fake_build_for(eng_box):
        def factory(subset, rounds):
            eng = _StubEngine(content="ok")
            eng_box["engine"] = eng
            return eng
        return factory

    dispatcher._build_sub_engine = fake_build_for(built_holder)
    await dispatcher.run(task="核验", role="result_verifier")
    msg = built_holder["engine"].last_message
    assert "[期望输出]" in msg
    assert "completion_verdict" in msg

    dispatcher2 = SubagentDispatcher(reg, "sess-v4-outputs2")
    box2 = {}
    dispatcher2._build_sub_engine = fake_build_for(box2)
    await dispatcher2.run(task="摘要", role="cheap_summarizer")  # 旧角色无 expected_outputs
    assert "[期望输出]" not in box2["engine"].last_message
    assert "[角色]" in box2["engine"].last_message               # 旧角色头保留

    dispatcher3 = SubagentDispatcher(reg, "sess-v4-outputs3")
    box3 = {}
    dispatcher3._build_sub_engine = fake_build_for(box3)
    await dispatcher3.run(task="adhoc")
    assert "[角色]" not in box3["engine"].last_message           # 无角色 → 原文


# ─── spawn_subagent 工具：role fail-closed ───────────────────


@pytest.fixture()
def tool_registry():
    from app.tools.subagent import register_subagent_tools

    r = ToolRegistry()
    register_subagent_tools(r)
    return r


@pytest.mark.asyncio
async def test_spawn_tool_unknown_role_fails_closed(tool_registry):
    result = await tool_registry.dispatch(
        "spawn_subagent", {"task": "x", "role": "ninja_hacker"},
        session_id="sess-v4-tool",
    )
    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert "ninja_hacker" in result["message"]
    assert "spatial_scientist" in result["message"]       # 诚实列出可用角色


@pytest.mark.asyncio
async def test_spawn_tool_blank_role_fails_closed(tool_registry):
    result = await tool_registry.dispatch(
        "spawn_subagent", {"task": "x", "role": "   "}, session_id="sess-v4-tool",
    )
    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_spawn_tool_description_documents_role_budgets(tool_registry):
    """工具描述逐角色披露预算（有界：每角色一行）。"""
    schemas = tool_registry.get_schemas_subset({"spawn_subagent"})
    desc = schemas[0]["function"]["description"]
    for name in SUBAGENT_ROLES:
        assert name in desc, name
    assert "墙钟≤90s" in desc                             # doc_crosschecker 的预算
    assert "只读" in desc


def test_spawn_tool_no_role_args_unchanged_shape(tool_registry):
    """兼容：无 role 的 schema 参数集合 = 旧集合 + role（可选，默认 None）。"""
    schemas = tool_registry.get_schemas_subset({"spawn_subagent"})
    params = schemas[0]["function"]["parameters"]["properties"]
    assert set(params) >= {"task", "domains", "extra_tools", "max_rounds", "role"}
    assert "role" not in schemas[0]["function"]["parameters"].get("required", [])


# ─── 并行委派 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_completion_with_parent_budget_rollup(reg):
    """父预算 roll-up：子代理工具调用计入父预算（8 次全部成功）。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-ok")
    factory, built = _engine_factory([{"n_calls": 4}, {"n_calls": 4}])
    dispatcher._build_sub_engine = factory
    parent = SubagentBudget(max_tool_calls=8, max_heavy_tool_calls=4, max_wall_time_s=60)
    batch = await dispatcher.run_parallel(
        [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b")],
        parent_budget=parent,
    )
    assert batch.status == "completed"
    assert all(r.success for r in batch.results)
    assert batch.budget_usage["tool_calls"] == 8
    assert batch.results[0].summary == "done"


@pytest.mark.asyncio
async def test_parallel_budget_exhaustion_is_honest_and_partial(reg):
    """父预算耗尽：超限子代理诚实失败（budget_exceeded:tools），兄弟不受影响，
    父 settle 为 partial。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-cap")
    factory, built = _engine_factory([{"n_calls": 4}, {"n_calls": 4}])
    dispatcher._build_sub_engine = factory
    parent = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=2, max_wall_time_s=60)
    batch = await dispatcher.run_parallel(
        [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b")],
        parent_budget=parent,
    )
    assert batch.status == "partial"
    assert batch.results[0].success is True               # 兄弟完成，未被取消
    assert batch.results[1].success is False
    assert batch.results[1].error == "budget_exceeded:tools"
    assert batch.budget_usage["tool_calls"] == 6          # 第 6 次调用触发超限


@pytest.mark.asyncio
async def test_parallel_child_failure_does_not_cancel_siblings(reg):
    """失败隔离：一个子代理崩溃 → 诚实失败，兄弟照常完成。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-iso")
    factory, built = _engine_factory([
        {"n_calls": 2, "fail": (1, RuntimeError("boom"))},
        {"n_calls": 2, "content": "sibling-done"},
    ])
    dispatcher._build_sub_engine = factory
    batch = await dispatcher.run_parallel(
        [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b")],
    )
    assert batch.status == "partial"
    assert batch.results[0].success is False
    assert "boom" in (batch.results[0].error or "")
    assert batch.results[1].success is True
    assert batch.results[1].summary == "sibling-done"


@pytest.mark.asyncio
async def test_parallel_result_order_matches_specs(reg):
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-order")
    factory, built = _engine_factory([
        {"content": "R1"}, {"content": "R2"}, {"content": "R3"},
    ])
    dispatcher._build_sub_engine = factory
    batch = await dispatcher.run_parallel([
        SubagentTaskSpec(task="t1"), SubagentTaskSpec(task="t2"), SubagentTaskSpec(task="t3"),
    ])
    assert [r.summary for r in batch.results] == ["R1", "R2", "R3"]


@pytest.mark.asyncio
async def test_parallel_concurrency_bounded():
    """并发上限 clamp 到模块常量（防调用方放大）。"""
    assert SUBAGENT_PARALLEL_CONCURRENCY == 2
    assert SUBAGENT_MAX_PARALLEL_CHILDREN == 6


@pytest.mark.asyncio
async def test_parallel_bounds_fail_closed(reg):
    """空列表 / 超上限 / 未知角色 → fail-closed（不启动任何子代理）。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-bounds")
    factory, built = _engine_factory([{"n_calls": 1}])
    dispatcher._build_sub_engine = factory

    with pytest.raises(ValueError, match="至少一个"):
        await dispatcher.run_parallel([])
    with pytest.raises(ValueError, match="上限"):
        await dispatcher.run_parallel(
            [SubagentTaskSpec(task=f"t{i}") for i in range(SUBAGENT_MAX_PARALLEL_CHILDREN + 1)]
        )
    with pytest.raises(ValueError, match="未知子代理角色"):
        await dispatcher.run_parallel([
            SubagentTaskSpec(task="ok"),
            SubagentTaskSpec(task="bad", role="ninja_hacker"),
        ])
    assert built == []                                    # 未启动任何子引擎


@pytest.mark.asyncio
async def test_parallel_recursion_depth_guard(reg, monkeypatch):
    """递归深度护栏在并行路径仍然生效（每个子代理诚实失败）。"""
    from app.services import subagent as subagent_module

    dispatcher = SubagentDispatcher(reg, "sess-v4-par-depth")
    factory, built = _engine_factory([{"n_calls": 1}])
    dispatcher._build_sub_engine = factory

    depth_token = subagent_module._subagent_depth.set(2)
    try:
        batch = await dispatcher.run_parallel(
            [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b")],
        )
    finally:
        subagent_module._subagent_depth.reset(depth_token)
    assert batch.status == "failed"
    assert len(batch.results) == 2
    assert all("recursion depth" in (r.error or "") for r in batch.results)
    assert built == []                                    # 深度护栏先于引擎构建


@pytest.mark.asyncio
async def test_parallel_cancellation_between_children(reg):
    """协作式取消：子代理之间检查当前令牌 → 剩余子代理不再启动，
    逐个返回 cancelled 诚实结果。"""
    from app.lib.cancellation import CancellationToken, registry as cancel_registry, use_token

    dispatcher = SubagentDispatcher(reg, "sess-v4-par-cancel")
    gate = asyncio.Event()
    factory, built = _engine_factory([{"gate": gate}, {"n_calls": 1}, {"n_calls": 1}])
    dispatcher._build_sub_engine = factory

    tok = CancellationToken(job_id="v4-team-cancel")
    cancel_registry.register("v4-team-cancel", tok)

    async def scenario():
        with use_token(tok):
            task = asyncio.create_task(dispatcher.run_parallel(
                [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b"),
                 SubagentTaskSpec(task="c")],
                concurrency=1,
            ))
            while len(built) < 1:
                await asyncio.sleep(0.01)
            tok.cancel("user abort")
            return await task

    batch = await scenario()
    gate.set()
    assert batch.results[0].error == "cancelled"          # 在跑的子代理诚实取消
    assert batch.results[1].error == "cancelled"          # 未启动的子代理诚实取消
    assert batch.results[2].error == "cancelled"
    assert len(built) == 1                                # 后续子代理从未启动
    assert batch.status == "failed"
    assert batch.results[1].summary.startswith("子代理已取消")


@pytest.mark.asyncio
async def test_parallel_cancellation_via_task_registry(reg):
    """取消传播走既有任务注册表：current token 未直接取消，但
    cancellation.registry.is_cancelled(job_id) 命中 → 同样协作停止。"""
    from app.lib.cancellation import CancellationToken, registry as cancel_registry, use_token

    dispatcher = SubagentDispatcher(reg, "sess-v4-par-regcancel")
    gate = asyncio.Event()
    factory, built = _engine_factory([{"gate": gate}, {"n_calls": 1}])
    dispatcher._build_sub_engine = factory

    # current token 持有同一 job_id 但本身未被取消；注册表内的 token 被取消
    current_tok = CancellationToken(job_id="v4-registry-cancel")
    victim = cancel_registry.register("v4-registry-cancel", CancellationToken(job_id="v4-registry-cancel"))

    async def scenario():
        with use_token(current_tok):
            task = asyncio.create_task(dispatcher.run_parallel(
                [SubagentTaskSpec(task="a"), SubagentTaskSpec(task="b")],
                concurrency=1,
            ))
            while len(built) < 1:
                await asyncio.sleep(0.01)
            # 先取消（注册表路径），再放行在跑的子代理 A 正常完成；
            # B 在「子代理之间」检查点被拦截（registry.is_cancelled 命中）。
            cancel_registry.cancel("v4-registry-cancel", "registry abort")
            gate.set()
            return await task

    batch = await scenario()
    gate.set()
    assert victim.cancelled is True
    # 第一个子代理已完成（gate 释放后正常返回）；第二个在检查点被拦截
    assert batch.results[0].success is True
    assert batch.results[1].error == "cancelled"
    assert "registry" in batch.results[1].summary
    assert len(built) == 1


@pytest.mark.asyncio
async def test_parallel_kill_switch_disabled(reg, monkeypatch):
    """GIS_SUBAGENT_PARALLEL=0 → 诚实拒绝，不 spawn 任何子代理。"""
    dispatcher = SubagentDispatcher(reg, "sess-v4-par-kill")
    factory, built = _engine_factory([{"n_calls": 1}])
    dispatcher._build_sub_engine = factory
    monkeypatch.setenv("GIS_SUBAGENT_PARALLEL", "0")
    batch = await dispatcher.run_parallel([SubagentTaskSpec(task="a")])
    assert batch.status == "failed"
    assert batch.error is not None and "disabled" in batch.error
    assert batch.results == []
    assert built == []


# ─── 预算工具 ────────────────────────────────────────────────


def test_budget_remaining_wall_time_bounded():
    import time as _t

    b = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=1, max_wall_time_s=0.05)
    _t.sleep(0.08)
    assert b.remaining_wall_time_s() == 0.0
    with pytest.raises(BudgetExceeded):
        b.check_wall_time()


# ─── spawn_subagent parallel_tasks 接线（review R3 MAJOR）────────────────

@pytest.mark.asyncio
async def test_spawn_subagent_parallel_tasks_wired(tool_registry, monkeypatch):
    """parallel_tasks 经工具面路由到 run_parallel（≤6、fail-closed、批量结果）。"""
    sid = "sess-v4-parallel"
    calls = {"n": 0}

    class _FakeBatch:
        status = "completed"
        results = []

        def to_dict(self):
            calls["n"] += 1
            return {"success": True, "status": "completed", "results": []}

    async def _fake_run_parallel(self, specs, **kwargs):
        assert 1 <= len(specs) <= 6
        assert specs[0].task == "t1"
        assert specs[0].role == "result_verifier"
        return _FakeBatch()

    import app.services.subagent as sub_mod
    monkeypatch.setattr(
        sub_mod.SubagentDispatcher, "run_parallel", _fake_run_parallel,
    )
    res = await tool_registry.dispatch(
        "spawn_subagent",
        {"task": "ignored",
         "parallel_tasks": [{"task": "t1", "role": "result_verifier"},
                            {"task": "t2"}]},
        session_id=sid,
    )
    if not res.get("success"):
        raise AssertionError(f"unexpected dispatch result: {res}")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_spawn_subagent_parallel_tasks_validation(tool_registry):
    res = await tool_registry.dispatch(
        "spawn_subagent",
        {"task": "ignored", "parallel_tasks": [{"task": "t1"}] * 7},
        session_id="sess-v4-pv",
    )
    assert res["success"] is False and res["code"] == "VALIDATION_ERROR"
    res = await tool_registry.dispatch(
        "spawn_subagent",
        {"task": "ignored", "parallel_tasks": [{"role": "x"}]},
        session_id="sess-v4-pv",
    )
    assert res["success"] is False and res["code"] == "VALIDATION_ERROR"
    res = await tool_registry.dispatch(
        "spawn_subagent",
        {"task": "ignored", "parallel_tasks": [{"task": "t", "role": "nope"}]},
        session_id="sess-v4-pv",
    )
    assert res["success"] is False and res["code"] == "VALIDATION_ERROR"
