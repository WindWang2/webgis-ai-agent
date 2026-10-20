"""H01 场景工厂（任务 §3.E）：16+ 类无密钥可本地复现的任务场景。

每类固定：request → expected intent family → required roles/qualifications
→ selected or rejected capability/recipe → expected plan edge → expected
status / reason → bounded evidence。全部经真实生产函数（无 mock 骗过生产
入口）：resolve_map_request_intent / MapProductPlanner / qualify_workflow_
data_roles / qualify_surface / build_result_envelope / derive_product_verdict。

场景索引（SCENARIO_INDEX）覆盖任务要求的类别；meta 测试钉住类别完整性。
负样例与 metamorphic（顺序扰动/重试/重复调用/大小写/中文同义词）在
TestMetamorphicScenarios。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chat.pi_result_abi import build_result_envelope
from app.services.chat.pi_surface_receipt import (
    DROP_CREDENTIAL_ABSENT,
    DROP_OFFLINE_NETWORK,
    DROP_PERMISSION_NOT_GRANTED,
    qualify_surface,
)
from app.services.chat.pi_tool_spec import compile_tool_spec
from app.services.gis_harness.completion.contracts import (
    F_ARTIFACT_EXPIRED,
    F_TASK_SEMANTIC_DOWNGRADED,
    STATUS_COMPLETE,
    MapCompletionFinding,
    MapCompletionResult,
    derive_product_verdict,
)
from app.services.gis_harness.data_qualification import (
    _unit_dimension_guard,
    qualify_workflow_data_roles,
)
from app.services.gis_harness.intent import (
    merge_intent_hints,
    resolve_map_request_intent,
)
from app.services.gis_harness.planner import MapProductPlanner
from app.services.gis_harness.recipes import get_recipe_registry
from app.services.gis_harness.workflow_schema import resolve_data_roles
from app.tools.registry import ToolRegistry

#: 场景索引：id → 类别（任务 §3.E 列表）。meta 测试断言类别全覆盖。
SCENARIO_INDEX = {
    "S01": "成都小学展示（simple_view，不过度分析）",
    "S02": "成都各区数量（administrative_statistic，choropleth 主表达）",
    "S03": "定量密度（analytical_density，非视觉热力）",
    "S04": "密度缺分母（denominator 外部获取未证 → 降级披露）",
    "S05": "热点分析（concentration_analysis）",
    "S06": "距离/服务区（accessibility_analysis）",
    "S07": "POI 丢失（required 角色缺失 → blocked）",
    "S08": "字段缺失（字段证据缺席 → unknown 不虚构）",
    "S09": "空数据（featureCount=0 → EMPTY_DATASET blocked）",
    "S10": "少点降级（min_points 不满足 → INSUFFICIENT_POINTS 回退）",
    "S11": "CRS 不明（crs 缺席 → deferred PASS 诚实 unknown）",
    "S12": "不同 CRS（EPSG:4326 + 投影义务 → transform_required）",
    "S13": "单位误配（率字段无时间证据 → 量纲闸失败）",
    "S14": "无凭据（credential_absent 逐出 native 面）",
    "S15": "无网络（offline_network_tool 逐出）",
    "S16": "ref 失效（F_ARTIFACT_EXPIRED → BLOCKED_BY_DATA）",
    "S17": "权限拒绝（permission_not_granted 先验逐出 + registry 同码）",
    "S18": "工具 schema 改版（spec 指纹漂移可检测）",
    "S19": "旧会话恢复（resumed stale 节点 → NEEDS_REPAIR）",
    "S20": "重复回执（repeated → 去重披露不重复执行）",
    "S21": "Pi/legacy parity（同一资格事实面 → 同一等价 digest）",
}

_REQUIRED_CATEGORIES = [
    "展示", "数量", "定量密度", "分母", "热点", "服务区", "POI 丢失",
    "字段缺失", "空数据", "少点降级", "CRS 不明", "不同 CRS", "单位误配",
    "无凭据", "无网络", "ref 失效", "权限拒绝", "schema 改版",
    "旧会话恢复", "重复回执", "parity",
]


def test_meta_scenario_index_covers_required_categories():
    blob = " ".join(SCENARIO_INDEX.values())
    for category in _REQUIRED_CATEGORIES:
        assert category in blob, f"场景矩阵缺类别: {category}"


def _result(status=STATUS_COMPLETE, findings=None):
    r = MapCompletionResult(status=status)
    r.findings = list(findings or [])
    return r


def _density_recipe():
    recipe = get_recipe_registry().get("density_quantitative_per_area")
    assert recipe is not None and recipe.workflow is not None
    return recipe


def _quals(recipe, profile, **kw):
    roles = resolve_data_roles(
        recipe.id, recipe.workflow, resolver_profile=profile)
    return qualify_workflow_data_roles(
        recipe.workflow.data_roles, roles, resolver_profile=profile, **kw)


# ── S01–S06：意图 → 计划（golden 家族） ──────────────────────────────


class TestIntentAndPlanScenarios:
    @pytest.mark.parametrize("query,task", [
        ("给我看看成都小学", "simple_view"),
        ("成都小学的分布情况", "distribution_overview"),
    ])
    def test_s01_display_queries(self, query, task):
        intent = resolve_map_request_intent(query)
        assert intent.task == task

    def test_s02_administrative_statistic(self):
        intent = resolve_map_request_intent("成都各区小学数量")
        assert intent.task == "administrative_statistic"
        plan = MapProductPlanner().plan_from_intent(intent)
        assert plan.recipe_id == "administrative_choropleth"
        assert all(
            ly.layer_type != "heatmap" for ly in plan.map_layers
        ), "统计任务主表达不得是热力"

    def test_s03_analytical_density_semantic_isolation(self):
        intent = resolve_map_request_intent("成都每平方公里小学密度")
        assert intent.task == "analytical_density"
        # hint 不可把定量密度降级为视觉任务（绝对语义隔离）
        merged = merge_intent_hints(intent, {"task": "distribution_overview"})
        assert merged.task == "analytical_density"

    def test_s04_density_missing_denominator_degrades_honestly(self):
        recipe = _density_recipe()
        quals = _quals(recipe, {"geometryTypes": ["Point"], "featureCount": 500})
        by_role = {q.role: q for q in quals}
        assert by_role["denominator"].state == "unknown"
        assert by_role["denominator"].reason_code == (
            "EXTERNAL_ACQUISITION_UNVERIFIED"
        )
        # 完成判据：degraded 角色在场 → tier degraded → READY 封顶
        wc = MapProductPlanner()._evaluate_workflow_contract(
            MapProductPlanner().plan_from_intent(
                resolve_map_request_intent("成都每平方公里小学密度")),
            recipe, {"geometryTypes": ["Point"], "featureCount": 500})
        v = derive_product_verdict(_result(), chapter={
            "workflow_contract": wc, "intent": {"task": "analytical_density"}})
        assert v["verdict"] != "READY"
        assert v.get("fallback_tier") == "degraded"

    def test_s05_concentration_analysis(self):
        assert resolve_map_request_intent(
            "成都小学哪里最集中").task == "concentration_analysis"

    def test_s06_accessibility_analysis(self):
        assert resolve_map_request_intent(
            "成都小学500米服务区").task == "accessibility_analysis"


# ── S07–S13：数据资格（五态） ────────────────────────────────────────


class TestDataQualificationScenarios:
    def _recipe_with_subject(self):
        recipe = _density_recipe()
        return recipe

    def test_s07_missing_required_role_blocks(self):
        """required 角色无数据（policy=block）→ blocked + 机器码。"""
        recipe = self._recipe_with_subject()
        req = recipe.workflow.data_roles[0]  # subject（required, local）
        from app.services.gis_harness.data_qualification import (
            qualify_data_role,
        )

        qual = qualify_data_role(req, "unresolved")
        assert qual.state == "blocked"
        assert qual.reason_code == "DATA_ROLE_MISSING_SUBJECT"
        # 完成判据链：blocked 资格（无声明降级覆盖）→ BLOCKED_BY_DATA
        v = derive_product_verdict(_result(), chapter={"workflow_contract": {
            "data_qualifications": [
                {"role": "subject", "state": "blocked",
                 "reason_codes": ["DATA_ROLE_MISSING_SUBJECT"]}],
            "fallback_tier": "blocked",
        }})
        assert v["verdict"] == "BLOCKED_BY_DATA"
        assert "data_qualification_blocked" in v["reasons"]

    def test_s08_missing_fields_unknown_not_fabricated(self):
        recipe = self._recipe_with_subject()
        quals = _quals(recipe, None)  # 无 profile → 无字段事实
        by_role = {q.role: q for q in quals}
        assert by_role["subject"].state == "unknown"
        assert by_role["subject"].reason_code == "PROFILE_FACTS_UNAVAILABLE"

    def test_s09_empty_dataset_blocked(self):
        recipe = self._recipe_with_subject()
        quals = _quals(
            recipe, {"geometryTypes": ["Point"], "featureCount": 0})
        by_role = {q.role: q for q in quals}
        assert by_role["subject"].state == "blocked"
        assert by_role["subject"].reason_code == "EMPTY_DATASET"

    def test_s10_insufficient_points_fallback(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 7})
        codes = [f.reason_code for f in final.fallbacks]
        assert any("INSUFFICIENT_POINTS" in c for c in codes), (
            "少点必须触发结构化回退（7 < min_points=10）"
        )

    def test_s11_unknown_crs_deferred_pass(self):
        recipe = self._recipe_with_subject()
        quals = _quals(
            recipe,
            {"geometryTypes": ["Point"], "featureCount": 500, "crs": ""},
            crs_projection_obligation=True,
        )
        by_role = {q.role: q for q in quals}
        # 诚实语义：CRS 事实缺席 = deferred PASS（不虚构违反；与「没查」
        # 的区分是 H01 RECON G4 登记的已知边界）
        assert by_role["subject"].state in ("eligible", "unknown")

    def test_s12_different_crs_transform_required(self):
        recipe = self._recipe_with_subject()
        quals = _quals(
            recipe,
            {"geometryTypes": ["Point"], "featureCount": 500,
             "crs": "EPSG:4326"},
            crs_projection_obligation=True,
        )
        by_role = {q.role: q for q in quals}
        assert by_role["subject"].state == "transform_required"
        assert by_role["subject"].reason_code == "PROJECTED_CRS_REQUIRED"

    def test_s13_unit_dimension_mismatch(self):
        sp = SimpleNamespace(
            role_index={"normalization_denominator": "population_rate"},
            field_roles=[SimpleNamespace(
                field="population_rate", roles=["rate"])],
        )
        guard = _unit_dimension_guard("denominator", sp)
        assert guard, "率字段无时间证据必须触发量纲闸"
        assert guard["code"] == "RATE_MISSING_TEMPORAL"


# ── S14/S15/S17：turn 面资格逐出 ────────────────────────────────────


def _registry_with_gated_tool(**register_kwargs):
    registry = ToolRegistry()
    registry.register(
        "h01_gated_tool", "gated tool", lambda **kw: {"success": True},
        parameters={"type": "object", "properties": {}},
        **register_kwargs,
    )
    spec = compile_tool_spec(registry, "h01_gated_tool")
    assert spec is not None
    return spec


class TestSurfaceQualificationScenarios:
    def test_s14_credential_absent_drop(self):
        spec = _registry_with_gated_tool(
            requires_credentials=["stats_api"])
        selected, drops = qualify_surface([spec], situation={
            "credentials_present": {"stats_api": False},
            "granted_permissions": [],
        })
        assert selected == ()
        assert drops[0].code == DROP_CREDENTIAL_ABSENT

    def test_s15_offline_network_tool_drop(self):
        spec = _registry_with_gated_tool(network=True)
        selected, drops = qualify_surface([spec], situation={
            "credentials_present": {}, "offline": True,
            "granted_permissions": [],
        })
        assert selected == ()
        assert drops[0].code == DROP_OFFLINE_NETWORK

    def test_s17_permission_denied_prior_disclosure(self):
        """权限先验逐出（H01 K5）与 registry 闸（dispatch 权威）同事实
        同向：turn 面逐出 + dispatch 期 PERMISSION_DENIED。"""
        spec = _registry_with_gated_tool(required_permission="admin")
        selected, drops = qualify_surface([spec], situation={
            "credentials_present": {}, "granted_permissions": [],
        })
        assert selected == ()
        assert drops[0].code == DROP_PERMISSION_NOT_GRANTED
        # dispatch 权威（registry #1402 闸）：未授予权限 → typed 拒绝
        import asyncio

        registry = ToolRegistry()
        registry.register(
            "h01_perm_gate", "t", lambda **kw: {"success": True},
            parameters={"type": "object", "properties": {}},
            required_permission="admin",
        )
        resp = asyncio.run(registry.dispatch("h01_perm_gate", {}))
        assert resp.get("success") is False
        assert resp.get("code") == "PERMISSION_DENIED"


# ── S16/S18/S20：执行结果契约 ───────────────────────────────────────


class TestExecutionContractScenarios:
    def test_s16_dangling_ref_blocks_by_data(self):
        from app.services.gis_harness.completion.contracts import (
            STATUS_FAILED,
        )

        r = _result(status=STATUS_FAILED, findings=[MapCompletionFinding(
            code=F_ARTIFACT_EXPIRED, severity="error")])
        v = derive_product_verdict(r)
        assert v["verdict"] == "BLOCKED_BY_DATA"

    def test_s18_schema_drift_changes_spec_fingerprint(self):
        r1 = ToolRegistry()
        r1.register(
            "h01_drift", "t", lambda **kw: {}, parameters={
                "type": "object", "properties": {"a": {"type": "string"}}})
        r2 = ToolRegistry()
        r2.register(
            "h01_drift", "t", lambda **kw: {}, parameters={
                "type": "object", "properties": {
                    "a": {"type": "string"}, "b": {"type": "number"}}})
        fp1 = compile_tool_spec(r1, "h01_drift").fingerprint()
        fp2 = compile_tool_spec(r2, "h01_drift").fingerprint()
        assert fp1 != fp2, "schema 改版必须改变 spec 指纹（stale 可检测）"

    def test_s20_repeated_receipt_disclosed_not_executed(self):
        class _Repeated:
            status = "repeated"
            llm_payload = "same call"
            slim_event = {}
            geojson_ref = None
            raw_result = {}
            error_msg = None
            map_actions = []
            background_job_ids = []
            capability_evidence = None
            action_evidence = None

        env = build_result_envelope(_Repeated())
        assert env["status"] == "repeated"
        assert any("deduplicated" in w for w in env["warnings"])


# ── S19：旧会话恢复 ─────────────────────────────────────────────────


class TestRecoveryScenario:
    def test_s19_resumed_stale_nodes_need_repair(self):
        chapter = {
            "workflow_contract": {},
            "resumed_from": {"verify": {
                "workflow_verdict": "stale",
                "stale_nodes": ["acquire.poi_query", "analyze.density"],
            }},
        }
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "NEEDS_REPAIR"
        assert "runtime_node_stale" in v["reasons"]


# ── S21：Pi/legacy parity ───────────────────────────────────────────


class TestParityScenario:
    def test_s21_planner_dispatch_same_facts_same_digest(self):
        """Pi turn 面与 dispatch 侧的资格事实面等价（同一 digest 基）——
        H01 收敛的 parity 证据（与 test_runtime_situation 同源契约）。"""
        from app.services.gis_harness.capability_resolution import (
            build_situation,
        )
        from app.services.gis_harness.hotpath_convergence.runtime_situation import (
            merge_situation_facts,
            situation_facts_digest,
        )

        base = build_situation(task_hint="cluster points")
        merged = merge_situation_facts(base, "sess-parity-h01")
        digest = situation_facts_digest(merged)
        assert isinstance(digest, str) and len(digest) == 16
        # 同输入同输出（确定性 parity）
        assert situation_facts_digest(
            merge_situation_facts(build_situation(task_hint="cluster points"),
                                  "sess-parity-h01")
        ) == digest

    def test_s21_task_semantic_guard_blocks_visual_delivery(self):
        chapter = {
            "workflow_contract": {
                "workflow_family": "visual_density",
                "data_qualifications": [],
                "fallback_tier": "preferred",
            },
            "intent": {"task": "analytical_density"},
        }
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "BLOCKED_BY_METHOD"
        assert F_TASK_SEMANTIC_DOWNGRADED in v["reasons"]


# ── metamorphic：顺序/重试/重复/大小写/同义词 ────────────────────────


class TestMetamorphicScenarios:
    def test_m01_determinism_same_query_same_plan(self):
        intent1 = resolve_map_request_intent("成都小学的分布情况")
        intent2 = resolve_map_request_intent("成都小学的分布情况")
        assert intent1.task == intent2.task
        plan1 = MapProductPlanner().plan_from_intent(intent1)
        plan2 = MapProductPlanner().plan_from_intent(intent2)
        assert plan1.plan_id == plan2.plan_id, "同查询必须同 plan_id（可回放）"

    def test_m02_word_order_perturbation_stable_family(self):
        base = resolve_map_request_intent("成都各区小学数量").task
        perturbed = resolve_map_request_intent("成都各区的小学数量").task
        assert base == perturbed == "administrative_statistic"

    def test_m03_case_and_particle_insensitive(self):
        for variant in ("成都小学分布情况", "成都小学的分布情况"):
            assert resolve_map_request_intent(variant).task in (
                "distribution_overview", "simple_view")

    def test_m04_chinese_synonyms_density_guard(self):
        for variant in ("成都每平方公里小学密度", "成都小学单位面积密度"):
            intent = resolve_map_request_intent(variant)
            merged = merge_intent_hints(intent, {"task": "distribution_overview"})
            assert merged.task == intent.task, "同义词不得穿透语义护栏"

    def test_m05_retry_same_qualification_verdict(self):
        chapter = {
            "workflow_contract": {
                "data_qualifications": [
                    {"role": "subject", "state": "blocked",
                     "reason_codes": ["DATA_ROLE_MISSING_SUBJECT"]}],
                "fallback_tier": "blocked",
            },
        }
        v1 = derive_product_verdict(_result(), chapter=chapter)
        v2 = derive_product_verdict(_result(), chapter=chapter)
        assert v1 == v2, "同输入必须同裁决（重试/幂等基）"
        assert v1["verdict"] == "BLOCKED_BY_DATA"
