"""Semantic V2（ADR-0098）：决策族一等产品语义回归锁。

选址/适建/风险/公平 不再落入 distribution 兜底 —— 它们有专属 intent
task、专属 recipe、含 mcda_evaluation 的能力计划，以及随行的规划期
义务披露（稳定机器可读 code）。本套件锁定：
1. 四族中英查询解析到专属 task；
2. recipe 选择命中专属 recipe（且不再退回 poi_distribution_overview）；
3. 计划包含 mcda_evaluation（site/suitability）或暴露叠加能力（risk）；
4. 方法论警告 code 精确触发（不多发、不漏发）；
5. 纯统计/邻近查询不受新规则污染（负例回归）。
"""
from __future__ import annotations

import pytest


def _plan(query: str):
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    return MapProductPlanner().plan_from_intent(
        resolve_map_request_intent(query), use_memo=False
    )


class TestFirstClassIntentTasks:
    @pytest.mark.parametrize(
        "query,task", [
            ("为成都新分校选址推荐最优位置", "site_selection"),
            ("帮我在成都选一个最优位置建新医院", "site_selection"),
            ("Choose the best location for a new school site in Chengdu", "site_selection"),
            ("分析成都洪水风险区域", "risk_exposure"),
            ("Flood risk assessment for Chengdu", "risk_exposure"),
            ("成都适建区评价", "suitability_assessment"),
            ("Evaluate land suitability for construction in Chengdu", "suitability_assessment"),
            ("分析成都市小学分布是否公平", "spatial_equity"),
            ("Assess the equity of school distribution in Chengdu", "spatial_equity"),
        ],
    )
    def test_decision_families_resolve_to_dedicated_tasks(self, query, task):
        from app.services.gis_harness.intent import resolve_map_request_intent

        intent = resolve_map_request_intent(query)
        assert intent.task == task, f"{query!r} → {intent.task}"

    @pytest.mark.parametrize(
        "query,task", [
            ("生成成都各区小学数量柱状图", "administrative_statistic"),
            ("成都各区小学数量统计", "administrative_statistic"),
            ("给我看看成都的咖啡馆", "simple_view"),
            ("找出距离学校500米以内的地铁站", "proximity_analysis"),
            ("成都小学分布情况", "distribution_overview"),
            ("用克里金插值成都PM2.5监测站数据", "raster_distribution"),
        ],
    )
    def test_existing_tasks_unpolluted(self, query, task):
        from app.services.gis_harness.intent import resolve_map_request_intent

        intent = resolve_map_request_intent(query)
        assert intent.task == task, f"{query!r} → {intent.task}"


class TestFirstClassRecipes:
    @pytest.mark.parametrize(
        "query,recipe,capability", [
            ("为成都新分校选址推荐最优位置", "site_selection", "mcda_evaluation"),
            ("成都适建区评价", "suitability_assessment", "mcda_evaluation"),
            ("分析成都洪水风险区域", "risk_exposure", "spatial_join"),
            ("分析成都市小学分布是否公平", "spatial_equity", "admin_aggregation"),
        ],
    )
    def test_dedicated_recipe_and_capability(self, query, recipe, capability):
        plan = _plan(query)
        assert plan.recipe_id == recipe
        caps = {r.capability for r in plan.data_requirements}
        assert capability in caps
        # 决策族绝不退回分布兜底 recipe
        assert plan.recipe_id != "poi_distribution_overview"

    def test_equity_keeps_g18_capability_contract(self):
        """G18 回归锚：equity 产品族必须仍计划 poi_query + admin_aggregation。"""
        plan = _plan("分析成都各区小学教育资源公平性")
        caps = {r.capability for r in plan.data_requirements}
        assert {"poi_query", "admin_aggregation"} <= caps


class TestDecisionDisclosures:
    @pytest.mark.parametrize(
        "query,pattern,code", [
            ("为成都新分校选址推荐最优位置", "site_selection",
             "SITE_SELECTION_CRITERIA_UNDECLARED"),
            ("成都适建区评价", "suitability",
             "SUITABILITY_WEIGHT_PROVENANCE"),
            ("分析成都洪水风险区域", "risk_exposure",
             "RISK_RECEPTORS_UNCONFIRMED"),
            ("分析成都各区小学教育资源公平性", "spatial_equity",
             "EQUITY_MISSING_DENOMINATOR"),
        ],
    )
    def test_obligation_disclosures_with_stable_codes(
        self, query, pattern, code,
    ):
        plan = _plan(query)
        by_pattern = {w["pattern"]: w for w in plan.methodology_warnings}
        assert pattern in by_pattern, f"{query!r} missing {pattern} warning"
        assert by_pattern[pattern]["code"] == code
        assert by_pattern[pattern]["disclosures"], "disclosure text must ride"

    def test_disclosure_precision_no_crossfire(self):
        """义务披露精确触发：选址查询不背适宜性披露，邻近查询不背风险披露。"""
        site = _plan("为成都新分校选址推荐最优位置")
        patterns = {w["pattern"] for w in site.methodology_warnings}
        assert "suitability" not in patterns
        assert "risk_exposure" not in patterns

        proximity = _plan("找出距离学校500米以内的地铁站")
        assert proximity.methodology_warnings == []

    def test_plain_statistics_stay_clean(self):
        for q in ("生成成都各区小学数量柱状图", "成都各区小学数量统计"):
            plan = _plan(q)
            assert plan.methodology_warnings == [], q


class TestHintProtection:
    def test_decision_tasks_not_downgradable(self):
        """决策族任务不可被 hint 降级为视觉任务（诚实护栏）。"""
        from app.services.gis_harness.intent import (
            merge_intent_hints,
            resolve_map_request_intent,
        )

        base = resolve_map_request_intent("分析成都洪水风险区域")
        merged = merge_intent_hints(base, {"task": "distribution_overview"})
        assert merged.task == "risk_exposure"
        assert any("rejected" in h for h in merged.hint_applied)


class TestRegistryIntegration:
    def test_mcda_capability_and_algorithm_registered(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.capability_registry import get_capability_registry

        caps = get_capability_registry()
        assert caps.has("mcda_evaluation")
        algos = get_algorithm_registry()
        resolved = [a for a in algos.all_ids if a.startswith("decision.")]
        assert resolved == ["decision.mcda.wsm"]
        desc = algos.get("decision.mcda.wsm")
        assert "spatial_decision_v3" in desc.tool_candidates

    def test_registry_cross_validation_clean(self):
        from app.services.gis_harness.registry_validation import validate_gis_library

        issues = validate_gis_library()
        assert issues == [], issues

    def test_hint_merge_task_recompute_keeps_mcda_intent(self):
        """hint 把任务改到决策族后，派生意图按新任务重算（mcda 随行）。"""
        from app.services.gis_harness.intent import (
            merge_intent_hints,
            resolve_map_request_intent,
        )

        base = resolve_map_request_intent("成都学校与公园的空间数据")
        merged = merge_intent_hints(base, {"task": "site_selection"})
        assert merged.task == "site_selection"
        assert "mcda_evaluation" in merged.analysis_intents
