"""选择性重算与生命周期回归锁（goal-02 M7）。

- style-only 变更 → 永不触发科学重算（核心验收）；
- 角色内容变更 → 直接消费者 + 下游闭包（只失效该失效的）；
- 指纹对账：同身份零漂移；ref 换/内容 revision 变 → 漂移；
- 取消：步骤间检查点 → cancelled 终态、不冒充完成；
- 恢复：持久状态 round-trip 后 resume，未完成步骤继续、已完成步骤
  复用（ref 不重铸）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import WorkflowExecutor
from app.services.gis_harness.analysis_workflows.families import build_contract
from app.services.gis_harness.analysis_workflows.orchestrator import plan_workflow
from app.services.gis_harness.analysis_workflows.recompute import (
    collect_stale_steps,
    downstream_closure,
    plan_invalidate_on_role_change,
    ref_identity,
    style_only_invalidation,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
    state_from_payload,
    state_to_payload,
)
from app.services.session_data import session_data_manager

SESSION = "awf-recompute-test-session"


def _districts() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"district": name},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, y], [x + 0.02, y], [x + 0.02, y + 0.016],
                 [x, y + 0.016], [x, y]]]}}
            for x, y, name in [
                (104.00, 30.600, "nw"), (104.02, 30.600, "ne"),
                (104.00, 30.616, "sw"), (104.02, 30.616, "se")]]}


def _pois(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": {"type": "Point",
                          "coordinates": [104.006 + (i % 6) * 0.0055,
                                          30.605 + (i // 6) * 0.0042]}}
            for i in range(n)]}


@pytest.fixture
def registry():
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


async def _plan(bindings):
    return plan_workflow("admin_facility_distribution",
                         workflow_id="awf.rc.test", bindings=bindings)


async def test_style_only_never_invalidates():
    contract = build_contract("admin_facility_distribution",
                              workflow_id="awf.rc.style")
    assert style_only_invalidation(contract) == set()


async def test_role_change_invalidates_consumers_and_downstream_only():
    contract = build_contract("admin_facility_distribution",
                              workflow_id="awf.rc.role")
    state = new_state(contract, contract_fingerprint="fp")
    # subject 变更 → zones_count/area_density/per_capita_rate（全部直接或
    # 下游消费 subject）；boundary 变更 → 同样全部（两角色都是直接输入）。
    # 用 hazard 族区分直接 vs 下游：hazard 变更 → hazard_buffer（直接）+
    # exposure_intersect（下游）；receptor 变更 → 只 exposure_intersect。
    hz = build_contract("hazard_exposure", workflow_id="awf.rc.hz")
    affected = plan_invalidate_on_role_change(hz, state, "receptor", "ref:x@2")
    assert affected == {"exposure_intersect"}          # 不含 hazard_buffer
    affected2 = plan_invalidate_on_role_change(hz, state, "hazard", "ref:y@2")
    assert affected2 == {"hazard_buffer", "exposure_intersect"}


async def test_collect_stale_steps_detects_identity_drift(registry):
    poi_ref = await session_data_manager.store(SESSION, _pois(18),
                                               prefix="geojson")
    dist_ref = await session_data_manager.store(SESSION, _districts(),
                                                prefix="geojson")
    bindings = {"subject": DataRoleBinding(role="subject", ref=poi_ref),
                "boundary": DataRoleBinding(role="boundary", ref=dist_ref)}
    plan = await _plan(bindings)
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status == "satisfied"

    # 同身份对账：零漂移
    same = collect_stale_steps(
        plan.contract, state,
        current_identities={
            "subject": {"points": ref_identity(poi_ref, 1)},
            "boundary": {"polygons": ref_identity(dist_ref, 1)}})
    assert same == []

    # 输入身份漂移（新 ref / revision 变）→ zones_count 与下游 stale
    drifted = collect_stale_steps(
        plan.contract, state,
        current_identities={
            "subject": {"points": ref_identity("ref:geojson-new", 1)},
            "boundary": {"polygons": ref_identity(dist_ref, 1)}})
    stale_ids = {d["step_id"] for d in drifted}
    # per_capita_rate 是 skipped（无计算发生）——skipped 步骤不进 stale 集
    assert stale_ids == {"zones_count", "area_density"}
    by_id = {d["step_id"]: d for d in drifted}
    assert by_id["zones_count"]["downstream"] == [
        "area_density", "per_capita_rate"]
    assert by_id["area_density"]["downstream"] == []


async def test_cancellation_between_steps_is_honest(registry):
    poi_ref = await session_data_manager.store(SESSION, _pois(12),
                                               prefix="geojson")
    dist_ref = await session_data_manager.store(SESSION, _districts(),
                                                prefix="geojson")
    plan = await _plan({
        "subject": DataRoleBinding(role="subject", ref=poi_ref),
        "boundary": DataRoleBinding(role="boundary", ref=dist_ref)})
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)

    class CancelledToken:
        cancelled = True

    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager,
                                cancel_token=CancelledToken())
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status == "cancelled"
    assert all(e.state in ("cancelled", "pending", "skipped")
               for e in state.steps.values())
    assert state.receipts == {}                    # 未执行 = 零 receipt


async def test_resume_after_persist_roundtrip_reuses_satisfied(registry):
    poi_ref = await session_data_manager.store(SESSION, _pois(20),
                                               prefix="geojson")
    dist_ref = await session_data_manager.store(SESSION, _districts(),
                                                prefix="geojson")
    plan = await _plan({
        "subject": DataRoleBinding(role="subject", ref=poi_ref),
        "boundary": DataRoleBinding(role="boundary", ref=dist_ref)})
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    first_ref = state.envelopes["zones_count"].ref

    # 模拟进程重启：持久 → 载荷 → 反序列化 → 只跑 zones_count
    restored = state_from_payload(state_to_payload(state))
    assert restored is not None
    executor2 = WorkflowExecutor(registry=registry,
                                 session_data=session_data_manager)
    restored = await executor2.execute(
        SESSION, plan.contract, restored, only_steps={"zones_count"})
    # 幂等：同指纹复用，ref 不重铸
    assert restored.envelopes["zones_count"].ref == first_ref
    assert any(e.get("event") == "step_reused"
               for e in restored.run_log if isinstance(e, dict))
    assert restored.status == "satisfied"


async def test_downstream_closure_pure_function():
    contract = build_contract("service_accessibility",
                              workflow_id="awf.rc.closure")
    closure = downstream_closure(contract, {"isochrone"})
    assert closure == {"coverage_zones", "coverage_population"}
    assert downstream_closure(contract, {"coverage_population"}) == set()


async def test_final_gate_projection_blocks_on_science_rejection():
    """G02 终局门接线：执行层科学硬拒 → G-SCIENCE-BLOCKED（工具失败不 pass）。"""
    from app.services.gis_harness.analysis_workflows.evidence import (
        run_final_gate,
    )
    contract = build_contract("hazard_exposure", workflow_id="awf.gate.t")
    state = new_state(contract, contract_fingerprint="fp")
    state.steps["hazard_buffer"].state = "failed"
    state.steps["hazard_buffer"].reason_code = (
        "scientific_precondition:local_metric_crs_required")
    gate = run_final_gate(state)
    assert gate["blocked"] is True
    assert any(f["code"] == "G-SCIENCE-BLOCKED" for f in gate["findings"])
    # 正常满足态：无阻断
    ok_state = new_state(contract, contract_fingerprint="fp")
    for entry in ok_state.steps.values():
        entry.state = "satisfied"
    gate2 = run_final_gate(ok_state)
    assert gate2["blocked"] is False


async def test_repeated_dispatch_status_is_honest_failure():
    """B3 钉扎：dispatch 缝 repeated 拦截 → 步骤诚实失败（不吞成空成功）。"""
    from app.services.gis_harness.analysis_workflows.contracts import (
        AnalysisStepSpec,
    )
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )

    class _Outcome:
        status = "repeated"
        raw_result = {"success": True, "note": "Loop blocked"}
        geojson_ref = None
        error_msg = None
        ref_descriptor = None

    async def _repeated_dispatch(tool, args, session_id):
        return _Outcome()

    contract = build_contract("hazard_exposure", workflow_id="awf.rep.t")
    contract = contract.model_copy(update={"steps": (
        AnalysisStepSpec(step_id="only", tool_name="buffer_analysis",
                         role_inputs={"geojson": "role:hazard"},
                         step_params={"distance": 100.0, "unit": "m"}),)})
    contract.data_contracts["hazard"] = DataRoleBinding(
        role="hazard", ref="ref:geojson-abc")
    state = new_state(contract, contract_fingerprint="fp")
    executor = WorkflowExecutor(
        registry=None, session_data=None, dispatch=_repeated_dispatch)
    await executor._run_step("s", contract, contract.steps[0], state)
    entry = state.steps["only"]
    assert entry.state == "failed"
    assert entry.reason_code == "TOOL_DISPATCH_REPEATED"
    assert state.receipts == {}


async def test_upstream_budget_gate_blocks_oversized_intermediate():
    """B7/P2-1b 钉扎：step: 上游产物超预算 → typed blocked（不无界传递）。"""
    from app.services.gis_harness.analysis_workflows.contracts import (
        AnalysisStepSpec,
    )
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )
    contract = build_contract("hazard_exposure", workflow_id="awf.up.t")
    contract = contract.model_copy(update={"steps": (
        AnalysisStepSpec(step_id="up", tool_name="buffer_analysis",
                         role_inputs={"geojson": "role:hazard"}),
        AnalysisStepSpec(step_id="down", tool_name="spatial_join",
                         role_inputs={"left_layer": "role:receptor",
                                      "right_layer": "step:up"},
                         depends_on=("up",)))})
    contract.data_contracts["hazard"] = DataRoleBinding(
        role="hazard", ref="ref:geojson-h")
    contract.data_contracts["receptor"] = DataRoleBinding(
        role="receptor", ref="ref:geojson-r")
    state = new_state(contract, contract_fingerprint="fp")
    state.steps["up"].state = "satisfied"
    executor = WorkflowExecutor(registry=None, session_data=None,
                                max_features=10)
    # 直接注入超限上游产物（绕过 store——单元级钉扎门行为）
    executor._step_payloads["up"] = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {},
                      "geometry": {"type": "Point",
                                   "coordinates": [104.0, 30.6]}}] * 11}
    args, identities, warnings = await executor._resolve_inputs(
        "s", contract, contract.steps[1], state)
    assert args is None
    assert any("AWF_BUDGET_EXCEEDED" in w for w in warnings)


async def test_plan_rebinding_same_id_is_replan_not_silent_reuse(registry):
    """B1 钉扎：同 id 换绑 → 显式重规划（新裁决持久化），不静默复用旧绑定。"""
    import json as _json
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools
    from app.agent_pi_bridge import set_tool_registry
    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    from app.services.session_data import session_data_manager
    S = "awf-b1-session"
    pois = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": i},
         "geometry": {"type": "Point",
                      "coordinates": [104.006 + (i % 6) * 0.0055,
                                      30.605 + (i // 6) * 0.0042]}}
        for i in range(12)]}
    dists = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"district": "n", "population": 1},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.00, 30.60], [104.04, 30.60], [104.04, 30.632],
             [104.00, 30.632], [104.00, 30.60]]]}}]}
    pr = await session_data_manager.store(S, pois, prefix="geojson")
    dr = await session_data_manager.store(S, dists, prefix="geojson")
    # plan 1：无 denominator
    p1 = await reg.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution", "workflow_id": "awf.b1.fixed",
        "role_refs": _json.dumps({"subject": pr, "boundary": dr}),
        "session_id": S})
    assert p1["success"]
    # plan 2：同 id + 补 denominator → 必须重规划（per_capita ready 持久化）
    p2 = await reg.dispatch("webgis_analysis_workflow_plan", {
        "family": "admin_facility_distribution", "workflow_id": "awf.b1.fixed",
        "role_refs": _json.dumps({"subject": pr, "boundary": dr,
                                  "denominator": dr}),
        "session_id": S})
    assert p2["workflow_id"] == "awf.b1.fixed"
    run = await reg.dispatch("webgis_analysis_workflow_run", {
        "workflow_id": "awf.b1.fixed", "session_id": S})
    assert run["status"] == "satisfied"
    steps = {s["step_id"]: s["state"] for s in run["steps"]}
    assert steps["per_capita_rate"] == "satisfied"   # 新绑定真实生效
    ev = run["evidence"]
    assert "per_capita_rate" in ev["steps_executed"]
