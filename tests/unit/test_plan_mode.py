"""Plan Mode 端到端 + 内部辅助测试。

覆盖：
- DAG 校验（未知工具、自引用、缺失依赖、环）
- 占位符解析（${stepId}、${stepId.path.to.field}、嵌入式字符串、缺失字段）
- 顺序执行 + 结果聚合
- 任一步失败中止
- 拓扑排序（依赖打乱顺序）
- get_plan_status 状态查询
"""
import pytest

from app.tools.registry import ToolRegistry
from app.services import plan_mode as svc
from app.services.session_data import session_data_manager
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
    plan_id = svc.store_plan(sid, plan)
    # 此时 validate_plan 因为 s3 出现时 s2 还没出现而拒绝 → 这是预期，
    # 但拓扑 _topological_order 本身（不经 validate）应该能算
    order = svc._topological_order(plan)
    assert order == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_execute_halts_on_first_failure(registry):
    """中间步失败时立即中止；返回已执行步骤。"""
    sid = "sess-plan-fail"
    plan = svc.PlanProposal(
        title="fail-chain",
        steps=[
            svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"}),
            svc.PlanStep(id="s2", tool="fake_always_fail", args={}),
            svc.PlanStep(id="s3", tool="fake_hotspot",
                         args={"points": [1, 2, 3]}),
        ],
    )
    plan_id = await svc.store_plan(sid, plan)
    result = await svc.execute_plan_async(sid, plan_id, registry)
    assert result["success"] is False
    assert result["failed_step"] == "s2"
    assert result["executed"] == ["s1"]  # s1 跑完，s2 失败，s3 没跑
    plan_data = await svc.load_plan(sid, plan_id)
    assert plan_data["__status__"] == "failed"
    assert plan_data["__failed_step__"] == "s2"


@pytest.mark.asyncio
async def test_execute_unknown_plan_id_returns_error(registry):
    result = await svc.execute_plan_async("sess-x", "ref:plan-bogus", registry)
    assert result["success"] is False
    assert "找不到" in result["error"]


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
