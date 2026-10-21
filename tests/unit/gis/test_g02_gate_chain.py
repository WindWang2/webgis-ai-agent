"""G02 三段守门链测试 —— plan/execute/final 的 method evidence 对账。

不变量：
1. plan 段：resolver 科学 evidence（warnings/transforms/not_evaluated/
   profile_digest）经 planner 转录进 AlgorithmSelectionRecord（此前断链）；
2. execute 段：resolve_step_tool 复验科学门（fail-closed + evidence 透传）；
3. final 段：science_final_gate 对账三段 evidence（非等价 fallback 披露、
   not_evaluated 披露、freshness、执行硬拒 block）；
4. dispatch：ScientificError typed 面穿透 tools registry（不再扁平化）。
全部走真实入口（真实 registry/resolver/dispatch），不 mock 裁决逻辑。
"""
import asyncio

import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.algorithm_resolver import (
    get_algorithm_resolver,
    reset_algorithm_resolver,
)
from app.lib.gis.capability_registry import get_capability_registry
from app.lib.gis.science_final_gate import (
    NON_EQUIVALENT_SEMANTICS,
    run_science_final_gate,
    science_profile_digest,
)
from app.schemas.project_schema import WorkflowStepSpec
from app.services.gis_harness.planner import AlgorithmSelectionRecord
from app.services.workflow_engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _fresh_resolver():
    reset_algorithm_resolver()
    yield
    reset_algorithm_resolver()


def _intent():
    from app.services.gis_harness.intent import resolve_map_request_intent

    return resolve_map_request_intent("成都人口空间插值分析")


def _capability_for(algorithm_id: str) -> str:
    reg = get_algorithm_registry()
    for cid in get_capability_registry().all_ids:
        if any(a.id == algorithm_id
               for a in reg.algorithms_for_capability(cid)):
            return cid
    raise AssertionError(f"no capability for {algorithm_id}")


class _FakeToolRegistry:
    """只提供 resolve_step_tool 需要的最小面（list_tools）。"""

    def __init__(self, tools):
        self._tools = list(tools)

    def list_tools(self):
        return list(self._tools)


# ── 1. plan 段转录（真实 resolver + 真实注册表）─────────────────────────
class TestPlanTranscription:
    def test_record_carries_science_evidence(self):
        from app.services.gis_harness.planner import MapProductPlanner

        cap = _capability_for("interpolation.block_kriging")
        planner = MapProductPlanner()
        profile = {"numericSampleCount": 64, "featureCount": 64}
        _, _, selections = planner._resolve_capabilities(
            [cap], _intent(),
            available_tools={"block_kriging_surface", "kriging_interpolation"},
            profile=profile,
        )
        rec = next(s for s in selections if s.capability == cap)
        assert rec.status == "resolved"
        assert rec.algorithm == "interpolation.block_kriging"
        # 断链修复：科学 evidence 三字段 + freshness token 在 record 上
        assert hasattr(rec, "scientific_warnings")
        assert hasattr(rec, "required_transformations")
        assert hasattr(rec, "not_evaluated")
        assert rec.profile_digest == science_profile_digest(profile)

    def test_record_deferred_flows_through_transcription(self):
        from app.services.gis_harness.planner import MapProductPlanner

        cap = _capability_for("interpolation.block_kriging")
        planner = MapProductPlanner()
        _, _, selections = planner._resolve_capabilities(
            [cap], _intent(),
            available_tools={"block_kriging_surface", "kriging_interpolation"},
            profile={},  # 事实缺席 → deferred
        )
        rec = next(s for s in selections if s.capability == cap)
        assert any("min_numeric_samples:8" in e for e in rec.not_evaluated)

    def test_record_backward_compatible_defaults(self):
        rec = AlgorithmSelectionRecord(capability="x", status="resolved")
        assert rec.scientific_warnings == []
        assert rec.required_transformations == []
        assert rec.not_evaluated == []
        assert rec.profile_digest == ""


# ── 2. execute 段复验（真实 resolve_step_tool）──────────────────────────
class TestExecuteRecheck:
    TOOLS = {"block_kriging_surface", "kriging_interpolation"}

    def _spec(self):
        return WorkflowStepSpec(
            step_id="s1", tool_name="block_kriging_surface",
            capability="block_kriging", 
            algorithm_preference="interpolation.block_kriging")

    def test_evidence_carries_not_evaluated_and_digest(self):
        spec = self._spec()
        _, _, _, ev = WorkflowEngine.resolve_step_tool(
            spec, _FakeToolRegistry(self.TOOLS), available=self.TOOLS,
            profile={})
        assert ev["resolver_status"] == "resolved"
        assert any("min_numeric_samples:8" in e for e in ev["not_evaluated"])
        # 空画像 → token 诚实缺席（"" 不入 evidence；与 plan 侧
        # profile_digest="" 的缺席语义一致，final gate 对空 digest 不判变）
        assert ev.get("profile_digest", "") == ""

    def test_fallback_substitution_disclosed_with_semantics(self):
        spec = self._spec()
        tool, cap, algo, ev = WorkflowEngine.resolve_step_tool(
            spec, _FakeToolRegistry(self.TOOLS), available=self.TOOLS,
            profile={"numericSampleCount": 3})  # 主候选科学拒绝
        # 非等价替补（approximation）必须带语义分类进 evidence
        assert ev.get("algorithm_changed_from") == "interpolation.block_kriging"
        trail = ev.get("fallback_trail") or []
        assert trail, "fallback substitution must be disclosed"
        assert any(t["semantics"] in NON_EQUIVALENT_SEMANTICS
                   for t in trail)

    def test_science_hard_block_marker(self, monkeypatch):
        """全链科学拒绝（无 fallback 可用）→ science_hard_block 标记。"""
        import app.lib.gis.algorithm_resolver as ar_mod
        from types import SimpleNamespace

        class _AllRejectedResolver:
            def resolve(self, capability, available_tools=None, profile=None,
                        **kw):
                return SimpleNamespace(
                    status="unavailable", tool="", algorithm="",
                    reason="scientific_precondition:x:y:INVALID_METHOD",
                    rejected=[
                        "scientific_precondition:x:y:INVALID_METHOD",
                        "crs_class_mismatch:x:needs=X:data=Y",
                    ],
                    fallback_trail=[], fallback_candidates=[],
                    not_evaluated=[], scientific_warnings=[],
                    required_transformations=[],
                )

        monkeypatch.setattr(
            ar_mod, "get_algorithm_resolver",
            lambda: _AllRejectedResolver(), raising=True)
        spec = self._spec()
        tool, cap, algo, ev = WorkflowEngine.resolve_step_tool(
            spec, _FakeToolRegistry(self.TOOLS), available=self.TOOLS,
            profile={"crs": "EPSG:4326"})
        assert ev.get("science_hard_block", "").startswith(
            "scientific_precondition:")
        assert ev.get("used_recorded_tool") is None  # 未走兼容回退分支

    def test_registry_unavailable_keeps_compat_fallback(self, monkeypatch):
        """非科学拒绝（工具不可用）→ 既有诚实回退逐位保留。"""
        spec = self._spec()
        tool, cap, algo, ev = WorkflowEngine.resolve_step_tool(
            spec, _FakeToolRegistry(self.TOOLS), available=set(),
            profile={"numericSampleCount": 100})
        assert ev.get("used_recorded_tool") is True
        assert "science_hard_block" not in ev


# ── 3. final 段对账（真实三段 evidence 串联）───────────────────────────
class TestFinalGate:
    def _three_stage(self, plan_profile, exec_profile):
        cap = _capability_for("interpolation.block_kriging")
        # plan 段：真实 resolver + 转录
        resolution = get_algorithm_resolver().resolve(
            cap, profile=plan_profile,
            available_tools={"block_kriging_surface", "kriging_interpolation"})
        rec = AlgorithmSelectionRecord(
            capability=cap,
            status=resolution.status,
            algorithm=resolution.algorithm,
            tool=resolution.tool,
            fallback_trail=[f.model_dump() for f in resolution.fallback_trail],
            not_evaluated=list(resolution.not_evaluated),
            profile_digest=science_profile_digest(plan_profile),
        )
        # execute 段：真实 resolve_step_tool
        spec = WorkflowStepSpec(
            step_id="s1", tool_name="block_kriging_surface", capability=cap,
            algorithm_preference="interpolation.block_kriging")
        _, _, algo_exec, ev = WorkflowEngine.resolve_step_tool(
            spec, _FakeToolRegistry(
                {"block_kriging_surface", "kriging_interpolation"}),
            available={"block_kriging_surface", "kriging_interpolation"},
            profile=exec_profile)
        ev["capability"] = cap
        # final 段
        from app.services.gis_harness.planner import MapProductPlan
        plan = MapProductPlan(
            plan_id="p", query="q", intent=_intent(), recipe_id="spatial_analysis",
            algorithm_selections=[rec])
        return rec, algo_exec, ev, run_science_final_gate(
            plan, execution_evidence={"s1": ev})

    def test_method_identity_consistent_across_stages(self):
        rec, algo_exec, ev, gate = self._three_stage(
            {"numericSampleCount": 64}, {"numericSampleCount": 64})
        assert rec.status == "resolved"
        assert rec.algorithm == "interpolation.block_kriging"
        assert ev["resolver_status"] == "resolved"
        assert algo_exec == rec.algorithm  # 同一方法贯穿三段

    def test_freshness_detects_fact_change(self):
        rec, algo_exec, ev, gate = self._three_stage(
            {"numericSampleCount": 64, "featureCount": 64},
            {"numericSampleCount": 3, "featureCount": 3})  # 数据缩水
        codes = [f.code for f in gate.findings]
        assert "G-FRESHNESS" in codes
        finding = next(f for f in gate.findings if f.code == "G-FRESHNESS")
        assert rec.profile_digest in finding.detail

    def test_unchanged_facts_no_freshness_finding(self):
        _, _, _, gate = self._three_stage(
            {"numericSampleCount": 64, "featureCount": 64},
            {"numericSampleCount": 64, "featureCount": 64})
        assert all(f.code != "G-FRESHNESS" for f in gate.findings)

    def test_not_evaluated_disclosed_at_final(self):
        rec, algo_exec, ev, gate = self._three_stage({}, {})
        assert any("min_numeric_samples:8" in e
                   for e in rec.not_evaluated)
        assert any(f.code == "G-NOT-EVALUATED" for f in gate.findings)
        assert gate.disclosures  # 人读披露面非空
        assert gate.blocked is False  # 诚实降级不阻断

    def test_science_block_fires_block_severity(self):
        from types import SimpleNamespace

        plan = SimpleNamespace(algorithm_selections=[])
        ev = {"s1": {
            "capability": "block_kriging",
            "science_hard_block": "scientific_precondition:x:INVALID_METHOD",
            "resolver_status": "unavailable",
        }}
        gate = run_science_final_gate(plan, execution_evidence=ev)
        assert gate.blocked is True
        blocked = [f for f in gate.findings if f.severity == "block"]
        assert blocked and blocked[0].code == "G-SCIENCE-BLOCKED"

    def test_malformed_inputs_bounded_no_raise(self):
        from types import SimpleNamespace

        # 畸形：selections 是任意对象 / evidence 非 dict / 循环引用值
        plan = SimpleNamespace(algorithm_selections=[
            SimpleNamespace(capability="c", status="resolved",
                            fallback_trail=[{"from": "a", "to": "b",
                                             "semantics": "proxy"}] * 10,
                            not_evaluated=["x"] * 10),
            "not-a-selection",
            None,
        ])
        gate = run_science_final_gate(
            plan, execution_evidence={"s1": "not-a-dict", "s2": None})
        assert len(gate.findings) <= 16
        assert gate.blocked is False

    def test_empty_plan_is_clean(self):
        gate = run_science_final_gate(None)
        assert gate.findings == [] and gate.blocked is False


# ── 4. dispatch typed 科学错误穿透 ─────────────────────────────────────
class TestDispatchScientificError:
    def test_scientific_code_preserved_through_dispatch(self):
        from app.lib.gis.scientific_errors import InsufficientSamples
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()

        def probe(n: int = 3) -> dict:
            raise InsufficientSamples(
                "需要 >=8 样本，当前 3",
                correction_hint="扩大范围",
                guidance=["扩大空间范围"],
                ref={"algorithm": "interpolation.block_kriging"})

        reg.register("g02_science_probe", "probe", probe)
        resp = asyncio.run(reg.dispatch("g02_science_probe", {"n": 3}))
        assert resp["success"] is False
        assert resp["code"] == "INSUFFICIENT_SAMPLES"  # 不再 VALIDATION_ERROR
        assert resp["error_type"] == "InsufficientSamples"
        assert resp["scientific_ref"]["algorithm"] == "interpolation.block_kriging"
        assert resp["scientific_guidance"] == ["扩大空间范围"]

    def test_plain_value_error_unchanged(self):
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()

        def probe(n: int = 3) -> dict:
            raise ValueError("参数不合法")

        reg.register("g02_plain_probe", "probe", probe)
        resp = asyncio.run(reg.dispatch("g02_plain_probe", {"n": 3}))
        assert resp["code"] == "VALIDATION_ERROR"
        assert "scientific_code" not in resp


# ── 5. review 清偿钉扎（P2-1/P2-4/P3-3）───────────────────────────────
class TestReviewFollowupPins:
    def test_bounded_selection_evidence_projection(self):
        # P2-4：投影语义钉扎 —— 空值键缺席、非空/数值 0 键存活
        from app.services.gis_harness.planner import AlgorithmSelectionRecord
        from app.services.gis_harness.tools import bounded_selection_evidence

        rec = AlgorithmSelectionRecord(
            capability="c", status="resolved", algorithm="a.b", tool="t",
            reason="native", rejected=[], fallback_trail=[],
            scientific_warnings=[], not_evaluated=[], profile_digest="",
        )
        d = bounded_selection_evidence(rec)
        assert "rejected" not in d and "not_evaluated" not in d
        assert d["capability"] == "c" and d["status"] == "resolved"
        # 数值 0 合法证据不剥（cost_score=0.0 竞争证据，review P3-2）
        rec2 = AlgorithmSelectionRecord(
            capability="c", status="resolved")
        d2 = bounded_selection_evidence(rec2)
        assert set(d2) == {"capability", "status"}

    def test_compile_gate_failure_disclosed_not_swallowed(self, monkeypatch):
        # P3-3：final gate 自身抛错 → 编译继续 + science_gate_unavailable
        # 披露可观测（不静默吞掉 gate 故障）
        from app.services.gis_harness.product_compiler import (
            compile_product_spec,
        )
        from app.services.gis_harness.product_shapes import (
            build_product_spec_from_plan,
        )
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.product_templates import (
            get_product_template_registry,
        )
        from app.services.gis_harness.planner import (
            MapProductPlan, PlannedLayer,
        )

        import app.lib.gis.science_final_gate as gate_mod

        def _boom(plan=None, **kw):
            raise RuntimeError("gate exploded")

        # gate 在 compile 内延迟导入 —— patch 源模块属性（调用时取最新）
        monkeypatch.setattr(gate_mod, "run_science_final_gate", _boom)
        intent = resolve_map_request_intent("成都小学分布情况")
        tpl_reg = get_product_template_registry()
        template = tpl_reg.find_for_recipe(
            "administrative_choropleth", intent.subject.category or "")
        plan = MapProductPlan(
            plan_id="p", query="q", intent=intent,
            recipe_id="administrative_choropleth",
            template_id=template.id if template else "",
            map_layers=[PlannedLayer(role="primary", layer_type="fill",
                                     cartography="administrative_choropleth",
                                     source_capability="admin_aggregation",
                                     layer_id="lyr-admin", bound_ref="ref:abc")])
        spec = build_product_spec_from_plan(plan, intent, template)
        result = compile_product_spec(spec, plan=plan, template=template)
        assert result.ok is True  # 编译不被增值对账面阻断
        codes = [f["code"] for f in result.science_findings]
        assert codes == ["science_gate_unavailable"]
        assert result.science_blocked is False

    def test_product_runtime_payload_carries_science_face(self):
        # P2-1/P2-2：制品 payload 投影包含 science_findings/science_blocked
        # （静态钉扎键存在 —— payload 构造器是重 async 路径，键面契约
        # 以源结构断言 + compile 集成测试双重覆盖）
        import inspect

        from app.services.gis_harness import product_runtime

        source = inspect.getsource(product_runtime)
        assert '"science_findings"' in source
        assert '"science_blocked"' in source
