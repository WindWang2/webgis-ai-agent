"""SkillResolver V1 测试（ADR-0182 S4/S9：确定性选择 + 资格硬门槛）。"""
import pytest

from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.situation import SelectionFacts


@pytest.fixture(scope="module")
def library() -> SkillResolver:
    return get_skill_library().resolver


class TestDeterministicSelection:
    def test_same_input_same_output(self, library):
        facts = SelectionFacts(goal_text="成都小学分布情况")
        a = library.resolve(facts)
        b = library.resolve(facts)
        assert a.to_bounded_dict() == b.to_bounded_dict()

    def test_ontology_signal_dominates(self, library):
        facts = SelectionFacts(
            goal_text="分布", ontology_matches=["network.accessibility"])
        result = library.resolve(facts)
        assert result.selected == "network_accessibility_analysis"

    def test_task_type_signal(self, library):
        facts = SelectionFacts(goal_text="对比", task_type="administrative_statistic")
        result = library.resolve(facts)
        assert result.selected == "administrative_aggregation"

    def test_ranked_order_deterministic(self, library):
        facts = SelectionFacts(goal_text="分析数据的分布情况")
        r1 = [c.skill_id for c in library.resolve(facts).ranked]
        r2 = [c.skill_id for c in library.resolve(facts).ranked]
        assert r1 == r2

    def test_confidence_bands(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"])
        top = library.resolve(facts).top
        assert top is not None and top.confidence_band in ("low", "medium", "high")
        assert top.confidence > 0.5


class TestEligibility:
    def test_unknown_facts_pass(self, library):
        # 红线：unknown ≠ 不满足 —— 空事实不触发任何硬门槛拒绝
        facts = SelectionFacts(goal_text="成都小学分布情况")
        result = library.resolve(facts)
        assert result.rejected == []
        assert result.selected == "point_distribution_analysis"

    def test_point_data_cannot_choropleth(self, library):
        # S9 红线：点数据集不能偷偷画 choropleth
        facts = SelectionFacts(goal_text="做个分级统计图",
                               geometry_kinds=["point"])
        result = library.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "choropleth_map_design" in rej
        assert rej["choropleth_map_design"].fallback_skill_id == \
            "distribution_map_design"

    def test_min_features_gate(self, library):
        facts = SelectionFacts(goal_text="学校分布", geometry_kinds=["point"],
                               feature_count=3)
        result = library.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "point_distribution_analysis" in rej
        assert any(c.startswith("INSUFFICIENT_DATA")
                   for c in rej["point_distribution_analysis"].reason_codes)

    def test_boundary_gate_with_explicit_false(self, library):
        facts = SelectionFacts(goal_text="各区学校数量",
                               task_type="administrative_statistic",
                               has_boundary=False)
        result = library.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "administrative_aggregation" in rej
        assert any(c.startswith("BOUNDARY_MISSING")
                   for c in rej["administrative_aggregation"].reason_codes)

    def test_all_roles_semantics(self, library):
        # facts.data_roles 是穷举集合：change_detection 需要 baseline+target_time
        facts = SelectionFacts(goal_text="变化检测", geometry_kinds=["raster"],
                               data_roles=["target_time"],
                               ontology_matches=["remote_sensing.change_detection"])
        result = library.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "change_detection_mapping" in rej
        assert any(c.startswith("DATA_ROLE_MISSING")
                   for c in rej["change_detection_mapping"].reason_codes)

    def test_temporal_period_gate(self, library):
        facts = SelectionFacts(goal_text="趋势分析",
                               ontology_matches=["remote_sensing.temporal_analysis"],
                               period_count=1)
        result = library.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "time_series_result_mapping" in rej
        assert any(c.startswith("TEMPORAL_INSUFFICIENT")
                   for c in rej["time_series_result_mapping"].reason_codes)
        assert rej["time_series_result_mapping"].fallback_skill_id == \
            "temporal_comparison_workflow"

    def test_unknown_geometry_fact_not_rejected(self, library):
        # "unknown" 是事实缺席，不是矛盾证据
        facts = SelectionFacts(goal_text="做个分级统计图",
                               geometry_kinds=["unknown"])
        result = library.resolve(facts)
        rej = {r.skill_id for r in result.rejected}
        assert "choropleth_map_design" not in rej

    def test_capability_hard_gate(self, library):
        facts = SelectionFacts(goal_text="做个分级统计图",
                               geometry_kinds=["polygon"],
                               data_roles=["subject", "measure"])
        result = library.resolve(
            facts)
        assert result.selected == "choropleth_map_design"

        strict = SkillResolver(
            [library.get("choropleth_map_design")],
            capability_exists=lambda c: False)
        result2 = strict.resolve(facts)
        rej = {r.skill_id: r for r in result2.rejected}
        assert "choropleth_map_design" in rej
        assert any(c.startswith("CAPABILITY_MISSING")
                   for c in rej["choropleth_map_design"].reason_codes)


class TestClarification:
    def test_empty_goal(self, library):
        result = library.resolve(SelectionFacts(goal_text=""))
        assert result.clarification is not None
        assert result.clarification.reason_code == "EMPTY_GOAL"

    def test_no_match_clarifies(self, library):
        result = library.resolve(SelectionFacts(goal_text="帮我处理一下量子纠缠"))
        assert result.clarification is not None
        assert result.clarification.reason_code == "NO_MATCH"

    def test_tied_top_candidates_clarify(self, library):
        facts = SelectionFacts(goal_text="对比两个时期的土地利用")
        result = library.resolve(facts)
        assert result.clarification is not None
        assert result.clarification.reason_code == "AMBIGUOUS_TOP_CANDIDATES"

    def test_no_clarification_with_strong_facts(self, library):
        facts = SelectionFacts(
            goal_text="2025和2026两期影像土地利用变化检测",
            ontology_matches=["remote_sensing.change_detection"],
            data_roles=["baseline", "target_time"],
            geometry_kinds=["raster"])
        result = library.resolve(facts)
        assert result.clarification is None
        assert result.selected == "change_detection_mapping"


class TestGovernance:
    def test_deprecated_excluded_with_pointer(self, library):
        from app.services.gis_harness.skills.resolver import SkillResolver as R
        import copy
        lib = get_skill_library()
        dep = copy.deepcopy(lib.get("density_map_design"))
        dep.deprecated = True
        dep.deprecated_by = "density_hotspot_analysis"
        # 用弃用副本替换原条目（同 id 双注册会被构造器拒绝）
        patched_skills = [dep if s.id == dep.id else s for s in lib.skills]
        patched = R(patched_skills)
        facts = SelectionFacts(goal_text="密度图设计")
        result = patched.resolve(facts)
        rej = {r.skill_id: r for r in result.rejected}
        assert "density_map_design" in rej
        assert rej["density_map_design"].fallback_skill_id == \
            "density_hotspot_analysis"

    def test_duplicate_ids_rejected_at_construction(self, library):
        import copy
        lib = get_skill_library()
        skill = copy.deepcopy(lib.skills[0])
        with pytest.raises(ValueError):
            SkillResolver([lib.skills[0], skill])

    def test_bounded_projection(self, library):
        result = library.resolve(SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"]))
        d = result.to_bounded_dict()
        assert len(d["ranked"]) <= 5
        assert len(d["rejected"]) <= 8
