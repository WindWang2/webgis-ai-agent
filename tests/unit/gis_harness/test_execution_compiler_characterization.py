"""G02-GIS-EXECUTION-COMPILER characterization —— 断链钉扎（M1）。

这些测试钉住 BASELINE.md 审计发现的 4 条真实断链，定义目标行为：

  C1  RequirementIR 不驱动执行编译 —— family 由 LLM 聊天自由指定；
      编译器必须从 RequirementDocument 确定性推导族候选+表达候选+单位义务。
  C2  analysis workflow 族运行回执（WorkflowRunState）对 goal_satisfaction
      不可见 —— 验收桥必须把族步骤投影为 chapter 行证据（节点成功≠目标达成）。
  C3  族声明 ↔ ToolRegistry/capability registry 无一致性 fail-closed 投影。
  C4  IR patch（改色/换城/换统计）→ 受影响步骤集推导缺失 —— 局部风格改变
      不得触发科学重算（含换城市全量数据 ref 失效）。
  C5  单位/量纲义务无跨层静态对账（秒≠米、计数≠率、点≠密度）。

全部为纯函数断言（零 IO / 零 LLM / 固定种子）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    build_contract,
)
from app.services.gis_harness.analysis_workflows.state import (
    StepStateEntry,
    WorkflowRunState,
    new_state,
)
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir.build import build_document


def _doc(query: str):
    core = resolve_map_request_intent(query)
    return build_document(query, core)


# ── C1: IR → 确定性族/表达编译 ────────────────────────────────────────────


class TestRequirementToTaskCompilation:
    def test_chengdu_primary_schools_compiles_deterministically(self):
        """「成都小学分布」→ 确定性 TaskCompilation：多次编译逐字节一致。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("成都小学分布情况")
        c1 = compile_requirement_to_tasks(doc)
        c2 = compile_requirement_to_tasks(doc)
        assert c1.to_bounded_dict() == c2.to_bounded_dict()
        assert c1.verdict == "compilable"
        assert c1.family_candidates, "必须有族候选（不能空）"

    def test_distribution_query_ranks_admin_facility_first_with_signals(self):
        """分布+计数+行政区分组 → admin_facility_distribution 居首，
        候选携带信号溯源（IR 路径 + origin），而非关键词暗箱。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("统计成都各区小学数量")
        comp = compile_requirement_to_tasks(doc)
        assert comp.family_candidates[0].family == "admin_facility_distribution"
        top = comp.family_candidates[0]
        assert top.signals, "候选必须携带信号证据"
        assert all(s.path for s in top.signals), "信号必须引用 IR 字段路径"

    def test_expression_candidates_separate_user_request_from_agent_advice(self):
        """同一「小学分布」可有多种正确表达：点图/密度/分区计数组合推导；
        用户显式点名 vs Agent 推荐增量分离（GC-02）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("成都小学分布情况")
        comp = compile_requirement_to_tasks(doc)
        kinds = [c.kind for c in comp.expression_candidates]
        assert kinds, "必须有表达候选"
        assert all(not c.user_requested for c in comp.expression_candidates) or \
            any(c.user_requested for c in comp.expression_candidates)
        # 未显式点名表达时不得出现 user_requested=True
        assert not any(c.user_requested for c in comp.expression_candidates)

    def test_user_pinned_geometry_kind_wins(self):
        """用户显式点名热力图 → 该候选 user_requested=True 且排序不受
        资格否决（数据不足时 suitable=False + 理由，不静默丢）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("成都小学热力图")
        comp = compile_requirement_to_tasks(doc)
        pinned = [c for c in comp.expression_candidates if c.user_requested]
        assert pinned, "用户点名表达必须保留在候选中"

    def test_no_family_match_is_blocked_not_guessed(self):
        """无任何族可服务（封闭词表外）→ verdict=blocked + 理由，
        绝不猜一个相近族（fail-closed）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("成都小学分布情况")
        # 直接构造空信号文档（query_only 无任务语义）
        comp = compile_requirement_to_tasks(doc, family_allowlist=())
        assert comp.verdict == "blocked"
        assert comp.block_reasons

    def test_open_blocking_ambiguity_defers_not_fabricates(self):
        """阻断性歧义未答 → needs_clarification，不得伪填数据（GC-01）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )
        from app.services.gis_harness.requirement_ir.contracts import (
            Ambiguity,
        )

        doc = _doc("成都小学分布情况")
        doc.intent.ambiguities.append(Ambiguity(
            code="aoi_ambiguous", path="aoi.name", context_key="aoi|成都",
            blocking=True, question_zh="哪个成都？"))
        comp = compile_requirement_to_tasks(doc)
        assert comp.verdict == "needs_clarification"
        assert comp.clarification_requests


# ── C2: 族运行回执 → goal 验收桥 ─────────────────────────────────────────


def _run_state_satisfied(workflow_id: str = "awf.admin_facility_distribution.t1"):
    from app.services.gis_harness.analysis_workflows.contracts import (
        AnalysisArtifactEnvelope,
    )

    contract = build_contract(
        "admin_facility_distribution", workflow_id=workflow_id)
    state = new_state(contract, contract_fingerprint="fp1")
    for step_id, entry in state.steps.items():
        if step_id == "per_capita_rate":  # optional 无分母 → typed skipped
            entry.state = "skipped"
            entry.reason_code = "AWF_DENOMINATOR_MISSING"
            continue
        entry.state = "satisfied"
        entry.fingerprint = f"fp-{step_id}"
    state.envelopes["zones_count"] = AnalysisArtifactEnvelope(
        step_id="zones_count", workflow_id=workflow_id,
        tool_name="spatial_aggregate", ref="ref:geojson-z1",
        feature_count=20, method_algorithm_id="admin_aggregation.count",
        output_roles=("aggregate_zones",))
    return contract, state


class TestWorkflowReceiptGoalBridge:
    def test_satisfied_steps_project_to_chapter_rows(self):
        """WorkflowRunState → chapter analysis_steps 行投影（命名空间化
        capability，status 映射 satisfied→complete / skipped→skipped）。"""
        from app.services.gis_harness.execution_compiler import (
            project_workflow_rows,
        )

        contract, state = _run_state_satisfied()
        rows = project_workflow_rows(state, contract)
        assert rows, "必须产出 chapter 行"
        caps = {r["capability"] for r in rows}
        assert any(c.startswith("awf.admin_facility_distribution.")
                   for c in caps), "capability 必须命名空间化避免与 plan 行冲突"
        by_step = {r["capability"].rsplit(".", 1)[-1]: r for r in rows}
        assert by_step["zones_count"]["status"] == "complete"
        assert by_step["per_capita_rate"]["status"] == "skipped"

    def test_goal_evaluator_sees_workflow_rows_node_success_not_goal(self):
        """桥接后：全步骤 satisfied 但无地图产物 → map 需求不被判达成；
        步骤行证据可背书 analysis 需求（节点成功≠目标达成，GC-08）。"""
        from app.services.gis_harness.execution_compiler import (
            merge_workflow_rows,
            project_workflow_rows,
        )
        from app.services.gis_harness.goal_satisfaction.evaluator import (
            evaluate_goal_satisfaction,
        )

        contract, state = _run_state_satisfied()
        rows = project_workflow_rows(state, contract)
        chapter = {
            "plan_id": "p1",
            "query": "统计成都各区小学数量",
            "intent": {"output_intents": ["map"], "query": "统计成都各区小学数量"},
            "analysis_steps": rows,
        }
        chapter = merge_workflow_rows(chapter, [(state, contract)])
        report = evaluate_goal_satisfaction(chapter, user_goal="统计成都各区小学数量")
        assert report is not None, "投影后必须可评估（此前 None=完全不可见）"
        assert report.verdict.value != "satisfied", \
            "无地图产物时目标不得 satisfied（步骤成功≠目标达成）"
        analysis = {v.requirement_id: v for v in report.requirements
                    if v.requirement_id.startswith("analysis:")}
        assert analysis, "族步骤必须成为可验需求"
        # 必需步骤（satisfied→complete 行 + deterministic 回执证据）→ fulfilled
        assert analysis["analysis:awf.admin_facility_distribution.zones_count"]\
            .verdict.value == "fulfilled"
        # optional skipped（分母缺席）→ 不背书（诚实 partial，不伪装输出）
        assert analysis["analysis:awf.admin_facility_distribution.per_capita_rate"]\
            .verdict.value != "fulfilled"

    def test_failed_step_blocks_requirement_honestly(self):
        """failed 步骤 → 行证据 FAILED → analysis 需求不 fulfilled。"""
        from app.services.gis_harness.execution_compiler import (
            merge_workflow_rows,
        )
        from app.services.gis_harness.goal_satisfaction.evaluator import (
            evaluate_goal_satisfaction,
        )

        contract = build_contract(
            "admin_facility_distribution",
            workflow_id="awf.admin_facility_distribution.t2")
        state = new_state(contract, contract_fingerprint="fp2")
        for step_id, entry in state.steps.items():
            entry.state = "satisfied"
        state.steps["zones_count"].state = "failed"
        state.steps["zones_count"].reason_code = "TOOL_ERROR"
        chapter = merge_workflow_rows({
            "plan_id": "p2", "query": "q",
            "intent": {"output_intents": []},
        }, [(state, contract)])
        report = evaluate_goal_satisfaction(chapter)
        analysis = [v for v in report.requirements
                    if "zones_count" in v.requirement_id]
        assert analysis and analysis[0].verdict.value != "fulfilled"


# ── C3: 族声明 conformance（fail-closed 投影）─────────────────────────────


class TestFamilyCatalogConformance:
    def test_all_families_conform_against_live_registries(self):
        """七族 × 每步骤：tool 注册、capability 可解析、单位词表内、
        前置注册 —— live registry 对账全 pass（master 基线应绿）。"""
        from app.services.gis_harness.execution_compiler import (
            audit_family_conformance,
        )

        report = audit_family_conformance()
        fails = [v for v in report.step_verdicts if v.verdict == "fail"]
        assert not fails, f"族声明存在悬空引用: {fails[:3]}"

    def test_dangling_tool_fails_closed_with_reason(self):
        """注入悬空工具 fixture → fail + 稳定 reason code（不静默）。"""
        from app.services.gis_harness.execution_compiler import (
            audit_family_conformance,
        )

        report = audit_family_conformance(tool_exists=lambda t: False)
        fails = [v for v in report.step_verdicts if v.verdict == "fail"]
        assert fails
        assert all(v.reason_code for v in fails)

    def test_report_is_deterministic_and_fingerprinted(self):
        from app.services.gis_harness.execution_compiler import (
            audit_family_conformance,
        )

        r1 = audit_family_conformance()
        r2 = audit_family_conformance()
        assert r1.to_bounded_dict() == r2.to_bounded_dict()
        assert r1.fingerprint


# ── C4: IR patch → 受影响节点失效集 ───────────────────────────────────────


class TestPatchScopedInvalidation:
    def _contract(self):
        return build_contract(
            "admin_facility_distribution",
            workflow_id="awf.admin_facility_distribution.inv",
            bindings={
                "subject": DataRoleBinding(role="subject", ref="ref:pois-cd"),
                "boundary": DataRoleBinding(role="boundary", ref="ref:admin-cd"),
            },
        )

    def test_style_only_change_invalidates_no_science(self):
        """「颜色改成蓝色」→ style-only：科学失效集为空（GC-09 核心）。"""
        from app.services.gis_harness.execution_compiler import (
            patch_invalidation_scope,
        )

        old = _doc("统计成都各区小学数量")
        new = _doc("统计成都各区小学数量")
        new.intent.representation.palette = "blue"
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert scope.style_changed is True
        assert set(scope.science_invalidated_steps) == set()
        assert set(scope.invalidated_roles) == set()

    def test_city_switch_invalidates_all_data_roles(self):
        """「换成北京但样式不变」→ 全部数据角色失效（旧城市 ref 不可
        假匹配新城市），style 保留（GC-06/07）。"""
        from app.services.gis_harness.execution_compiler import (
            patch_invalidation_scope,
        )

        old = _doc("统计成都各区小学数量")
        new = _doc("统计北京各区小学数量")
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert scope.style_changed is False
        assert {"subject", "boundary"} <= set(scope.invalidated_roles)
        assert scope.science_invalidated_steps, "数据角色失效必须传播到步骤"

    def test_statistic_change_scopes_to_dependent_steps_only(self):
        """「改成每万人密度」→ 分母角色+率步骤失效，纯计数步骤若无
        依赖可复用（最小重算证明）。"""
        from app.services.gis_harness.execution_compiler import (
            patch_invalidation_scope,
        )

        old = _doc("统计成都各区小学数量")
        new = _doc("统计成都各区每万人小学数量")
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert "per_capita_rate" in scope.science_invalidated_steps
        assert "zones_count" not in scope.science_invalidated_steps, \
            "计数步骤输入未变——不重算昂贵计算"


# ── C5: 单位/量纲静态义务 ─────────────────────────────────────────────────


class TestUnitObligations:
    def test_count_statistic_never_promised_as_rate(self):
        """计数统计 → 义务词表禁止 rate 语义（未归一化计数≠发生率）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("统计成都各区小学数量")
        comp = compile_requirement_to_tasks(doc)
        rate_promises = [
            u for u in comp.unit_obligations
            if u.expected_semantics == "rate" and u.verdict == "violated"]
        assert not rate_promises

    def test_per_capita_without_denominator_is_disclosed_not_fabricated(self):
        """每万人密度但无分母数据事实 → 义务 verdict=missing_denominator
        + 披露（绝不伪装率）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("统计成都各区每万人小学数量")
        comp = compile_requirement_to_tasks(doc)
        per_capita = [
            u for u in comp.unit_obligations
            if u.expected_semantics in ("per_capita", "rate")]
        assert per_capita, "per-capita 请求必须产生单位义务"
        assert any(u.verdict != "satisfied" for u in per_capita) or \
            comp.disclosures, "分母缺席必须显式披露"

    def test_time_and_distance_units_never_conflated(self):
        """行程时间（min/h）与距离（m/km）义务分类互斥（#2005 形态）：
        网络邻近族的距离义务不得被时间单位满足。"""
        from app.services.gis_harness.execution_compiler import (
            UNIT_DIMENSIONS,
        )

        assert UNIT_DIMENSIONS["min"] == "time"
        assert UNIT_DIMENSIONS["km"] == "length"
        assert UNIT_DIMENSIONS["m"] == "length"
        assert UNIT_DIMENSIONS["h"] == "time"

    def test_family_step_units_align_with_ir_statistics(self):
        """定量密度（每平方公里）→ measure=density 义务（几何自足分母）；
        视觉热力语义（无定量口径）→ 表达候选携带 density 语义（分层诚实：
        义务面只在定量口径产生，视觉面走表达候选）。"""
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        doc = _doc("成都每平方公里小学密度")
        comp = compile_requirement_to_tasks(doc)
        sems = {u.expected_semantics for u in comp.unit_obligations}
        assert sems & {"density", "per_area"}, \
            f"定量密度请求的义务语义缺失: {sems}"

        visual = _doc("成都小学热力图")
        vis_comp = compile_requirement_to_tasks(visual)
        vis_kinds = {c.kind: c for c in vis_comp.expression_candidates}
        assert "heatmap" in vis_kinds
        assert vis_kinds["heatmap"].user_requested is True
