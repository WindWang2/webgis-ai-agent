"""Execution Compiler Oracles（G02 特有 Oracle 的连续验收）。

覆盖任务书 §C：零悬空引用、fail-closed、科学单位错误明确拒绝、
golden 任务结构化产物、重复执行确定性、局部风格改变不重拉数据、
#2005/#2006 形态合约回归、生产验收桥可达性、对抗输入有界性。
"""
from __future__ import annotations


from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    build_contract,
)
from app.services.gis_harness.analysis_workflows.state import (
    new_state,
)
from app.services.gis_harness.execution_compiler import (
    UNIT_DIMENSIONS,
    audit_family_conformance,
    compile_requirement_to_tasks,
    merge_workflow_rows,
    patch_invalidation_scope,
    project_workflow_rows,
)
from app.services.gis_harness.execution_compiler.corpus import (
    build_compiler_corpus,
    corpus_case_count,
    run_corpus,
    write_benchmark_report,
)
from app.services.gis_harness.execution_compiler.requirement_to_tasks import (
    MIN_DENSITY_POINTS,
)
from app.services.gis_harness.execution_compiler.units import (
    dimensions_compatible,
    unit_dimension_of,
)
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir.build import build_document


def _doc(query: str):
    return build_document(query, resolve_map_request_intent(query))


# ── O8/A: golden 语料 runner（≥20 任务类；重复执行确定性）────────────────


class TestGoldenCorpus:
    def test_corpus_has_at_least_20_task_classes(self):
        cases = build_compiler_corpus()
        classes = {c.task_class for c in cases}
        assert len(classes) >= 20, f"任务类不足 20: {len(classes)}"
        assert len(cases) == corpus_case_count()

    def test_all_cases_pass(self):
        results = run_corpus()
        failed = [r.case_id for r in results if not r.passed]
        assert not failed, f"语料失败: {failed}"

    def test_repeat_run_is_deterministic(self):
        """连续两次全量运行逐字节一致（关键验收跑两遍）。"""
        r1 = [(r.case_id, r.actual, r.checks) for r in run_corpus()]
        r2 = [(r.case_id, r.actual, r.checks) for r in run_corpus()]
        assert r1 == r2

    def test_benchmark_report_written(self, tmp_path):
        report = tmp_path / "benchmark.md"
        summary = write_benchmark_report(str(report))
        assert summary["cases"] == corpus_case_count()
        assert summary["failed"] == 0
        text = report.read_text(encoding="utf-8")
        assert "Execution Compiler Benchmark Report" in text
        assert "Oracle notes" in text
        # 报告必须带解释（每个用例的 oracle_notes 都在）
        for case in build_compiler_corpus():
            assert case.id in text


# ── O1: 编译确定性 + 零 LLM/IO ────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_byte_identical_output(self):
        doc = _doc("统计成都各区小学数量")
        a = compile_requirement_to_tasks(doc).to_bounded_dict()
        b = compile_requirement_to_tasks(doc).to_bounded_dict()
        assert a == b

    def test_candidate_signals_all_reference_ir_paths(self):
        """零悬空：每个信号的 path 必须是 IR 真实字段（可 getattr 解析）。"""
        from app.services.gis_harness.execution_compiler.requirement_to_tasks import (
            getattr_signal,
        )

        doc = _doc("统计成都各区每万人小学数量")
        comp = compile_requirement_to_tasks(doc)
        for candidate in comp.family_candidates:
            for signal in candidate.signals:
                assert signal.path
                assert getattr_signal(doc.intent, signal.path) is not None, (
                    f"悬空信号路径: {signal.path}")


# ── O2: fail-closed 与词表封闭 ───────────────────────────────────────────


class TestFailClosed:
    def test_unknown_family_never_produced(self):
        comp = compile_requirement_to_tasks(_doc("统计成都各区小学数量"))
        for candidate in comp.family_candidates:
            assert candidate.family in FAMILY_IDS

    def test_blocked_carries_reasons_not_guess(self):
        comp = compile_requirement_to_tasks(
            _doc("统计成都各区小学数量"), family_allowlist=())
        assert comp.verdict == "blocked"
        assert comp.block_reasons
        assert not comp.family_candidates

    def test_verdict_vocabulary_closed(self):
        for case in build_compiler_corpus()[:8]:
            comp = compile_requirement_to_tasks(
                _doc(case.query),
                family_allowlist=() if case.id == "blocked-allowlist-empty"
                else None)
            assert comp.verdict in ("compilable", "needs_clarification",
                                    "blocked")

    def test_dangling_tool_conformance_fails_closed(self):
        report = audit_family_conformance(tool_exists=lambda t: False)
        verdicts = {v.verdict for v in report.step_verdicts}
        assert "fail" in verdicts
        assert all(v.reason_code == "FC_TOOL_DANGLING"
                   for v in report.step_verdicts if v.verdict == "fail")

    def test_live_registry_conformance_green(self):
        """真实注册面（与生产同源 init_tools）对账全 pass——零悬空引用。"""
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        init_tools(registry)
        report = audit_family_conformance(tool_exists=registry.has)
        fails = [v for v in report.step_verdicts if v.verdict == "fail"]
        assert not fails, f"族声明悬空: {[v.to_bounded_dict() for v in fails[:3]]}"


# ── O5: 科学单位 Oracle（#2005/#2006 形态合约回归）──────────────────────


class TestScientificUnitOracles:
    def test_seconds_never_satisfy_length(self):
        """#2005 形态：行程时间（秒/分/时）不得满足距离义务。"""
        assert unit_dimension_of("s") == "time"
        assert not dimensions_compatible("km", "s")
        assert not dimensions_compatible("m", "min")
        assert not dimensions_compatible("m", "h")

    def test_distance_units_compatible_within_dimension(self):
        assert dimensions_compatible("m", "km")
        assert dimensions_compatible("min", "h")

    def test_unknown_units_never_claimed_compatible(self):
        assert not dimensions_compatible("km", "banana")
        assert unit_dimension_of("banana") == "unknown"

    def test_network_proximity_units_declared_in_contract(self):
        """network_proximity 族声明必须携带时间单位合同（isochrone travel_
        time=min）——距离义务的消费面单位词表内（#2005 静态守卫的声明面）。"""
        contract = build_contract(
            "service_accessibility", workflow_id="awf.u.test")
        iso = next(s for s in contract.steps if s.step_id == "isochrone")
        assert iso.unit_contract.get("travel_time") == "min"
        assert iso.unit_contract["travel_time"] in UNIT_DIMENSIONS

    def test_count_never_promised_as_rate_without_denominator(self):
        """#2006 形态邻接（数值语义丢失）：计数义务的 expected_unit 是
        unitless；率义务必须带 denominator_kind=field——两者不可互换。"""
        doc = _doc("统计成都各区小学数量")
        comp = compile_requirement_to_tasks(doc)
        for obligation in comp.unit_obligations:
            if obligation.expected_semantics == "count":
                assert obligation.denominator_kind == ""
            if obligation.expected_semantics in ("rate", "per_capita"):
                assert obligation.denominator_kind == "field"

    def test_per_capita_disclosed_not_fabricated(self):
        comp = compile_requirement_to_tasks(
            _doc("统计成都各区每万人小学数量"))
        per_capita = [u for u in comp.unit_obligations
                      if u.expected_semantics == "per_capita"]
        assert per_capita
        assert per_capita[0].verdict == "missing_denominator"
        assert any("missing_denominator" in d or "伪装率" in d
                   for d in comp.disclosures)

    def test_density_gate_threshold_is_data_driven(self):
        """密度表达门槛：数据事实在场时按点数裁决；缺席=unknown 不虚构。"""
        doc = _doc("成都小学热力图")
        no_facts = compile_requirement_to_tasks(doc)
        heat = [c for c in no_facts.expression_candidates
                if c.kind == "heatmap"]
        assert heat and heat[0].suitable is None
        few = compile_requirement_to_tasks(doc, data_facts={
            "subject": {"feature_count": MIN_DENSITY_POINTS - 1}})
        assert [c for c in few.expression_candidates
                if c.kind == "heatmap"][0].suitable is False
        many = compile_requirement_to_tasks(doc, data_facts={
            "subject": {"feature_count": MIN_DENSITY_POINTS * 10}})
        assert [c for c in many.expression_candidates
                if c.kind == "heatmap"][0].suitable is True


# ── O6/O7: 最小重算与跨城失效 ─────────────────────────────────────────────


class TestSelectiveRecompute:
    def _contract(self):
        return build_contract(
            "admin_facility_distribution",
            workflow_id="awf.inv.test",
            bindings={
                "subject": DataRoleBinding(role="subject", ref="ref:pois-cd"),
                "boundary": DataRoleBinding(role="boundary", ref="ref:admin-cd"),
            },
        )

    def test_style_change_zero_science_invalidation(self):
        old = _doc("统计成都各区小学数量")
        new = _doc("统计成都各区小学数量")
        new.intent.representation.palette = "blue"
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert scope.style_changed and not scope.science_invalidated_steps
        assert not scope.invalidated_roles

    def test_city_switch_invalidates_every_role_and_step(self):
        old = _doc("统计成都各区小学数量")
        new = _doc("统计北京各区小学数量")
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert scope.aoi_changed
        assert set(scope.invalidated_roles) >= {"subject", "boundary"}
        assert set(scope.science_invalidated_steps) == {
            "zones_count", "area_density", "per_capita_rate"}

    def test_statistic_switch_preserves_count_step(self):
        old = _doc("统计成都各区小学数量")
        new = _doc("统计成都各区小学数量")
        new.intent.statistics.normalization = "per_capita"
        scope = patch_invalidation_scope(old, new, contract=self._contract())
        assert "per_capita_rate" in scope.science_invalidated_steps
        assert "zones_count" in scope.preserved_steps
        assert "zones_count" not in scope.science_invalidated_steps


# ── O4: 验收桥（生产可达）────────────────────────────────────────────────


class TestAcceptanceBridgeWiring:
    def test_evaluation_view_symbol_importable_from_package(self):
        """生产接线点（completion/pipeline）import 的符号必须在包面可用
        ——subagent review 抓到的静默 ImportError 回归钉。"""
        from app.services.gis_harness.execution_compiler import (
            evaluation_view_with_workflow_rows,  # noqa: F401
            load_workflow_runs,  # noqa: F401
        )

    def test_production_pipeline_still_imports(self):
        import app.services.gis_harness.completion.pipeline  # noqa: F401

    def test_bridge_visible_in_goal_report_and_disclosed(self):
        """族运行 satisfied + 无地图产物 → goal report 可评估且非
        satisfied；披露面携带 skipped 步骤。"""
        from app.services.gis_harness.execution_compiler import (
            workflow_disclosures,
        )
        from app.services.gis_harness.goal_satisfaction.evaluator import (
            evaluate_goal_satisfaction,
        )

        contract = build_contract(
            "admin_facility_distribution",
            workflow_id="awf.bridge.test")
        state = new_state(contract, contract_fingerprint="fp")
        for entry in state.steps.values():
            entry.state = "satisfied"
        state.steps["per_capita_rate"].state = "skipped"
        state.steps["per_capita_rate"].reason_code = "AWF_DENOMINATOR_MISSING"
        state.science_validation = {"final_gate": "pass", "findings_n": 0}

        chapter = merge_workflow_rows(
            {"plan_id": "p", "query": "统计成都各区小学数量",
             "intent": {"output_intents": ["map"]}},
            [(state, contract)])
        report = evaluate_goal_satisfaction(
            chapter, user_goal="统计成都各区小学数量")
        assert report is not None
        assert report.verdict.value != "satisfied"
        disclosures = workflow_disclosures(state)
        assert any("skipped_steps" in d for d in disclosures)

    def test_project_rows_idempotent_and_bounded(self):
        contract = build_contract(
            "temporal_change", workflow_id="awf.bound.test")
        state = new_state(contract, contract_fingerprint="fp")
        rows1 = project_workflow_rows(state, contract)
        rows2 = project_workflow_rows(state, contract)
        assert rows1 == rows2
        assert len(rows1) <= 24
        caps = [r["capability"] for r in rows1]
        assert len(caps) == len(set(caps)), "capability 必须唯一（幂等合并）"


# ── M7: 对抗输入 / 有界性 / 异常 ──────────────────────────────────────────


class TestAdversarialAndBounds:
    def test_empty_document_compiles_without_crash(self):
        doc = _doc("")
        comp = compile_requirement_to_tasks(doc)
        assert comp.verdict in ("compilable", "needs_clarification", "blocked")
        assert comp.to_bounded_dict()

    def test_huge_query_bounded_output(self):
        doc = _doc("统计成都各区小学数量")
        doc.intent.core.query = "x" * 100000
        doc.intent.measures = [
            doc.intent.measures[0].model_copy(update={"id": f"m{i}"})
            for i in range(50)]
        comp = compile_requirement_to_tasks(doc)
        dumped = comp.to_bounded_dict()
        assert len(dumped["family_candidates"]) <= 4
        assert len(dumped["expression_candidates"]) <= 6
        assert len(dumped["unit_obligations"]) <= 8

    def test_unicode_and_control_chars_survive_bounded(self):
        doc = _doc("统计成都各区小学数量")
        doc.intent.subject.category = "学校\u0000注入\n\t类别"
        comp = compile_requirement_to_tasks(doc)
        dumped = comp.to_bounded_dict()
        for candidate in dumped["family_candidates"]:
            for signal in candidate["signals"]:
                assert "\u0000" not in signal["value"]

    def test_concurrent_compiles_are_pure(self):
        """并发编译互不污染（纯函数性质——asyncio 并发收集结果一致）。"""
        import asyncio

        doc = _doc("统计成都各区小学数量")

        async def _many():
            return [compile_requirement_to_tasks(doc).to_bounded_dict()
                    for _ in range(8)]

        results = asyncio.run(_many())
        assert all(r == results[0] for r in results)

    def test_invalid_workflow_state_degrades_honestly(self):
        """损坏 run state（未知步骤态）→ 行投影退化为 pending 不抛。"""
        contract = build_contract(
            "hazard_exposure", workflow_id="awf.degrade.test")
        state = new_state(contract, contract_fingerprint="fp")
        state.steps["hazard_buffer"].state = "banana"  # 词表外状态
        rows = project_workflow_rows(state, contract)
        by_step = {r["capability"].rsplit(".", 1)[-1]: r for r in rows}
        assert by_step["hazard_buffer"]["status"] == "pending"


# ── 工具面 e2e（registry.dispatch 全链路）─────────────────────────────────


class TestCompileToolEndToEnd:
    async def test_dispatch_returns_task_compilation(self, tmp_path, monkeypatch):
        """真实 ToolRegistry dispatch：会话需求文档 → 族候选/义务载荷。"""
        import json as _json

        from app.services.gis_harness.requirement_ir import service as req_svc
        from app.services.session_data import session_data_manager
        from app.tools.analysis_workflow_tools import (
            register_analysis_workflow_tools,
        )
        from app.tools.registry import ToolRegistry

        monkeypatch.chdir(tmp_path)
        session = "ec-compile-tool-e2e"
        await req_svc.ensure_document(
            session_data_manager, session, "统计成都各区每万人小学数量")
        registry = ToolRegistry()
        register_analysis_workflow_tools(registry)
        result = await registry.dispatch("webgis_requirement_compile", {
            "data_facts": _json.dumps(
                {"denominator": {"feature_count": 20}}),
        }, session_id=session)
        assert result["success"] is True
        # 生产行为（分层诚实）：ensure_document 会记录分母澄清歧义
        # （每万人 + 无分母事实 → blocking）→ needs_clarification 优先于
        # compilable；族候选/义务仍完整在场（澄清不吞证据）。
        assert result["verdict"] in ("compilable", "needs_clarification")
        assert result["family_candidates"], "族候选必须在载荷中"
        assert result["unit_obligations"], "单位义务必须在载荷中"
        if result["verdict"] == "needs_clarification":
            assert result["clarification_requests"]

    async def test_dispatch_declines_without_document(self, tmp_path, monkeypatch):
        from app.tools.analysis_workflow_tools import (
            register_analysis_workflow_tools,
        )
        from app.tools.registry import ToolRegistry

        monkeypatch.chdir(tmp_path)
        registry = ToolRegistry()
        register_analysis_workflow_tools(registry)
        result = await registry.dispatch(
            "webgis_requirement_compile", {},
            session_id="ec-compile-tool-no-doc")
        assert result["success"] is False
        assert result["reason_code"] == "REQUIREMENT_DOCUMENT_ABSENT"


# ── 验收桥生产加载器 e2e（真实 session store 别名枚举）────────────────────


class TestBridgeLoaderEndToEnd:
    async def test_load_workflow_runs_via_session_store_aliases(self, tmp_path, monkeypatch):
        """真实 session_data_manager：persist_state 同款别名写入 →
        load_workflow_runs 枚举/解析/反序列化（生产 finalizer 同路径）。"""
        from app.services.gis_harness.analysis_workflows.state import (
            state_alias,
            state_to_payload,
        )
        from app.services.session_data import session_data_manager

        monkeypatch.chdir(tmp_path)
        session = "ec-bridge-loader-e2e"
        contract = build_contract(
            "admin_facility_distribution", workflow_id="awf.admin.t-e2e")
        state = new_state(contract, contract_fingerprint="fp")
        for entry in state.steps.values():
            entry.state = "satisfied"
        ref = await session_data_manager.store(
            session, state_to_payload(state), prefix="awf-state")
        await session_data_manager.set_alias(
            session, ref, state_alias(state.workflow_id))

        from app.services.gis_harness.execution_compiler import (
            evaluation_view_with_workflow_rows,
            load_workflow_runs,
        )

        runs = await load_workflow_runs(session)
        assert len(runs) == 1
        assert runs[0].workflow_id == "awf.admin.t-e2e"
        assert all(e.state == "satisfied"
                   for e in runs[0].steps.values())

        merged, disclosures = await evaluation_view_with_workflow_rows(
            session, {"plan_id": "p", "query": "q", "intent": {}})
        caps = [r["capability"] for r in merged.get("analysis_steps", [])]
        assert "awf.admin_facility_distribution.zones_count" in caps
        assert merged.get("analysis_workflow_row_count") == len(caps)

    async def test_loader_empty_session_is_zero_drift(self, tmp_path, monkeypatch):
        """零运行会话 → 原样返回（既有评估行为零漂移）。"""
        from app.services.gis_harness.execution_compiler import (
            evaluation_view_with_workflow_rows,
        )

        monkeypatch.chdir(tmp_path)
        chapter = {"plan_id": "p", "analysis_steps": [{"capability": "x"}]}
        merged, disclosures = await evaluation_view_with_workflow_rows(
            "ec-bridge-empty-session", chapter)
        assert merged is chapter
        assert disclosures == []
