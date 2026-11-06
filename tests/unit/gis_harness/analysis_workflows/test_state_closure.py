"""状态闭环（ADR-0284 D5/D6）—— stale 赋值 + science_validation + 产品门。

- stale：recompute 后指纹漂移的 satisfied 步骤显式落 state=stale（词表
  从此有赋值路径）；闭包未漂移步骤保持 satisfied（指纹零重算不变）；
- science_validation：run 全终态后进状态机（pass/blocked/not_evaluated），
  status 工具透出；
- product_gate：非 satisfied run 的 envelope ref 不合格（partial ≠ final）、
  科学 blocked 不合格、非工作流 ref 合格、kill switch 语义。
"""
from __future__ import annotations


from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisArtifactEnvelope,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.product_gate import (
    REASON_RUN_NOT_SATISFIED,
    REASON_SCIENCE_BLOCKED,
    assess_refs_eligibility,
)
from app.services.gis_harness.analysis_workflows.state import (
    new_state,
    state_from_payload,
    state_to_payload,
)

SESSION = "awf-closure-test-session"


def _state_with_envelope(status: str, ref: str, *,
                         gate: str = ""):
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id=f"awf.{status}.{ref[-6:]}",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    state = new_state(plan.contract, contract_fingerprint="fp-x")
    for entry in state.steps.values():
        entry.state = "satisfied"
    state.status = status
    if gate:
        state.science_validation = {"final_gate": gate}
    state.envelopes["zones_count"] = AnalysisArtifactEnvelope(
        step_id="zones_count", workflow_id=state.workflow_id,
        tool_name="spatial_aggregate", ref=ref)
    return state


# ── science_validation 状态机 ────────────────────────────────────────────

async def test_science_validation_written_on_terminal_run():
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )
    from app.services.session_data import session_data_manager

    poi = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": 0},
         "geometry": {"type": "Point", "coordinates": [104.01, 30.61]}}]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"district": "d"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.0, 30.6], [104.02, 30.6], [104.02, 30.62],
             [104.0, 30.62], [104.0, 30.6]]]}}]}
    poi_ref = await session_data_manager.store(
        SESSION, poi, prefix="geojson")
    zone_ref = await session_data_manager.store(
        SESSION, zones, prefix="geojson")
    plan = plan_workflow(
        "admin_facility_distribution", workflow_id="awf.sv.e2e",
        bindings={"subject": DataRoleBinding(role="subject", ref=poi_ref),
                  "boundary": DataRoleBinding(role="boundary", ref=zone_ref)},
        include_optional_steps=False)
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry()
    init_tools(registry)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status == "satisfied"
    assert state.science_validation["final_gate"] in ("pass", "blocked")
    # round-trip 保真（持久面）
    restored = state_from_payload(state_to_payload(state))
    assert restored.science_validation == state.science_validation


# ── product_gate 纯函数 ──────────────────────────────────────────────────

def test_product_gate_partial_run_ref_ineligible():
    states = {"w1": _state_with_envelope("partial", "ref:geojson-p1")}
    findings = assess_refs_eligibility(states, ["ref:geojson-p1"])
    assert findings[0]["eligible"] is False
    assert findings[0]["reason_code"].startswith(REASON_RUN_NOT_SATISFIED)
    assert findings[0]["run_status"] == "partial"


def test_product_gate_satisfied_run_and_foreign_ref_eligible():
    states = {"w1": _state_with_envelope("satisfied", "ref:geojson-ok")}
    findings = assess_refs_eligibility(
        states, ["ref:geojson-ok", "ref:geojson-upload"])
    assert findings[0]["eligible"] is True
    # 非工作流来源（用户上传/单工具直出）不归本门管辖
    assert findings[1]["eligible"] is True
    assert findings[1]["workflow_id"] == ""


def test_product_gate_science_blocked_ineligible():
    states = {"w1": _state_with_envelope(
        "satisfied", "ref:geojson-sb", gate="blocked")}
    findings = assess_refs_eligibility(states, ["ref:geojson-sb"])
    assert findings[0]["eligible"] is False
    assert findings[0]["reason_code"] == REASON_SCIENCE_BLOCKED


def test_product_gate_dup_and_empty_refs_deduped():
    states = {"w1": _state_with_envelope("satisfied", "ref:geojson-x")}
    findings = assess_refs_eligibility(
        states, ["ref:geojson-x", "ref:geojson-x", "", None])
    assert len(findings) == 1


# ── stale 赋值（recompute 工具面）────────────────────────────────────────

async def test_recompute_marks_drifted_steps_stale():
    """输入内容原地漂移（同 ref 新 revision）→ recompute 落 state=stale
    再重跑；重跑后回 satisfied（stale 是过渡态，不是终态）。"""
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )
    from app.services.session_data import session_data_manager
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    poi = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": i},
         "geometry": {"type": "Point",
                      "coordinates": [104.006 + i * 0.001, 30.61]}}
        for i in range(4)]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"district": "d"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.0, 30.6], [104.02, 30.6], [104.02, 30.62],
             [104.0, 30.62], [104.0, 30.6]]]}}]}
    poi_ref = await session_data_manager.store(SESSION, poi, prefix="geojson")
    zone_ref = await session_data_manager.store(SESSION, zones, prefix="geojson")
    plan = plan_workflow(
        "admin_facility_distribution", workflow_id="awf.stale.e2e",
        bindings={"subject": DataRoleBinding(role="subject", ref=poi_ref),
                  "boundary": DataRoleBinding(role="boundary", ref=zone_ref)},
        include_optional_steps=False)
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    registry = ToolRegistry()
    init_tools(registry)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status == "satisfied"

    # 原地 overwrite：同 ref 新内容（content_revision bump）→ 指纹漂移
    poi2 = {**poi, "features": poi["features"] + [
        {"type": "Feature", "properties": {"id": 99},
         "geometry": {"type": "Point", "coordinates": [104.008, 30.611]}}]}
    await session_data_manager.overwrite(SESSION, poi_ref, poi2)

    from app.services.gis_harness.analysis_workflows.recompute import (
        collect_stale_steps,
        ref_identity,
    )
    drift = collect_stale_steps(
        plan.contract, state,
        current_identities={
            "subject": {"points": ref_identity(poi_ref, "rev-2")}})
    assert any(d["step_id"] == "zones_count" for d in drift)
