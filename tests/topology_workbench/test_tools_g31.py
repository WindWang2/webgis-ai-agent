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
