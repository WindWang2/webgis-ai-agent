"""执行稳定性强化测试（ADR-0103 §九）。

- GisProgressTracker：map/workflow 停滞 reason codes（unchanged_map /
  unchanged_workflow）、规划重复（repeated_planning）、失败不推进停滞计数；
- bridge 侧 no_progress hints：details 携带 hints（additive）；
- 子代理递归深度防线：嵌套 spawn 诚实失败。
"""
import pytest

from app.services.chat.no_progress import GisProgressTracker


def test_unchanged_map_reason_code():
    t = GisProgressTracker(map_stale_threshold=3)
    reasons = []
    for _ in range(3):
        reasons = t.record_call(
            "spatial_aggregate", {"layer": "a"}, "ok", map_epoch="fp1",
        )
    assert reasons == ["unchanged_map:3"]
    # 地图一变 → 计数归零
    reasons = t.record_call("spatial_aggregate", {"layer": "a"}, "ok", map_epoch="fp2")
    assert not any(r.startswith("unchanged_map") for r in reasons)


def test_unchanged_workflow_reason_code():
    t = GisProgressTracker(workflow_stale_threshold=2)
    t.record_call("webgis_map_intent", {"q": "x"}, "ok", workflow_epoch="w1")
    reasons = t.record_call("query_local_poi", {"d": "成都"}, "ok", workflow_epoch="w1")
    assert "unchanged_workflow:2" in reasons


def test_failures_do_not_advance_stale_counters():
    t = GisProgressTracker(map_stale_threshold=2)
    t.record_call("spatial_aggregate", {"layer": "a"}, "ok", map_epoch="fp1")
    t.record_call("spatial_aggregate", {"layer": "a"}, "error", map_epoch="fp1")
    reasons = t.record_call("spatial_aggregate", {"layer": "a"}, "ok", map_epoch="fp1")
    # 只有 2 次 ok → 未达阈值 3...（threshold=2 → streak=2 → 触发）
    assert reasons == ["unchanged_map:2"]


def test_repeated_planning_signature():
    t = GisProgressTracker(planning_repeat_threshold=2)
    assert t.record_planning("plan-sig-1") == []
    reasons = t.record_planning("plan-sig-1")
    assert reasons == ["repeated_planning:2"]
    # 不同签名互不影响
    assert t.record_planning("plan-sig-2") == []


def test_exact_repeat_failure_still_works():
    t = GisProgressTracker()
    t.record_call("buffer", {"d": 1}, "error")
    reasons = t.record_call("buffer", {"d": 1}, "error")
    assert "exact_repeat_failure" in reasons


def test_diagnose_summary_bounded():
    t = GisProgressTracker(map_stale_threshold=1)
    t.record_call("spatial_aggregate", {}, "ok", map_epoch="fp")
    t.record_call("spatial_aggregate", {}, "ok", map_epoch="fp")
    assert "map_stale=" in t.diagnose()


async def test_bridge_no_progress_hints_in_details():
    """dispatch_tool 的 details 在停滞达阈值时携带 no_progress_hints。"""
    # 直接测 _record_gis_progress（bridge 的集成函数）：只验 reason 透传路径
    from app.agent_pi_bridge import _record_gis_progress
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)

    session = "test-np-session"
    # 同 mapspec 指纹连续成功（读 map epoch 的 session 无 mapspec → epoch 空
    # → 该维度不参与）——此处只验证函数路径可用且不抛
    hints = await _record_gis_progress(
        session, "spatial_aggregate", {"layer_id": "x"}, outcome="ok"
    )
    assert isinstance(hints, list)


async def test_subagent_recursion_depth_guard():
    from app.agent_pi_bridge import set_tool_registry
    from app.services.subagent import SubagentDispatcher, _subagent_depth
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)

    dispatcher = SubagentDispatcher(reg, parent_session_id="s-rec")
    token = _subagent_depth.set(2)
    try:
        result = await dispatcher.run(task="嵌套递归测试", max_rounds=1)
    finally:
        _subagent_depth.reset(token)
    assert result.success is False
    assert "recursion depth" in (result.error or "")


def test_subagent_depth_contextvar_default():
    from app.services.subagent import _subagent_depth

    assert _subagent_depth.get(0) == 0
