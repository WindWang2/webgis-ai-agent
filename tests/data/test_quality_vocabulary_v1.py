"""质量词表受控扩展回归锁（Data Quality Harmonization V1）。

不变式：
- 新增 4 码（timezone_missing / unit_ambiguous / field_role_ambiguous /
  admin_mismatch）必须同时进入：QualityIssueCode、_REMEDIATIONS（修复
  建议文本）、repair_planning 映射或**诚实缺席**（无确定性修复 op 的码
  不得硬凑提案 —— field_role_ambiguous 属于澄清语义，不是数据变换）；
- 新码提案的 operation 必须 ⊆ REMEDIATION_OPS（RepairStep 构造期校验
  同源，这里锁映射产出可被 build_repair_plan 消费）；
- unit_ambiguous ≠ inconsistent_unit：欠定（无可判定证据）与矛盾（名称
  提示 vs 值域冲突）是两个发现，词表必须区分；
- 既有 21 码与既有映射不受影响（受控扩展 = 纯增量）。
"""
from __future__ import annotations

import pytest

from app.lib.data.quality import QualityIssueCode, _REMEDIATIONS
from app.services.data_ingest.repair_planning import (
    REPAIRABLE_ISSUE_CODES,
    propose_repairs_for_issue_codes,
)
from app.services.data_quality.repair_plan import build_repair_plan

NEW_CODES = (
    "timezone_missing",
    "unit_ambiguous",
    "field_role_ambiguous",
    "admin_mismatch",
)


class TestVocabularyControlledExtension:
    def test_new_codes_exist(self):
        values = {c.value for c in QualityIssueCode}
        for code in NEW_CODES:
            assert code in values, f"缺受控码 {code}"

    def test_every_code_has_remediation_text(self):
        for code in QualityIssueCode:
            assert code in _REMEDIATIONS, f"码 {code.value} 缺修复建议文本"

    def test_new_codes_absent_from_legacy_set_does_not_shrink(self):
        # 受控扩展纯增量：既有可修复码集合不被本方向收窄。
        legacy = {
            "crs_missing", "crs_suspicious", "impossible_coordinates",
            "zero_coordinates", "invalid_geometry", "self_intersection",
            "empty_geometry", "duplicate_geometries", "duplicate_rows",
            "null_heavy_field", "inconsistent_unit", "invalid_dates",
            "encoding_issues", "nodata_saturation", "resolution_mismatch",
        }
        assert legacy <= set(REPAIRABLE_ISSUE_CODES)

    def test_transformable_new_codes_have_honest_proposals(self):
        # 数据可变换的三码必须有提案；提案 operation 必须在 REMEDIATION_OPS
        # 词表内（RepairStep 构造期同源校验，build_repair_plan 走通即证明）。
        for code in ("timezone_missing", "unit_ambiguous", "admin_mismatch"):
            proposals = propose_repairs_for_issue_codes([code])
            assert proposals, f"{code} 应有修复提案"
            for p in proposals:
                assert p.auto_applicable is False, (
                    f"{code} 修复需要用户声明（时区/单位/映射），绝不自动应用"
                )
                plan = build_repair_plan(
                    _fake_report(code), dataset_identity="digest-test")
                assert plan.operations, f"{code} 应能落成 RepairPlan 步骤"

    def test_field_role_ambiguous_is_clarification_not_data_op(self):
        # 角色歧义的出路是澄清/用户声明，不是任何既有数据变换 op ——
        # 诚实不提案（与 EMPTY_PAYLOAD 同类），绝不硬凑。
        assert "field_role_ambiguous" not in REPAIRABLE_ISSUE_CODES
        assert propose_repairs_for_issue_codes(["field_role_ambiguous"]) == []

    def test_deterministic_same_codes_same_plan(self):
        codes = ["timezone_missing", "unit_ambiguous", "admin_mismatch"]
        p1 = build_repair_plan(_fake_report(*codes), dataset_identity="d-1")
        p2 = build_repair_plan(_fake_report(*codes), dataset_identity="d-1")
        # 既有契约：plan_id 与操作序列确定性；created_at 是墙钟元数据，
        # 明确不参与指纹（repair_plan.compute_plan_id docstring）。
        assert p1.plan_id == p2.plan_id
        d1, d2 = p1.to_bounded_dict(), p2.to_bounded_dict()
        d1.pop("created_at"), d2.pop("created_at")
        assert d1 == d2


def _fake_report(*codes: str):
    """最小 QualityReport 鸭子类型（propose_repairs 只读 issues[].code）。"""
    from app.lib.data.quality import QualityIssue, QualityReport

    return QualityReport(
        target_ref="ref:test",
        issues=[QualityIssue(code=QualityIssueCode(c)) for c in codes],
    )


@pytest.mark.parametrize("code", NEW_CODES)
def test_issue_model_accepts_new_code(code: str):
    from app.lib.data.quality import QualityIssue

    issue = QualityIssue(code=QualityIssueCode(code))
    assert issue.remediation  # model_post_init 自动补文本
