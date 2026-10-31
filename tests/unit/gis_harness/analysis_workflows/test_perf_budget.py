"""性能/容量锁（goal-02 M9；-m perf lane）。

>10 万 features 输入：typed 预算阻断（不无界加载、不进上下文）——
用真实 100k 点 FC（非 descriptor 注入）验证 store→plan→run 全链在
预算门处停下，dispatch 面零执行。
"""
from __future__ import annotations

import json

import pytest

from app.services.session_data import session_data_manager

pytestmark = pytest.mark.perf

SESSION = "awf-perf-session"


@pytest.fixture
def registry():
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


def _points_100k() -> dict:
    n = 100_001
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"id": i},
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + (i % 1000) * 0.00004,
                                          30.6 + (i // 1000) * 0.00004]}}
            for i in range(n)],
    }


async def test_100k_features_budget_blocked_not_loaded(registry):
    big_ref = await session_data_manager.store(
        SESSION, _points_100k(), prefix="geojson")
    dists = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"district": "whole"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [104.0, 30.6], [104.04, 30.6], [104.04, 30.64],
                 [104.0, 30.64], [104.0, 30.6]]]}}]}
    dist_ref = await session_data_manager.store(SESSION, dists,
                                                prefix="geojson")
    plan = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": big_ref,
                                 "boundary": dist_ref}),
        "session_id": SESSION})
    assert plan["success"]
    run = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": plan["workflow_id"], "session_id": SESSION})
    # typed 阻断：预算门在 descriptor 层拦截——大数据不进内存/上下文
    assert run["status"] == "blocked"
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["zones_count"]["reason_code"] == "AWF_BUDGET_EXCEEDED"
    assert steps["zones_count"]["state"] == "blocked"
