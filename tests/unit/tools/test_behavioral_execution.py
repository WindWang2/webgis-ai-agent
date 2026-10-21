"""行为化 dispatch 测试 —— GeoCompute 执行计划 3 工具（ADR-0096 D1）。

用最小真实 plan（filter 节点 + 内联小 FeatureCollection）真实执行；
not-found / 未知类别等依赖缺失语义被显式断言。run 状态查询与取消依赖
``app.services.geocompute.executor.engine`` 的进程内 run 账本。
"""
import pytest

from app.tools.geocompute_tools import register_geocompute_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_geocompute_tools(reg)
    return reg


def _filter_plan_nodes():
    """最小 wired plan：filter 节点 + 内联 4 要素。"""
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]},
         "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b"}}
        for i in range(4)
    ]
    return [{
        "node_id": "f1",
        "category": "filter",
        "parameters": {
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": features,
        },
    }]


@pytest.mark.asyncio
async def test_execute_execution_plan_behavioral(registry):
    # validation：缺 nodes → 校验错误
    bad = await registry.dispatch("execute_execution_plan", {"plan_id": "p-behavioral"})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：未接线类别 → typed 错误（构建期诚实拒绝）
    unknown = await registry.dispatch("execute_execution_plan", {
        "plan_id": "p-bad",
        "nodes": [{"node_id": "x", "category": "quantum_teleport"}],
    })
    assert isinstance(unknown, dict) and unknown.get("success") is False
    assert "unknown node category" in (unknown.get("message") or "")

    # happy path：filter 真实执行 → completed + 有界证据
    out = await registry.dispatch("execute_execution_plan", {
        "plan_id": "p-behavioral",
        "nodes": _filter_plan_nodes(),
        "session_id": "bdx-exec",
    })
    assert out.get("status") == "completed", out
    run_id = out.get("run_id")
    assert isinstance(run_id, str) and run_id
    assert "f1" in (out.get("evidence") or {})
    assert isinstance(out.get("summary_lines"), list) and out["summary_lines"]
    assert out.get("plan_fingerprint")


@pytest.mark.asyncio
async def test_execute_execution_plan_rejects_foreign_session(registry):
    """#1708（SEC）：会话隔离承诺落地 —— 显式 session_id 必须与调用方
    执行上下文的会话身份一致，否则 typed 拒绝（fail-closed，先于执行）。"""
    from app.services.provenance.context import (
        ToolExecutionContext,
        reset_tool_execution_context,
        set_tool_execution_context,
    )

    token = set_tool_execution_context(
        ToolExecutionContext(user_id="u-1", session_id="sess-mine")
    )
    try:
        # 他人会话 id → typed 拒绝（不是静默透传去写别人的 session 存储）。
        denied = await registry.dispatch("execute_execution_plan", {
            "plan_id": "p-foreign",
            "nodes": _filter_plan_nodes(),
            "session_id": "sess-someone-else",
        })
        assert denied.get("success") is False, denied
        assert denied.get("error_type") == "AuthorizationError", denied
        assert denied.get("code") == "TOOL_ERROR", denied

        # 一致 → 放行。
        ok = await registry.dispatch("execute_execution_plan", {
            "plan_id": "p-mine",
            "nodes": _filter_plan_nodes(),
            "session_id": "sess-mine",
        })
        assert ok.get("status") == "completed", ok

        # 省略 session_id → 从执行上下文取（materialize 策略必需）。
        omitted = await registry.dispatch("execute_execution_plan", {
            "plan_id": "p-omit", "nodes": _filter_plan_nodes(),
        })
        assert omitted.get("status") == "completed", omitted
    finally:
        reset_tool_execution_context(token)


def test_execute_execution_plan_session_param_promise(registry):
    """#1708（SEC）：param_descriptions 的会话承诺必须与实现同口径 ——
    明写「不一致即拒绝」，不再宣称「原样透传」。"""
    schema = next(
        s for s in registry.get_schemas()
        if s["function"]["name"] == "execute_execution_plan"
    )
    desc = schema["function"]["parameters"]["properties"]["session_id"]["description"]
    assert "一致" in desc and "拒绝" in desc, desc


@pytest.mark.asyncio
async def test_get_execution_run_behavioral(registry):
    # validation：缺 run_id → 校验错误
    bad = await registry.dispatch("get_execution_run", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：不存在的 run → not_found（不泄漏存在性）
    missing = await registry.dispatch(
        "get_execution_run", {"run_id": "run-does-not-exist"})
    assert isinstance(missing, dict)
    assert missing.get("status") == "not_found"
    assert missing.get("run_id") == "run-does-not-exist"

    # happy path：真实执行后可查询到 run 的终态与证据
    done = await registry.dispatch("execute_execution_plan", {
        "plan_id": "p-get", "nodes": _filter_plan_nodes(),
    })
    assert done.get("status") == "completed", done
    out = await registry.dispatch(
        "get_execution_run", {"run_id": done["run_id"]})
    assert out.get("status") == "completed", out
    assert out.get("run_id") == done["run_id"]
    assert "f1" in (out.get("evidence") or {})
    assert isinstance(out.get("summary_lines"), list)


@pytest.mark.asyncio
async def test_cancel_execution_run_behavioral(registry):
    # validation：缺 run_id → 校验错误
    bad = await registry.dispatch("cancel_execution_run", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：他人/不存在的 run → not_found + cancelled=False
    missing = await registry.dispatch(
        "cancel_execution_run", {"run_id": "run-does-not-exist"})
    assert isinstance(missing, dict)
    assert missing.get("status") == "not_found"
    assert missing.get("cancelled") is False

    # happy path（幂等语义）：已终态 run 的取消请求 → cancelled=False + 当前状态
    done = await registry.dispatch("execute_execution_plan", {
        "plan_id": "p-cancel", "nodes": _filter_plan_nodes(),
    })
    assert done.get("status") == "completed", done
    out = await registry.dispatch(
        "cancel_execution_run", {"run_id": done["run_id"]})
    assert out.get("cancelled") is False, out
    assert out.get("status") == "completed"
    assert out.get("run_id") == done["run_id"]
