"""链③ 网络→最近设施→行政汇总（ADR-0282 D4；断点 2/4 修复）。

- network_proximity 新族：真实 Dijkstra 最近设施路线 → 区均网络成本
  （numerator=Σtotal_cost / count 分母——均值语义非伪装率）；
- 断网设施 5m 占位面：族默认 exclude_unreachable=True 不进覆盖产物，
  计数进 stats.excluded_unreachable（部分失败显性化）；
- plan 期预算估计：ref descriptor 行数事实超限 → runnable=False +
  typed 指引（run 期 executor 仍硬门）；
- grid_rate（statistical_zones）：显式分母通道真率（人口/设施负荷），
  不再是改名计数（断点 1）。
"""
from __future__ import annotations


import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_NO_NETWORK_FOR_ACCESSIBILITY,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import (
    WorkflowExecutor,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-net-test-session"


def _road_fc(*, isolated: bool = False) -> dict:
    """3×3 网格路网（6 条 LineString，间距 0.01°，覆盖 104.00-104.02）。

    isolated=True 追加一条孤立道路（远离主网——图上不连通组件），用于
    触发真实的「设施落在不连通组件 → reachable=False」语义（断点 2）。
    """
    lines = []
    for i in range(3):
        y = 30.60 + i * 0.01
        lines.append({"type": "LineString", "coordinates": [
            [104.00, y], [104.02, y]]})
        x = 104.00 + i * 0.01
        lines.append({"type": "LineString", "coordinates": [
            [x, 30.60], [x, 30.62]]})
    if isolated:
        lines.append({"type": "LineString", "coordinates": [
            [104.50, 30.61], [104.52, 30.61]]})
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"road_id": i},
             "geometry": g} for i, g in enumerate(lines)],
    }


def _points_fc(coords, name: str) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"id": f"{name}-{i}"},
             "geometry": {"type": "Point", "coordinates": c}}
            for i, c in enumerate(coords)],
    }


def _districts_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"district": n},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, 30.60], [x + 0.01, 30.60], [x + 0.01, 30.62],
                 [x, 30.62], [x, 30.60]]]}}
            for x, n in [(104.00, "west"), (104.01, "east")]],
    }


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


async def _store(data: dict) -> str:
    return await session_data_manager.store(SESSION, data, prefix="geojson")


async def test_network_proximity_e2e_district_mean_cost(registry):
    """需求点→医院网络距离→区均成本端到端（真实 Dijkstra）。"""
    road_ref = await _store(_road_fc())
    incidents_ref = await _store(_points_fc(
        [(104.005, 30.605), (104.015, 30.615), (104.005, 30.615)],
        "community"))
    facilities_ref = await _store(_points_fc(
        [(104.005, 30.605), (104.015, 30.605)], "hospital"))
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "network_proximity",
        workflow_id="awf.np.e2e",
        bindings={
            "network": DataRoleBinding(role="network", ref=road_ref),
            "subject": DataRoleBinding(role="subject", ref=incidents_ref),
            "facility": DataRoleBinding(role="facility", ref=facilities_ref),
            "boundary": DataRoleBinding(role="boundary", ref=dist_ref),
        })
    assert plan.runnable, plan.blocked_reasons
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.steps["nearest_facility"].state == "satisfied", (
        state.steps["nearest_facility"].message)
    assert state.steps["accessibility_summary"].state == "satisfied", (
        state.steps["accessibility_summary"].message)
    env = state.envelopes["nearest_facility"]
    assert env.aggregation_basis == "one_to_one"
    summary_env = state.envelopes["accessibility_summary"]
    # 区均网络成本可回读（numerator=Σcost、denominator=路线数、rate=均值）
    payload = await session_data_manager.get(SESSION, summary_env.ref)
    props = [f["properties"] for f in payload["features"]]
    assert all("numerator" in p and "denominator" in p for p in props)
    assert any(p.get("rate") is not None for p in props)


def test_network_proximity_no_network_typed_blocked():
    plan = plan_workflow(
        "network_proximity",
        workflow_id="awf.np.nonet",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:communities"),
            "facility": DataRoleBinding(role="facility", ref="ref:hospitals"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    assert not plan.runnable
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "nearest_facility")
    assert verdict.reason_code == AWF_NO_NETWORK_FOR_ACCESSIBILITY


async def test_isochrone_excludes_unreachable_placeholders(
        registry, monkeypatch):
    """断网设施的 5m 占位面：族默认剔除 + 计数披露（断点 2）。

    算法层真实不可达（reachable=False + 5m 占位面）由 geo_analysis.network
    的不连通语义产生；本测试以同形状结果验证**工具过滤面**（exclude_
    unreachable 的剔除/计数/警告——工作流消费的正是这层契约）。master
    行为对照（默认 False：占位面保留）同时锁定。
    """
    from app.services.spatial_analyzer import SpatialAnalyzer

    def _fake_isochrone(net, facs, travel_time, mode):
        from app.lib.geo_processor.core import GeoAnalysisResult
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"facility": "on-net", "reachable": True},
                 "geometry": {"type": "Polygon", "coordinates": [[
                     [104.0, 30.6], [104.02, 30.6], [104.02, 30.62],
                     [104.0, 30.62], [104.0, 30.6]]]}},
                {"type": "Feature",
                 "properties": {"facility": "off-net", "reachable": False},
                 "geometry": {"type": "Polygon", "coordinates": [[
                     [104.5, 30.61], [104.501, 30.61], [104.501, 30.611],
                     [104.5, 30.611], [104.5, 30.61]]]}},
            ],
        }
        return GeoAnalysisResult(
            success=True, data=fc, summary="2 facilities")

    monkeypatch.setattr(
        SpatialAnalyzer, "isochrone_network",
        staticmethod(_fake_isochrone))
    road = _road_fc()
    facilities = _points_fc(
        [(104.005, 30.605), (104.51, 30.61)], "facility")
    result = await registry.dispatch(
        "isochrone_network",
        {"network_layer": road, "facilities": facilities,
         "travel_time": 10, "mode": "walking",
         "exclude_unreachable": True},
        session_id=SESSION)
    assert result.get("success") is True
    feats = result["data"]["features"]
    assert all(
        f["properties"].get("reachable") is not False for f in feats)
    stats = result.get("stats") or {}
    assert stats.get("excluded_unreachable") == 1
    assert any("不可达" in w for w in result.get("warnings") or [])
    # 对照：默认 False 保持 master 行为（占位面在、reachable=False 标记在）
    result2 = await registry.dispatch(
        "isochrone_network",
        {"network_layer": road, "facilities": facilities,
         "travel_time": 10, "mode": "walking"},
        session_id=SESSION)
    feats2 = result2["data"]["features"]
    assert any(
        f["properties"].get("reachable") is False for f in feats2)


def test_grid_rate_uses_explicit_denominator_channel():
    """断点 1：grid_rate 走显式分母（field=count），不再是改名计数。"""
    plan = plan_workflow(
        "statistical_zones",
        workflow_id="awf.sz.rate",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "population": DataRoleBinding(role="population", ref="ref:pop"),
        })
    grid_rate = next(s for s in plan.contract.steps
                     if s.step_id == "grid_rate")
    assert grid_rate.step_params["denominator_kind"] == "field"
    assert grid_rate.step_params["denominator"] == "count"
    # 族风险披露更新为真率语义（人口/设施负荷方向）
    assert any("人口/设施" in n or "率" in n
               for n in plan.contract.risk_notes)


async def test_plan_budget_estimate_over_limit_typed_not_runnable(
        registry, monkeypatch):
    """断点 4：descriptor 行数画像 > 预算 → plan 期 typed 指引（不进 run）。"""
    road_ref = await _store(_road_fc())
    role_refs = (
        '{"network": {"ref": "%s"}, "subject": {"ref": "ref:s"},'
        ' "facility": {"ref": "ref:f"}, "boundary": {"ref": "ref:b"}}'
        % road_ref)
    # 小 fixture：估计如实 not over
    result = await registry.dispatch(
        "webgis_analysis_workflow_plan",
        {"family": "network_proximity", "session_id": SESSION,
         "role_refs": role_refs},
        session_id=SESSION)
    assert result.get("success") is True
    by_role = {e["role"]: e for e in result.get("budget_estimates") or []}
    assert by_role["network"]["over"] is False
    assert by_role["network"]["feature_count"] == 6
    assert result.get("runnable") is True

    # 放大行数画像（模拟 12 万行路网 descriptor 事实——不改预算上限本身）
    from app.services.session_data import session_data_manager as sdm

    real_get_desc = sdm.get_ref_descriptor

    async def inflated(session_id, ref):
        desc = await real_get_desc(session_id, ref)
        if isinstance(desc, dict) and ref == road_ref:
            desc = {**desc, "feature_count": 120_000}
        return desc

    monkeypatch.setattr(sdm, "get_ref_descriptor", inflated)
    result2 = await registry.dispatch(
        "webgis_analysis_workflow_plan",
        {"family": "network_proximity",
         "workflow_id": "awf.np.budget2", "session_id": SESSION,
         "role_refs": role_refs},
        session_id=SESSION)
    assert result2.get("success") is True
    by_role2 = {e["role"]: e for e in result2.get("budget_estimates") or []}
    assert by_role2["network"]["over"] is True
    assert by_role2["network"]["reason_code"] == "AWF_BUDGET_EXCEEDED"
    assert result2.get("runnable") is False
    assert any("AWF_BUDGET_EXCEEDED" in b
               for b in result2.get("blocked_reasons") or [])
    assert "分块" in result2.get("next_action", "")
