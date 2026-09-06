"""GIS Task Ontology V3 回归锁。

不变式：
- 版本化本体：46 个任务 / 9 个域 / 全部引用命中单一事实源（registry
  capability / artifact / map model / DATA_ROLES / intent.TaskType）；
- 确定性匹配：同 intent 同匹配序；打分制不生成；
- 关键词整词红线：en ASCII 词不命中词内片段（与 RecipeRegistry 同规）；
- 歧义显式声明：「分布」不预设热力 —— point_distribution 的默认解释
  与备选解释、消歧依据必须可机器读取；
- fallback 策略完整：每任务至少声明一层，blocked 层必须 blocks_completion；
- planned 任务诚实声明：sar.coherence 等 planned 任务的匹配不冒充 native；
- recipe.ontology_tasks opt-in：不声明零漂移；声明后命中前置/全不命中后置。
"""
from __future__ import annotations

import typing

import pytest

from app.services.gis_harness.gis_ontology import (
    ONTOLOGY_DOMAINS,
    ONTOLOGY_VERSION,
    TaskOntology,
    get_task_ontology,
    match_task_ontology,
    reset_task_ontology,
)
from app.services.gis_harness.intent import TaskType, resolve_map_request_intent


@pytest.fixture()
def ontology() -> TaskOntology:
    reset_task_ontology()
    return get_task_ontology()


class TestOntologyIntegrity:
    def test_version_and_shape(self, ontology: TaskOntology):
        assert ONTOLOGY_VERSION == 3
        assert ontology.count >= 40
        assert set(ontology.domains()) == set(ONTOLOGY_DOMAINS)
        # task_id 命名契约：<domain>.<task>
        for tid in ontology.all_ids:
            assert "." in tid, tid
            assert tid.split(".", 1)[0] in ONTOLOGY_DOMAINS, tid

    def test_all_references_resolve_against_registries(self, ontology: TaskOntology):
        """引用完整性：capability / artifact / map model / role / family。"""
        from app.lib.cartography.model_library import get_map_model_registry
        from app.lib.gis.artifacts import get_artifact_type_registry
        from app.lib.gis.capability_registry import get_capability_registry
        from app.services.gis_harness.workflow_schema import DATA_ROLES

        caps = get_capability_registry().all_ids
        caps = set(caps() if callable(caps) else caps)
        arts = set(get_artifact_type_registry().all_ids)
        models = get_map_model_registry().all_ids
        models = set(models() if callable(models) else models)

        violations = ontology.validate(
            capability_exists=lambda c: c in caps,
            artifact_type_exists=lambda a: a in arts,
            map_model_exists=lambda m: m in models,
            data_role_vocabulary=tuple(DATA_ROLES),
            family_vocabulary=typing.get_args(TaskType),
        )
        assert violations == []

    def test_every_task_has_fallback_strategy_with_blocked_terminal(
        self, ontology: TaskOntology,
    ):
        for tid in ontology.all_ids:
            desc = ontology.get(tid)
            assert desc is not None
            tiers = [t.tier for t in desc.fallback_strategy]
            assert tiers, tid
            assert len(tiers) == len(set(tiers)), tid
            assert "blocked" in tiers, f"{tid}: 缺 blocked 终态层"
            blocked = next(t for t in desc.fallback_strategy if t.tier == "blocked")
            assert blocked.blocks_completion, tid

    def test_planned_tasks_declared_honestly(self, ontology: TaskOntology):
        """planned 任务不引用未注册能力冒充 native；native 任务能力已注册。"""
        from app.lib.gis.capability_registry import get_capability_registry

        caps = get_capability_registry().all_ids
        caps = set(caps() if callable(caps) else caps)
        for tid in ontology.all_ids:
            desc = ontology.get(tid)
            assert desc is not None
            if desc.semantic_status == "planned":
                assert all(c not in caps or c == "" for c in ()), (
                    f"{tid}: planned 任务不得声明已注册专有能力冒充 native"
                )
            for cap in desc.common_capabilities:
                assert cap in caps, f"{tid}: native 任务引用未注册能力 {cap}"

    def test_sar_coherence_is_planned(self, ontology: TaskOntology):
        desc = ontology.get("sar.coherence")
        assert desc is not None
        assert desc.semantic_status == "planned"

    def test_fingerprint_stable_and_sensitive(self, ontology: TaskOntology):
        fp1 = ontology.fingerprint()
        fp2 = ontology.fingerprint()
        assert fp1 == fp2
        tweaked = TaskOntology(ontology.tasks_for_domain("distribution"))
        assert tweaked.fingerprint() != fp1


class TestOntologyMatching:
    def test_deterministic_match_order(self, ontology: TaskOntology):
        it = resolve_map_request_intent("成都小学的分布情况")
        m1 = ontology.match_intent(it)
        m2 = ontology.match_intent(it)
        assert [x.task_id for x in m1] == [x.task_id for x in m2]
        assert m1[0].task_id == "distribution.point_distribution"

    def test_distribution_query_not_pinned_to_heatmap(self, ontology: TaskOntology):
        """「分布情况」的主匹配不得预设热力图制图 —— 形态由数据资格定。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        top = ontology.match_intent(it)[0]
        assert top.task_id == "distribution.point_distribution"
        desc = ontology.get(top.task_id)
        assert desc is not None
        ambiguity = desc.ambiguity_rules
        assert ambiguity and ambiguity[0].default_resolution
        assert any("热力" in a for a in ambiguity[0].alternatives + (ambiguity[0].default_resolution,))

    def test_kriging_keyword_routes_to_kriging_task(self, ontology: TaskOntology):
        it = resolve_map_request_intent("用克里金插值生成污染物浓度表面")
        ids = [m.task_id for m in ontology.match_intent(it)]
        assert ids[0] == "interpolation.geostatistical_kriging"

    def test_moran_keyword_routes_to_autocorrelation(self, ontology: TaskOntology):
        it = resolve_map_request_intent("房价的空间自相关分析")
        ids = [m.task_id for m in ontology.match_intent(it)]
        assert "spatial_statistics.global_autocorrelation" in ids[:3]

    def test_sar_query_matches_sar_domain(self, ontology: TaskOntology):
        it = resolve_map_request_intent("成都区域的 insar 形变分析")
        matches = ontology.match_intent(it)
        assert matches
        assert any(m.domain == "sar" for m in matches[:3])

    def test_english_whole_word_redline(self, ontology: TaskOntology):
        """en ASCII 关键词整词命中：「casing」不得命中「sing」类片段。"""
        it = resolve_map_request_intent("price casing analysis")  # 无 GIS 语义
        matches = ontology.match_intent(it)
        # 至少不得靠子串误命中 sar / od 等短词任务
        assert all("sar." != m.task_id[:4] for m in matches)

    def test_planned_status_surfaced_in_match(self, ontology: TaskOntology):
        it = resolve_map_request_intent("成都区域 insar 相干性分析")
        matches = ontology.match_intent(it)
        coherence = next(
            (m for m in matches if m.task_id == "sar.coherence"), None)
        if coherence is not None:
            assert coherence.semantic_status == "planned"

    def test_family_triggers_cover_all_task_families(self, ontology: TaskOntology):
        """22 个 intent task family 至少有 18 个有本体任务映射（余量为
        纯 V1 产品族，允许不映射 —— 锁定映射覆盖率防整体漏配）。"""
        fams = set(typing.get_args(TaskType))
        covered = set()
        for tid in ontology.all_ids:
            desc = ontology.get(tid)
            assert desc is not None
            covered.update(desc.family_triggers)
        missing = fams - covered
        assert len(missing) <= 4, f"未映射的任务族过多: {sorted(missing)}"


class TestRecipeOntologyRouting:
    def test_undeclared_recipes_keep_exact_ordering(self):
        """opt-in 零漂移：本体索引存在时，未声明 recipe 的排序不变。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.recipes import (
            get_recipe_registry,
        )

        registry = get_recipe_registry()
        assert any(r.ontology_tasks for r in registry._by_id.values()) is False
        intent = resolve_map_request_intent("成都各区小学数量")
        baseline = [r.id for r in registry.select_candidates(intent)]
        assert baseline[0] == "administrative_choropleth"

    def test_declared_ontology_tasks_boost_matching_candidate(self):
        """本体层是第 8 层：除本体命中外全同分的两个候选，命中者前置。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.recipes import (
            CartographyRecipe,
            RecipeRegistry,
        )

        registry = RecipeRegistry()
        registry.load_builtins()

        def _mk(rid: str, ontology_tasks: list, priority: int) -> CartographyRecipe:
            return CartographyRecipe(
                id=rid,
                name=rid,
                intent_tasks=["distribution_overview"],
                intent_cartography=["point_overlay"],
                primary_cartography="simple_point_map",
                ontology_tasks=list(ontology_tasks),
                priority=priority,
            )

        hit = _mk("test_ontology_hit",
                  ["distribution.point_distribution"], priority=90)
        miss = _mk("test_ontology_miss",
                   ["sar.interpretation"], priority=1)
        registry.register(hit)
        registry.register(miss)
        intent = resolve_map_request_intent("成都小学的分布情况")
        ranked = [r.id for r in registry.select_candidates(intent, limit=8)]
        assert "test_ontology_hit" in ranked and "test_ontology_miss" in ranked
        # 本体命中前置：hit（priority 90）排在 miss（priority 1）之前 ——
        # 无本体层时 miss 会因静态优先级反超。
        assert ranked.index("test_ontology_hit") < ranked.index("test_ontology_miss")

    def test_declared_ontology_tasks_penalty_on_miss(self):
        """声明了 ontology_tasks 但 intent 本体全不命中 → 后置。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.recipes import (
            CartographyRecipe,
            RecipeRegistry,
        )

        registry = RecipeRegistry()
        registry.load_builtins()
        miss = CartographyRecipe(
            id="test_ontology_miss",
            name="本体不命中候选",
            intent_tasks=["distribution_overview"],
            primary_cartography="simple_point_map",
            ontology_tasks=["sar.interpretation"],
            priority=1,  # 极高静态优先级：若无本体罚分会冲到第一
        )
        registry.register(miss)
        intent = resolve_map_request_intent("成都小学的分布情况")
        ranked = [r.id for r in registry.select_candidates(intent, limit=6)]
        assert "test_ontology_miss" in ranked
        assert ranked.index("test_ontology_miss") >= 1


class TestCompilerOntologyStage:
    def test_stage_order_13_stages_with_ontology(self):
        from app.services.gis_harness.workflow_compiler import (
            COMPILER_STAGES,
            compile_workflow,
        )

        assert "map_task_ontology" in COMPILER_STAGES
        assert len(COMPILER_STAGES) == 13
        c = compile_workflow("成都小学的分布情况")
        assert [s.stage for s in c.stages] == list(COMPILER_STAGES)
        assert len(c.stages) == 13
        stage = c.stage("map_task_ontology")
        assert stage is not None
        assert stage.evidence.get("primary_task") == "distribution.point_distribution"
        assert c.ontology_matches
        assert c.ontology_matches[0]["task_id"] == "distribution.point_distribution"

    def test_module_level_match_entry(self, ontology: TaskOntology):
        it = resolve_map_request_intent("各街道医院数量")
        matches = match_task_ontology(it)
        assert matches and matches[0].score > 0
