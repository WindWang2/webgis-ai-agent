"""H10：ActionPlan 执行器测试（dry-run / 失败策略 / 补偿 / precondition）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


from app.lib.gis.action_ir import (
    Compensation,
    GISAction,
    GISActionPlan,
    Precondition,
)
from app.services.gis_action.compiler import (
    ToolResolution,
    compile_actions,
)
from app.services.gis_action.executor import ActionPlanExecutor


class FakeRegistry:
    """registry.dispatch 替身：脚本化结果 + 调用记录。"""

    def __init__(self, script: Optional[Dict[str, Any]] = None):
        self.calls: List[Tuple[str, dict]] = []
        self.script = script or {}

    async def dispatch(self, tool_name: str, args: dict,
                       session_id: str = "") -> dict:
        self.calls.append((tool_name, dict(args)))
        outcome = self.script.get(tool_name)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return {"success": True, "tool": tool_name}
        return outcome


_RESOLVER_FACTS = {
    "t_upsert": dict(tool="t_upsert", side_effect="state_mutation"),
    "t_analyze": dict(tool="t_analyze", side_effect="deterministic_compute"),
    "t_export": dict(tool="t_export", side_effect="external_side_effect"),
}


def _compile(*actions: GISAction) -> Any:
    plan = GISActionPlan(plan_id="gap-x", actions=list(actions))
    return compile_actions(plan, resolver=_FakeResolver())


class _FakeResolver:
    def resolve_tool(self, name):
        fact = _RESOLVER_FACTS.get(name)
        return ToolResolution(**fact) if fact else None

    def capability_candidates(self, cap):
        return ()


def _mutation_step(action_id="act-1", failure="fail_closed",
                   compensation=None, preconditions=None) -> GISAction:
    return GISAction(
        action_id=action_id, kind="mutate_presentation", tool="t_upsert",
        side_effect="session_state", failure=failure,
        compensation=compensation or Compensation(),
        preconditions=preconditions or [],
        params={"layer_id": "L1"},
    )


class TestDryRun:
    async def test_dry_run_runs_nothing(self):
        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg)
        receipt = await exe.run(_compile(_mutation_step()), dry_run=True)
        assert reg.calls == []
        assert receipt.status == "ran"
        assert receipt.step_receipts[0].status == "would_run"

    async def test_blocked_plan_never_runs(self):
        reg = FakeRegistry()
        plan = GISActionPlan(plan_id="gap-x", actions=[
            GISAction(action_id="act-1", kind="analyze", tool="ghost")])
        comp = compile_actions(plan, resolver=_FakeResolver())
        receipt = await ActionPlanExecutor(registry=reg).run(comp)
        assert reg.calls == []
        assert receipt.status == "blocked"
        assert "TOOL_UNRESOLVED" in receipt.reason_codes


class TestExecution:
    async def test_sequential_execution_in_order(self):
        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg)
        receipt = await exe.run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze"),
            _mutation_step(action_id="act-2"),
        ))
        assert receipt.status == "ran"
        assert [c[0] for c in reg.calls] == ["t_analyze", "t_upsert"]
        assert all(r.status == "applied" for r in receipt.step_receipts)

    async def test_args_provider_controls_payload(self):
        reg = FakeRegistry()
        exe = ActionPlanExecutor(
            registry=reg,
            args_provider=lambda step: {"layer": {"id": "L9"}})
        await exe.run(_compile(_mutation_step()))
        assert reg.calls[0][1] == {"layer": {"id": "L9"}}

    async def test_tool_failure_fail_closed_aborts_rest(self):
        reg = FakeRegistry(script={"t_analyze": {"success": False,
                                                 "code": "BOOM"}})
        receipt = await ActionPlanExecutor(registry=reg).run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze"),
            _mutation_step(action_id="act-2"),
        ))
        assert receipt.status == "aborted"
        statuses = {r.action_id: r.status for r in receipt.step_receipts}
        assert statuses["act-1"] == "failed"
        assert statuses["act-2"] == "skipped"
        assert receipt.compensation_receipts == []  # fail_closed 不回滚

    async def test_best_effort_continues_after_failure(self):
        reg = FakeRegistry(script={"t_analyze": {"success": False}})
        receipt = await ActionPlanExecutor(registry=reg).run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze",
                      failure="best_effort"),
            _mutation_step(action_id="act-2"),
        ))
        assert receipt.status == "completed_with_failures"
        assert len(reg.calls) == 2
        assert receipt.step_receipts[1].status == "applied"

    async def test_compensate_strategy_rolls_back_executed_steps(self):
        reg = FakeRegistry(script={"t_analyze": {"success": False}})
        exe = ActionPlanExecutor(
            registry=reg,
            compensation_args_provider=lambda step, comp: {
                "layer_id": comp.target})
        receipt = await exe.run(_compile(
            _mutation_step(action_id="act-1",
                           failure="compensate",
                           compensation=Compensation(kind="remove_layer",
                                                     target="L1")),
            GISAction(action_id="act-2", kind="analyze", tool="t_analyze",
                      failure="compensate"),
        ))
        assert receipt.status == "aborted"
        # act-2 失败 → 逆序补偿 act-1（remove_layer）。
        assert reg.calls[-1][0] == "webgis_layer_remove"
        assert reg.calls[-1][1] == {"layer_id": "L1"}
        assert receipt.compensation_receipts[0].status == "compensated"

    async def test_unsynthesizable_compensation_reported_honestly(self):
        reg = FakeRegistry(script={"t_analyze": {"success": False}})
        exe = ActionPlanExecutor(registry=reg)
        receipt = await exe.run(_compile(
            _mutation_step(action_id="act-1",
                           failure="compensate",
                           compensation=Compensation()),  # 未声明补偿
            GISAction(action_id="act-2", kind="analyze", tool="t_analyze",
                      failure="compensate"),
        ))
        assert receipt.status == "aborted"
        assert receipt.compensation_receipts[0].status == "uncompensated"
        assert receipt.compensation_receipts[0].code == "NO_COMPENSATION_DECLARED"

    async def test_compensation_failure_recorded_not_raised(self):
        reg = FakeRegistry(script={"t_analyze": {"success": False},
                                   "webgis_layer_remove": {"success": False}})
        exe = ActionPlanExecutor(registry=reg)
        receipt = await exe.run(_compile(
            _mutation_step(action_id="act-1", failure="compensate",
                           compensation=Compensation(kind="remove_layer",
                                                     target="L1")),
            GISAction(action_id="act-2", kind="analyze", tool="t_analyze",
                      failure="compensate"),
        ))
        assert receipt.status == "aborted"
        assert receipt.compensation_receipts[0].status == "uncompensated"
        assert receipt.compensation_receipts[0].code == "COMPENSATION_FAILED"

    async def test_dispatch_exception_is_step_failure(self):
        reg = FakeRegistry(script={"t_analyze": RuntimeError("store down")})
        receipt = await ActionPlanExecutor(registry=reg).run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze")))
        assert receipt.status == "aborted"
        assert receipt.step_receipts[0].status == "failed"
        assert receipt.step_receipts[0].code == "DISPATCH_ERROR"


class TestPreconditions:
    async def test_ref_dead_blocks_step(self):
        async def ref_alive(ref):
            return False

        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg, ref_alive=ref_alive)
        step = GISAction(action_id="act-1", kind="analyze", tool="t_analyze",
                         preconditions=[Precondition(kind="data_ref_alive",
                                                     target="ref:gone")])
        receipt = await exe.run(_compile(step))
        assert reg.calls == []
        assert receipt.step_receipts[0].status == "precondition_failed"
        assert receipt.step_receipts[0].code == "REF_DEAD"

    async def test_layer_absent_precondition(self):
        async def state_view():
            return {"layers": [{"id": "L1"}]}

        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg, state_view=state_view)
        step = GISAction(action_id="act-1", kind="mutate_presentation",
                         tool="t_upsert", side_effect="session_state",
                         preconditions=[Precondition(kind="layer_present",
                                                     target="L2")])
        receipt = await exe.run(_compile(step))
        assert receipt.step_receipts[0].code == "LAYER_ABSENT"

    async def test_revision_mismatch_fails_closed(self):
        async def state_view():
            return {"_cartographic_mutation_revision": 7}

        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg, state_view=state_view)
        step = _mutation_step(preconditions=[
            Precondition(kind="revision_match", target="mapspec",
                         expected="5")])
        receipt = await exe.run(_compile(step))
        assert receipt.step_receipts[0].code == "REVISION_MISMATCH"
        assert receipt.status == "aborted"

    async def test_revision_match_passes(self):
        async def state_view():
            return {"_cartographic_mutation_revision": 5}

        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg, state_view=state_view)
        step = _mutation_step(preconditions=[
            Precondition(kind="revision_match", target="mapspec",
                         expected="5")])
        receipt = await exe.run(_compile(step))
        assert receipt.status == "ran"

    async def test_precondition_failure_aborts_downstream(self):
        async def ref_alive(ref):
            return False

        reg = FakeRegistry()
        exe = ActionPlanExecutor(registry=reg, ref_alive=ref_alive)
        receipt = await exe.run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze",
                      preconditions=[Precondition(kind="data_ref_alive",
                                                  target="ref:x")]),
            _mutation_step(action_id="act-2"),
        ))
        statuses = {r.action_id: r.status for r in receipt.step_receipts}
        assert statuses["act-2"] == "skipped"
        assert reg.calls == []


class TestReceiptBounds:
    async def test_receipt_carries_digests_not_bodies(self):
        reg = FakeRegistry(script={"t_analyze": {"success": True,
                                                 "huge": "x" * 100000}})
        receipt = await ActionPlanExecutor(registry=reg).run(_compile(
            GISAction(action_id="act-1", kind="analyze", tool="t_analyze")))
        dump = receipt.model_dump_json()
        assert len(dump) < 8000
        assert receipt.step_receipts[0].result_digest
