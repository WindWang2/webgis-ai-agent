"""G31 拓扑工作台工具面回执边界披露测试。"""
from __future__ import annotations

import asyncio

from app.tools.registry import ToolRegistry
from app.tools.topology_workbench_tools import register_topology_workbench_tools


def _run(rule_count: int) -> dict:
    return {
        "status": "completed",
        "findings_total": 0,
        "findings_open": 0,
        "completeness": {"all_skipped_or_unknown": False},
        "rule_results": [
            {
                "rule_id": f"r{i}", "rule_type": "area_coverage",
                "applicability": "applicable", "outcome": "pass",
                "completeness": "complete", "input_completeness": "complete",
            }
            for i in range(rule_count)
        ],
    }


def _receipt(monkeypatch, rule_count: int) -> dict:
    import app.services.topology_workbench.service as service

    monkeypatch.setattr(service, "get_run", lambda run_id, **kwargs: _run(rule_count))
    registry = ToolRegistry()
    register_topology_workbench_tools(registry)
    return asyncio.run(registry._tools["topology_check_status"](run_id="run-x"))


def test_check_status_discloses_rule_result_truncation(monkeypatch):
    """规则数 >16：回执必须带规则总数与截断标记，不得静默丢弃。"""
    receipt = _receipt(monkeypatch, 20)

    assert len(receipt["rule_results"]) == 16
    assert receipt["rule_results_total"] == 20
    assert receipt["rule_results_truncated"] is True


def test_check_status_reports_no_truncation_when_all_shown(monkeypatch):
    """规则数 ≤16：不虚报截断。"""
    receipt = _receipt(monkeypatch, 5)

    assert len(receipt["rule_results"]) == 5
    assert receipt["rule_results_total"] == 5
    assert receipt["rule_results_truncated"] is False


def test_ruleset_configure_rejects_rules_above_tool_cap(monkeypatch):
    """规则数 > _MAX_RULES：工具面直接拒绝，不把超长数组透传给服务端。"""
    import app.services.topology_workbench.service as service

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("service.create_ruleset 不应被调用")

    monkeypatch.setattr(service, "create_ruleset", _should_not_be_called)
    registry = ToolRegistry()
    register_topology_workbench_tools(registry)
    receipt = asyncio.run(registry._tools["topology_ruleset_configure"](
        name="rs",
        rules=[{"rule_id": f"r{i}"} for i in range(33)],
        session_id="sess-1",
    ))

    assert receipt["success"] is False
    assert receipt["code"] == "INVALID_ARGS"


def test_check_run_turns_binding_store_failure_into_receipt(monkeypatch):
    """绑定载荷存回会话失败（如存储预算超限）：返回 _fail 回执而非抛异常。"""
    from app.services.session_data import session_data_manager

    async def _raise(*args, **kwargs):
        raise ValueError("session payload exceeds the per-session byte budget")

    monkeypatch.setattr(session_data_manager, "store", _raise)
    registry = ToolRegistry()
    register_topology_workbench_tools(registry)
    receipt = asyncio.run(registry._tools["topology_check_run"](
        ruleset_id="rs-1",
        bindings={
            "checked": {
                "kind": "session_ref",
                "ref": {"type": "FeatureCollection", "features": []},
            },
        },
        session_id="sess-1",
    ))

    assert receipt["success"] is False
    assert receipt["code"] == "STORE_FAILED"


def test_repair_preview_unsupported_without_candidates_key(monkeypatch):
    """候选回执缺 candidates 键：不得抛 KeyError，应返回 REPAIR_UNSUPPORTED。"""
    import app.services.topology_workbench.repair as repair
    from app.services.provenance.context import (
        ToolExecutionContext,
        reset_tool_execution_context,
        set_tool_execution_context,
    )

    monkeypatch.setattr(
        repair, "build_repair_candidates",
        lambda finding_id, **kwargs: {
            "finding_id": finding_id, "unsupported_reason": "无确定性修复",
        },
    )
    registry = ToolRegistry()
    register_topology_workbench_tools(registry)
    token = set_tool_execution_context(
        ToolExecutionContext(user_id="u1", session_id="s1"))
    try:
        receipt = asyncio.run(registry._tools["topology_repair_preview"](
            finding_id="f1", kind="snap_endpoint"))
    finally:
        reset_tool_execution_context(token)

    assert receipt["success"] is False
    assert receipt["code"] == "REPAIR_UNSUPPORTED"
    assert receipt["candidates"] == []
