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
