"""Fallback V3 四层语义回退回归锁。

不变式：
- 四层确定性裁决：blocked > minimal > degraded > preferred（只降不升）；
- planned 能力诚实红线：uses_planned_capability 或本体任务 planned →
  不得 preferred，强制降档 + 披露（不冒充 native）；
- 无数据事实（规划期无 profile）：诚实缺省 preferred（unknown ≠ 不满足，
  不因画像缺席虚构降级），planned 在场仍降档；
- 事实在手且无任何可执行路径 → minimal + 披露（仅描述性输出）；
- 降级分类复用 DOWNGRADE_CLASSES 词表（proxy/approximation/degraded/
  not_allowed / equivalent）；
- compiler 集成：fallback_tier / 披露进入 completion contract 与编译产物。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.fallback_v3 import (
    FALLBACK_TIERS,
    resolve_fallback_tier,
)
from app.services.gis_harness.workflow_schema import DOWNGRADE_CLASSES


class TestTierResolution:
    def test_vocabulary_aligned(self):
        assert set(FALLBACK_TIERS) == {"preferred", "degraded", "minimal", "blocked"}

    def test_no_facts_defaults_preferred_not_minimal(self):
        """规划期无 profile：unknown ≠ unsatisfied，不虚构降级。"""
        r = resolve_fallback_tier(data_states=())
        assert r.tier == "preferred"
        assert r.downgrade_class == "equivalent"
        assert not r.blocked_reasons

    def test_unknown_only_states_stay_preferred(self):
        r = resolve_fallback_tier(data_states=("unknown", "unknown"))
        assert r.tier == "preferred"

    def test_method_blocker_blocked(self):
        r = resolve_fallback_tier(method_blockers=("kriging_numeric_field",))
        assert r.tier == "blocked"
        assert r.downgrade_class == "not_allowed"
        assert any("kriging_numeric_field" in x for x in r.blocked_reasons)

    def test_data_blocker_blocked(self):
        r = resolve_fallback_tier(data_blockers=("denominator",))
        assert r.tier == "blocked"

    def test_qualification_blocked_state_blocked(self):
        r = resolve_fallback_tier(data_states=("eligible", "blocked"))
        assert r.tier == "blocked"
        assert any("data_qualification_blocked" in x for x in r.blocked_reasons)

    def test_degraded_state_degraded_with_disclosure(self):
        r = resolve_fallback_tier(
            data_states=("eligible", "degraded"),
            extra_disclosures=("缺少分母：不得下人均结论。",))
        assert r.tier == "degraded"
        assert r.downgrade_class == "approximation"
        assert "缺少分母：不得下人均结论。" in r.disclosures

    def test_no_executable_path_minimal(self):
        r = resolve_fallback_tier(data_states=("degraded", "degraded"))
        assert r.tier == "minimal"
        assert r.downgrade_class == "degraded"
        assert r.disclosures  # 必须披露，不得静默

    def test_minimal_uses_scenario_disclosure(self):
        r = resolve_fallback_tier(
            data_states=("degraded",),
            scenario_minimal_disclosure="仅输出点位计数与描述统计。")
        assert r.tier == "minimal"
        assert "仅输出点位计数与描述统计。" in r.disclosures

    def test_eligible_all_preferred(self):
        r = resolve_fallback_tier(data_states=("eligible", "eligible"))
        assert r.tier == "preferred"
        assert r.downgrade_class == "equivalent"

    def test_transform_required_still_preferred_when_rest_eligible(self):
        """transform_required 是可自动修复路径，不构成降档事实。"""
        r = resolve_fallback_tier(data_states=("eligible", "transform_required"))
        assert r.tier == "preferred"

    def test_downgrade_classes_from_single_vocabulary(self):
        """降级分类不另造词表：全部命中 DOWNGRADE_CLASSES。"""
        for states in ((), ("unknown",), ("eligible",), ("eligible", "degraded"),
                       ("degraded",)):
            r = resolve_fallback_tier(data_states=states)
            assert r.downgrade_class in DOWNGRADE_CLASSES


class TestPlannedHonesty:
    def test_planned_capability_never_preferred(self):
        r = resolve_fallback_tier(
            data_states=("eligible",), uses_planned_capability=True)
        assert r.tier == "degraded"
        assert r.downgrade_class == "proxy"
        assert any("planned" in d for d in r.disclosures)

    def test_planned_ontology_task_never_preferred(self):
        """本体任务声明 planned（如 sar.coherence）→ 强制降档 + 披露。"""
        r = resolve_fallback_tier(
            ontology_task_id="sar.coherence", data_states=("eligible",))
        assert r.tier == "degraded"
        assert r.downgrade_class == "proxy"
        assert any("planned" in d for d in r.disclosures)

    def test_planned_with_no_facts_still_degraded(self):
        r = resolve_fallback_tier(uses_planned_capability=True, data_states=())
        assert r.tier == "degraded"
        assert r.disclosures

    def test_native_ontology_task_no_planned_penalty(self):
        r = resolve_fallback_tier(
            ontology_task_id="distribution.point_distribution",
            data_states=("eligible",))
        assert r.tier == "preferred"
        assert not any("planned" in d for d in r.disclosures)


class TestDeterminism:
    def test_same_input_same_resolution(self):
        kwargs = dict(
            ontology_task_id="distribution.point_distribution",
            data_states=("eligible", "degraded"),
            extra_disclosures=("x",),
        )
        r1 = resolve_fallback_tier(**kwargs)
        r2 = resolve_fallback_tier(**kwargs)
        assert r1.to_bounded_dict() == r2.to_bounded_dict()

    def test_bounded_serializable(self):
        import json

        r = resolve_fallback_tier(
            data_states=("eligible", "degraded"),
            extra_disclosures=("d1", "d2"))
        payload = json.dumps(r.to_bounded_dict(), ensure_ascii=False)
        assert len(payload) < 2000


class TestCompilerIntegration:
    def test_fallback_resolution_in_compilation(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow("成都小学的分布情况")
        fr = c.fallback_resolution
        assert fr.get("tier") in FALLBACK_TIERS
        cc = c.completion_contract
        assert cc.get("fallback_tier") in FALLBACK_TIERS
        assert cc.get("fallback_downgrade_class") in DOWNGRADE_CLASSES

    def test_blocked_contract_carries_blocked_tier(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        prof = {"featureCount": 5, "geometryTypes": ["Point"],
                "fields": {"pm25": {"type": "number"}},
                "numericFields": ["pm25"], "crs": "EPSG:4326"}
        c = compile_workflow(
            "用克里金插值生成污染物浓度表面",
            recipe_id="kriging_interpolation_workflow", profile=prof)
        # 样本量不足 → 义务 blocked → fallback tier blocked
        assert c.fallback_resolution["tier"] == "blocked"
        assert c.completion_contract["fallback_tier"] == "blocked"

    def test_stage_evidence_records_tier(self):
        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow("成都小学的分布情况")
        stage = c.stage("produce_completion_contract")
        assert stage is not None
        assert stage.evidence.get("fallback_tier") in FALLBACK_TIERS
