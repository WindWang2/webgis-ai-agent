"""可达性/时态/统计分区三族 E2E（goal-02 M5/M6）—— 真实 dispatch 全链。

- service_accessibility：**真实路网**（网格道路 LineString）→ 真 Dijkstra
  等时圈 → 边界求交；无路网不得数值结论（planner 负例已钉扎）；
- temporal_change：双期对齐（qualifier.year）→ overlay difference → 变化面；
- statistical_zones：H3 网格聚合 → （无人口分母）率步骤 typed skipped。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import WorkflowExecutor
from app.services.gis_harness.analysis_workflows.orchestrator import plan_workflow
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-families-e2e-session"


def _grid_roads_fc() -> dict:
    """5×4 网格路网（间隔 0.008°≈850m 的纵横道路，真实 LineString）。"""
    lines = []
    for i in range(6):        # 纵路 x = 104.00 + i*0.008
        x = 104.00 + i * 0.008
        lines.append({"type": "Feature",
                      "properties": {"road_id": f"v{i}", "kind": "primary"},
                      "geometry": {"type": "LineString", "coordinates": [
                          [x, 30.60], [x, 30.632]]}})
    for j in range(5):        # 横路 y = 30.60 + j*0.008
        y = 30.60 + j * 0.008
        lines.append({"type": "Feature",
                      "properties": {"road_id": f"h{j}", "kind": "primary"},
                      "geometry": {"type": "LineString", "coordinates": [
                          [104.00, y], [104.04, y]]}})
    return {"type": "FeatureCollection", "features": lines}


def _schools_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"id": f"sch-{i}"},
             "geometry": {"type": "Point",
                          "coordinates": [104.004 + i * 0.008,
                                          30.604 + (i % 3) * 0.008]}}
            for i in range(5)],
    }


def _districts_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"district": name},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, y], [x + 0.02, y], [x + 0.02, y + 0.016],
                 [x, y + 0.016], [x, y]]]}}
            for x, y, name in [
                (104.00, 30.600, "nw"), (104.02, 30.600, "ne"),
                (104.00, 30.616, "sw"), (104.02, 30.616, "se")]],
    }


def _builtup_fc(scale: float, label: str) -> dict:
    """双期建成区面（target 比 baseline 扩大 scale 倍）。"""
    base = 0.004
    w = base * scale
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"patch_id": f"{label}-{i}",
                            "year": label},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [104.00 + i * 0.01, 30.60],
                 [104.00 + i * 0.01 + w, 30.60],
                 [104.00 + i * 0.01 + w, 30.60 + w],
                 [104.00 + i * 0.01, 30.60 + w],
                 [104.00 + i * 0.01, 30.60]]]}}
            for i in range(3)],
    }


def _crime_points_fc(n: int = 60) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"id": i, "weight": 1},
             "geometry": {"type": "Point",
                          "coordinates": [
                              104.005 + (i % 12) * 0.0025,
                              30.602 + (i // 12) * 0.0022]}}
            for i in range(n)],
    }


@pytest.fixture
def registry():
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


async def _store(data: dict) -> str:
    return await session_data_manager.store(SESSION, data, prefix="geojson")


async def _run(registry, plan):
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    return await executor.execute(SESSION, plan.contract, state)


async def test_service_accessibility_real_network_chain(registry):
    """真路网 → 真 Dijkstra 等时圈 → 覆盖区求交（M5 核心链）。"""
    net_ref = await _store(_grid_roads_fc())
    sch_ref = await _store(_schools_fc())
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "service_accessibility", workflow_id="awf.fam.acc",
        bindings={
            "network": DataRoleBinding(role="network", ref=net_ref),
            "subject": DataRoleBinding(role="subject", ref=sch_ref),
            "boundary": DataRoleBinding(role="boundary", ref=dist_ref),
        })
    assert plan.runnable
    state = await _run(registry, plan)

    assert state.status == "satisfied", {
        sid: (e.state, e.reason_code, e.message)
        for sid, e in state.steps.items()}
    iso_env = state.envelopes["isochrone"]
    assert iso_env.ref.startswith("ref:geojson")
    assert iso_env.feature_count >= 1
    # 等时圈产物带可达性事实（reachable 披露在 FC 属性，由算法层保证）
    iso_fc = await session_data_manager.get(SESSION, iso_env.ref)
    reachable_flags = [f["properties"].get("reachable")
                       for f in iso_fc["features"]]
    assert all(isinstance(r, bool) for r in reachable_flags)
    # 覆盖区 = 等时圈 ∩ 行政区（真 overlay，非 buffer 近似）
    cov_env = state.envelopes["coverage_zones"]
    assert cov_env.feature_count >= 1
    # 无人口绑定 → 覆盖人口步骤 typed skipped（只给面积/占比口径）
    assert state.steps["coverage_population"].state == "skipped"


async def test_temporal_change_aligned_diff_chain(registry):
    base_ref = await _store(_builtup_fc(1.0, "2018"))
    target_ref = await _store(_builtup_fc(1.8, "2023"))
    plan = plan_workflow(
        "temporal_change", workflow_id="awf.fam.tc",
        bindings={
            "baseline": DataRoleBinding(role="baseline", ref=base_ref,
                                        qualifier={"year": 2018}),
            "target_time": DataRoleBinding(role="target_time", ref=target_ref,
                                           qualifier={"year": 2023}),
        })
    assert plan.runnable
    state = await _run(registry, plan)
    assert state.status == "satisfied", {
        sid: (e.state, e.reason_code, e.message)
        for sid, e in state.steps.items()}
    diff_env = state.envelopes["change_diff"]
    assert diff_env.feature_count == 3
    # target 更大 → 每个 patch 差分非空（扩张面积 > 0）
    diff_fc = await session_data_manager.get(SESSION, diff_env.ref)
    assert all(f["geometry"] for f in diff_fc["features"])
    # identity 步骤保留目标期完整几何供占比口径
    share_env = state.envelopes["change_share"]
    assert share_env.feature_count >= 3


async def test_statistical_zones_grid_chain_without_population(registry):
    pts_ref = await _store(_crime_points_fc(60))
    plan = plan_workflow(
        "statistical_zones", workflow_id="awf.fam.sz",
        bindings={"subject": DataRoleBinding(role="subject", ref=pts_ref)})
    assert plan.runnable
    state = await _run(registry, plan)
    assert state.status == "satisfied", {
        sid: (e.state, e.reason_code, e.message)
        for sid, e in state.steps.items()}
    grid_env = state.envelopes["grid_stats"]
    assert grid_env.feature_count > 0
    # 无 population 绑定 → 率步骤 typed skipped；热点检验照常（只依赖网格）
    assert state.steps["grid_rate"].state == "skipped"
    assert state.steps["hotspot"].state == "satisfied"


async def test_hazard_distance_unit_contract_in_fingerprint(registry):
    """单位契约进指纹：缓冲距离单位改变 = 不同计算（重算而非复用）。"""
    hazard_ref = await _store({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "h1"},
             "geometry": {"type": "Point",
                          "coordinates": [104.01, 30.61]}}]})
    receptor_ref = await _store(_crime_points_fc(30))
    bindings = {"hazard": DataRoleBinding(role="hazard", ref=hazard_ref),
                "receptor": DataRoleBinding(role="receptor", ref=receptor_ref)}
    plan1 = plan_workflow("hazard_exposure", workflow_id="awf.fam.unit",
                          bindings=bindings)
    plan2 = plan_workflow(
        "hazard_exposure", workflow_id="awf.fam.unit", bindings=bindings,
        params_overrides={"hazard_buffer": {"distance": 2000.0}})
    s1 = next(s for s in plan1.contract.steps if s.step_id == "hazard_buffer")
    s2 = next(s for s in plan2.contract.steps if s.step_id == "hazard_buffer")
    assert s1.step_params["distance"] != s2.step_params["distance"]
    from app.services.gis_harness.analysis_workflows.recompute import (
        step_fingerprint,
    )
    fp1 = step_fingerprint(s1, input_identities={"geojson": "ref:x@1"},
                           science=plan1.contract.science_context)
    fp2 = step_fingerprint(s2, input_identities={"geojson": "ref:x@1"},
                           science=plan2.contract.science_context)
    assert fp1 != fp2
