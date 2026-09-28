"""H10：对抗面测试 —— S3 review 修复的回归锁定（P0/P1/P2）。

每条用例对应一个已修复的 review finding：

- P0-1  真实模块注册 + registry 调用（防「注册即抛 → ToolInit 吞掉 →
        工具不存在」再次漏网）；
- P1-1  k 越界（999999）→ DERIVE_SKIPPED，秒级阻塞/OOM 通道关闭；
- P1-2  unicode/转义 params 字节口径 → 指纹占位，ValidationError 不逃逸；
- P2-1  大 payload 投影成本受控（action_id 不再 digest 全量 args）；
- P2-2  int/float 参数变化 diff 必须报告（与指纹同口径）；
- P2-3  executor 视图异常 → typed receipt（不向 run() 逃逸）；
- P2-4  adapter→compiler→executor 全链补偿（remove_layer 真可合成）；
- P2-5  unknown-dep 的下游报 DEPENDENCY_UNKNOWN（不误报 cycle）。
"""
from __future__ import annotations

import time

import pytest

from app.lib.gis.action_ir import (
    Compensation,
    GISAction,
    GISActionPlan,
)
from app.services.gis_action.compiler import (
    ToolResolution,
    compile_actions,
)
from app.services.gis_action.derive import (
    DERIVE_SKIPPED,
    materialize_classification,
)
from app.services.gis_action.diff import diff_plans
from app.services.gis_action.executor import ActionPlanExecutor
from app.services.gis_action.legacy_adapter import (
    project_tool_call_to_action,
    project_tool_call_to_plan,
)
from app.services.gis_action.service import RegistryToolResolver


_FACTS = {
    "t": ToolResolution(tool="t", side_effect="deterministic_compute"),
    "t_upsert": ToolResolution(tool="t_upsert", side_effect="state_mutation"),
    "webgis_layer_upsert": ToolResolution(tool="webgis_layer_upsert",
                                          side_effect="state_mutation"),
}


class _Resolver:
    def resolve_tool(self, name):
        return _FACTS.get(name)

    def capability_candidates(self, cap):
        return ()


class TestP0RealRegistration:
    def test_tool_module_registers_cleanly(self):
        """真实 registry 上注册不抛（P0-1：非法档位曾致注册即败 +
        ToolInit 吞掉 = 工具 DOA）。"""
        from app.tools.gis_action_tools import register_gis_action_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_gis_action_tools(reg)
        assert reg.resolve_name("webgis_action_plan") is not None
        meta = reg.metadata("webgis_action_plan")
        assert meta.get("side_effect") == "pure"
        assert meta.get("deterministic") is True

    async def test_dry_run_operation_via_registry_dispatch(self):
        """经 registry.dispatch 全链调用 dry_run（真实工具面零执行）。"""
        from app.tools.gis_action_tools import register_gis_action_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_gis_action_tools(reg)
        result = await reg.dispatch("webgis_action_plan", {
            "operation": "dry_run",
            "actions": [
                {"kind": "inspect", "tool": "webgis_action_plan",
                 "params": {"p": 1}},
            ],
        })
        assert result["success"] is True
        assert result["status"] == "compiled"
        assert result["steps"][0]["tool"] == "webgis_action_plan"

    async def test_usage_operation_via_registry_dispatch(self):
        from app.tools.gis_action_tools import register_gis_action_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_gis_action_tools(reg)
        result = await reg.dispatch("webgis_action_plan", {"operation": "usage"})
        assert result["success"] is True
        assert "direct_routed" in result["usage"]

    async def test_invalid_action_typed_failure(self):
        from app.tools.gis_action_tools import register_gis_action_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_gis_action_tools(reg)
        result = await reg.dispatch("webgis_action_plan", {
            "operation": "dry_run",
            "actions": [{"kind": "teleport", "tool": "x"}],
        })
        assert result["success"] is False
        assert result["code"] == "INVALID_ACTION"


class TestP1KClamp:
    async def test_oversized_k_skipped_not_computed(self):
        """k=999999 → DERIVE_SKIPPED（修复前：numpy 3 秒阻塞 + 百万 breaks）。"""
        ir = project_tool_call_to_plan  # noqa: F841 — 语义锚（防误删 import）
        from app.lib.cartography.plan_ir import LayerBlueprint, LayerIntent, MapPlanIR

        bp = LayerBlueprint(
            layer_type="fill",
            classification={"k": 999999, "method": "quantiles",
                            "palette": "YlOrRd", "field": "pop"},
        )
        ir = MapPlanIR(ir_id="mpir-k", layer_intents=[
            LayerIntent(intent_id="li-1", action="present_primary",
                        layer_id="L1", source_ref="ref:d1", blueprint=bp)])

        async def loader(ref):
            return {"type": "FeatureCollection", "features": [
                {"properties": {"pop": float(i)}} for i in range(50)]}

        t0 = time.perf_counter()
        updated, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        elapsed = time.perf_counter() - t0
        assert [r.code for r in records] == [DERIVE_SKIPPED]
        assert elapsed < 0.5, f"k clamp failed: {elapsed:.2f}s"
        # IR 原样（token 形态保留，向后兼容）。
        assert updated.layer_intents[0].blueprint.legend_spec == {}

    def test_k_below_minimum_skipped(self):
        from app.services.gis_action.derive import classification_params_of
        assert classification_params_of({"k": 1}).k is None
        assert classification_params_of({"k": 33}).k is None
        assert classification_params_of({"k": 7}).k == 7


class TestP1ParamsByteBudget:
    def test_escape_heavy_values_fall_back_to_digest(self):
        """unicode/转义使 canonical 字节 >> str 长度：占位而非崩溃。"""
        args = {"a": "\n" * 3000, "b": '"' * 3000}
        action = project_tool_call_to_action(
            "query_dataset", args, {"side_effect": "pure"})
        assert "a" not in action.params and "b" not in action.params
        assert action.params["a__sha"] and action.params["b__sha"]

    def test_projection_never_raises_validation_error(self):
        """任意畸形 args（深层嵌套/混合类型）投影不抛（bind fail-open
        的前提是 adapter 自身不成为异常源）。"""
        nasty = {
            "deep": {"a": [{"b": [{"c": list(range(50))}]}]},
            "mixed": {"x": 1, "y": [1, "2", None]},
            "big_str": "x" * 5000,
        }
        action = project_tool_call_to_action(
            "query_dataset", nasty, {"side_effect": "pure"})
        assert action.action_id.startswith("act-")


def _timed_projection(args):
    t0 = time.perf_counter()
    a = project_tool_call_to_action(
        "webgis_layer_upsert", args, {"side_effect": "state_mutation"})
    action_params_cache.append(a.params)
    return time.perf_counter() - t0


action_params_cache: list = []


class TestP2LargePayloadCost:
    def test_big_payload_projection_is_fast(self):
        """P2-1：action_id 只 digest 投影 token，2MB payload 不应秒级。"""
        big_fc = {"type": "FeatureCollection", "features": [
            {"geometry": {"type": "Point",
                          "coordinates": [116.0 + i * 0.001, 39.9]},
             "properties": {"v": i}} for i in range(20000)]}
        args = {"layer": {"id": "L1", "source": "ref:abc"},
                "source_data": big_fc}
        # 预热（pydantic 首次构型有一次性成本，与被测语义无关）。
        project_tool_call_to_action("webgis_layer_upsert", args,
                                    {"side_effect": "state_mutation"})
        best = min(_timed_projection(args) for _ in range(3))
        # O(payload) canonical digest ≈ 400ms+；O(1) 采样指纹应远低于此。
        assert best < 0.05, f"projection O(payload): {best*1000:.0f}ms"
        assert "source_data" not in action_params_cache[0]


class TestP2DiffIntFloat:
    def test_int_vs_float_param_change_reported(self):
        """`5 == 5.0` 为真但指纹不同：diff 必须报告（与指纹同口径）。"""
        p1 = GISActionPlan(plan_id="gap-1", actions=[
            GISAction(action_id="act-a", kind="cartograph", tool="t",
                      params={"k": 5})])
        p2 = GISActionPlan(plan_id="gap-2", actions=[
            GISAction(action_id="act-a", kind="cartograph", tool="t",
                      params={"k": 5.0})])
        d = diff_plans(p1, p2)
        assert not d.empty
        assert [c.key for c in d.param_changed["act-a"]] == ["k"]


class TestP2ExecutorViewErrors:
    async def test_state_view_crash_typed_not_raised(self):
        from app.lib.gis.action_ir import Precondition

        async def bad_view():
            raise RuntimeError("store down")

        exe = ActionPlanExecutor(registry=_FakeReg(), state_view=bad_view)
        receipt = await exe.run(_compile_plan(GISAction(
            action_id="act-1", kind="mutate_presentation", tool="t_upsert",
            side_effect="session_state",
            preconditions=[Precondition(kind="layer_present", target="L1")])))
        assert receipt.step_receipts[0].status == "precondition_failed"
        assert receipt.step_receipts[0].code == "VIEW_ERROR"

    async def test_args_provider_crash_typed_not_raised(self):
        def bad_provider(step):
            raise RuntimeError("bad provider")

        exe = ActionPlanExecutor(registry=_FakeReg(),
                                 args_provider=bad_provider)
        receipt = await exe.run(_compile_plan(GISAction(
            action_id="act-1", kind="analyze", tool="t")))
        assert receipt.step_receipts[0].status == "failed"
        assert receipt.step_receipts[0].code == "ARGS_ERROR"

    async def test_malformed_revision_value_tolerated(self):
        async def weird_view():
            return {"_cartographic_mutation_revision": "not-a-number"}

        exe = ActionPlanExecutor(registry=_FakeReg(), state_view=weird_view)
        from app.lib.gis.action_ir import Precondition
        receipt = await exe.run(_compile_plan(GISAction(
            action_id="act-1", kind="analyze", tool="t",
            preconditions=[Precondition(kind="revision_match",
                                        target="m", expected="3")])))
        assert receipt.step_receipts[0].code == "REVISION_MISMATCH"


class TestP2FullChainCompensation:
    async def test_adapter_compensation_synthesizes_end_to_end(self):
        """adapter 声明的 remove_layer 补偿经 compiler→executor 真可合成。"""
        action = project_tool_call_to_action(
            "webgis_layer_upsert", {"layer": {"id": "L1"}},
            {"side_effect": "state_mutation"})
        action = action.model_copy(update={"failure": "compensate"})
        plan = GISActionPlan(plan_id="gap-c", actions=[
            action,
            GISAction(action_id="act-2", kind="analyze", tool="t",
                      side_effect="pure", failure="compensate"),
        ])
        comp = compile_actions(plan, resolver=_Resolver())
        assert comp.status == "compiled"

        class _ChainReg:
            def __init__(self):
                self.calls = []

            async def dispatch(self, tool, args, session_id=""):
                self.calls.append(tool)
                if tool == "t":
                    return {"success": False}
                return {"success": True}

        reg = _ChainReg()
        receipt = await ActionPlanExecutor(
            registry=reg,
            compensation_args_provider=lambda s, c: {"layer_id": c.target},
        ).run(comp)
        assert receipt.status == "aborted"
        assert receipt.compensation_receipts[0].status == "compensated"
        assert reg.calls[-1] == "webgis_layer_remove"


class TestP2UnknownDepDownstream:
    def test_downstream_of_unknown_gets_unknown_not_cycle(self):
        plan = GISActionPlan(plan_id="gap-u", actions=[
            GISAction(action_id="act-a", kind="analyze", tool="t",
                      depends_on=["act-ghost"]),
            GISAction(action_id="act-b", kind="analyze", tool="t",
                      depends_on=["act-a"]),
        ])
        comp = compile_actions(plan, resolver=_Resolver())
        codes = [f.code for f in comp.findings]
        assert codes.count("DEPENDENCY_UNKNOWN") == 2
        assert "DEPENDENCY_CYCLE" not in codes


class _FakeReg:
    async def dispatch(self, tool, args, session_id=""):
        return {"success": True}


def _compile_plan(*actions: GISAction):
    plan = GISActionPlan(plan_id="gap-x", actions=list(actions))
    return compile_actions(plan, resolver=_Resolver())
