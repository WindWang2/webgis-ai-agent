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
        "session_id": SESSION}, session_id=SESSION)
    assert plan_res["success"], plan_res
    wf_id = plan_res["workflow_id"]
    assert plan_res["runnable"] is True
    states = {s["step_id"]: s["verdict"] for s in plan_res["steps"]}
    assert states["per_capita_rate"] == "skipped"    # 无分母绑定
    assert plan_res["risk_notes"]

    # 2) run：真实执行主链
    run_res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf_id, "session_id": SESSION}, session_id=SESSION)
    assert run_res["status"] == "satisfied", run_res
    layers = {item["step_id"]: item for item in run_res["layers"]}
    assert layers["zones_count"]["ref"].startswith("ref:geojson")
    first_ref = layers["zones_count"]["ref"]
    assert run_res["evidence"]["all_have_output_ref"] is True
    assert set(run_res["evidence"]["steps_executed"]) == {
        "zones_count", "area_density"}

    # 3) status：只读投影一致
    status_res = await registry.dispatch("webgis_analysis_workflow_status", {
        "workflow_id": wf_id, "session_id": SESSION}, session_id=SESSION)
    assert status_res["success"]
    assert status_res["status"] == "satisfied"
    assert {"zones_count", "area_density"} <= set(status_res["receipts"])

    # 4) 幂等重跑：ref 不变
    rerun = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf_id, "session_id": SESSION}, session_id=SESSION)
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
        "session_id": SESSION}, session_id=SESSION)
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
        "session_id": SESSION}, session_id=SESSION)
    assert plan_res["success"] is True                 # plan 本身成功
    assert plan_res["runnable"] is False               # 但不可执行
    codes = {s["reason_code"] for s in plan_res["steps"]}
    assert any("AWF_DATA_ROLE_MISSING:BOUNDARY" == c for c in codes)
    assert plan_res["alternatives"]

    run_res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": plan_res["workflow_id"], "session_id": SESSION}, session_id=SESSION)
    assert run_res["status"] in ("blocked", "failed")
    assert run_res["status"] != "satisfied"            # 工具失败不得 pass


async def test_plan_no_network_declines_with_alternatives(registry):
    poi_ref, dist_ref = await _store_fixtures()
    plan_res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "service_accessibility",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    assert plan_res["runnable"] is False
    iso = next(s for s in plan_res["steps"] if s["step_id"] == "isochrone")
    assert iso["reason_code"] == "AWF_NO_NETWORK_FOR_ACCESSIBILITY"
    joined_alts = " ".join(plan_res["alternatives"])
    assert "科学含义" in joined_alts or "欧氏距离" in joined_alts


async def test_plan_unknown_family_and_bad_json(registry):
    res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "no_such_family", "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is False
    assert res["reason_code"] == "AWF_UNKNOWN_FAMILY"

    res2 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "hazard_exposure", "role_refs": "{not-json",
        "session_id": SESSION}, session_id=SESSION)
    assert res2["success"] is False
    assert res2["reason_code"] == "AWF_BAD_JSON"


async def test_run_unknown_workflow_declined(registry):
    res = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": "awf.ghost.000", "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is False
    assert res["reason_code"] == "AWF_WORKFLOW_NOT_FOUND"


async def test_recompute_unblocks_with_new_data_and_reports_honestly(registry):
    """P1-1 清偿钉扎：补齐缺失角色 → 解除阻断真实执行；recomputed_steps
    只报真实重新 dispatch 的步骤。"""
    poi_ref, dist_ref = await _store_fixtures()
    plan1 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "service_accessibility",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    wf = plan1["workflow_id"]
    assert plan1["runnable"] is False                 # 无路网 blocked
    run1 = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert run1["status"] == "blocked"
    assert run1["layers"] == []                       # 零执行零产物

    # 补齐路网 → recompute 解除阻断（不需要换 workflow_id 重新 plan）
    roads = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"road_id": "h0"},
             "geometry": {"type": "LineString", "coordinates": [
                 [104.00, 30.605], [104.04, 30.605]]}},
            {"type": "Feature", "properties": {"road_id": "v0"},
             "geometry": {"type": "LineString", "coordinates": [
                 [104.01, 30.60], [104.01, 30.63]]}}]}
    road_ref = await session_data_manager.store(SESSION, roads,
                                                prefix="geojson")
    rec = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": wf,
        "role_refs": json.dumps({"network": road_ref}),
        "session_id": SESSION}, session_id=SESSION)
    assert rec["success"], rec
    assert rec["status"] == "satisfied"
    # 诚实报告：列出的步骤全部真实重新执行过（有产物/ref）
    layers = {item["step_id"]: item for item in rec["layers"]}
    assert set(rec["recomputed_steps"]) == {
        "isochrone", "coverage_zones"}                  # population skipped
    assert layers["isochrone"]["ref"].startswith("ref:geojson")


async def test_plan_idempotent_preserves_run_state(registry):
    """P1-2/P3-3 清偿钉扎：同 id 同指纹重复 plan 不清空运行态。"""
    poi_ref, dist_ref = await _store_fixtures()
    plan1 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    wf = plan1["workflow_id"]
    run = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert run["status"] == "satisfied"
    first_layers = {item["step_id"]: item["ref"]
                    for item in run["layers"]}
    # 重复 plan（同绑定）→ 状态保留（幂等复用，非空覆写）
    plan2 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    assert plan2["workflow_id"] == wf
    status = await registry.dispatch("webgis_analysis_workflow_status", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert status["status"] == "satisfied"             # 不是被重置的 pending
    assert set(status["receipts"]) == {"zones_count", "area_density"}
    assert {item["step_id"]: item["ref"]
            for item in status["layers"]} == first_layers


async def test_run_max_features_clamped_server_side(registry):
    """P2-1a 清偿钉扎：max_features 不可被调用方解锁（只能调低）。"""
    big = {"type": "FeatureCollection",
           "features": [
               {"type": "Feature", "properties": {"id": i},
                "geometry": {"type": "Point",
                             "coordinates": [104.006 + (i % 6) * 0.0055,
                                             30.605 + (i // 6) * 0.0042]}}
               for i in range(150_000)]}
    big_ref = await session_data_manager.store(SESSION, big, prefix="geojson")
    _, dist_ref = await _store_fixtures()
    plan = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": big_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    # 尝试用超大 max_features 解锁 → 服务端钳制回默认上限 → 仍 typed blocked
    run = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": plan["workflow_id"],
        "max_features": 10**12,
        "session_id": SESSION}, session_id=SESSION)
    assert run["status"] == "blocked"
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["zones_count"]["reason_code"] == "AWF_BUDGET_EXCEEDED"


async def test_recompute_noop_reuses_by_fingerprint(registry):
    """B2 钉扎：原样重发绑定（同身份）→ 指纹复用，零重跑零重铸。"""
    import json as _json
    poi_ref, dist_ref = await _store_fixtures()
    plan = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": _json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    wf = plan["workflow_id"]
    run = await registry.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert run["status"] == "satisfied"
    first = {item["step_id"]: item["ref"] for item in run["layers"]}
    rec = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": wf,
        "role_refs": _json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    assert rec["success"], rec
    assert rec["recomputed_steps"] == []          # 同身份 → 零重新 dispatch
    assert {item["step_id"]: item["ref"]
            for item in rec["layers"]} == first   # 产物 ref 不重铸


async def test_plan_qualifier_change_is_replan(registry):
    """R2b P3 钉扎：同 id 同 ref 只改 qualifier.year → 显式重规划
    （temporal 门裁决轴不撕裂）。"""
    import json as _json
    from app.services.session_data import session_data_manager as sdm
    phase = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"year": 1},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.0, 30.6], [104.01, 30.6], [104.01, 30.61],
             [104.0, 30.61], [104.0, 30.6]]]}}]}
    ref = await sdm.store(SESSION, phase, prefix="geojson")
    p1 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "temporal_change", "workflow_id": "awf.qual.t",
        "role_refs": _json.dumps({
            "baseline": {"ref": ref, "qualifier": {"year": 2023}},
            "target_time": {"ref": ref, "qualifier": {"year": 2023}}}),
        "session_id": SESSION}, session_id=SESSION)
    assert p1["runnable"] is False                   # 同年 → MISMATCH
    p2 = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "temporal_change", "workflow_id": "awf.qual.t",
        "role_refs": _json.dumps({
            "baseline": {"ref": ref, "qualifier": {"year": 2018}},
            "target_time": {"ref": ref, "qualifier": {"year": 2023}}}),
        "session_id": SESSION}, session_id=SESSION)
    assert p2["runnable"] is True                    # 年份对齐 → ready
    st = await registry.dispatch("webgis_analysis_workflow_status", {
        "workflow_id": "awf.qual.t", "session_id": SESSION}, session_id=SESSION)
    steps = {s["step_id"]: s for s in st["steps"]}
    assert steps["change_diff"]["state"] == "pending"   # 新裁决持久化
    assert steps["change_diff"]["reason_code"] == ""    # 不再持旧 MISMATCH


async def test_plan_reason_code_without_colon_does_not_crash(registry,
                                                             monkeypatch):
    """P2 钉扎：reason_code 只有族前缀无冒号 → blocked_next_steps 不得
    IndexError（role 提取需冒号保护）。"""
    from app.services.gis_harness.analysis_workflows import orchestrator
    import app.tools.analysis_workflow_tools as awf_tools

    real_plan = orchestrator.plan_workflow

    def _fake_plan(family, **kwargs):
        result = real_plan(family, **kwargs)
        verdicts = list(result.step_verdicts)
        idx = next(i for i, v in enumerate(verdicts)
                   if v.verdict in ("blocked", "skipped"))
        verdicts[idx] = orchestrator.StepPlanVerdict(
            step_id=verdicts[idx].step_id, verdict="blocked",
            reason_code="AWF_DATA_ROLE_MISSING")     # 刻意不带 ":<ROLE>"
        return result.model_copy(update={"step_verdicts": verdicts})

    monkeypatch.setattr(awf_tools, "plan_workflow", _fake_plan)
    poi_ref, dist_ref = await _store_fixtures()
    res = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is True, res
    entry = next(e for e in res["blocked_next_steps"]
                 if e["reason_code"] == "AWF_DATA_ROLE_MISSING")
    assert entry["next_step"]["action"] == "bind_role"


async def test_recompute_corrupt_contract_snapshot_typed_decline(registry):
    """P2 钉扎：损坏的契约快照 → AWF_CONTRACT_SNAPSHOT_CORRUPT 而非
    ValidationError 冒泡。"""
    from app.services.gis_harness.analysis_workflows.state import (
        WorkflowRunState,
        state_alias,
        state_to_payload,
    )
    wf = "awf.corrupt.snapshot"
    state = WorkflowRunState(
        workflow_id=wf, family="admin_facility_distribution",
        contract_snapshot={"workflow_id": wf})   # 缺 family 等必填 → 校验失败
    ref = await session_data_manager.store(
        SESSION, state_to_payload(state), prefix="awf-state")
    await session_data_manager.set_alias(SESSION, ref, state_alias(wf))
    res = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is False
    assert res["reason_code"] == "AWF_CONTRACT_SNAPSHOT_CORRUPT"


async def test_recompute_legacy_family_typed_decline(registry):
    """P2 钉扎：快照携带已下线族 id → AWF_UNKNOWN_FAMILY 而非 KeyError。"""
    from app.services.gis_harness.analysis_workflows.orchestrator import (
        plan_workflow,
    )
    from app.services.gis_harness.analysis_workflows.state import (
        WorkflowRunState,
        state_alias,
        state_to_payload,
    )
    wf = "awf.legacy.family"
    result = plan_workflow(
        "admin_facility_distribution", workflow_id=wf, bindings={},
        include_optional_steps=True)
    snapshot = result.contract.model_dump()
    snapshot["family"] = "legacy_retired_family"
    state = WorkflowRunState(
        workflow_id=wf, family="legacy_retired_family",
        contract_snapshot=snapshot)
    ref = await session_data_manager.store(
        SESSION, state_to_payload(state), prefix="awf-state")
    await session_data_manager.set_alias(SESSION, ref, state_alias(wf))
    res = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is False
    assert res["reason_code"] == "AWF_UNKNOWN_FAMILY"


async def test_recompute_invalid_role_kind_typed_decline(registry):
    """P2 钉扎：recompute 传入越界 role_kind → AWF_INVALID_CONTRACT
    而非 ContractValidationError 冒泡。"""
    poi_ref, dist_ref = await _store_fixtures()
    plan = await registry.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution",
        "role_refs": json.dumps({"subject": poi_ref, "boundary": dist_ref}),
        "session_id": SESSION}, session_id=SESSION)
    res = await registry.dispatch("webgis_analysis_workflow_recompute", {
        "workflow_id": plan["workflow_id"],
        "role_refs": json.dumps(
            {"subject": {"ref": poi_ref, "role_kind": "bogus"}}),
        "session_id": SESSION}, session_id=SESSION)
    assert res["success"] is False
    assert res["reason_code"] == "AWF_INVALID_CONTRACT"
    assert res["next_step"]["action"] == "bind_role"
