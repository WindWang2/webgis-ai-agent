"""Deterministic Workflow Compiler（C5）与 Completion/Verdict V2（C7）回归锁。

不变式：
- 13 阶段固定序（V3 扩展 map_task_ontology）、同输入同输出（纯函数编译，
  零 LLM / 零 I/O）；
- 每阶段有机器可读 reason codes 与有界 evidence；
- 新任务族路由到 V2 专业 recipe；通用短语仍归 V1 seed（资历层）；
- workflow 契约的科学/数据硬违反压过 complete 档位（BLOCKED_BY_METHOD /
  BLOCKED_BY_DATA）——「方法不成立不能被漂亮地图掩盖」；
- V1 会话（无 workflow_contract）verdict 行为与历史一致。
"""
from __future__ import annotations


from app.services.gis_harness.workflow_compiler import (
    COMPILER_STAGES,
    compile_workflow,
)

_PROFILE_POINTS = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}


class TestCompilerStages:
    def test_fixed_stage_order(self):
        c = compile_workflow("成都小学的分布情况")
        assert [s.stage for s in c.stages] == list(COMPILER_STAGES)
        assert len(c.stages) == 14

    def test_deterministic_same_input_same_output(self):
        c1 = compile_workflow("成都各区小学数量是否均衡", profile=_PROFILE_POINTS)
        c2 = compile_workflow("成都各区小学数量是否均衡", profile=_PROFILE_POINTS)
        assert c1.to_bounded_dict() == c2.to_bounded_dict()

    def test_bounded_serializable(self):
        import json

        c = compile_workflow("用克里金插值生成污染物浓度表面", profile=_PROFILE_POINTS)
        payload = json.dumps(c.to_bounded_dict(), ensure_ascii=False, default=str)
        assert len(payload) < 64_000  # 有界：编译产物不是证据倾倒场

    def test_explicit_recipe_override_recorded(self):
        c = compile_workflow(
            "成都各区小学数量是否均衡", recipe_id="education_equity_per_capita",
            profile=_PROFILE_POINTS,
        )
        assert c.recipe_id == "education_equity_per_capita"
        rec = c.stage("resolve_recipe_candidates")
        assert "explicit_recipe_override" in rec.reason_codes


class TestCompilerRouting:
    def test_generic_phrases_stay_with_v1_seed(self):
        """资历层回归锚：通用「各区数量」仍归 administrative_choropleth。"""
        c = compile_workflow("成都各区小学数量")
        assert c.recipe_id == "administrative_choropleth"

    def test_new_task_family_routes_to_v2_workflow(self):
        """新任务族没有 V1 seed → V2 专业工作流可被选中并携带义务。"""
        c = compile_workflow("成都周边的坡度分析")
        assert c.recipe_id == "slope_analysis_workflow"
        obligations = {o["obligation_id"] for o in c.obligations}
        assert "terrain_dem_required" in obligations

    def test_explicit_kriging_workflow_engages_obligations(self):
        from app.lib.gis.scientific_preconditions import evaluate_precondition

        prof = {"featureCount": 5, "geometryTypes": ["Point"],
                "fields": {"pm25": {"type": "number"}},
                "numericFields": ["pm25"], "crs": "EPSG:4326"}
        c = compile_workflow(
            "用克里金插值生成污染物浓度表面",
            recipe_id="kriging_interpolation_workflow", profile=prof,
        )
        statuses = {o["obligation_id"]: o["status"] for o in c.obligations}
        assert statuses["kriging_min_samples"] in ("warning", "blocked", "degraded")
        # 委托一致性：workflow 义务结论与算法层同源
        assert evaluate_precondition(
            "min_numeric_samples:8", prof).verdict in (
            "PASS_WITH_WARNINGS", "INSUFFICIENT_DATA")

    def test_stage_reason_codes_surfaced(self):
        c = compile_workflow(
            "成都各区的教育资源公平性如何", recipe_id="education_equity_per_capita",
            profile={"featureCount": 60, "geometryTypes": ["Point"],
                     "fields": {"name": {"type": "string"}}},
        )
        all_codes = set(c.reason_codes)
        assert "EQUITY_MISSING_DENOMINATOR" in all_codes


class TestVerdictV2:
    def _result(self, status="complete", findings=(), render_status="verified"):
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
        )

        return MapCompletionResult(
            status=status,
            findings=list(findings),
            render_status=render_status,
        )

    def test_v1_chapter_verdict_unchanged(self):
        """无 workflow_contract 的章节：READY 行为与历史一致 + additive 维度。"""
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )

        out = derive_product_verdict(self._result())
        assert out["verdict"] == "READY"
        assert out["workflow_contract_present"] is False
        assert set(out["completion_dimensions"]) == {
            "data", "analysis", "science", "cartography",
            "observed_map", "methodology_disclosure", "uncertainty_disclosure",
        }

    def test_method_blocker_demotes_pretty_map_to_blocked(self):
        """science 维硬违反：complete + 完美渲染仍不得 READY。"""
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )

        chapter = {
            "workflow_contract": {
                "method_blockers": ["kriging_numeric_field"],
                "data_blockers": [],
                "obligations": [
                    {"obligation_id": "kriging_numeric_field", "kind": "precondition",
                     "status": "blocked", "on_violation": "block_method",
                     "warning_code": "KRIGING_NUMERIC_FIELD_REQUIRED"},
                ],
            },
        }
        out = derive_product_verdict(self._result(), chapter=chapter)
        assert out["verdict"] == "BLOCKED_BY_METHOD"
        assert out["reasons"] == ["kriging_numeric_field"]
        assert out["completion_dimensions"]["science"] is False

    def test_data_blocker_demotes_to_blocked_by_data(self):
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )

        chapter = {
            "workflow_contract": {
                "method_blockers": [],
                "data_blockers": ["denominator"],
                "obligations": [],
            },
        }
        out = derive_product_verdict(self._result(), chapter=chapter)
        assert out["verdict"] == "BLOCKED_BY_DATA"

    def test_undisclosed_obligation_fails_methodology_dimension(self):
        """触发的义务码未披露 → methodology_disclosure 维 False。"""
        from app.services.gis_harness.completion.contracts import (
            evaluate_completion_contract,
        )

        chapter = {
            "workflow_contract": {
                "method_blockers": [], "data_blockers": [],
                "obligations": [
                    {"obligation_id": "o", "kind": "disclosure", "status": "warning",
                     "on_violation": "warn", "warning_code": "SOME_DISCLOSURE"},
                ],
            },
        }
        contract = evaluate_completion_contract(self._result(), [], chapter)
        assert contract["dimensions"]["methodology_disclosure"] is False
        # 同码出现在 methodology_warnings → 披露达成
        contract2 = evaluate_completion_contract(
            self._result(),
            [{"code": "SOME_DISCLOSURE", "disclosures": ["x"]}],
            chapter,
        )
        assert contract2["dimensions"]["methodology_disclosure"] is True

    def test_failed_data_still_wins_over_method_contract(self):
        """failed + 数据族错误：数据先行（与 V1 failed 分支同序，不被契约翻转）。"""
        from app.services.gis_harness.completion.contracts import (
            MapCompletionFinding,
            derive_product_verdict,
        )

        chapter = {"workflow_contract": {
            "method_blockers": ["m"], "data_blockers": [], "obligations": []}}
        result = self._result(
            status="failed",
            findings=[MapCompletionFinding(code="artifact_missing", severity="error")],
        )
        out = derive_product_verdict(result, chapter=chapter)
        assert out["verdict"] == "BLOCKED_BY_DATA"


class TestPlannerWorkflowIntegration:
    def test_finalize_writes_workflow_contract_and_dedupes_warnings(self):
        from app.services.gis_harness.planner import MapProductPlanner
        from app.services.gis_harness.planner_runtime import reset_planner_runtime

        reset_planner_runtime()
        planner = MapProductPlanner()
        it = __import__("app.services.gis_harness.intent",
                        fromlist=["resolve_map_request_intent"]).resolve_map_request_intent(
            "成都各区的教育资源公平性如何")
        plan = planner.plan_from_intent(it, recipe_id="education_equity_per_capita")
        fin = planner.finalize_with_profile(
            plan, {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}})
        wc = fin.workflow_contract
        assert wc is not None and wc["schema_version"] == 2
        assert wc["domain"] == "equity"
        codes = [w.get("code") for w in fin.methodology_warnings]
        assert codes.count("EQUITY_MISSING_DENOMINATOR") == 1  # 披露幂等（去重）
        reset_planner_runtime()

    def test_v1_recipe_finalize_has_no_workflow_contract(self):
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        it = __import__("app.services.gis_harness.intent",
                        fromlist=["resolve_map_request_intent"]).resolve_map_request_intent(
            "成都小学的分布情况")
        plan = planner.plan_from_intent(it)
        fin = planner.finalize_with_profile(
            plan, {"featureCount": 60, "geometryTypes": ["Point"], "fields": {}})
        assert fin.workflow_contract is None
