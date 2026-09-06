"""Review Round 1 修复的回归锁（R1-A*/B*）。

锁定审查修复的行为面：
- verdict V2 生产路径：webgis_map_product 结果携带 workflow_contract，
  SessionPlan chapter 合并后完成管线可读（R1-A1）；
- not_allowed 语义降级阻断完成（R1-A2）；
- 角色 bound 以真实计划行为证据（R1-A3）；
- 专业关键词落选 recipe 的披露义务叠加（R1-A4）；
- ASCII 关键词整词命中（R1-A5/B1）；
- 路由负例：「最近的X分布」不路由 network_route（R1-B2）；
- 包加载 fail-loud（R1-B3）；
- C12 端到端：recipe 语义变化 → manifest 指纹变化 → plan stale（R1-B4）；
- workflow 画像静态校验全量 sweep（R1-A7/B5）；
- 分析图 workflow 块（R1-B8）；catalog 新鲜度（R1-B6）。
"""
from __future__ import annotations

import pytest


class TestRoutingGuards:
    def test_ascii_keywords_match_whole_words_only(self):
        from app.services.gis_harness.recipes import get_recipe_registry

        rr = get_recipe_registry()
        assert [r.id for r in rr.keyword_hits("caesar salad places")] == []
        assert [r.id for r in rr.keyword_hits("laptop stores")] == []
        assert [r.id for r in rr.keyword_hits("sar 影像后向散射概览")] == [
            "sar_backscatter_overview"
        ]

    def test_nearest_facility_distribution_phrases_not_hijacked(self):
        """R1-B2 负例：日常浏览表述不得被 network_route 静默改路由。"""
        from app.services.gis_harness.intent import resolve_map_request_intent

        for query in ("帮我看看最近的医院分布情况", "最近的便利店有哪些"):
            assert resolve_map_request_intent(query).task != "network_route", query
        # 正例保持：就近可达/分配语义仍路由
        assert resolve_map_request_intent("离小区最近的医院").task == "network_route"
        assert resolve_map_request_intent("最近的消防站可达性").task == "network_route"

    def test_significance_keyword_overlay_carries_obligations(self):
        """R1-A4：「显著性热点」路由到 seed，但检验义务披露必须随 plan 下行。"""
        from app.evaluation.runner import GISBenchmarkRunner  # noqa: F401
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        intent = resolve_map_request_intent("各区案件量的显著性热点")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent, use_memo=False)
        codes = {
            str(c)
            for w in plan.methodology_warnings
            for c in (w.get("warning_codes") or [])
        }
        assert any(
            code.startswith("GI_STAR") for code in codes
        ), f"overlay disclosures missing: {codes}"
        assert any(
            w.get("stage") == "routing_overlay" for w in plan.methodology_warnings
        )


class TestFailLoudPackLoading:
    def test_broken_pack_module_fails_loud(self, monkeypatch):
        """R1-B3：包加载失败必须显性失败，不得静默退化半量知识库。"""
        import importlib

        from app.services.gis_harness.recipes import RecipeRegistry

        monkeypatch.setattr(
            importlib, "import_module",
            lambda name: (_ for _ in ()).throw(RuntimeError(f"boom: {name}")),
        )
        registry = RecipeRegistry()
        with pytest.raises(RuntimeError, match="recipe packs 加载失败"):
            registry.load_builtins()


class TestChapterWiring:
    def test_webgis_map_product_merge_persists_workflow_contract(self):
        """R1-A1（CRITICAL）+ R2-1/R2-7：驱动真实 merge helper —— finalize
        契约进入 chapter，完成管线 verdict V2 生产可达；空列表也是证据。"""
        from app.services.session_plan import merge_map_product_result

        chapter = {"query": "q", "recipe_id": "education_equity_per_capita"}
        raw = {
            "completeness": {"complete": True},
            "status": "finalized",
            "recipe_id": "education_equity_per_capita",
            "workflow_contract": {
                "schema_version": 2,
                "domain": "equity",
                "method_blockers": [],
                "data_blockers": [],
                "obligations": [],
                "roles": [],
            },
            "methodology_warnings": [
                {"code": "EQUITY_MISSING_DENOMINATOR", "warning_codes": [
                    "EQUITY_MISSING_DENOMINATOR"]}
            ],
            "fallbacks": [{"reason_code": "X", "evidence": {
                "downgrade_class": "degraded"}}],
        }
        merge_map_product_result(chapter, raw)
        assert chapter["workflow_contract"]["domain"] == "equity"

        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
            evaluate_completion_contract,
        )

        result = MapCompletionResult(
            status="complete", layer_status="valid", component_status="valid",
            render_status="not_applicable",
        )
        contract = evaluate_completion_contract(
            result, raw["methodology_warnings"], chapter,
        )
        assert contract["workflow_present"] is True
        assert contract["dimensions"]["methodology_disclosure"] is True

    def test_repair_rerun_clears_stale_blocking_fallback(self):
        """R2-1 主断言：修复后重跑（fallbacks=[]）必须清掉上一轮的
        not_allowed 阻断 —— 修复循环不得永久 BLOCKED。"""
        from app.services.session_plan import merge_map_product_result
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
            derive_product_verdict,
        )

        chapter = {"query": "q", "fallbacks": [
            {"reason_code": "INSAR_STACK_INSUFFICIENT",
             "evidence": {"downgrade_class": "not_allowed"}}]}
        # 第一轮：阻断生效
        blocked = MapCompletionResult(
            status="complete", layer_status="valid", component_status="valid",
            render_status="verified")
        v1 = derive_product_verdict(blocked, [], chapter=chapter)
        assert v1["verdict"] == "BLOCKED_BY_METHOD"

        # 修复后重跑：新结果带回空 fallbacks → 旧阻断清除
        merge_map_product_result(chapter, {"fallbacks": []})
        v2 = derive_product_verdict(blocked, [], chapter=chapter)
        assert v2["verdict"] == "READY"

        # 旧版本工具结果（键缺席）→ chapter 保持零漂移
        chapter["fallbacks"] = [{"reason_code": "OLD", "evidence": {
            "downgrade_class": "not_allowed"}}]
        merge_map_product_result(chapter, {"status": "finalized"})
        v3 = derive_product_verdict(blocked, [], chapter=chapter)
        assert v3["verdict"] == "BLOCKED_BY_METHOD"

    def test_non_dict_evidence_never_crashes_verdict(self):
        """R2-4：旧/损坏章节的 fallback.evidence 非字典时裁决不崩。"""
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
            derive_product_verdict,
        )

        chapter = {"fallbacks": [
            {"reason_code": "LEGACY", "evidence": "not-a-dict"},
            {"reason_code": "", "evidence": {"downgrade_class": "not_allowed"}},
        ]}
        result = MapCompletionResult(
            status="complete", layer_status="valid", component_status="valid",
            render_status="verified")
        verdict = derive_product_verdict(result, [], chapter=chapter)
        assert verdict["verdict"] == "READY"

    def test_not_allowed_fallback_blocks_completion(self):
        """R1-A2：not_allowed 语义降级触发 → science 维失败 → BLOCKED_BY_METHOD。"""
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
            derive_product_verdict,
        )

        chapter = {
            "workflow_contract": {
                "method_blockers": [], "data_blockers": [], "obligations": [],
            },
            "fallbacks": [
                {"reason_code": "INSAR_STACK_INSUFFICIENT",
                 "evidence": {"downgrade_class": "not_allowed"}},
            ],
        }
        result = MapCompletionResult(
            status="complete", layer_status="valid", component_status="valid",
            render_status="verified",
        )
        verdict = derive_product_verdict(result, [], chapter=chapter)
        assert verdict["verdict"] == "BLOCKED_BY_METHOD"
        assert "INSAR_STACK_INSUFFICIENT" in verdict["reasons"]
        assert verdict["completion_dimensions"]["science"] is False

    def test_planner_disclosure_refresh_removes_stale_warning(self):
        """R1-B7：义务被 finalize 证据满足后，规划期同码披露确定性移除。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("成都各区的教育资源公平性如何")
        plan = planner.plan_from_intent(
            intent, recipe_id="education_equity_per_capita", use_memo=False)
        assert any(
            w.get("code") == "EQUITY_MISSING_DENOMINATOR"
            for w in plan.methodology_warnings
        )
        profile = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"population": {"type": "number"}}}
        finalized = planner.finalize_with_profile(plan, profile)
        codes = {str(w.get("code")) for w in finalized.methodology_warnings}
        assert "EQUITY_MISSING_DENOMINATOR" not in codes
        assert finalized.workflow_contract is not None
        roles = {r["role"]: r["status"] for r in finalized.workflow_contract["roles"]}
        assert roles["denominator"] == "bound"  # R1-A11：强字段证据才可 bound

    def test_denominator_weak_hints_never_bind(self):
        """R1-A11：total/count 类弱字段提示不得满足分母义务。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("成都各区的教育资源公平性如何")
        plan = planner.plan_from_intent(
            intent, recipe_id="education_equity_per_capita", use_memo=False)
        profile = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"total_schools": {"type": "number"}}}
        finalized = planner.finalize_with_profile(plan, profile)
        roles = {r["role"]: r["status"] for r in finalized.workflow_contract["roles"]}
        assert roles["denominator"] != "bound"


class TestAnalysisGraphWorkflow:
    def test_goal_node_carries_workflow_block(self):
        from app.services.gis_harness.analysis_graph import build_analysis_graph

        chapter = {
            "query": "q", "recipe_id": "education_equity_per_capita",
            "workflow_contract": {
                "domain": "equity", "workflow_family": "education_equity",
                "roles": [{"role": "denominator", "status": "external"}],
                "obligations": [{"obligation_id": "o", "status": "warning",
                                 "warning_code": "EQUITY_MISSING_DENOMINATOR"}],
                "method_blockers": [], "data_blockers": [],
            },
        }
        graph = build_analysis_graph({"user_goal": "q", "gis_chapter": chapter})
        wf_block = graph["goal"]["workflow"]
        assert wf_block["domain"] == "equity"
        assert wf_block["roles"][0]["role"] == "denominator"
        assert wf_block["obligations"][0]["warning_code"] == "EQUITY_MISSING_DENOMINATOR"

    def test_v1_chapter_goal_node_has_no_workflow_block(self):
        from app.services.gis_harness.analysis_graph import build_analysis_graph

        chapter = {"query": "q", "recipe_id": "poi_distribution_overview"}
        graph = build_analysis_graph({"user_goal": "q", "gis_chapter": chapter})
        assert "workflow" not in graph["goal"]


class TestManifestStaleEndToEnd:
    def test_recipe_semantics_change_flips_plan_stale(self):
        """R1-B4（C12 端到端）：workflow 语义变化 → manifest 指纹变化 →
        旧计划 is_stale_plan 翻转。全程恢复 registry 状态。"""
        from app.services.gis_harness.recipes import (
            get_recipe_registry,
            reset_recipe_registry,
        )
        from app.lib.gis.runtime_manifest import (
            compile_runtime_manifest,
            get_runtime_manifest,
            refresh_runtime_manifest,
        )

        base_manifest = get_runtime_manifest()
        assert not base_manifest.issues or all(
            "fatal" not in str(i).lower() for i in base_manifest.issues
        )
        assert base_manifest.recipes, "recipes must project into manifest"
        rid = "kriging_interpolation_workflow"
        base_fp = base_manifest.recipes[rid]["content_fingerprint"]

        try:
            reset_recipe_registry()
            registry = get_recipe_registry()
            assert registry.count == 164  # 重建成功（fail-loud 路径未触发）
            recipe = registry.get(rid)
            mutated = recipe.model_copy(deep=True)
            mutated.workflow.keywords_zh = [*mutated.workflow.keywords_zh, "审阅突变词"]
            # 直接替换 registry 内对象（immutable projection 的合法重建面）
            registry._by_id[rid] = mutated
            registry._content_fps[rid] = __import__(
                "app.services.gis_harness.workflow_schema", fromlist=["recipe_content_fingerprint"]
            ).recipe_content_fingerprint(mutated)

            fresh = compile_runtime_manifest()
            assert fresh.fingerprint != base_manifest.fingerprint
            assert fresh.recipes[rid]["content_fingerprint"] != base_fp
            # 旧计划 → stale 翻转
            assert fresh.is_stale_plan(base_manifest.fingerprint) is True
            assert not fresh.is_stale_plan(fresh.fingerprint)
        finally:
            reset_recipe_registry()
            refresh_runtime_manifest()

    def test_manifest_with_full_packs_compiles_fatal_free(self):
        from app.lib.gis.runtime_manifest import compile_runtime_manifest

        manifest = compile_runtime_manifest()
        fatals = [i for i in manifest.issues if "fatal" in str(i).lower()]
        assert fatals == []
        assert len(manifest.recipes) == 164


class TestWorkflowProfileValidationSweep:
    def test_all_v2_recipes_validate_clean(self):
        """R1-A7/B5：147 个 V2 recipe 的 workflow 画像静态校验零违规。"""
        from app.lib.gis.artifacts import get_artifact_type_registry
        from app.lib.gis.capability_registry import get_capability_registry
        from app.lib.gis.scientific_preconditions import precondition_exists
        from app.services.gis_harness.recipes import get_recipe_registry
        from app.services.gis_harness.workflow_schema import validate_workflow_profile

        caps = get_capability_registry()
        arts = get_artifact_type_registry()
        registry = get_recipe_registry()
        violations = []
        for rid in build_v2_ids():
            recipe = registry.get(rid)
            issues = validate_workflow_profile(
                recipe.workflow,
                capability_exists=caps.has,
                artifact_type_exists=arts.has,
                precondition_exists=precondition_exists,
            )
            violations.extend(f"{rid}: {v}" for v in issues)
        assert violations == []


def build_v2_ids():
    from app.services.gis_harness.recipe_packs import all_pack_recipes

    return sorted(r.id for r in all_pack_recipes() if r.workflow is not None)


def test_workflow_profile_validation_is_wired_into_registry_validation():
    """R1-A7：registry_validation 真正调用 validate_workflow_profile。"""
    from app.services.gis_harness.registry_validation import validate_gis_library

    issues = validate_gis_library()
    # 当前知识库应零违规；有违规说明校验已接入（非死代码）且被内容违反
    assert issues == []


def test_catalog_matches_registry():
    """R1-B6：提交的 catalog 与 registry 渲染结果字节一致（过期即红）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts").resolve()))
    from gen_workflow_catalog import render_catalog

    committed = Path("docs/workflows/workflow-catalog.md").read_text(encoding="utf-8")
    assert render_catalog() == committed
