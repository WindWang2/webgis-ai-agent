"""统一 DataQualityProfile 回归锁（DQH V1）。

不变式：
- 聚合而非引擎：各来源保持原生形状的**有界投影**，不发明第二词表；
- gate 四态确定性收敛：unknown（无任何检查事实）< ready（仅 info 披露）
  < degraded（warning/可修复 error/来源警告）< blocked（不可修复 error /
  lib blocked / spatial blocking）；
- digest 确定性：同输入同 digest，墙钟（generated_at）绝不参与；
- 提案只走既有 propose_repairs 单点映射（新码提案已在词表锁覆盖）；
- 有界：issues ≤32（QualityReport 校验器）、字段截断投影。
"""
from __future__ import annotations

from app.lib.data.profile import FieldProfile
from app.lib.data.quality import QualityIssue, QualityIssueCode, QualityReport, QualityStatus
from app.services.data_quality.profile import (
    DataQualityProfile,
    build_data_quality_profile,
)
from app.services.data_quality.semantic_checks import (
    CHECK_TIMEZONE,
    CHECK_UNIT,
    detect_unit_ambiguity,
    evaluate_semantic_checks,
)
from app.services.spatial_quality_service import QualityIssue as AuditIssue
from app.services.spatial_quality_service import SpatialQualityReport


def _lib_report(issues, status=None):
    r = QualityReport(target_ref="ref:x", issues=list(issues),
                      checks_run=["crs_missing"], checks_not_run=["duplicate_rows"])
    if status is not None:
        r.status = status
    else:
        from app.lib.data.quality import compose_status
        r.status = compose_status(list(issues))
    return r


class TestGateConvergence:
    def test_no_facts_unknown(self):
        p = build_data_quality_profile(target_ref="ref:e")
        assert p.gate == "unknown"
        assert p.issues == []

    def test_warning_issue_degrades(self):
        p = build_data_quality_profile(
            target_ref="ref:a",
            lib_report=_lib_report([QualityIssue(code=QualityIssueCode.CRS_MISSING)]))
        assert p.gate == "degraded"
        assert any(i.code == QualityIssueCode.CRS_MISSING for i in p.issues)

    def test_info_only_issues_stay_ready(self):
        issues, run, not_run = evaluate_semantic_checks(
            fields={"面积": FieldProfile(name="面积", dtype="number",
                                         samples=[12.0, 3.0])})
        assert all(i.severity == "info" for i in issues)
        p = build_data_quality_profile(
            target_ref="ref:b", semantic_issues=issues,
            semantic_run=run, semantic_not_run=not_run)
        assert p.gate == "ready"
        assert p.disclosure["issues_total"] == len(issues)

    def test_semantic_warning_degrades(self):
        fields = {"ts": FieldProfile(name="ts", dtype="string", temporal_hint=True,
                                     samples=["2024-01-01 08:00:00"])}
        issues, run, not_run = evaluate_semantic_checks(fields=fields)
        p = build_data_quality_profile(
            target_ref="ref:c", semantic_issues=issues,
            semantic_run=run, semantic_not_run=not_run)
        assert p.gate == "degraded"

    def test_non_repairable_error_blocks(self):
        p = build_data_quality_profile(
            target_ref="ref:d",
            lib_report=_lib_report([QualityIssue(
                code=QualityIssueCode.EMPTY_PAYLOAD, severity="error",
                repairable=False)]))
        assert p.gate == "blocked"

    def test_lib_blocked_status_blocks(self):
        p = build_data_quality_profile(
            target_ref="ref:d2",
            lib_report=_lib_report(
                [QualityIssue(code=QualityIssueCode.EMPTY_PAYLOAD, severity="error",
                              repairable=False)],
                status=QualityStatus.BLOCKED))
        assert p.gate == "blocked"

    def test_spatial_blocking_blocks_warning_degrades(self):
        spatial_blocked = SpatialQualityReport(
            dataset_id="s1", overall_status="blocking",
            issue_summary={"info": 0, "warning": 0, "error": 1, "blocking": 1})
        p1 = build_data_quality_profile(target_ref="ref:s1",
                                        spatial_report=spatial_blocked)
        assert p1.gate == "blocked"
        spatial_warn = SpatialQualityReport(
            dataset_id="s2", overall_status="warning",
            issue_summary={"info": 0, "warning": 2, "error": 0, "blocking": 0})
        p2 = build_data_quality_profile(target_ref="ref:s2",
                                        spatial_report=spatial_warn)
        assert p2.gate == "degraded"

    def test_rule_fail_degrades(self):
        p = build_data_quality_profile(
            target_ref="ref:r",
            rule_report={"overall_status": "fail", "failed_count": 1,
                         "warn_count": 0, "ruleset_digest": "d1",
                         "target_kind": "vector"})
        assert p.gate == "degraded"
        assert p.sections["rules"]["ruleset_digest"] == "d1"


class TestDeterminismAndBounding:
    def _build(self):
        lib = _lib_report([QualityIssue(code=QualityIssueCode.CRS_MISSING)])
        sem_issues, sem_run, sem_not = evaluate_semantic_checks(
            fields={"面积": FieldProfile(name="面积", dtype="number",
                                         samples=[1.0, 2.0])})
        return build_data_quality_profile(
            target_ref="ref:det", dataset_fingerprint="fp-1",
            lib_report=lib, semantic_issues=sem_issues,
            semantic_run=sem_run, semantic_not_run=sem_not)

    def test_digest_two_runs_identical(self):
        p1, p2 = self._build(), self._build()
        assert p1.profile_digest == p2.profile_digest
        assert p1.profile_digest.startswith("dqprof_")
        # 墙钟元数据不参与 digest
        assert p1.generated_at != "" and p1.profile_digest == p2.profile_digest

    def test_gate_matches_inputs(self):
        p = self._build()
        assert p.gate == "degraded"

    def test_proposals_from_single_source_mapping(self):
        p = self._build()
        ops = {pr["reason_code"]: pr["operation"] for pr in p.proposals}
        assert ops.get("crs_missing") == "reproject"
        assert all("auto_applicable" in pr for pr in p.proposals)

    def test_bounded_projection_no_payload(self):
        p = self._build()
        d = p.to_bounded_dict()
        assert len(d["issues"]) <= 32
        assert len(d["proposals"]) <= 8
        assert "features" not in d and "payload" not in d

    def test_repair_plan_consumable(self):
        # 画像提案可与 RepairPlan 层对接（同词表）。
        from app.services.data_quality.repair_plan import build_repair_plan
        p = self._build()
        plan = build_repair_plan(p, dataset_identity="fp-1")
        assert plan.operations
        assert plan.dataset_identity == "fp-1"

    def test_semantic_roles_projected(self):
        from app.lib.gis.semantic_profile import (
            FieldRoleAssignment, RoleConfidence, SemanticDatasetProfile,
        )
        sem = SemanticDatasetProfile(
            field_roles=[FieldRoleAssignment(
                field="value", roles=["count_measure"],
                confidence=RoleConfidence.RULE_DERIVED)],
            role_index={"count_measure": "value"})
        p = build_data_quality_profile(target_ref="ref:sem",
                                       semantic_profile=sem)
        assert p.sections["semantic"]["role_index"] == {"count_measure": "value"}
