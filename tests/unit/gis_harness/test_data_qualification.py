"""Typed Data Qualification Contract 回归锁（Goal V3）。

不变式：
- 四态裁决 + unknown：画像无事实 → unknown ≠ 不满足（与义务评估同红线）；
- 科学性检查委托算法层 scientific_preconditions（facts_used 空的
  PASS-on-no-facts 不得计为满足）；
- remediation 只引用既有修复词表；auto_applicable=True 仅限有确定性
  实现的操作；不可自动修复 → degraded 而非 transform_required；
- blocked 仅限结构性不可能（必选角色缺失 block / 空数据集）；
- 全部确定性：同 profile 同裁决；
- compiler 集成：qualify_data 阶段证据 + remediation 物化为 transform。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.data_qualification import (
    QUALIFICATION_STATES,
    REMEDIATION_OPS,
    DataQualification,
    qualify_data_role,
    qualify_workflow_data_roles,
)
from app.services.gis_harness.recipe_packs._kit import role
from app.services.gis_harness.workflow_schema import (
    DataRoleRequirement,
    resolve_data_roles,
)

_SUBJECT_POINT = role("subject", capability="poi_query",
                      artifacts=("poi_feature_set",), geometry=("point",))
_SUBJECT_POLYGON = role("subject", capability="admin_boundary_query",
                        artifacts=("polygon_feature_set",),
                        geometry=("polygon",))


class TestQualificationStates:
    def test_no_profile_is_unknown_not_unsatisfied(self):
        q = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=None)
        assert q.state == "unknown"
        assert q.reason_code == "PROFILE_FACTS_UNAVAILABLE"
        assert q.confidence == 0.0

    def test_point_profile_with_point_role_eligible(self):
        profile = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}
        q = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=profile)
        assert q.state == "eligible"
        assert q.reason_code == "PROFILE_FACTS_SATISFIED"
        assert q.confidence > 0

    def test_geometry_mismatch_point_data_polygon_role_degraded(self):
        """点数据遇面需求：聚合不可盲自动 → degraded + remediation 声明。"""
        profile = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {}}
        q = qualify_data_role(_SUBJECT_POLYGON, "bound", resolver_profile=profile)
        assert q.state == "degraded"
        assert q.remediation
        assert q.remediation[0].operation == "aggregate"
        assert q.remediation[0].auto_applicable is False

    def test_geometry_mismatch_polygon_data_point_role_transform(self):
        """面数据遇点需求：质心派生可自动 → transform_required。"""
        profile = {"featureCount": 30, "geometryTypes": ["Polygon"],
                   "fields": {}}
        q = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=profile)
        assert q.state == "transform_required"
        assert q.remediation[0].operation == "derive_field"
        assert q.remediation[0].auto_applicable is True
        assert q.remediation[0].reason_code == "GEOMETRY_DERIVE_CENTROID"

    def test_empty_dataset_blocked(self):
        profile = {"featureCount": 0, "geometryTypes": ["Point"], "fields": {}}
        q = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=profile)
        assert q.state == "blocked"
        assert q.reason_code == "EMPTY_DATASET"

    def test_required_role_unresolved_block_blocked(self):
        req = role("denominator", policy="block")
        q = qualify_data_role(req, "unresolved", resolver_profile=None)
        assert q.state == "blocked"
        assert q.reason_code == "DATA_ROLE_MISSING_DENOMINATOR"

    def test_required_role_unresolved_degrade_degraded(self):
        req = role("boundary", policy="degrade", disclosure="边界缺失降级")
        q = qualify_data_role(req, "unresolved", resolver_profile=None)
        assert q.state == "degraded"
        assert q.detail == "边界缺失降级"

    def test_external_acquisition_unknown(self):
        req = role("population", acquisition="data_fabric")
        q = qualify_data_role(req, "external", resolver_profile={"featureCount": 10})
        assert q.state == "unknown"
        assert q.reason_code == "EXTERNAL_ACQUISITION_UNVERIFIED"

    def test_optional_role_unresolved_unknown(self):
        req = role("boundary", required=False)
        q = qualify_data_role(req, "unresolved", resolver_profile=None)
        assert q.state == "unknown"

    def test_degraded_resolution_stays_degraded(self):
        q = qualify_data_role(_SUBJECT_POINT, "degraded",
                              resolver_profile={"featureCount": 60,
                                                "geometryTypes": ["Point"]})
        assert q.state == "degraded"


class TestScientificDelegation:
    def test_numeric_measure_role_with_numeric_field(self):
        req = role("measure", capability="", artifacts=(), geometry=())
        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "fields": {"value": {"type": "number"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        checks = {c["check"]: c["passed"] for c in q.checks}
        assert checks.get("numeric_field") is True

    def test_numeric_measure_role_without_numeric_field(self):
        req = role("measure", capability="", artifacts=(), geometry=())
        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        checks = {c["check"]: c["passed"] for c in q.checks}
        assert checks.get("numeric_field") is False
        assert q.state == "degraded"

    def test_denominator_strong_field_evidence(self):
        req = role("denominator", acquisition="local")
        profile = {"featureCount": 12, "geometryTypes": ["Polygon"],
                   "fields": {"population": {"type": "number"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        checks = {c["check"]: c["passed"] for c in q.checks}
        assert checks.get("denominator_field") is True

    def test_denominator_weak_hint_never_satisfies(self):
        """弱提示（total/count）不满足分母 —— 与义务评估同一保守红线。"""
        req = role("denominator", acquisition="local")
        profile = {"featureCount": 12, "geometryTypes": ["Polygon"],
                   "fields": {"total_count": {"type": "number"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        checks = {c["check"]: c["passed"] for c in q.checks}
        assert checks.get("denominator_field") is False
        assert q.state == "degraded"
        assert q.remediation[0].reason_code == "DENOMINATOR_FIELD_REQUIRED"

    def test_projection_obligation_geo_crs_requires_reproject(self):
        req = role("measure", capability="", artifacts=(), geometry=())
        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "crs": "EPSG:4326",
                   "fields": {"value": {"type": "number"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile,
                              crs_projection_obligation=True)
        ops = [r.operation for r in q.remediation]
        assert "reproject" in ops
        assert q.state == "transform_required"

    def test_no_projection_obligation_skips_crs_check(self):
        req = role("measure", capability="", artifacts=(), geometry=())
        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "crs": "EPSG:4326",
                   "fields": {"value": {"type": "number"}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        assert all(c["check"] != "projected_crs" for c in q.checks)

    def test_high_null_ratio_fields_trigger_filter(self):
        req = role("measure", capability="", artifacts=(), geometry=())
        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "fields": {"value": {"type": "number", "null_ratio": 0.8}}}
        q = qualify_data_role(req, "bound", resolver_profile=profile)
        ops = [r.operation for r in q.remediation]
        assert "filter_null" in ops
        assert q.state == "transform_required"

    def test_temporal_roles_check_time_fact(self):
        req = DataRoleRequirement(role="target_time", acquisition="local")
        with_time = {"featureCount": 5, "fields": {"年份": {"type": "integer"}}}
        q1 = qualify_data_role(req, "bound", resolver_profile=with_time)
        q2 = qualify_data_role(req, "bound",
                               resolver_profile={"featureCount": 5, "fields": {}})
        checks1 = {c["check"]: c["passed"] for c in q1.checks}
        assert checks1.get("temporal_dimension") is True
        assert all(c["check"] != "temporal_dimension" or c["passed"]
                   for c in q2.checks) is False or "temporal_dimension" not in {
            c["check"] for c in q2.checks}


class TestWorkflowIntegration:
    def test_qualify_workflow_roles_shapes(self):
        from app.services.gis_harness.recipe_packs._kit import (
            boundary_role,
            subject_role,
        )
        wf_roles = [subject_role(), boundary_role()]
        resolutions = resolve_data_roles(
            "test", None)  # 无 profile → 空
        # 构造最小 workflow profile 的数据角色解析
        from app.services.gis_harness.workflow_schema import WorkflowProfile
        profile = WorkflowProfile(domain="distribution", workflow_family="t",
                                  data_roles=wf_roles)
        resolutions = resolve_data_roles("test", profile)
        quals = qualify_workflow_data_roles(wf_roles, resolutions,
                                            resolver_profile=None)
        assert [q.state for q in quals] == ["unknown", "unknown"]

    def test_vocabulary_contract(self):
        assert set(QUALIFICATION_STATES) == {
            "eligible", "transform_required", "degraded", "blocked", "unknown"}
        for op in REMEDIATION_OPS:
            assert op.replace("_", "").isalpha()

    def test_deterministic_qualification(self):
        profile = {"featureCount": 60.0, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}
        q1 = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=profile)
        q2 = qualify_data_role(_SUBJECT_POINT, "bound", resolver_profile=profile)
        assert q1.to_bounded_dict() == q2.to_bounded_dict()

    def test_bounded_serializable(self):
        import json

        profile = {"featureCount": 40, "geometryTypes": ["Point"],
                   "crs": "EPSG:4326",
                   "fields": {"value": {"type": "number", "null_ratio": 0.9},
                              "人口": {"type": "number"}}}
        req = role("measure", capability="", artifacts=(), geometry=())
        q = qualify_data_role(req, "bound", resolver_profile=profile,
                              crs_projection_obligation=True)
        payload = json.dumps(q.to_bounded_dict(), ensure_ascii=False)
        assert len(payload) < 4000


class TestCompilerQualifyStage:
    def test_qualify_data_stage_present_and_ordered(self):
        from app.services.gis_harness.workflow_compiler import (
            COMPILER_STAGES,
            compile_workflow,
        )

        assert "qualify_data" in COMPILER_STAGES
        assert len(COMPILER_STAGES) == 15
        c = compile_workflow("成都各区的教育资源公平性如何",
                             recipe_id="education_equity_per_capita",
                             profile={"featureCount": 60,
                                      "geometryTypes": ["Point"],
                                      "fields": {"name": {"type": "string"}}})
        stage = c.stage("qualify_data")
        assert stage is not None and stage.status == "ok"
        # 分母角色走 data_fabric 通道（external）：规划期不可证伪 →
        # unknown（诚实）；分母缺失的 block 语义仍由义务阶段发出
        # （EQUITY_MISSING_DENOMINATOR）。
        quals = {q["role"]: q for q in c.data_qualifications}
        assert quals["denominator"]["state"] == "unknown"
        assert quals["subject"]["state"] == "eligible"

    def test_qualify_stage_skipped_without_workflow_profile(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow("成都小学的分布情况")
        stage = c.stage("qualify_data")
        assert stage is not None and stage.status == "skipped"

    def test_remediation_materialized_into_transformations(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        prof = {"featureCount": 5, "geometryTypes": ["Point"],
                "crs": "EPSG:4326", "numericFields": ["pm25"],
                "fields": {"pm25": {"type": "number"}}}
        c = compile_workflow(
            "用克里金插值生成污染物浓度表面",
            recipe_id="kriging_interpolation_workflow", profile=prof,
        )
        qual_sources = [t for t in c.transformations
                        if t.get("source") == "data_qualification"]
        assert qual_sources
        assert any(t["operation"] == "reproject" for t in qual_sources)
        # 自动可修复的 remediation 才进入 transform（带角色与操作）
        assert all(t["target"] for t in qual_sources)

    def test_blocked_qualification_blocks_stage(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow(
            "用克里金插值生成污染物浓度表面",
            recipe_id="kriging_interpolation_workflow",
            profile={"featureCount": 0, "geometryTypes": ["Point"],
                     "fields": {"pm25": {"type": "number"}}},
        )
        # 空数据集：结构性不可能 → 阶段 blocked
        stage = c.stage("qualify_data")
        assert stage is not None
        assert stage.status == "blocked"
