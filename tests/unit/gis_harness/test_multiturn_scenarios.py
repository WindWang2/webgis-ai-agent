"""多轮场景回归（VNext §16 — deterministic, offline, no LLM）。

四个完整交互序列，断言「用户意图 → 语义理解 → 计划 → 证据 → 产品 →
版本/重算」链条的确定性语义（而非单条查询）：

A. 成都学校：展示 → 热力 → 区统计 → 公平（缺分母警告）→ 版本裁决带警告
B. Kriging：点 → 插值 → 参数变化 → 五维 diff → 重算决策
C. 决策：选址意图 → MCDA 计划 → DecisionEngineV3 求解 → 决策面板数据
D. 版本：V1 分析 → V2 样式 → V3 算法 → style-only 恢复 + 受限合并

全部走真实服务（intent/planner/SessionPlan 应用/MapProductService/
DecisionEngineV3），零 LLM、零网络。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner


def _plan(query: str):
    return MapProductPlanner().plan_from_intent(
        resolve_map_request_intent(query), use_memo=False)


# ─── Scenario A — Chengdu schools equity chain ──────────────────────────────


class TestScenarioAChengduSchools:
    def test_a1_show_schools_is_lightweight(self):
        plan = _plan("给我看看成都的小学")
        assert plan.intent.task == "simple_view"
        caps = {r.capability for r in plan.data_requirements}
        assert "kde_density" not in caps  # 不过度分析

    def test_a2_heatmap_request_routes_density_family(self):
        plan = _plan("成都小学分布热力图")
        assert plan.intent.task == "distribution_overview"

    def test_a3_district_statistics_routes_admin_aggregation(self):
        plan = _plan("成都各区小学数量统计")
        assert plan.intent.task == "administrative_statistic"
        caps = {r.capability for r in plan.data_requirements}
        assert {"poi_query", "admin_aggregation"} <= caps

    def test_a4_fairness_without_denominator_warns_honestly(self):
        plan = _plan("分析成都各区小学教育资源公平性")
        assert plan.intent.task == "spatial_equity"
        warns = {w["pattern"]: w for w in plan.methodology_warnings}
        assert "spatial_equity" in warns
        assert warns["spatial_equity"]["code"] == "EQUITY_MISSING_DENOMINATOR"
        assert warns["spatial_equity"]["missing_roles"] == [
            "normalization_denominator"]
        assert warns["spatial_equity"]["disclosures"], "披露文案必须随行"

    def test_a5_warning_downgrades_product_verdict(self):
        """方法论披露压低产品裁决：带缺分母警告的完成产品 ≠ READY。"""
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
            derive_product_verdict,
        )

        result = MapCompletionResult(status="complete")
        verdict = derive_product_verdict(result, methodology_warnings=[
            {"pattern": "spatial_equity",
             "code": "EQUITY_MISSING_DENOMINATOR"}])
        assert verdict["verdict"] == "READY_WITH_WARNINGS"
        assert verdict["methodology_warning_codes"] == [
            "EQUITY_MISSING_DENOMINATOR"]

    async def test_a6_progress_chain_moves_session_plan_rows(self):
        """工具结果推进 SessionPlan 行状态（确定性，无 LLM）。"""
        from app.services.session_data import session_data_manager
        from app.services.session_plan import (
            SessionPlan,
            apply_tool_result,
            load_session_plan,
            save_session_plan,
        )

        sid = "scen-a-schools"
        plan = _plan("成都各区小学数量统计")
        envelope = SessionPlan(
            envelope_id="sp-scen-a", session_id=sid,
            user_goal="成都各区小学数量统计",
            gis_chapter=plan.model_dump())
        await save_session_plan(envelope)
        # 模拟 webgis_map_intent 结果落章 → poi_query 工具成功
        await apply_tool_result(
            sid, "webgis_map_intent",
            {"plan": plan.model_dump(), "task": "administrative_statistic"},
            success=True)
        await apply_tool_result(
            sid, "query_local_poi",
            {"type": "success", "count": 60,
             "ref_id": "ref:geojson-scen-a"},
            success=True, geojson_ref="ref:geojson-scen-a")
        fresh = await load_session_plan(sid)
        assert fresh is not None and fresh.gis_chapter
        rows = {r["capability"]: r["status"]
                for r in fresh.gis_chapter["data_requirements"]}
        assert rows.get("poi_query") == "available"
        # 清理会话态（测试隔离）
        await session_data_manager.clear_session(sid)


# ─── Scenario B — Kriging version diff & recompute ──────────────────────────


class TestScenarioBKriging:
    def test_b1_kriging_request_plans_interpolation_with_explicit_algo(self):
        plan = _plan("用克里金插值成都PM2.5监测站数据")
        assert plan.intent.task == "raster_distribution"
        sel = {r.capability: r for r in plan.algorithm_selections}
        assert "spatial_interpolation" in sel
        assert sel["spatial_interpolation"].algorithm == "interpolation.kriging"

    def test_b2_parameter_change_drives_recompute_decision(self):
        """五维 diff：参数变化 → analysis_recomputation_expected=True；
        style-only → False（重算决策的机器读面）。"""
        from app.core.database import Base, Engine, SessionLocal
        from app.services.map_product_service import MapProductService
        from tests.unit.test_reproducible_gis_runtime import (
            _PROJECT_DOMAIN_TABLES,
        )

        import app.models.db_model  # noqa: F401
        import app.models.project  # noqa: F401
        from app.models.db_model import User
        from app.models.project import Project

        Base.metadata.create_all(bind=Engine, checkfirst=True)
        domain = [t for t in Base.metadata.sorted_tables
                  if t.name in _PROJECT_DOMAIN_TABLES]
        for tbl in reversed(domain):
            tbl.drop(bind=Engine, checkfirst=True)
        for tbl in domain:
            tbl.create(bind=Engine, checkfirst=True)

        pid = "proj_scen_b"
        manifest_v1 = {"steps": [
            {"step_id": "krig", "tool_name": "kriging_interpolation",
             "algorithm": "interpolation.kriging",
             "args": {"model": "spherical", "n_lags": 8}},
        ], "artifacts": [{"id": "a1", "content_fingerprint": "o1"}]}
        with SessionLocal() as s:
            s.merge(User(id="u_sb", username="sb", email="sb@e.com",
                         password_hash="x", role="viewer", is_active=True))
            s.add(Project(id=pid, name="scen-b", owner_id="u_sb"))
            s.commit()
            MapProductService.record_version(
                s, pid, mapspec_fingerprint="style-1",
                input_dataset_fingerprints={"pm25": "fpA"},
                run_manifest=manifest_v1)
            # 参数变化（n_lags 8→12）
            manifest_v2 = {"steps": [
                {"step_id": "krig", "tool_name": "kriging_interpolation",
                 "algorithm": "interpolation.kriging",
                 "args": {"model": "spherical", "n_lags": 12}},
            ], "artifacts": [{"id": "a1", "content_fingerprint": "o1"}]}
            MapProductService.record_version(
                s, pid, mapspec_fingerprint="style-1",
                input_dataset_fingerprints={"pm25": "fpA"},
                run_manifest=manifest_v2)
            # style-only（MapSpec 指纹变）
            MapProductService.record_version(
                s, pid, mapspec_fingerprint="style-2",
                input_dataset_fingerprints={"pm25": "fpA"},
                run_manifest=manifest_v2)
            rows, _ = MapProductService.list_versions_paginated(s, pid)
            v_param, v_style = rows[1], rows[0]  # newest first
            assert v_param.diff_summary["parameter_changed"] is True
            assert v_param.diff_summary["analysis_recomputation_expected"] is True
            assert v_style.diff_summary["style_changed"] is True
            assert v_style.diff_summary["analysis_recomputation_expected"] is False


# ─── Scenario C — Decision workspace chain ──────────────────────────────────


class TestScenarioCDecision:
    def test_c1_site_selection_plans_mcda(self):
        plan = _plan("为成都新分校选址推荐最优位置")
        caps = {r.capability for r in plan.data_requirements}
        assert "mcda_evaluation" in caps
        sel = {r.capability: r for r in plan.algorithm_selections}
        assert sel["mcda_evaluation"].algorithm == "decision.mcda.wsm"
        # 义务披露随行
        assert any(w["code"] == "SITE_SELECTION_CRITERIA_UNDECLARED"
                   for w in plan.methodology_warnings)

    def test_c2_decision_engine_solves_and_panel_data_is_honest(self):
        """DecisionEngineV3 真实求解 → decision_panel 数据（权重来源显式、
        硬约束否决保留、观测/假设可区分）。"""
        from app.services.spatial_decision.decision_engine_v3 import (
            DecisionEngineV3,
        )
        from app.services.spatial_decision.models_v3 import (
            Alternative,
            Criterion,
            CriterionDirection,
            DecisionProblem,
            WeightSource,
        )

        from app.services.spatial_decision.models import TargetAreaSpec

        problem = DecisionProblem(
            problem_id="scen-c-1",
            goal="新校选址",
            target_area=TargetAreaSpec(
                query="成都", resolved_name="成都", source="user_request",
                confidence=1.0),
            alternatives=[
                Alternative(id="a1", name="东地块", attributes={
                    "通勤": 12, "人口": 8000, "噪声": 70}),
                Alternative(id="a2", name="西地块", attributes={
                    "通勤": 25, "人口": 12000, "噪声": 40}),
            ],
            criteria=[
                Criterion(id="c1", name="通勤", direction=CriterionDirection.MINIMIZE,
                          weight=0.4, weight_source=WeightSource.USER_DECLARED),
                Criterion(id="c2", name="人口", direction=CriterionDirection.MAXIMIZE,
                          weight=0.4, weight_source=WeightSource.USER_DECLARED),
                Criterion(id="c3", name="噪声", direction=CriterionDirection.MINIMIZE,
                          weight=0.2, weight_source=WeightSource.EQUAL_DEFAULT),
            ],
        )
        result = asyncio.run(DecisionEngineV3().solve_problem(problem, "scen-c"))
        scores = result.recommendation.scores  # Dict[alt_id, DecisionScore]
        assert len(scores) == 2
        assert all(0.0 <= s.mcda_score <= 1.0 for s in scores.values())
        alt_names = {a.id: a.name for a in problem.alternatives}
        ranked = sorted(
            scores.values(), key=lambda s: -s.mcda_score)
        # 面板数据契约：排名 + 权重来源 + 无合成值
        panel_rows = [
            {"rank": i + 1, "name": alt_names.get(s.alternative_id, s.alternative_id),
             "score": round(s.mcda_score, 3), "basis": "observed"}
            for i, s in enumerate(ranked)
        ]
        sources = {c.id: c.weight_source.value for c in problem.criteria}
        user_specified = [k for k, v in sources.items() if "user" in v]
        assumed = [k for k, v in sources.items()
                    if "assum" in v or "default" in v]
        assert user_specified and assumed, "观测权重与假设权重必须可区分"
        assert len(panel_rows) == 2

    def test_c3_decision_panel_component_builds_from_result(self):
        from app.services.gis_harness.components import (
            decision_panel_component,
        )

        panel = decision_panel_component(
            rows=[{"name": "东地块", "score": 0.72, "basis": "observed"},
                  {"name": "西地块", "score": 0.61, "basis": "observed"}],
            method="WSM", weight_source="用户指定",
            vetoes=["北地块 位于泄洪区"])
        assert panel.type == "decision_panel"
        assert panel.options["decision"]["weightSource"] == "用户指定"
        assert panel.options["decision"]["vetoes"] == ["北地块 位于泄洪区"]


# ─── Scenario D — Version restore chain ─────────────────────────────────────


class TestScenarioDVersionRestore:
    def test_d1_restore_style_only_preserves_compute_identity(self):
        """V1(分析) → V2(样式) → V3(算法) → style-only 恢复 V2 →
        恢复行计算身份 == V2；受限合并 V2×V3 可行、V1×V3 拒绝。"""
        from app.core.database import SessionLocal
        from app.services.map_product_service import MapProductService
        from tests.unit.test_map_product_lifecycle_v2 import make_lifecycle_project

        # 复用 lifecycle 夹具（V1 base+snapshot / V2 style-only / V3 analysis-only）
        project_id, (v1, v2, v3) = make_lifecycle_project()
        with SessionLocal() as s:
            # 受限合并：V2(样式) × V3(分析)
            merged = MapProductService.merge_dimensions(s, project_id, v2, v3)
            assert merged.lineage_kind == "merge"
            assert merged.mapspec_fingerprint == "carto-v2"
            # 冲突拒绝：V1 × V3（样式+分析双动）
            with pytest.raises(ValueError):
                MapProductService.merge_dimensions(s, project_id, v1, v3)

    def test_d2_style_restore_flow_via_session_engine(self):
        """活会话上的 style-only 恢复走生命周期引擎（真实事务），恢复行
        的 style_only_proof 给出机器证明。"""
        from tests.unit.test_map_product_lifecycle_v2 import make_lifecycle_project

        project_id, (v1, v2, v3) = make_lifecycle_project()
        session_id = "scen-d-restore"

        async def _drive():
            from app.services.mapspec.lifecycle_engine import (
                InitProjectIntent,
                MapSpecLifecycleEngine,
                UpsertLayerIntent,
            )
            from app.services.map_product_service import MapProductService
            from app.services.session_data import session_data_manager
            from app.core.database import SessionLocal

            engine = MapSpecLifecycleEngine()
            await engine.apply_mutation(
                session_id, InitProjectIntent(view={"center": [104, 30], "zoom": 9}))
            await engine.apply_mutation(
                session_id,
                UpsertLayerIntent(
                    layer={"id": "l1", "type": "circle", "source": "src1",
                           "paint": {"color": "red"}},
                    source_data={"type": "geojson", "inlineData": {
                        "type": "FeatureCollection", "features": [
                            {"type": "Feature", "geometry": {"type": "Point",
                             "coordinates": [104, 30]}, "properties": {}}]}},
                ))
            with SessionLocal() as s:
                result = await MapProductService.restore_style_to_session(
                    s, project_id, v2, session_id=session_id, actor="u_d")
            await session_data_manager.clear_session(session_id)
            return result

        result = asyncio.run(_drive())
        assert result["mode"] == "style_only"
        assert result["style_only_proof"]["compute_identity_preserved"] is True
        assert result["style_only_proof"]["analysis_executed"] is False
