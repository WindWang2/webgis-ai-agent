"""Multi-Candidate GIS Planning 回归锁（Goal V3）。

不变式：
- 候选生成确定性：同 intent/profile/tools → 同候选集、同评分、同选择；
- 候选池 = 语义路由 recipe + 触发 composite + 激活 scenario 变体；
- 科学合法过滤复用既有资格/义务评估（blocked 候选保留并携带拒绝理由）；
- 评分九维齐全且加权聚合确定性；selected 唯一；
- 零漂移：常规场景下 selected recipe 与语义 top-1 一致；
- compiler 集成：plan_candidates 阶段记录 bounded 候选集。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.plan_candidates import (
    SCORE_DIMENSIONS,
    generate_plan_candidates,
)


_PROFILE_POINTS = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}


class TestCandidateGeneration:
    def test_deterministic_candidates_and_selection(self):
        it = resolve_map_request_intent("成都小学的分布情况")
        s1 = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        s2 = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        assert s1.to_bounded_dict() == s2.to_bounded_dict()

    def test_candidate_pool_sources(self):
        """池内同时存在 recipe 候选与 scenario/composite 变体。"""
        it = resolve_map_request_intent("成都小学的分布情况")
        s = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        kinds = {c.kind for c in s.candidates}
        assert "recipe" in kinds
        assert kinds & {"composite", "scenario_variant"}
        # 成都小学 → 点分布上下文组合被触发
        assert any(c.composite_id == "composite.point_distribution_context"
                   for c in s.candidates)

    def test_selection_is_unique_and_scored(self):
        it = resolve_map_request_intent("成都小学的分布情况")
        s = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        selected = [c for c in s.candidates if c.status == "selected"]
        assert len(selected) == 1
        assert s.selected_id
        for c in s.candidates:
            assert set(c.scores.keys()) == set(SCORE_DIMENSIONS)
            assert 0.0 <= c.score <= 1.0
            # 加权聚合与逐维分数一致
            from app.services.gis_harness.plan_candidates import _DIMENSION_WEIGHTS
            expect = round(sum(c.scores[d] * _DIMENSION_WEIGHTS[d]
                               for d in SCORE_DIMENSIONS), 4)
            assert c.score == pytest.approx(expect, abs=1e-6)

    def test_kriging_candidates_ranked(self):
        it = resolve_map_request_intent("用克里金插值生成污染物浓度表面")
        prof = {"featureCount": 60, "geometryTypes": ["Point"],
                "numericFields": ["pm25"],
                "fields": {"pm25": {"type": "number"}}, "crs": "EPSG:32648"}
        s = generate_plan_candidates(it, profile=prof, available_tools=[])
        ids = [c.recipe_id for c in s.candidates]
        assert "kriging_interpolation_workflow" in ids

    def test_blocked_candidates_kept_with_reasons(self):
        """科学阻断候选不消失：保留在集合中并携带拒绝理由（可解释性）。"""
        it = resolve_map_request_intent("用克里金插值生成污染物浓度表面")
        prof = {"featureCount": 60, "geometryTypes": ["Point"],
                "fields": {"name": {"type": "string"}}}  # 无数值字段
        s = generate_plan_candidates(it, profile=prof, available_tools=[])
        blocked = [c for c in s.candidates
                   if c.recipe_id == "kriging_interpolation_workflow"]
        assert blocked
        kriging = blocked[0]
        assert (kriging.method_blockers or kriging.data_blockers
                or kriging.rejection_reasons)

    def test_reroute_only_on_science_block(self):
        """零漂移：常规场景 selected recipe = 语义 top-1；不轻易改选。"""
        it = resolve_map_request_intent("成都各区小学数量")
        s = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        selected = s.selected
        assert selected is not None
        assert selected.recipe_id == "administrative_choropleth"
        assert not s.rerouted

    def test_tools_unavailable_rejection(self):
        it = resolve_map_request_intent("用克里金插值生成污染物浓度表面")
        prof = {"featureCount": 60, "geometryTypes": ["Point"],
                "numericFields": ["pm25"],
                "fields": {"pm25": {"type": "number"}}, "crs": "EPSG:32648"}
        # available_tools 为空列表 = 无任何工具 → 依赖工具链的候选必须
        # 出现 tools_available=False 且被拒绝（review R8：恒真断言修正）
        s = generate_plan_candidates(it, profile=prof, available_tools=[])
        unavailable = [c for c in s.candidates if c.tools_available is False]
        assert unavailable, (
            "empty available_tools must produce at least one "
            "tools_available=False candidate")
        for c in unavailable:
            assert "tools_unavailable" in c.rejection_reasons
            assert c.status == "rejected"

    def test_bounded_serializable(self):
        import json

        it = resolve_map_request_intent("成都小学的分布情况")
        s = generate_plan_candidates(it, profile=_PROFILE_POINTS)
        payload = json.dumps(s.to_bounded_dict(), ensure_ascii=False, default=str)
        assert len(payload) < 32_000


class TestCompilerIntegration:
    def test_plan_candidates_stage_recorded(self):
        from app.services.gis_harness.workflow_compiler import (
            COMPILER_STAGES,
            compile_workflow,
        )

        assert "plan_candidates" in COMPILER_STAGES
        assert len(COMPILER_STAGES) == 15
        c = compile_workflow("成都小学的分布情况", profile=_PROFILE_POINTS)
        stage = c.stage("plan_candidates")
        assert stage is not None
        pc = c.plan_candidates
        assert pc.get("selected_id")
        assert pc.get("candidates")
        selected = next((x for x in pc["candidates"]
                         if x["status"] == "selected"), None)
        assert selected is not None
        assert selected["scores"]

    def test_scenario_disclosure_surfaced(self):
        """场景激活时，minimal 兜底披露进入编译证据。"""
        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow("成都小学的分布情况", profile=_PROFILE_POINTS)
        stage = c.stage("plan_candidates")
        assert stage is not None
        disclosures = stage.evidence.get("scenario_disclosures") or []
        assert disclosures

    def test_full_compilation_still_bounded(self):
        import json

        from app.services.gis_harness.workflow_compiler import compile_workflow

        c = compile_workflow("成都小学的分布情况", profile=_PROFILE_POINTS)
        payload = json.dumps(c.to_bounded_dict(), ensure_ascii=False, default=str)
        assert len(payload) < 64_000
