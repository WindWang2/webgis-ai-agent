"""Envelope v2 实测类型化产物 + typed 衔接门（ADR-0282 M3）。

锁定：
- envelope v2 additive 字段默认=未知（绝不虚填），v1 旧载荷 round-trip 兼容；
- envelope_facts：FC 有界扫描（geometry_types/extent）、result 实测口径采集、
  栅格事实采集、聚合基数静态映射；
- semantic_gate：geometry_kind 违例 typed blocked（新码 AWF_INPUT_SEMANTIC_
  MISMATCH / AWF_ROLE_GEOMETRY_MISMATCH）；事实缺失=披露不阻断；
- executor：envelope v2 装配（crs/几何/extent/口径/owner/aggregation_basis）、
  step semantic_contract 执行期核验、dispatch repeated 步骤级幂等复用；
- FINGERPRINT_SCHEMA=2（语义相关实测字段进指纹的升版事实）。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.gis_harness.analysis_workflows import envelope_facts
from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_INPUT_SEMANTIC_MISMATCH,
    AWF_ROLE_GEOMETRY_MISMATCH,
    AGGREGATION_BASIS_VOCABULARY,
    AnalysisArtifactEnvelope,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.recompute import (
    FINGERPRINT_SCHEMA,
    step_fingerprint,
)
from app.services.gis_harness.analysis_workflows.semantic_gate import (
    check_payload_semantics,
    check_role_qualifier,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-env2-test-session"


def _fc(types: List[str], n: int = 3) -> Dict[str, Any]:
    geom = {
        "Point": {"type": "Point", "coordinates": [104.01, 30.61]},
        "MultiPoint": {"type": "MultiPoint",
                       "coordinates": [[104.01, 30.61], [104.02, 30.62]]},
        "LineString": {"type": "LineString",
                       "coordinates": [[104.0, 30.6], [104.1, 30.7]]},
        "Polygon": {"type": "Polygon", "coordinates": [[
            [104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7],
            [104.0, 30.6]]]},
    }
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": geom[t]}
            for i, t in enumerate(types * n)],
    }


# ── 1. envelope v2 字段 ──────────────────────────────────────────────────

def test_envelope_v2_defaults_unknown_and_roundtrips_v1_payload():
    env = AnalysisArtifactEnvelope(
        step_id="s", workflow_id="w", tool_name="t")
    assert env.schema_version == 2
    assert env.geometry_types == ()
    assert env.extent == ()
    assert env.result_semantics == {}
    assert env.raster == {}
    assert env.aggregation_basis == ""
    assert env.owner_session_id == ""
    # v1 旧载荷（无新键）反序列化不炸，缺省=未知
    v1_payload = {
        "step_id": "s", "workflow_id": "w", "tool_name": "t",
        "ref": "ref:geojson-x", "crs": "", "units": {},
        "feature_count": 5, "empty": False, "stats": {},
    }
    restored = AnalysisArtifactEnvelope.model_validate(v1_payload)
    assert restored.schema_version == 2
    assert restored.geometry_types == ()
    assert restored.aggregation_basis == ""


def test_aggregation_basis_vocabulary_closed():
    assert set(AGGREGATION_BASIS_VOCABULARY) == {
        "", "one_to_one", "many_to_one_sum", "m_n_join",
        "zonal_mean", "zonal_sum"}


# ── 2. envelope_facts 有界采集 ───────────────────────────────────────────

def test_collect_fc_facts_geometry_types_and_extent():
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [104.2, 30.8]}},
        ],
    }
    facts = envelope_facts.collect_fc_facts(fc)
    assert facts.geometry_types == ("Point",)
    assert facts.extent == (104.0, 30.6, 104.2, 30.8)
    assert facts.crs == "EPSG:4326"


def test_collect_fc_facts_mixed_types_dedup_bounded():
    fc = _fc(["Point", "MultiPoint", "Polygon"], n=1)
    facts = envelope_facts.collect_fc_facts(fc)
    assert set(facts.geometry_types) == {"Point", "MultiPoint", "Polygon"}


def test_collect_fc_facts_scan_cap_deterministic():
    # 3000 特征只扫前 2000——扫描上界是确定性契约（有界）
    fc = _fc(["Point"], n=2001)
    fc["features"].append(
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [999.0, 999.0]}})
    facts = envelope_facts.collect_fc_facts(fc)
    assert facts.extent[2] < 999.0


def test_collect_fc_facts_empty_and_malformed():
    facts = envelope_facts.collect_fc_facts(
        {"type": "FeatureCollection", "features": []})
    assert facts.geometry_types == ()
    assert facts.extent == ()
    assert envelope_facts.collect_fc_facts({}) .geometry_types == ()


def test_collect_result_semantics_from_spatial_semantics_block():
    result = {
        "success": True,
        "spatial_semantics": {
            "source_crs": "EPSG:4326",
            "crs_class": "geographic",
            "working_crs": "EPSG:32648",
            "calculation_mode": "projected_metric",
        },
        "stats": {"density_unit": "个/km²", "count": 10},
    }
    sem = envelope_facts.collect_result_semantics(result)
    assert sem["source_crs"] == "EPSG:4326"
    assert sem["working_crs"] == "EPSG:32648"
    assert sem["density_unit"] == "个/km²"
    assert len(sem) <= 8


def test_collect_raster_facts_conservative():
    result = {
        "success": True,
        "result_ref": "ref:raster-abc",
        "stats": {"nodata": -9999.0, "target_resolution": 30.0,
                  "band_count": 1, "dtype": "float32", "unrelated": "x"},
    }
    facts = envelope_facts.collect_raster_facts(result)
    assert facts["nodata"] == -9999.0
    assert facts["target_resolution"] == 30.0
    assert "unrelated" not in facts
    # R1 评审 P3：通用键（crs/width/height/resolution）不进栅格白名单——
    # 矢量结果携带同键不污染 envelope.raster
    polluted = envelope_facts.collect_raster_facts(
        {"stats": {"crs": "EPSG:4326", "resolution": 10, "width": 3}})
    assert polluted == {}


def test_aggregation_basis_static_mapping():
    assert envelope_facts.aggregation_basis_for(
        "spatial_aggregate", {}) == "many_to_one_sum"
    assert envelope_facts.aggregation_basis_for(
        "spatial_join", {"join_type": "inner"}) == "m_n_join"
    assert envelope_facts.aggregation_basis_for(
        "zonal_stats", {}) == "zonal_mean"
    assert envelope_facts.aggregation_basis_for(
        "buffer_analysis", {}) == "one_to_one"
    # overlay 是几何变换不是聚合——不声明（"" 比错误声明诚实）；
    # zonal_stats 工具签名无 stats 参数（R1 评审 P3 移除参数级死分支）
    assert envelope_facts.aggregation_basis_for(
        "overlay_analysis", {}) == ""
    assert envelope_facts.aggregation_basis_for(
        "zonal_stats", {"stats": ["sum"]}) == "zonal_mean"


# ── 3. semantic_gate ─────────────────────────────────────────────────────

def test_gate_geometry_kind_mismatch_blocks():
    fc = _fc(["Polygon"], n=1)
    finding = check_payload_semantics(
        "points", fc, {"points": "geometry_kind:point"})
    assert finding is not None
    assert finding.code == f"{AWF_INPUT_SEMANTIC_MISMATCH}:points"


def test_gate_multi_kind_normalized_match():
    fc = _fc(["MultiPoint"], n=1)
    assert check_payload_semantics(
        "points", fc, {"points": "geometry_kind:point"}) is None


def test_gate_no_contract_or_unknown_facts_no_block():
    assert check_payload_semantics("x", _fc(["Polygon"], 1), {}) is None
    # 无几何特征的载荷（非 FC dict）→ 不阻断（权威守门在工具 preflight）
    assert check_payload_semantics(
        "x", {"foo": 1}, {"x": "geometry_kind:point"}) is None


def test_gate_role_qualifier_mismatch_and_disclosure():
    req_point = {"role": "subject", "geometry_kinds": ["point"]}
    blocked = check_role_qualifier(
        req_point, DataRoleBinding(
            role="subject", ref="ref:x",
            qualifier={"geometry_types": ["Polygon"]}))
    assert blocked is not None
    assert blocked.code == f"{AWF_ROLE_GEOMETRY_MISMATCH}:SUBJECT"
    # 画像缺失 → 披露不阻断
    assert check_role_qualifier(
        req_point, DataRoleBinding(role="subject", ref="ref:x")) is None
    # 画像一致 → 无发现
    assert check_role_qualifier(
        req_point, DataRoleBinding(
            role="subject", ref="ref:x",
            qualifier={"geometry_types": ["Point"]})) is None


# ── 4. 指纹升版 ──────────────────────────────────────────────────────────

def test_fingerprint_schema_bumped_to_2():
    assert FINGERPRINT_SCHEMA == 2
    fp = step_fingerprint(
        AnalysisStepSpec(step_id="s", tool_name="t"),
        input_identities={"a": "ref:x@1"},
        science=__import__(
            "app.services.gis_harness.analysis_workflows.contracts",
            fromlist=["ScienceContext"]).ScienceContext())
    assert len(fp) == 64


# ── 5. orchestrator plan 期几何门 ────────────────────────────────────────

def test_plan_role_geometry_mismatch_typed_blocked():
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.plan.geom",
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref="ref:polygon-subject",
                qualifier={"geometry_types": ["Polygon"]}),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    assert not plan.runnable
    assert any(
        v.reason_code.startswith(AWF_ROLE_GEOMETRY_MISMATCH)
        for v in plan.step_verdicts)


def test_plan_role_geometry_unknown_disclosed_not_blocked():
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.plan.geom2",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:subj"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    assert plan.runnable
    assert any("几何画像缺失" in w for w in plan.warnings)


# ── 6. executor 装配 + 执行期门 + repeated 复用 ───────────────────────────

@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


async def _store(data: dict) -> str:
    return await session_data_manager.store(SESSION, data, prefix="geojson")


async def test_executor_envelope_measured_facts(registry):
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )
    poi_ref = await _store(_fc(["Point"], n=6))
    dist_ref = await _store(_fc(["Polygon"], n=2))
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.env2.e2e",
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref=poi_ref,
                qualifier={"geometry_types": ["Point"]}),
            "boundary": DataRoleBinding(
                role="boundary", ref=dist_ref,
                qualifier={"geometry_types": ["Polygon"]}),
        })
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.steps["zones_count"].state == "satisfied"
    env = state.envelopes["zones_count"]
    # 实测：产物是聚合后的区面（Polygon）——不是输入点；extent 有界实测；
    # 聚合基数/owner 语义正确；声明单位仍在 units
    assert "Polygon" in env.geometry_types
    assert env.aggregation_basis == "many_to_one_sum"
    assert env.owner_session_id == SESSION
    assert len(env.extent) == 4


async def test_executor_semantic_contract_blocks_mismatch(registry):
    """subject 绑定面数据 + zones_count 声明 geometry_kind=point →
    执行期 typed blocked（本测试用契约级 semantic_contract 直接构造）。"""
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.env2.gate",
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref="ref:anything"),   # 不真实水合——直灌载荷
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    contract = plan.contract.model_copy(deep=True)
    # 给 zones_count 加严语义合同（面数据应被拒）
    for step in contract.steps:
        if step.step_id == "zones_count":
            step.semantic_contract = {"points": "geometry_kind:point"}
    poly_ref = await _store(_fc(["Polygon"], n=3))
    contract.data_contracts["subject"] = DataRoleBinding(
        role="subject", ref=poly_ref)
    state = new_state(contract, contract_fingerprint="fp-test")
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, contract, state)
    entry = state.steps["zones_count"]
    assert entry.state == "blocked"
    assert entry.reason_code.startswith(AWF_INPUT_SEMANTIC_MISMATCH)


async def test_executor_repeated_dispatch_reuses_prior_step():
    """同 run 内两步骤同工具同参同输入 → 第二步 dispatch repeated →
    复用第一步 envelope（步骤级幂等，不再 failed）。"""
    from app.services.gis_harness.analysis_workflows.executor import (
        WorkflowExecutor,
    )

    class RepeatedOutcome:
        status = "repeated"
        raw_result = None
        error_msg = ""
        geojson_ref = ""

    call_state = {"n": 0}
    fc = _fc(["Point"], n=2)

    async def fake_dispatch(tool, args, session_id):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return {"success": True, "data": fc}
        return RepeatedOutcome()

    contract = AnalysisWorkflowContract(
        workflow_id="awf.repeat", family="admin_facility_distribution",
        steps=(
            AnalysisStepSpec(step_id="a", tool_name="t1"),
            AnalysisStepSpec(step_id="b", tool_name="t1"),
        ))
    state = new_state(contract, contract_fingerprint="fp-repeat")
    executor = WorkflowExecutor(
        registry=None, session_data=session_data_manager,
        dispatch=fake_dispatch)
    state = await executor.execute(SESSION, contract, state)
    assert state.steps["a"].state == "satisfied"
    assert state.steps["b"].state == "satisfied", (
        f"repeated 应复用而非 failed: {state.steps['b'].reason_code}")
    assert state.envelopes["b"].ref == state.envelopes["a"].ref
