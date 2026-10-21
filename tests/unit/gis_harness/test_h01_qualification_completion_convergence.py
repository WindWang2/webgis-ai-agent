"""H01（K2/K3/K4）：资格裁决 ↔ 完成判据收敛。

master 现状：五态 data_qualification 与 fallback_tier 只在编译器证据侧
（无生产消费方，K2/K3）；finalize 热路径的 ``workflow_contract`` 不携带
它们；``evaluate_completion_contract`` 读不到 → degraded/minimal 产品可
判 READY，「拒绝/降级」裁决与完成判据脱钩。任务语义存续（定量密度被
降级为视觉表达，K4）在完成层无校验。

本文件钉住：
- finalize 契约加性携带 ``data_qualifications`` + ``fallback_tier``
  （与编译器同源：``qualify_workflow_data_roles`` / ``resolve_fallback_tier``）；
- 完成判据消费：blocked 资格（无声明降级覆盖）→ BLOCKED_BY_DATA；
  minimal/degraded 档 → READY 封顶 READY_WITH_WARNINGS（披露义务）；
- 已声明允许降级（recipe fallback blocks=False）的 blocked 角色不误伤；
- K4：intent.task=analytical_density 交付 visual_density 族 →
  F_TASK_SEMANTIC_DOWNGRADED → BLOCKED_BY_METHOD；
- 键缺席（V1 recipe / 旧章节）→ 逐位 master 行为。
"""
from __future__ import annotations

from app.services.gis_harness.completion.contracts import (
    STATUS_COMPLETE,
    MapCompletionResult,
    derive_product_verdict,
    evaluate_completion_contract,
)
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner


def _result(status=STATUS_COMPLETE, findings=None):
    r = MapCompletionResult(status=status)
    r.findings = list(findings or [])
    return r


# ── finalize 契约加性键（K2 生产写入） ───────────────────────────────


class TestFinalizeContractAdditiveKeys:
    def test_quantitative_density_missing_denominator_carries_qualifications(self):
        """定量密度 recipe（分母角色缺席）：finalize 契约评估携带五态 + tier
        （与编译器同源语义）。recipe 经注册表获取（路由层选择序是另一层
        语义，不在本测试面）。"""
        intent = resolve_map_request_intent("成都每平方公里小学密度")
        assert intent.task == "analytical_density"
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get("density_quantitative_per_area")
        assert recipe is not None and recipe.workflow is not None
        wc = planner._evaluate_workflow_contract(
            plan, recipe, {"geometryTypes": ["Point"], "featureCount": 500})
        assert wc and "data_qualifications" in wc, (
            "finalize 契约必须携带五态资格"
        )
        by_role = {q["role"]: q for q in wc["data_qualifications"]}
        # 生产语义（与 data_qualification 同源）：点数据 → subject 就绪；
        # boundary 需聚合（degraded）；denominator 走 data_fabric 外部获取
        # → 规划期不可证伪（unknown + EXTERNAL_ACQUISITION_UNVERIFIED）。
        assert by_role["subject"]["state"] == "eligible"
        assert by_role["boundary"]["state"] == "degraded"
        assert any(
            "GEOMETRY_AGGREGATE_REQUIRED" in c
            for c in by_role["boundary"].get("reason_codes", [])
        )
        assert by_role["denominator"]["state"] == "unknown"
        assert any(
            "EXTERNAL_ACQUISITION_UNVERIFIED" in c
            for c in by_role["denominator"].get("reason_codes", [])
        )
        assert wc.get("fallback_tier") == "degraded", (
            "finalize 契约必须携带回退档（degraded 角色在场 → degraded）"
        )

    def test_healthy_finalize_qualifications_all_eligible(self):
        intent = resolve_map_request_intent("成都小学的分布情况")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 1260})
        wc = final.workflow_contract
        # 该 query 命中无 workflow 画像的 V1 recipe（poi_distribution_
        # overview）→ finalize 契约 None —— 键缺席路径（旧读者零漂移）。
        assert wc is None, "V1 recipe finalize 契约应为 None（无 workflow）"

    def test_v1_recipe_without_workflow_has_no_new_keys(self):
        """无 workflow 画像的 recipe：_evaluate_workflow_contract 返回
        None → 键缺席 → 旧读者零漂移（确定性断言，无软守卫）。"""
        intent = resolve_map_request_intent("给我看看成都小学")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 30})
        wc = final.workflow_contract
        assert wc is None or "data_qualifications" not in wc, (
            "无 workflow 画像的 finalize 契约不得携带五态键"
        )


# ── 完成判据消费（K2/K3） ────────────────────────────────────────────


def _chapter(**wf):
    return {"workflow_contract": dict(wf)}


class TestCompletionConsumesQualifications:
    def test_blocked_qualification_blocks_by_data(self):
        chapter = _chapter(
            data_qualifications=[
                {"role": "subject", "state": "eligible", "reason_codes": []},
                {"role": "denominator", "state": "blocked",
                 "reason_codes": ["DATA_ROLE_MISSING_DENOMINATOR"]},
            ],
            fallback_tier="blocked",
        )
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "BLOCKED_BY_DATA"
        assert "data_qualification_blocked" in v["reasons"]
        assert v["completion_dimensions"]["data"] is False

    def test_blocked_role_with_declared_degradable_fallback_not_blocked(self):
        """已声明允许降级（recipe fallback blocks=False，如分母缺失 →
        raw_counts）：不 BLOCKED，由方法论披露压档。"""
        chapter = {
            "workflow_contract": {
                "data_qualifications": [
                    {"role": "denominator", "state": "blocked",
                     "reason_codes": ["DATA_ROLE_MISSING_DENOMINATOR"]},
                ],
                "fallback_tier": "blocked",
            },
            "fallbacks": [{
                "reason_code": "DATA_ROLE_MISSING_DENOMINATOR",
                "evidence": {"downgrade_class": "degraded"},
            }],
        }
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] != "BLOCKED_BY_DATA"

    def test_minimal_tier_caps_ready_at_warnings(self):
        chapter = _chapter(
            data_qualifications=[
                {"role": "subject", "state": "degraded", "reason_codes": []},
            ],
            fallback_tier="minimal",
        )
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "READY_WITH_WARNINGS"
        assert any(r.startswith("fallback_tier:") for r in v["reasons"])

    def test_degraded_tier_caps_ready(self):
        chapter = _chapter(
            data_qualifications=[
                {"role": "subject", "state": "degraded", "reason_codes": []},
            ],
            fallback_tier="degraded",
        )
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "READY_WITH_WARNINGS"

    def test_preferred_tier_keeps_ready(self):
        chapter = _chapter(
            data_qualifications=[
                {"role": "subject", "state": "eligible", "reason_codes": []},
            ],
            fallback_tier="preferred",
        )
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "READY"

    def test_absent_keys_master_behavior(self):
        """键缺席（V1 recipe / 旧章节）→ 与 master 逐位一致。"""
        v = derive_product_verdict(_result(), chapter=_chapter())
        assert v["verdict"] == "READY"
        assert v["reasons"] == []
        c = evaluate_completion_contract(_result(), [], _chapter())
        assert c["dimensions"]["data"] is True

    def test_contract_exposes_qualification_summary(self):
        chapter = _chapter(
            data_qualifications=[
                {"role": "denominator", "state": "blocked",
                 "reason_codes": ["DATA_ROLE_MISSING_DENOMINATOR"]},
            ],
            fallback_tier="blocked",
        )
        c = evaluate_completion_contract(_result(), [], chapter)
        assert c.get("fallback_tier") == "blocked"
        assert c.get("data_qualification_states", {}).get("denominator") == "blocked"


# ── K4：任务语义存续 ─────────────────────────────────────────────────


class TestTaskSemanticPersistence:
    def test_analytical_density_delivered_as_visual_is_blocked_by_method(self):
        chapter = _chapter(
            workflow_family="visual_density",
            data_qualifications=[],
            fallback_tier="preferred",
        )
        chapter["intent"] = {"task": "analytical_density", "scope": {"name": "成都"}}
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "BLOCKED_BY_METHOD"
        assert "F_TASK_SEMANTIC_DOWNGRADED" in v["reasons"]

    def test_analytical_density_quantitative_family_is_fine(self):
        chapter = _chapter(
            workflow_family="quantitative_density",
            data_qualifications=[],
            fallback_tier="preferred",
        )
        chapter["intent"] = {"task": "analytical_density"}
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "READY"

    def test_visual_task_with_visual_family_unaffected(self):
        chapter = _chapter(
            workflow_family="visual_density",
            data_qualifications=[],
            fallback_tier="preferred",
        )
        chapter["intent"] = {"task": "distribution_overview"}
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] == "READY"

    def test_missing_intent_or_family_skips_guard(self):
        v = derive_product_verdict(_result(), chapter=_chapter(
            workflow_family="visual_density"))
        assert v["verdict"] == "READY"


# ── 端到端：真实 planner 链（资格 → 契约 → 裁决同源） ────────────────


class TestEndToEndConvergence:
    def test_missing_denominator_golden_case_verdict(self):
        """金样例：定量密度缺分母 → 不允许 READY（诚实降级面）。真实
        planner 契约评估（五态 + tier）→ 章节数据形状 → 产品裁决，全链
        生产函数。"""
        intent = resolve_map_request_intent("成都每平方公里小学密度")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get("density_quantitative_per_area")
        wc = planner._evaluate_workflow_contract(
            plan, recipe, {"geometryTypes": ["Point"], "featureCount": 500})
        # 声明的降级策略（DATA_ROLE_MISSING_DENOMINATOR → raw_counts，
        # blocks=False）按生产形状进章节 fallbacks。
        chapter = {
            "workflow_contract": wc,
            "intent": (
                intent.model_dump() if hasattr(intent, "model_dump") else {}
            ),
            "fallbacks": [
                f.model_dump() if hasattr(f, "model_dump") else dict(f)
                for f in (plan.fallbacks or [])
            ],
            "methodology_warnings": [
                w if isinstance(w, dict) else dict(w)
                for w in (plan.methodology_warnings or [])
            ],
        }
        v = derive_product_verdict(_result(), chapter=chapter)
        assert v["verdict"] != "READY", "缺分母的定量密度不得判 READY"
        assert v.get("fallback_tier"), "裁决面必须携带回退档披露"
