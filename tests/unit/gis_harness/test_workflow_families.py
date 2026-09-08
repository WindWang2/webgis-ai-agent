"""Recipe V3 分层组合（family / composite / scenario）回归锁。

不变式：
- Family 是 RecipeRegistry 的确定性投影：同 registry 同投影；成员变化
  → family 变化（指纹可感知）；
- 覆盖靠组合：atomic 164 + family + composite + scenario ≥ 300 且零复制
  （composite 只引用既有 recipe id）；
- 全部引用（recipe/family/ontology task/component）真实存在，悬空 fatal；
- 场景匹配确定性：主体词 + 本体任务双信号命中才激活；成都小学示范场景
  必须激活且制图候选不预设热力图；
- registry_validation 收口分层校验。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.recipes import (
    CartographyRecipe,
    RecipeRegistry,
    get_recipe_registry,
)
from app.services.gis_harness.workflow_families import (
    FAMILY_LAYER_VERSION,
    CompositeRecipe,
    WorkflowFamilyRegistry,
    build_families_from_registry,
    get_workflow_family_registry,
    reset_workflow_family_registry,
)


@pytest.fixture()
def layer() -> WorkflowFamilyRegistry:
    reset_workflow_family_registry()
    return get_workflow_family_registry()


class TestFamilyProjection:
    def test_families_derived_from_registry(self, layer: WorkflowFamilyRegistry):
        registry = get_recipe_registry()
        assert layer.family_count >= 100
        # 全部 164 个 recipe 都归属唯一 family
        covered = [rid for f in layer.families for rid in f.member_recipe_ids]
        assert sorted(covered) == sorted(registry.all_ids)
        assert len(covered) == len(set(covered))

    def test_projection_deterministic(self, layer: WorkflowFamilyRegistry):
        registry = get_recipe_registry()
        again = build_families_from_registry(registry)
        assert [f.model_dump() for f in again] == [
            f.model_dump() for f in layer.families]

    def test_projection_tracks_member_change(self, layer: WorkflowFamilyRegistry):
        """成员 recipe 变化 → family 投影变化（不另建第二事实源）。"""
        registry = RecipeRegistry()
        registry.load_builtins()
        before = build_families_from_registry(registry)
        registry.register(CartographyRecipe(
            id="test_family_probe", name="probe",
            intent_tasks=["distribution_overview"],
            primary_cartography="simple_point_map",
            workflow=__import__(
                "app.services.gis_harness.recipe_packs._kit",
                fromlist=["wf"]).wf("density", "probe_family", zh=["探针"]),
        ))
        after = build_families_from_registry(registry)
        assert len(after) == len(before) + 1
        new_ids = {f.family_id for f in after} - {f.family_id for f in before}
        assert new_ids == {"density.probe_family"}

    def test_family_aggregates_member_facts(self, layer: WorkflowFamilyRegistry):
        fam = layer.family("statistics.global_autocorrelation")
        assert fam is not None
        assert "global_moran_autocorrelation" in fam.member_recipe_ids
        assert "global_morans_i" in fam.capabilities
        assert fam.obligations

    def test_seed_families_single_member(self, layer: WorkflowFamilyRegistry):
        fam = layer.family("seed.poi_distribution_overview")
        assert fam is not None
        assert fam.is_seed_family
        assert fam.member_recipe_ids == ("poi_distribution_overview",)

    def test_ontology_auto_link_via_family_triggers(
            self, layer: WorkflowFamilyRegistry):
        fam = layer.family("seed.poi_distribution_overview")
        assert fam is not None
        # distribution.point_distribution 的 family_triggers 含
        # distribution_overview → 自动链接
        assert "distribution.point_distribution" in fam.ontology_tasks

    def test_family_of_recipe(self, layer: WorkflowFamilyRegistry):
        fam = layer.family_of_recipe("kriging_interpolation_workflow")
        assert fam is not None
        assert "interpolation" in fam.family_id


class TestComposites:
    def test_all_references_exist(self, layer: WorkflowFamilyRegistry):
        registry = get_recipe_registry()
        for c in layer.composites:
            assert registry.get(c.base_recipe_id) is not None, c.composite_id
            for rid in c.supporting_recipe_ids:
                assert registry.get(rid) is not None, c.composite_id

    def test_count_in_target_range(self, layer: WorkflowFamilyRegistry):
        """atomic + 分层实体合计在 300-500 参考区间（组合生成覆盖）。"""
        total = (get_recipe_registry().count + layer.family_count
                 + layer.composite_count + layer.scenario_count)
        assert 300 <= total <= 500

    def test_no_duplicate_member_copies(self):
        """composite 不复制算法事实：成员引用即全部内容。"""
        for c in COMPOSITE_RECIPES_REF():
            dumped = c.model_dump()
            # 组合层不得携带 capability/precondition 等算法事实字段
            assert not any(k in dumped for k in (
                "preferred_analysis", "obligations", "preconditions"))

    def test_density_chain_has_significance_guard(self, layer: WorkflowFamilyRegistry):
        c = layer.composite("composite.density_screening_chain")
        assert c is not None
        # 显著性层只在 eligible（统计前提满足）时并入 —— 防「任何分布都上热点」
        assert c.supporting_requires_states == ("eligible",)
        assert c.disclosures


class TestScenarios:
    def test_chengdu_school_scenario_activates(self, layer: WorkflowFamilyRegistry):
        from app.services.gis_harness.gis_ontology import match_task_ontology
        from app.services.gis_harness.intent import resolve_map_request_intent

        intent = resolve_map_request_intent("成都小学的分布情况")
        tasks = [m.task_id for m in match_task_ontology(intent)]
        activated = layer.match_scenarios(intent, tasks)
        assert "scenario.school_distribution" in [s.scenario_id for s in activated]
        scenario = layer.scenario("scenario.school_distribution")
        assert scenario is not None
        # 制图候选含点图/格网/热力等多种形态 —— 由数据资格裁决，不预设热力
        assert "poi_distribution_overview" in scenario.candidate_recipes
        assert "administrative_choropleth" in scenario.candidate_recipes
        assert scenario.minimal_disclosure

    def test_scenario_requires_both_signals(self, layer: WorkflowFamilyRegistry):
        from app.services.gis_harness.intent import resolve_map_request_intent

        # 只命中主体词、不命中本体任务 → 不激活（双信号契约）
        intent = resolve_map_request_intent("成都小学的分布情况")
        activated = layer.match_scenarios(intent, ["sar.interpretation"])
        assert activated == []

    def test_all_scenarios_have_minimal_fallback(self, layer: WorkflowFamilyRegistry):
        for s in layer.scenarios:
            assert s.minimal_disclosure, s.scenario_id
            assert s.candidate_recipes, s.scenario_id


class TestValidationAndFingerprint:
    def test_validate_clean(self, layer: WorkflowFamilyRegistry):
        issues = layer.validate(get_recipe_registry())
        assert issues == []

    def test_validate_catches_dangling_refs(self, layer: WorkflowFamilyRegistry):
        layer._composites["composite.broken_probe"] = CompositeRecipe(
            composite_id="composite.broken_probe", label_zh="坏引用",
            base_recipe_id="nonexistent_recipe",
            trigger_ontology_tasks=["nonexistent.task"],
        )
        issues = layer.validate(get_recipe_registry())
        assert any("nonexistent_recipe" in i for i in issues)
        assert any("nonexistent.task" in i for i in issues)
        del layer._composites["composite.broken_probe"]

    def test_fingerprint_stable(self, layer: WorkflowFamilyRegistry):
        assert FAMILY_LAYER_VERSION == 3
        fp1 = layer.fingerprint()
        reset_workflow_family_registry()
        layer2 = get_workflow_family_registry()
        assert layer2.fingerprint() == fp1


class TestRegistryValidationIntegration:
    def test_validate_gis_library_covers_family_layer(self):
        from app.services.gis_harness.registry_validation import (
            validate_gis_library,
        )

        issues = validate_gis_library()
        assert not any("workflow_families" in i for i in issues)


def COMPOSITE_RECIPES_REF():
    from app.services.gis_harness.workflow_families import COMPOSITE_RECIPES
    return COMPOSITE_RECIPES
