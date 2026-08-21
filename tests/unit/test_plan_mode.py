"""Plan Mode 端到端 + 内部辅助测试。

覆盖：
- DAG 校验（未知工具、自引用、缺失依赖、环）
- 占位符解析（${stepId}、${stepId.path.to.field}、嵌入式字符串、缺失字段）
- 波次并行执行（独立步骤并发 + 结果聚合；链式依赖保持拓扑序）
- 任一步失败中止（不再启动新波次）
- 拓扑排序（依赖打乱顺序）
- get_plan_status 状态查询
"""
import pytest

from app.tools.registry import ToolRegistry
from app.services import plan_mode as svc
from app.tools.plan_mode import register_plan_mode_tools


# ─── 测试用 registry：装几个简单工具 ──────────────────────────


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_plan_mode_tools(r)

    # 一个返回 bbox 的虚拟工具
    @r.tool(name="fake_get_bbox", description="返回固定 bbox")
    def fake_get_bbox(area: str) -> dict:
        return {"success": True, "data": {"area": area, "bbox": [116, 39, 117, 40]}}

    # 接收 bbox 返回点集合
    @r.tool(name="fake_query_points", description="按 bbox 查询点")
    def fake_query_points(bbox: list, count: int = 3) -> dict:
        pts = [{"x": bbox[0] + i, "y": bbox[1] + i} for i in range(count)]
        return {"success": True, "data": {"points": pts, "count": count}}

    # 接收点集合返回热点
    @r.tool(name="fake_hotspot", description="假装算热点")
    def fake_hotspot(points: list) -> dict:
        return {"success": True, "data": {"hot_count": len(points), "from": points}}

    # 永远失败的工具
    @r.tool(name="fake_always_fail", description="刻意失败")
    def fake_always_fail() -> dict:
        return {"success": False, "code": "TOOL_ERROR", "message": "deliberate fail"}

    return r


# ─── 占位符解析 ────────────────────────────────────────────────


def test_resolve_full_placeholder_returns_object():
    results = {"s1": {"data": {"bbox": [1, 2, 3, 4]}}}
    assert svc.resolve_refs("${s1.data.bbox}", results) == [1, 2, 3, 4]
    assert svc.resolve_refs("${s1}", results) == {"data": {"bbox": [1, 2, 3, 4]}}


def test_resolve_embedded_placeholder_stringifies():
    results = {"s1": {"area": "海淀"}}
    assert svc.resolve_refs("查询 ${s1.area} 区域", results) == "查询 海淀 区域"


def test_resolve_missing_step_returns_none():
    """单一占位符引用不存在的 step → None；嵌入式 → 空串。"""
    results = {}
    assert svc.resolve_refs("${ghost}", results) is None
    assert svc.resolve_refs("prefix-${ghost}-suffix", results) == "prefix--suffix"


def test_resolve_walks_nested_structures():
    results = {"s1": {"bbox": [10, 20]}}
    args = {
        "list_arg": ["${s1.bbox}", "static"],
        "dict_arg": {"x": "${s1.bbox.0}", "y": "${s1.bbox.1}"},
    }
    out = svc.resolve_refs(args, results)
    assert out["list_arg"][0] == [10, 20]
    assert out["dict_arg"]["x"] == 10
    assert out["dict_arg"]["y"] == 20


# ─── DAG 校验 ──────────────────────────────────────────────────


def test_validate_unknown_tool(registry):
    plan = svc.PlanProposal(
        title="x",
        steps=[svc.PlanStep(id="s1", tool="does_not_exist")],
    )
    err = svc.validate_plan(plan, set(registry.list_tools()))
    assert err is not None and "does_not_exist" in err


def test_validate_self_reference(registry):
    plan = svc.PlanProposal(
        title="x",
        steps=[svc.PlanStep(id="s1", tool="fake_get_bbox",
                            args={"area": "${s1}"})],
    )
    err = svc.validate_plan(plan, set(registry.list_tools()))
    assert err is not None and "自我引用" in err


def test_validate_forward_reference(registry):
    """先出现的 step 引用还没声明的 step。"""
    plan = svc.PlanProposal(
        title="x",
        steps=[
            svc.PlanStep(id="s1", tool="fake_query_points",
                         args={"bbox": "${s2.bbox}"}),
            svc.PlanStep(id="s2", tool="fake_get_bbox", args={"area": "北京"}),
        ],
    )
    err = svc.validate_plan(plan, set(registry.list_tools()))
    assert err is not None and "s2" in err


def test_validate_duplicate_step_id(registry):
    plan = svc.PlanProposal(
        title="x",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "A"}),
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "B"}),
        ],
    )
    err = svc.validate_plan(plan, set(registry.list_tools()))
    assert err is not None and "重复" in err


def test_validate_clean_plan_passes(registry):
    plan = svc.PlanProposal(
        title="ok",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "北京"}),
            svc.PlanStep(id="s2", tool="fake_query_points",
                         args={"bbox": "${s1.data.bbox}"}),
        ],
    )
    assert svc.validate_plan(plan, set(registry.list_tools())) is None


# ─── 执行 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_simple_chain(registry):
    """3 步链式，每步消费前一步输出。"""
    sid = "sess-plan-exec-1"
    plan = svc.PlanProposal(
        title="chain",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "海淀"}),
            svc.PlanStep(id="s2", tool="fake_query_points",
                         args={"bbox": "${s1.data.bbox}", "count": 5}),
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": "${s2.data.points}"}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is True
    assert result["executed"] == ["s1", "s2", "s3"]
    assert result["results"]["s3"]["data"]["hot_count"] == 5


@pytest.mark.asyncio
async def test_execute_topological_order_with_shuffled_input(registry):
    """步骤声明顺序乱，但靠 depends_on 拓扑序应当按依赖跑。"""
    sid = "sess-plan-topo"
    plan = svc.PlanProposal(
        title="topo",
        steps=[
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": "${s2.data.points}"},
                         depends_on=["s2"]),
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "北京"}),
            svc.PlanStep(id="s2", tool="fake_query_points",
                         args={"bbox": "${s1.data.bbox}", "count": 2},
                         depends_on=["s1"]),
        ],
    )
    # store_plan has the side effect of persisting the plan; the return is unused
    # here (validated separately in test_plan_store).
    await svc.store_plan(sid, plan)
    # 此时 validate_plan 因为 s3 出现时 s2 还没出现而拒绝 → 这是预期，
    # 但拓扑 _topological_order 本身（不经 validate）应该能算
    order = svc._topological_order(plan)
    assert order == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_execute_halts_on_first_failure(registry):
    """中间步失败时立即中止；不再启动后续波次。

    链式依赖设计（s3 依赖 s2）：s2 失败后 s3 属于后续波次，确定不会运行
    —— 与串行语义一致且测试确定性。
    """
    sid = "sess-plan-fail"
    plan = svc.PlanProposal(
        title="fail-chain",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"}),
            svc.PlanStep(id="s2", tool="fake_always_fail", args={}),
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": [1, 2, 3]},
                         depends_on=["s2"]),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is False
    assert result["failed_step"] == "s2"
    assert result["executed"] == ["s1"]  # s1 跑完，s2 失败，s3（依赖 s2）没跑
    plan_data = await svc.load_plan(sid, plan_id)
    assert plan_data["__status__"] == "partially_completed"  # s1 已成功（P2-1）
    assert plan_data["__failed_step__"] == "s2"


@pytest.mark.asyncio
async def test_execute_parallelizes_independent_steps(registry):
    """同波次独立步骤必须并发执行（#703：屏障判定，去墙钟阈值）。

    核心并发不变量：3 个互不依赖步骤全部进入后才放行——串行执行时先到者
    等不到同伴、确定性超时失败。不再依赖 0.45s 墙钟边距（满载间歇红来源）。
    """
    import asyncio as _aio
    sid = "sess-plan-parallel"

    entered = {"n": 0}
    all_in = _aio.Event()

    @registry.tool(name="fake_slow_ok", description="barrier 后成功返回")
    async def fake_slow_ok(tag: str, delay: float = 0.2) -> dict:
        entered["n"] += 1
        if entered["n"] == 3:
            all_in.set()
        # 只有三个步骤并发进入才会被放行；串行者在此确定性超时
        await _aio.wait_for(all_in.wait(), timeout=5.0)
        return {"success": True, "data": {"tag": tag}}

    plan = svc.PlanProposal(
        title="parallel",
        steps=[
            svc.PlanStep(id="p1", tool="fake_slow_ok", args={"tag": "a"}),
            svc.PlanStep(id="p2", tool="fake_slow_ok", args={"tag": "b"}),
            svc.PlanStep(id="p3", tool="fake_slow_ok", args={"tag": "c"}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)

    assert entered["n"] == 3
    assert result["success"] is True  # 屏障放行 = 三者曾同时在场（任一串行即超时失败）
    assert sorted(result["executed"]) == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_execute_failure_in_wave_keeps_completed_siblings(registry):
    """同波次内兄弟步骤与失败步骤并行：已完成兄弟计入 executed。

    并行语义的显式契约：s2（延迟 0.2s 后失败）与 s1/s3 无依赖同处一波；
    s1/s3 瞬时完成、s2 迟到失败 → s1/s3 必然已计入 executed；失败后不再
    启动新波次。
    """
    sid = "sess-plan-wave-fail"

    @registry.tool(name="fake_slow_fail", description="延迟后失败")
    async def fake_slow_fail(delay: float = 0.2) -> dict:
        import asyncio as _aio
        await _aio.sleep(delay)
        return {"success": False, "code": "TOOL_ERROR", "message": "delayed fail"}

    plan = svc.PlanProposal(
        title="wave-fail",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"}),
            svc.PlanStep(id="s2", tool="fake_slow_fail", args={"delay": 0.2}),
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": [1, 2, 3]}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is False
    assert result["failed_step"] == "s2"
    assert "s1" in result["executed"]           # 同波兄弟，先于失败完成
    assert "s3" in result["executed"]           # 同波兄弟，先于失败完成
    assert "s2" not in result["executed"]       # 失败步骤不计入
    plan_data = await svc.load_plan(sid, plan_id)
    assert plan_data["__status__"] == "partially_completed"  # s1/s3 已成功（P2-1）
    assert plan_data["__failed_step__"] == "s2"


@pytest.mark.asyncio
async def test_execute_unknown_plan_id_returns_error(registry):
    result = await svc.execute_plan_async("sess-x", "ref:plan-bogus", registry)
    assert result["success"] is False
    assert "找不到" in result["error"]


# ─── design-v3：resume / typed refs / supersede ─────────────────


@pytest.mark.asyncio
async def test_execute_resume_skips_completed_steps_and_reuses_refs(registry):
    """design-v3 §4：失败后 resume——已完成步骤不重跑、其结果继续供 ${} 引用，
    只有 pending/failed 步骤执行。"""
    sid = "sess-plan-resume"
    calls = {"s2": 0}

    @registry.tool(name="fake_flaky_hotspot", description="第一次失败第二次成功")
    def fake_flaky_hotspot(points: list) -> dict:
        calls["s2"] += 1
        if calls["s2"] == 1:
            # 瞬时网络失败（transient_network）→ 可恢复重试，不触发 livelock guard
            return {"success": False, "code": "TOOL_ERROR", "message": "网络超时 连接失败"}
        return {"success": True, "data": {"hot_count": len(points), "from": points}}

    plan = svc.PlanProposal(
        title="resume",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "海淀"}),
            svc.PlanStep(id="s2", tool="fake_flaky_hotspot",
                         args={"points": "${s1.data.bbox}"}),
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": [1, 2, 3]}, depends_on=["s2"]),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)

    r1 = await svc.execute_plan_async(sid, plan_id, registry)
    assert r1["success"] is False
    assert r1["failed_step"] == "s2"
    assert r1["failure_class"] == "transient_network"
    assert r1["executed"] == ["s1"]
    # 失败也持久化了已完成结果（resume 的数据基础）
    data = await svc.load_plan(sid, plan_id)
    assert "s1" in data["__step_results__"]
    assert data["__status__"] == "partially_completed"  # s1 已成功（P2-1）

    # resume：s1 跳过（复用结果）、s2 重试成功、s3 执行
    r2 = await svc.execute_plan_async(sid, plan_id, registry)
    assert r2["success"] is True
    assert r2["executed"] == ["s1", "s2", "s3"]
    assert r2["results"]["s3"]["data"]["hot_count"] == 3
    assert calls["s2"] == 2  # 只重试了一次
    data2 = await svc.load_plan(sid, plan_id)
    assert data2["__status__"] == "completed"


@pytest.mark.asyncio
async def test_execute_bad_path_ref_fails_with_missing_ref(registry):
    """design-v3 §deps：${s1.bad.path} 坏路径 → 立即失败 + failure_class=missing_ref
    + 修正提示（命名坏路径与可用 keys），不再静默 None/""。"""
    sid = "sess-plan-badpath"
    plan = svc.PlanProposal(
        title="badpath",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "北京"}),
            svc.PlanStep(id="s2", tool="fake_query_points",
                         args={"bbox": "${s1.bad.path}", "count": 3}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)

    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is False
    assert result["failed_step"] == "s2"
    assert result["failure_class"] == "missing_ref"
    assert "bad.path" in result["error"]      # 提示里带坏路径
    assert "s1" in result["error"]            # 及可用 keys
    assert result["recovery_action"] == "reuse_ref"
    data = await svc.load_plan(sid, plan_id)
    assert data["__status__"] == "partially_completed"  # s1 已成功（P2-1）
    assert data["__failure_class__"] == "missing_ref"


@pytest.mark.asyncio
async def test_propose_supersedes_old_pending_plan(registry):
    """design-v3 §4：新 propose 把旧 pending 计划标记 superseded；superseded
    计划不重放（返回已存状态）。"""
    sid = "sess-plan-super"
    p1 = await registry.dispatch("propose_plan", {
        "title": "旧计划",
        "steps": [{"id": "s1", "tool": "fake_get_bbox", "args": {"area": "A"}}],
    }, session_id=sid)
    assert p1["success"] is True

    p2 = await registry.dispatch("propose_plan", {
        "title": "新计划",
        "steps": [{"id": "s1", "tool": "fake_get_bbox", "args": {"area": "B"}}],
    }, session_id=sid)
    assert p2["success"] is True

    old_data = await svc.load_plan(sid, p1["plan_id"])
    assert old_data["__status__"] == "superseded"
    status = await registry.dispatch("get_plan_status", {"plan_id": p1["plan_id"]}, session_id=sid)
    assert status["status"] == "superseded"
    # superseded 计划**从未执行过**（无已存结果）→ 明确失败，不假装成功（P2-3）
    r = await svc.execute_plan_async(sid, p1["plan_id"], registry)
    assert r["success"] is False
    assert r["status"] == "superseded"
    assert "取代" in r["error"]


# ─── 工具入口集成 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_propose_plan_via_dispatch(registry):
    """走 registry.dispatch 验证 propose_plan 的 schema 校验链路。"""
    sid = "sess-propose-1"
    args = {
        "title": "demo",
        "summary": "test",
        "steps": [
            {"id": "s1", "tool": "fake_get_bbox", "args": {"area": "x"}},
        ],
    }
    result = await registry.dispatch("propose_plan", args, session_id=sid)
    assert result["success"] is True
    assert result["plan_id"].startswith("ref:plan-")
    assert result["step_count"] == 1


@pytest.mark.asyncio
async def test_propose_then_execute_then_status(registry):
    """propose → execute → get_plan_status 端到端闭环。"""
    sid = "sess-loop"
    proposal_args = {
        "title": "loop",
        "summary": "two-step",
        "steps": [
            {"id": "s1", "tool": "fake_get_bbox", "args": {"area": "海淀"}},
            {"id": "s2", "tool": "fake_query_points",
             "args": {"bbox": "${s1.data.bbox}", "count": 3}},
        ],
    }
    p = await registry.dispatch("propose_plan", proposal_args, session_id=sid)
    assert p["success"] is True
    plan_id = p["plan_id"]

    exec_result = await registry.dispatch("execute_plan", {"plan_id": plan_id}, session_id=sid)
    assert exec_result["success"] is True
    assert exec_result["status"] == "completed"

    status = await registry.dispatch("get_plan_status", {"plan_id": plan_id}, session_id=sid)
    assert status["success"] is True
    assert status["status"] == "completed"
    assert status["step_count"] == 2


@pytest.mark.asyncio
async def test_propose_plan_rejects_unknown_tool(registry):
    sid = "sess-bad"
    args = {
        "title": "bad",
        "steps": [
            {"id": "s1", "tool": "nonexistent_tool", "args": {}},
        ],
    }
    result = await registry.dispatch("propose_plan", args, session_id=sid)
    assert result["success"] is False
    assert "nonexistent_tool" in result["message"]


@pytest.mark.asyncio
async def test_propose_plan_requires_session(registry):
    args = {"title": "x", "steps": [{"id": "s1", "tool": "fake_get_bbox", "args": {"area": "a"}}]}
    result = await registry.dispatch("propose_plan", args, session_id=None)
    assert result["success"] is False
    assert "session_id" in result["message"]


# ─────────────────────────────────────────────────────────────────────────
# P2-1 / P2-3 补充：superseded 但有已存结果 → 返回结果（不重放）；失败无结果 → failed
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_superseded_plan_with_stored_results_still_returns_them(registry):
    """superseded 且已执行过部分步骤（有 __step_results__）→ success=True 返回
    已存结果，不重放；与"从没跑过"的空 superseded（P2-3）区分。"""
    sid = "sess-plan-super-results"
    plan = svc.PlanProposal(
        title="ran-then-superseded",
        steps=[svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"})],
    )
    plan_id = await svc.store_plan(sid, plan)
    await svc.execute_plan_async(sid, plan_id, registry)  # 先跑完 → completed
    await svc.update_plan_status(sid, plan_id, __status__="superseded")  # 再被取代
    r = await svc.execute_plan_async(sid, plan_id, registry)
    assert r["success"] is True
    assert r["status"] == "superseded"
    assert r["executed"] == ["s1"]  # 结果复用，不重放
    assert "s1" in r["results"]


@pytest.mark.asyncio
async def test_failure_with_no_results_writes_failed(registry):
    """第一个步骤就失败（无任何已存结果）→ 状态写 failed（不是 partially_completed）。"""
    sid = "sess-plan-fail-first"
    plan = svc.PlanProposal(
        title="fail-first",
        steps=[svc.PlanStep(id="s1", tool="fake_always_fail", args={})],
    )
    plan_id = await svc.store_plan(sid, plan)
    r = await svc.execute_plan_async(sid, plan_id, registry)
    assert r["success"] is False
    data = await svc.load_plan(sid, plan_id)
    assert data["__status__"] == "failed"
    assert data["__failure_class__"] == "internal"


# ─────────────────────────────────────────────────────────────────────────
# P3 延后项 #1：__step_results__ slim 持久化（完整结果存 ref:planresult-*，
# payload 有界；resume 水合回完整结果，${stepId.path} 解析不受影响）
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_results_slim_persisted_and_resume_resolves(registry):
    """大结果不嵌入 plan payload；resume 按 ref 水合后 ${s1.data.bbox} 正确解析。"""
    import json as _json

    sid = "sess-plan-slim"
    big_blob = "x" * 200_000  # 大对象（≈200KB，模拟大 GeoJSON）
    s1_calls = {"n": 0}
    s2_calls = {"n": 0}

    @registry.tool(name="fake_big_bbox", description="返回带大 blob 的 bbox")
    def fake_big_bbox(area: str) -> dict:
        s1_calls["n"] += 1
        return {
            "success": True,
            "data": {"area": area, "bbox": [116, 39, 117, 40], "blob": big_blob},
        }

    @registry.tool(name="fake_ref_consumer", description="消费 bbox 引用")
    def fake_ref_consumer(bbox: list) -> dict:
        s2_calls["n"] += 1
        if s2_calls["n"] == 1:
            # 瞬时网络失败（transient_network）→ 可恢复重试，不触发 livelock guard
            return {"success": False, "code": "TOOL_ERROR", "message": "网络超时 连接失败"}
        return {"success": True, "data": {"bbox": bbox}}

    plan = svc.PlanProposal(
        title="slim",
        steps=[
            svc.PlanStep(id="s1", tool="fake_big_bbox", args={"area": "海淀"}),
            svc.PlanStep(id="s2", tool="fake_ref_consumer",
                         args={"bbox": "${s1.data.bbox}"}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)

    r1 = await svc.execute_plan_async(sid, plan_id, registry)
    assert r1["success"] is False
    assert r1["failed_step"] == "s2"

    data = await svc.load_plan(sid, plan_id)
    assert data["__status__"] == "partially_completed"
    # slim 形状：完整结果不在 plan payload 里（大 blob 绝不嵌入）
    s1_entry = data["__step_results__"]["s1"]
    assert s1_entry.get("__slim__") is True
    assert s1_entry["ref"].startswith("ref:planresult-")
    assert "keys" in s1_entry
    assert big_blob not in _json.dumps(data)

    # resume：s1 跳过（水合回完整结果），s2 用 ${s1.data.bbox} 重试成功
    r2 = await svc.execute_plan_async(sid, plan_id, registry)
    assert r2["success"] is True, f"resume failed: {r2}"
    assert r2["executed"] == ["s1", "s2"]
    assert s1_calls["n"] == 1  # s1 绝不被重新 dispatch
    assert r2["results"]["s2"]["data"]["bbox"] == [116, 39, 117, 40]
    data2 = await svc.load_plan(sid, plan_id)
    assert data2["__status__"] == "completed"
    # 完成态 payload 同样有界
    assert data2["__step_results__"]["s1"].get("__slim__") is True
    assert data2["__step_results__"]["s2"].get("__slim__") is True
    assert big_blob not in _json.dumps(data2)


# ─────────────────────────────────────────────────────────────────────────
# issue #387：execute_plan 返回载荷瘦身（嵌套 results 不再无界穿透进 LLM
# 上下文 —— 大结果 → ref + 摘要，整体 ≤ MSG_MAX_CHARS；持久化语义不变）
# ─────────────────────────────────────────────────────────────────────────


def _fc_with_features(n: int) -> dict:
    """构造 n 要素的 FeatureCollection（几何为点，体积随 n 线性增长）。"""
    return {
        "success": True,
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": i, "name": f"p{i}"},
                "geometry": {"type": "Point", "coordinates": [116.0 + i / 1000.0, 39.0]},
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_execute_plan_return_results_slimmed_under_msg_max_chars(registry):
    """#387 验收：两个步骤各返回 10k 要素 FeatureCollection 的 plan → 返回
    payload 只含 ref/摘要（无嵌套几何），且整体序列化后 ≤ MSG_MAX_CHARS。"""
    import json as _json

    from app.services.llm_result_formatter import MSG_MAX_CHARS
    from app.services.session_data import session_data_manager

    @registry.tool(name="fake_big_buffer", description="返回大 FeatureCollection")
    def fake_big_buffer(count: int = 10000) -> dict:
        return _fc_with_features(count)

    sid = "sess-plan-387"
    plan = svc.PlanProposal(
        title="slim-387",
        steps=[
            svc.PlanStep(id="s1", tool="fake_big_buffer", args={"count": 10000}),
            svc.PlanStep(id="s2", tool="fake_big_buffer", args={"count": 10000}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)

    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is True
    assert result["executed"] == ["s1", "s2"]

    for step_id in ("s1", "s2"):
        slim = result["results"][step_id]
        assert slim.get("__slim__") is True, f"{step_id} 应被瘦身"
        assert str(slim.get("ref", "")).startswith("ref:"), f"{step_id} 应有 ref"
        assert slim.get("feature_count") == 10000, f"{step_id} 应带要素计数摘要"
        # 无嵌套几何穿透
        assert "geojson" not in slim and "features" not in slim
        assert "geometry" not in _json.dumps(result["results"])

    # 整个返回 dict（LLM payload 来源）≤ MSG_MAX_CHARS
    payload = _json.dumps(result, ensure_ascii=False)
    assert len(payload) <= MSG_MAX_CHARS, f"payload {len(payload)} > {MSG_MAX_CHARS}"

    # 数据本身不丢：ref 可水合回完整结果（引用仍在）
    for step_id in ("s1", "s2"):
        full = await session_data_manager.get(sid, result["results"][step_id]["ref"])
        assert full is not None
        assert len(full["features"]) == 10000

    # 持久化路径语义不变：__step_results__ 仍是 slim 摘要 + 可水合
    data = await svc.load_plan(sid, plan_id)
    assert data["__status__"] == "completed"
    assert data["__step_results__"]["s1"].get("__slim__") is True
    assert data["__step_results__"]["s2"].get("__slim__") is True

    # completed 早退路径（不重放）同样返回瘦身结果
    result2 = await svc.execute_plan_async(sid, plan_id, registry)
    assert result2["status"] == "completed"
    assert result2["results"]["s1"].get("__slim__") is True
    assert len(_json.dumps(result2, ensure_ascii=False)) <= MSG_MAX_CHARS


@pytest.mark.asyncio
async def test_execute_plan_failure_last_result_slimmed(registry):
    """#387：失败步骤的 last_result 含大 FeatureCollection 时同样瘦身。"""
    import json as _json

    from app.services.llm_result_formatter import MSG_MAX_CHARS

    @registry.tool(name="fake_big_fail", description="返回带大 FC 的失败结果")
    def fake_big_fail(count: int = 10000) -> dict:
        return {**_fc_with_features(count), "success": False, "message": "boom"}

    sid = "sess-plan-387-fail"
    plan = svc.PlanProposal(
        title="slim-387-fail",
        steps=[svc.PlanStep(id="s1", tool="fake_big_fail", args={"count": 10000})],
    )
    plan_id = await svc.store_plan(sid, plan)

    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is False
    last = result["last_result"]
    assert last.get("__slim__") is True
    assert str(last.get("ref", "")).startswith("ref:")
    assert "features" not in last and "geojson" not in last
    assert "geometry" not in _json.dumps(result)
    assert len(_json.dumps(result, ensure_ascii=False)) <= MSG_MAX_CHARS


@pytest.mark.asyncio
async def test_execute_plan_small_results_kept_intact(registry):
    """#387：小结果（≤ 阈值）在返回 payload 中保持完整，不无故瘦身。"""
    @registry.tool(name="fake_small_geo", description="返回小 FC")
    def fake_small_geo(tag: str) -> dict:
        return {"success": True, "type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"tag": tag}, "geometry": {"type": "Point", "coordinates": [1, 2]}},
        ]}

    sid = "sess-plan-387-small"
    plan = svc.PlanProposal(
        title="small",
        steps=[
            svc.PlanStep(id="s1", tool="fake_small_geo", args={"tag": "a"}),
            svc.PlanStep(id="s2", tool="fake_small_geo", args={"tag": "b"}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is True
    assert result["results"]["s1"]["features"][0]["properties"]["tag"] == "a"
    assert result["results"]["s2"]["features"][0]["properties"]["tag"] == "b"
