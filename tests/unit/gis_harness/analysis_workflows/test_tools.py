"""分析工作流工具面测试（goal-02 M4）—— 真实 registry dispatch 缝，零 LLM。

对齐 scene3d 工具面测试惯例（set_tool_registry 注入）：
- plan：裁决入状态块、typed blocked 携带 alternatives；
- run：真实执行、receipts 有界投影、幂等重跑；
- status：只读投影；
- recompute：输入变更 → 只重跑受影响子图、未受影响步骤复用原 ref。
"""
from __future__ import annotations

import json

import pytest

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry

SESSION = "awf-tools-test-session"


@pytest.fixture
def registry():
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


@pytest.fixture
def district_and_pois():
    """预置 POI 与行政区两个 ref（模块级 async store 不能进 fixture——
    用返回 coroutine 的工厂）。"""
    return _store_fixtures


async def _store_fixtures() -> tuple[str, str]:
    pois = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": {"type": "Point",
                          "coordinates": [104.006 + (i % 6) * 0.0055,
                                          30.605 + (i // 6) * 0.0042]}}
            for i in range(24)]}
    districts = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"district": name},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, y], [x + 0.02, y], [x + 0.02, y + 0.016],
                 [x, y + 0.016], [x, y]]]}}
            for x, y, name in [
                (104.00, 30.600, "nw"), (104.02, 30.600, "ne"),
                (104.00, 30.616, "sw"), (104.02, 30.616, "se")]]}
    poi_ref = await session_data_manager.store(SESSION, pois, prefix="geojson")
    dist_ref = await session_data_manager.store(
        SESSION, districts, prefix="geojson")
    return poi_ref, dist_ref


async def test_plan_run_status_recompute_full_chain(registry):
    poi_ref, dist_ref = await _store_fixtures()

    # 1) plan：typed 裁决 + 状态块持久
    plan_res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION})
    assert plan_res["success"], plan_res
    wf_id = plan_res["workflow_id"]
    assert plan_res["runnable"] is True
    states = {s["step_id"]: s["verdict"] for s in plan_res["steps"]}
    assert states["per_capita_rate"] == "skipped"    # 无分母绑定
    assert plan_res["risk_notes"]

    # 2) run：真实执行主链
    run_res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf_id, "session_id": SESSION})
    assert run_res["status"] == "satisfied", run_res
    layers = {item["step_id"]: item for item in run_res["layers"]}
    assert layers["zones_count"]["ref"].startswith("ref:geojson")
    first_ref = layers["zones_count"]["ref"]
    assert run_res["evidence"]["all_have_output_ref"] is True
    assert set(run_res["evidence"]["steps_executed"]) == {
        "zones_count", "area_density"}

    # 3) status：只读投影一致
    status_res = await registry.dispatch("webgis_analysis_workflow_status", {
        "workflow_id": wf_id, "session_id": SESSION})
    assert status_res["success"]
    assert status_res["status"] == "satisfied"
    assert {"zones_count", "area_density"} <= set(status_res["receipts"])

    # 4) 幂等重跑：ref 不变
    rerun = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf_id, "session_id": SESSION})
    assert {item["step_id"]: item["ref"] for item in rerun["layers"]}["zones_count"] \
        == first_ref

    # 5) recompute：换 POI 数据 → zones_count/area_density 重跑（subject
    #    直接消费者 + 下游），per_capita_rate 保持 skipped
    poi_ref2 = await session_data_manager.store(SESSION, {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": {"type": "Point",
                          "coordinates": [104.006 + (i % 3) * 0.0055,
                                          30.605 + (i // 3) * 0.0042]}}
            for i in range(9)]}, prefix="geojson")
    rec_res = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": wf_id,
        "role_refs": json.dumps({"subject": poi_ref2}),
        "session_id": SESSION})
    assert rec_res["success"], rec_res
    assert "zones_count" in rec_res["recomputed_steps"]
    assert rec_res["layers"]["zones_count"]["ref"] if isinstance(
        rec_res["layers"], dict) else True
    layers2 = {item["step_id"]: item for item in rec_res["layers"]}
    assert layers2["zones_count"]["ref"] != first_ref   # 内容变了 → 新 ref
    assert layers2["zones_count"]["feature_count"] == 4
    fc = await session_data_manager.get(
        SESSION, layers2["zones_count"]["ref"])
    assert sum(f["properties"]["facility_count"]
               for f in fc["features"]) == 9


async def test_plan_missing_boundary_typed_blocked(registry):
    poi_ref, _ = await _store_fixtures()
    plan_res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref}),
        "session_id": SESSION})
    assert plan_res["success"] is True                 # plan 本身成功
    assert plan_res["runnable"] is False               # 但不可执行
    codes = {s["reason_code"] for s in plan_res["steps"]}
    assert any("AWF_DATA_ROLE_MISSING:BOUNDARY" == c for c in codes)
    assert plan_res["alternatives"]

    run_res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": plan_res["workflow_id"], "session_id": SESSION})
    assert run_res["status"] in ("blocked", "failed")
    assert run_res["status"] != "satisfied"            # 工具失败不得 pass


async def test_plan_no_network_declines_with_alternatives(registry):
    poi_ref, dist_ref = await _store_fixtures()
    plan_res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "service_accessibility",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION})
    assert plan_res["runnable"] is False
    iso = next(s for s in plan_res["steps"] if s["step_id"] == "isochrone")
    assert iso["reason_code"] == "AWF_NO_NETWORK_FOR_ACCESSIBILITY"
    joined_alts = " ".join(plan_res["alternatives"])
    assert "科学含义" in joined_alts or "欧氏距离" in joined_alts


async def test_plan_unknown_family_and_bad_json(registry):
    res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "no_such_family", "session_id": SESSION})
    assert res["success"] is False
    assert res["reason_code"] == "AWF_UNKNOWN_FAMILY"

    res2 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "hazard_exposure", "role_refs": "{not-json",
        "session_id": SESSION})
    assert res2["success"] is False
    assert res2["reason_code"] == "AWF_BAD_JSON"


async def test_run_unknown_workflow_declined(registry):
    res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": "awf.ghost.000", "session_id": SESSION})
    assert res["success"] is False
    assert res["reason_code"] == "AWF_WORKFLOW_NOT_FOUND"
