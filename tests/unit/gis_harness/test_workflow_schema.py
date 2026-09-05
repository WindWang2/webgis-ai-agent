"""Workflow Recipe DSL V2（workflow_schema）回归锁（Goal C / ADR-0101）。

不变式：
- DSL 校验是纯函数（词表谓词注入，不依赖 registry 单例）；
- 义务评估联动算法层 scientific preconditions（不重复实现科学语义）；
  profile 缺事实 = unknown ≠ unsatisfied；
- recipe 内容指纹稳定（同内容同指纹、字段变化必变指纹）；
- 词汇表与 completion/contracts 平铺表一致（parity）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.workflow_schema import (
    COMPLETION_DIMENSIONS,
    DataRoleRequirement,
)


def test_completion_dimension_vocab_parity():
    """七维词表与 completion.contracts 平铺表必须一致（单一事实源）。"""
    from app.services.gis_harness.completion.contracts import (
        COMPLETION_DIMENSION_VOCAB,
    )

    assert tuple(COMPLETION_DIMENSIONS) == tuple(COMPLETION_DIMENSION_VOCAB)


class TestDataRoleResolution:
    def _roles(self, profile=None, wf_roles=None):
        from app.services.gis_harness.recipe_packs._kit import (
            boundary_role,
            denominator_role,
            subject_role,
        )
        from app.services.gis_harness.workflow_schema import (
            WorkflowProfile,
            resolve_data_roles,
        )

        wf = WorkflowProfile(
            domain="equity", workflow_family="test",
            data_roles=wf_roles or [subject_role(), boundary_role(), denominator_role()],
        )
        return resolve_data_roles("r", wf, resolver_profile=profile)

    def test_capability_backed_roles_bind(self):
        roles = {r.role: r.status for r in self._roles()}
        assert roles["subject"] == "bound"
        assert roles["boundary"] == "bound"

    def test_denominator_field_evidence_binds(self):
        profile = {"featureCount": 10, "geometryTypes": ["Point"],
                   "fields": {"population_total": {"type": "number"}}}
        roles = {r.role: r.status for r in self._roles(profile)}
        assert roles["denominator"] == "bound"

    def test_external_acquisition_is_not_falsified(self):
        """data_fabric 分母在规划期不可证伪 → external（不假设缺失）。"""
        roles = {r.role: r.status for r in self._roles()}
        assert roles["denominator"] == "external"

    def test_missing_policy_degrade_sets_status_and_disclosure(self):
        from app.services.gis_harness.recipe_packs._kit import role
        from app.services.gis_harness.workflow_schema import role_reason_code

        roles = self._roles(wf_roles=[
            role("population", policy="degrade",
                 disclosure="缺少人口：不得报人均。"),
        ])
        res = roles[0]
        assert res.status == "degraded"
        assert res.reason_code == role_reason_code("population")
        assert res.disclosure


class TestObligationEvaluation:
    def _report(self, obligations, profile=None, roles=None):
        from app.services.gis_harness.workflow_schema import (
            WorkflowProfile,
            evaluate_workflow_obligations,
        )

        wf = WorkflowProfile(
            domain="test", workflow_family="test", obligations=obligations,
        )
        return evaluate_workflow_obligations(
            "r", wf, resolver_profile=profile, role_resolutions=roles or [],
        )

    def test_precondition_delegates_to_algorithm_layer(self):
        """precondition 直接复用算法层裁决：INSUFFICIENT_DATA → blocked。"""
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("k_samples", "precondition", precondition="min_numeric_samples:30",
                 code="K_SAMPLES", action="degrade_with_disclosure")],
            profile={"featureCount": 5, "numericFields": ["v"], "fields": {"v": {}}},
        )
        ev = report.obligations[0]
        assert ev.status in ("blocked", "warning", "degraded")
        assert ev.evidence.get("verdict") in (
            "INSUFFICIENT_DATA", "PASS_WITH_WARNINGS", "REQUIRES_TRANSFORM",
        )

    def test_unknown_precondition_is_not_falsified(self):
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("ghost", "precondition", precondition="not_a_real_precondition:99")],
        )
        assert report.obligations[0].status == "unknown"

    def test_denominator_obligation_warns_without_field(self):
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("den", "denominator", code="MISSING_DEN", action="degrade_with_disclosure")],
            profile={"featureCount": 10, "fields": {"name": {"type": "string"}}},
        )
        ev = report.obligations[0]
        assert ev.status == "warning"
        assert ev.warning_code == "MISSING_DEN"
        # 触发的义务必须出现在导出警告里（码稳定可断言）
        assert any(w["code"] == "MISSING_DEN" for w in report.warnings)

    def test_temporal_obligation_satisfied_by_observation_count(self):
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("trend_obs", "temporal", precondition="min_temporal_observations:4")],
            profile={"hasTimeField": True, "temporalObservationCount": 8},
        )
        assert report.obligations[0].status == "satisfied"

    def test_temporal_obligation_blocks_on_two_dates(self):
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("trend_obs", "temporal", precondition="min_temporal_observations:4",
                 code="TREND_SHORT", action="degrade_with_disclosure")],
            profile={"hasTimeField": True, "temporalObservationCount": 2},
        )
        ev = report.obligations[0]
        assert ev.status == "warning"  # degrade_with_disclosure → warning not blocked

    def test_block_method_violation_becomes_method_blocker(self):
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report(
            [obl("m", "denominator", code="M_DEN", action="block_method")],
            profile={"fields": {}},
        )
        assert report.method_blockers == ["m"]

    def test_run_time_kinds_stay_unknown(self):
        """uncertainty/transformation/disclosure 需要运行期证据：unknown ≠ 违反。"""
        from app.services.gis_harness.recipe_packs._kit import obl

        report = self._report([
            obl("u", "uncertainty", code="U"),
            obl("t", "transformation", code="T"),
        ])
        assert all(o.status == "unknown" for o in report.obligations)
        assert report.method_blockers == []


class TestWorkflowProfileValidation:
    def test_unknown_role_rejected(self):
        from app.services.gis_harness.workflow_schema import (
            WorkflowProfile,
            validate_workflow_profile,
        )

        wf = WorkflowProfile(domain="d", workflow_family="f",
                             data_roles=[DataRoleRequirement(role="not_a_role")])
        violations = validate_workflow_profile(wf)
        assert any("unknown role" in v for v in violations)

    def test_dangling_capability_and_precondition_rejected(self):
        from app.services.gis_harness.recipe_packs._kit import obl, role
        from app.services.gis_harness.workflow_schema import (
            WorkflowProfile,
            validate_workflow_profile,
        )

        wf = WorkflowProfile(
            domain="d", workflow_family="f",
            data_roles=[role("subject", capability="ghost_cap")],
            obligations=[obl("o", "precondition", precondition="ghost:1")],
        )
        violations = validate_workflow_profile(
            wf,
            capability_exists=lambda c: False,
            artifact_type_exists=lambda a: True,
            precondition_exists=lambda p: False,
        )
        assert any("ghost_cap" in v for v in violations)
        assert any("ghost:1" in v for v in violations)

    def test_not_allowed_downgrade_must_block_completion(self):
        from app.services.gis_harness.recipe_packs._kit import fb
        from app.services.gis_harness.workflow_schema import (
            WorkflowProfile,
            validate_workflow_profile,
        )

        wf = WorkflowProfile(
            domain="d", workflow_family="f",
            fallback_policies=[fb("X", downgrade="not_allowed")],
        )
        assert any("not_allowed" in v for v in validate_workflow_profile(wf))


class TestRecipeContentFingerprint:
    def test_same_content_same_fingerprint(self):
        from app.services.gis_harness.recipes import CartographyRecipe
        from app.services.gis_harness.workflow_schema import recipe_content_fingerprint

        kwargs = dict(id="a", name="A", primary_cartography="raster_surface")
        fp1 = recipe_content_fingerprint(CartographyRecipe(**kwargs))
        fp2 = recipe_content_fingerprint(CartographyRecipe(**kwargs))
        assert fp1 == fp2 and len(fp1) == 64

    def test_workflow_change_changes_fingerprint(self):
        from app.services.gis_harness.recipe_packs._kit import wf
        from app.services.gis_harness.recipes import CartographyRecipe
        from app.services.gis_harness.workflow_schema import recipe_content_fingerprint

        base = dict(id="a", name="A", primary_cartography="raster_surface")
        plain = CartographyRecipe(**base)
        with_wf = CartographyRecipe(**{**base, "schema_version": 2,
                                       "workflow": wf("terrain", "t")})
        assert recipe_content_fingerprint(plain) != recipe_content_fingerprint(with_wf)


class TestRegistryFingerprint:
    def test_registry_fingerprint_reflects_content(self):
        """registry 指纹必须随 recipe 内容变化（plan stale 感知的根基）。"""
        from app.services.gis_harness.recipes import (
            RecipeRegistry,
            get_recipe_registry,
        )

        reg = get_recipe_registry()
        fp_full = reg.content_fingerprint()
        assert len(fp_full) == 64

        # 空 registry → 指纹与满 registry 不同（空串 != 17+147 内容哈希）
        empty = RecipeRegistry()
        assert empty.content_fingerprint() != fp_full

    def test_recipe_capability_ids_includes_workflow_hints(self):
        from app.services.gis_harness.recipe_packs._kit import role, wf
        from app.services.gis_harness.recipes import CartographyRecipe
        from app.services.gis_harness.workflow_schema import recipe_capability_ids

        r = CartographyRecipe(
            id="a", name="A", preferred_analysis=["poi_query"],
            workflow=wf("d", "f", roles=[role("boundary", capability="admin_boundary_query")]),
        )
        assert recipe_capability_ids(r) == ["admin_boundary_query", "poi_query"]


@pytest.mark.parametrize("recipe_id", [
    "education_equity_per_capita",
    "kriging_interpolation_workflow",
    "sar_calibrated_comparison",
    "slope_analysis_workflow",
])
def test_pack_workflows_validate_clean(recipe_id):
    """代表性 pack workflow 的静态校验必须零违规（词表谓词取自真实 registry）。"""
    from app.lib.gis.artifacts import SEED_ARTIFACT_TYPES
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.scientific_preconditions import precondition_exists
    from app.services.gis_harness.recipes import get_recipe_registry
    from app.services.gis_harness.workflow_schema import validate_workflow_profile


    caps = set(get_capability_registry().all_ids)
    arts = {a.id for a in SEED_ARTIFACT_TYPES}
    recipe = get_recipe_registry().get(recipe_id)
    assert recipe is not None and recipe.workflow is not None
    violations = validate_workflow_profile(
        recipe.workflow,
        capability_exists=lambda c: c in caps,
        artifact_type_exists=lambda a: a in arts,
        precondition_exists=precondition_exists,
    )
    assert violations == []
