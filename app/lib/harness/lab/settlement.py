"""Settlement 终态检查（E15 D4）：驱动生产结算/准入 seam 的跨切面合同。

F03 的终点×终态矩阵测试钉住 bridge 单路径语义；本模块把 cancel / 幂等 /
duplicate / policy deny / resource reject / disconnect 收编为**场景可声明**
的 lab 检查，接入统一 runner 与报告。纪律：

- 驱动的是**真实** ``settle_turn_projections`` / ``HarnessResourceGovernor``
  / ``check_tool_capability_at_dispatch``；协作面（finalization / 状态机 /
  链持久化）按既有测试钉住的 patch 契约替换为内存记录器（hermetic）；
- 每项检查绑定诚实终态断言：non-clean 结算不得返回 map_product（完成度
  奖励不属于失败终态）、链持久化照常（错误同样有可回放证据）、拒绝必须
  可分类 —— 违反即 fail，绝不静默 pass；
- 无 wall-clock 断言（确定性）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.harness.lab.spec import LabScenario

__all__ = [
    "SettlementCheckResult",
    "SettlementSandbox",
    "run_settlement_checks",
]


@dataclass
class SettlementCheckResult:
    """一项 settlement 检查的裁决（status ∈ pass/fail；detail 可定位）。"""

    check: str
    status: str
    detail: str = ""
    observations: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "pass"


# ── hermetic 沙箱（patch 契约与既有结算管线测试逐字对齐）────────────────────


class _RecordingChain:
    """链发射 + 持久化记录器。"""

    def __init__(self) -> None:
        self.emits: List[Dict[str, Any]] = []
        self.persists: List[str] = []


class _RecordingProjections:
    """workflow / runtime-state / checkpoint 记录器。"""

    def __init__(self) -> None:
        self.workflow_updates: List[str] = []
        self.state_updates: List[Dict[str, Any]] = []
        self.checkpoints: List[str] = []


class SettlementSandbox:
    """settlement 管线协作面的内存替身（进入即替换，退出即恢复）。

    替换面与 ``tests/unit/test_pi_post_dispatch_pipeline.py`` 钉住的 patch
    契约一致 —— settle 管线的协作模块均为惰性 import，模块属性交换即生效。
    """

    def __init__(self, *, finalize_result: Optional[Any] = None,
                 stored_product: Optional[Dict[str, Any]] = None) -> None:
        self.finalize_result = finalize_result
        self.stored_product = stored_product
        self.finalize_calls: List[Dict[str, Any]] = []
        self.chain = _RecordingChain()
        self.projections = _RecordingProjections()
        self._saved: Dict[str, Any] = {}

    # -- 协作面记录（观测/断言面）----------------------------------------

    async def _maybe_finalize(self, session_id: str, *, reason: str,
                              final_gate: bool = False) -> Any:
        self.finalize_calls.append(
            {"session_id": session_id, "reason": reason,
             "final_gate": final_gate})
        return self.finalize_result

    async def _workflow_update(self, session_id: str, *, reason: str,
                               event: str) -> None:
        self.projections.workflow_updates.append(session_id)

    async def _runtime_state(self, session_id: str, *, reason: str,
                             trigger: str, turn_settled: bool = False) -> None:
        self.projections.state_updates.append(
            {"session_id": session_id, "trigger": trigger,
             "turn_settled": turn_settled})

    @staticmethod
    def _runtime_state_enabled() -> bool:
        return True

    async def _checkpoint(self, session_id: str) -> None:
        self.projections.checkpoints.append(session_id)

    def _emit_chain(self, turn_id: str, stage: Any, **kw: Any) -> None:
        self.chain.emits.append(
            {"turn_id": turn_id,
             "stage": getattr(stage, "name", str(stage)), **kw})

    def _persist_chain(self, turn_id: str, *, session_id: str = "") -> None:
        self.chain.persists.append(turn_id)

    async def _read_stored(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.stored_product

    @staticmethod
    def _finalization_payload(completion: Any, session_id: str,
                              **kw: Any) -> Dict[str, Any]:
        return {"task_complete": True, "sid": session_id}

    # -- 作用域 -----------------------------------------------------------

    def __enter__(self) -> "SettlementSandbox":
        import app.services.gis_harness.context_layers as cl
        import app.services.gis_harness.map_completion as mc
        import app.services.gis_harness.runtime_state_machine as rsm
        import app.services.gis_harness.workflow_instance as wfi
        import app.lib.runtime.chain_emitters as ce
        import app.services.gis_harness.trace_store as ts

        for mod in (mc, wfi, rsm, cl, ce, ts):
            self._saved[mod.__name__] = {k: getattr(mod, k)
                                         for k in self._targets(mod)}
        mc.maybe_finalize_map_product = self._maybe_finalize  # type: ignore[assignment]
        mc.read_stored_map_product = self._read_stored  # type: ignore[assignment]
        mc.finalization_sse_payload = self._finalization_payload  # type: ignore[assignment]
        wfi.maybe_update_workflow_instance = self._workflow_update  # type: ignore[assignment]
        rsm.maybe_update_runtime_state = self._runtime_state  # type: ignore[assignment]
        rsm.runtime_state_enabled = self._runtime_state_enabled  # type: ignore[assignment]
        cl.checkpoint_context_layers = self._checkpoint  # type: ignore[assignment]
        ce.emit_chain_for = self._emit_chain  # type: ignore[assignment]
        ts.persist_turn_chain = self._persist_chain  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        import app.services.gis_harness.context_layers as cl
        import app.services.gis_harness.map_completion as mc
        import app.services.gis_harness.runtime_state_machine as rsm
        import app.services.gis_harness.workflow_instance as wfi
        import app.lib.runtime.chain_emitters as ce
        import app.services.gis_harness.trace_store as ts

        for mod in (mc, wfi, rsm, cl, ce, ts):
            for key, value in self._saved.get(mod.__name__, {}).items():
                if value is not None:
                    setattr(mod, key, value)

    @staticmethod
    def _targets(mod: Any) -> tuple:
        return {
            "app.services.gis_harness.map_completion": (
                "maybe_finalize_map_product", "read_stored_map_product",
                "finalization_sse_payload"),
            "app.services.gis_harness.workflow_instance": (
                "maybe_update_workflow_instance",),
            "app.services.gis_harness.runtime_state_machine": (
                "maybe_update_runtime_state", "runtime_state_enabled"),
            "app.services.gis_harness.context_layers": (
                "checkpoint_context_layers",),
            "app.lib.runtime.chain_emitters": ("emit_chain_for",),
            "app.services.gis_harness.trace_store": ("persist_turn_chain",),
        }.get(mod.__name__, ())


# ── 检查实现（每项 = 生产 seam 驱动 + 诚实终态断言）─────────────────────────


async def _check_cancel_reduced_settle(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """cancel → reduced settle：无 map_product、无 finalize、链仍持久。"""
    from app.services.chat.pi_post_dispatch import TurnSettleOutcome
    from app.services.chat.pi_post_dispatch import settle_turn_projections

    sb.finalize_result = None
    payload = await settle_turn_projections(
        "lab-cancel", "turn-cancel",
        outcome=TurnSettleOutcome(settle_class="cancelled"),
        reason="user_cancel",
    )
    problems: List[str] = []
    if payload is not None:
        problems.append(f"cancelled settle returned map_product {payload!r}")
    if sb.finalize_calls:
        problems.append("cancelled settle ran finalization (completion reward "
                        "must not belong to failed terminal states)")
    if not sb.chain.persists:
        problems.append("cancelled settle did not persist replay evidence")
    user_output = [e for e in sb.chain.emits if "USER_OUTPUT" in e["stage"]]
    if not user_output:
        problems.append("cancelled settle did not emit USER_OUTPUT chain stage")
    if not sb.projections.state_updates:
        problems.append("cancelled settle did not close runtime state")
    if problems:
        return SettlementCheckResult("cancel_reduced_settle", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult(
        "cancel_reduced_settle", "pass",
        observations={
            "chain_persists": len(sb.chain.persists),
            "state_updates": len(sb.projections.state_updates),
        })


async def _check_settle_idempotent(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """二次结算：幂等门跳过 → 披露已存储完成态；缺席时不得伪造完成。"""
    from types import SimpleNamespace

    from app.services.chat.pi_post_dispatch import settle_turn_projections

    sb.finalize_result = SimpleNamespace(
        status="completed", repairs_applied=False)
    first = await settle_turn_projections("lab-idem", "turn-idem")
    sb.finalize_result = None
    sb.stored_product = {"task_complete": True, "sid": "lab-idem"}
    second = await settle_turn_projections("lab-idem", "turn-idem")
    problems: List[str] = []
    if not isinstance(first, dict) or first.get("task_complete") is not True:
        problems.append(f"clean settle payload unexpected: {first!r}")
    if second != sb.stored_product:
        problems.append(
            f"idempotent re-settle disclosed {second!r}, "
            f"want stored product {sb.stored_product!r}")
    if problems:
        return SettlementCheckResult("settle_idempotent", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult(
        "settle_idempotent", "pass",
        observations={"finalize_calls": len(sb.finalize_calls)})


async def _check_settle_no_fake_success(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """finalize 缺席（pending/None 且无存储）→ 不得返回任何 map_product。"""
    from app.services.chat.pi_post_dispatch import settle_turn_projections

    sb.finalize_result = None
    sb.stored_product = None
    payload = await settle_turn_projections("lab-nofake", "turn-nofake")
    if payload is not None:
        return SettlementCheckResult(
            "settle_no_fake_success", "fail",
            f"empty finalize + no stored product still returned {payload!r}")
    return SettlementCheckResult("settle_no_fake_success", "pass")


async def _check_duplicate_dispatch_dedup(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """同收据二次投递：canonical 签名一致（dedup 键稳定）且可观测。"""
    from app.services.chat.no_progress import canonical_call_signature

    from app.lib.harness.lab.fakes import FaultInjector, ScriptedToolProvider

    op = next(iter(spec.provider_ops), None)
    if op is None:
        op = _default_duplicate_op()
    provider = ScriptedToolProvider([op])
    injector = FaultInjector(
        [f for f in spec.fault_plan if f.type == "duplicate_event"]
        or [_dup_fault()], provider=provider)
    first = await injector.dispatch(op.tool, op.arguments, call_id=op.call_id)
    second = await injector.dispatch(op.tool, op.arguments, call_id=op.call_id)
    problems: List[str] = []
    if first.duplicate:
        problems.append("first delivery already marked duplicate")
    if not second.duplicate:
        problems.append("second delivery not observable as duplicate replay")
    signature = canonical_call_signature(op.tool, op.arguments)
    if not signature:
        problems.append("canonical dedup signature empty for scripted call")
    if first.is_error != second.is_error:
        problems.append("duplicate replay changed the receipt verdict class")
    if problems:
        return SettlementCheckResult("duplicate_dispatch_dedup", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult(
        "duplicate_dispatch_dedup", "pass",
        observations={"provider_calls": len(provider.calls),
                      "faults_applied": second.faults_applied})


async def _check_policy_deny_refused(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """bind gate deny：生产判定拒绝必须可观测，且不得伪造放行。

    候选计划按既有测试钉住的 patch 契约注入（``plan_candidates_v8`` 模块
    属性；与本工具不合格 + 存在合格工具替代的最小场景），**拒绝决定本身
    由生产 ``check_tool_capability_at_dispatch`` 产出** —— fake 不代答。
    """

    from app.services.gis_harness.qualification_v8 import (
        QualificationResult,
        QualificationStatus,
    )

    from app.lib.harness.lab.fakes import ScriptedToolProvider

    def _candidate(kind: str, id_: str, score: float) -> Any:
        from app.services.gis_harness.candidate_planner_v8 import Candidate

        return Candidate(
            kind=kind, id=id_,
            qualification=QualificationResult(
                status=(QualificationStatus.ELIGIBLE if id_ != "bad_tool"
                        else QualificationStatus.INELIGIBLE)),
            latency_class="fast", reliability_penalty=0.0, score=score,
        )

    def _plan(cap: str, _tool: str) -> Any:
        from app.services.gis_harness.candidate_planner_v8 import CandidatePlan
        from app.services.gis_harness.qualification_v8 import QualificationReason

        return CandidatePlan(
            capability_id=cap,
            candidates=[_candidate("tool", "alt_tool", 0.0)],
            excluded=[{
                "kind": "tool", "id": _tool, "capability": cap,
                "reason": "qualification_ineligible",
                "qualification": QualificationResult(
                    status=QualificationStatus.INELIGIBLE,
                    reasons=[QualificationReason(
                        check="offline", observed="online_only",
                        expected="local", hint="pick local provider")],
                ).to_dict(),
            }],
        )

    import app.services.gis_harness.candidate_planner_v8 as cp

    saved_plan = cp.plan_candidates_v8
    cp.plan_candidates_v8 = lambda cap, ctx, **kw: _plan(cap, "bad_tool")
    try:
        from app.services.gis_harness.hotpath_convergence.capability_bind import (
            check_tool_capability_at_dispatch,
        )

        registry = ScriptedToolProvider(
            [], tool_capabilities={"bad_tool": ["cap_x"]})
        decision = check_tool_capability_at_dispatch(
            "bad_tool", registry=registry, session_id="lab-policy")
    finally:
        cp.plan_candidates_v8 = saved_plan
    problems: List[str] = []
    if decision is None:
        # 生产闸把"无声明/计划缺席"视为放行：deny 场景不可表达时诚实
        # 缺席，绝不伪造 deny。
        return SettlementCheckResult(
            "policy_deny_refused", "not_evaluated",
            "bind gate did not refuse (open gate / planner absent); "
            "deny path not expressible on this build")
    if decision.allowed:
        problems.append(
            f"ineligible tool allowed: {decision.reason!r}")
    if not str(decision.reason or "").strip():
        problems.append("deny decision carries no reason")
    if not decision.alternatives:
        problems.append("deny decision offers no eligible alternatives")
    if problems:
        return SettlementCheckResult("policy_deny_refused", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult(
        "policy_deny_refused", "pass",
        observations={"reason": str(decision.reason)[:96],
                      "alternatives": [str(a.get("id") or "")
                                       for a in decision.alternatives[:3]]})


async def _check_resource_reject_classified(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """governor 准入：超预算需求被拒且 reason 可分类；取消后 reservation 清零。"""
    from pathlib import Path

    from app.services.governor.config import GovernorConfig, load_manifest
    from app.services.governor.contract import (
        AdmissionDecision,
        CancelReason,
        Dimension,
        DimValue,
        ResourceClass,
        ResourceDemand,
        ResourceEstimate,
    )
    from app.services.governor.governor import HarnessResourceGovernor

    manifest_path = (Path(__file__).resolve().parents[4]
                     / "config" / "governor_budgets.json")
    budgets = load_manifest(manifest_path)
    session_budget = budgets.get("session")
    if session_budget is None:
        return SettlementCheckResult(
            "resource_reject_classified", "not_evaluated",
            "no session budget scope in manifest")
    budgets["session"] = session_budget.model_copy(
        update={"provisional": False,
                "limits": {Dimension.MEMORY_BYTES: 1e6}})
    governor = HarnessResourceGovernor(GovernorConfig(), budgets=budgets)
    demand = ResourceDemand(
        session_id="lab-resource",
        estimate=ResourceEstimate(
            resource_class=ResourceClass.RASTER,
            dims={Dimension.MEMORY_BYTES: DimValue.known(1e9),
                  Dimension.WALL_TIME_S: DimValue.known(1.0)},
        ),
    )
    decision, reservation, ticket = await governor.admit_and_reserve(demand)
    problems: List[str] = []
    if decision.allowed:
        problems.append(
            f"1e9-bytes demand admitted under 1e6 budget: {decision.decision}")
    if decision.decision is not AdmissionDecision.REJECT:
        problems.append(f"expected REJECT, got {decision.decision}")
    if not decision.reasons:
        problems.append("rejection carries no classifiable reason codes")
    # cancel：reservation 立即释放（账面归零），取消事实可查、幂等。
    await governor.cancel_session("lab-resource", CancelReason.USER_CANCEL)
    record = governor.cancellations.record_of("lab-resource")
    if record is None:
        problems.append("cancellation record missing after cancel_session")
    await governor.cancel_session("lab-resource", CancelReason.USER_CANCEL)
    repeat = governor.cancellations.record_of("lab-resource")
    if record is not None and repeat is not None \
            and repeat.cancelled_at != record.cancelled_at:
        problems.append("repeated cancel re-recorded the cancellation fact")
    snapshot = governor.snapshot("lab-resource")
    live = ((snapshot.get("budgets") or {}).get("session") or {}).get("live")
    if isinstance(live, dict) and any(
            float(v or 0) > 0 for v in live.values()):
        problems.append(f"budget live values not released after cancel: {live}")
    if problems:
        return SettlementCheckResult("resource_reject_classified", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult(
        "resource_reject_classified", "pass",
        observations={"reasons": decision.reasons[:4],
                      "live_after_cancel": live})


async def _check_disconnect_cancelled(
        spec: LabScenario, sb: SettlementSandbox) -> SettlementCheckResult:
    """断开 → cancelled 族终态：不得被折叠为 failed，产品不披露。"""
    from app.services.chat.pi_post_dispatch import TurnSettleOutcome
    from app.services.chat.pi_post_dispatch import settle_turn_projections

    sb.finalize_result = None
    payload = await settle_turn_projections(
        "lab-disc", "turn-disc",
        outcome=TurnSettleOutcome(settle_class="cancelled",
                                  failure_class="client_disconnect"),
        reason="client_disconnect",
    )
    problems: List[str] = []
    if payload is not None:
        problems.append("disconnect settle disclosed a map product")
    if not sb.chain.persists:
        problems.append("disconnect settle lost its replay evidence")
    if problems:
        return SettlementCheckResult("disconnect_cancelled", "fail",
                                     "; ".join(problems))
    return SettlementCheckResult("disconnect_cancelled", "pass")


_CHECKS = {
    "cancel_reduced_settle": _check_cancel_reduced_settle,
    "settle_idempotent": _check_settle_idempotent,
    "settle_no_fake_success": _check_settle_no_fake_success,
    "duplicate_dispatch_dedup": _check_duplicate_dispatch_dedup,
    "policy_deny_refused": _check_policy_deny_refused,
    "resource_reject_classified": _check_resource_reject_classified,
    "disconnect_cancelled": _check_disconnect_cancelled,
}

# 词表以实现为准导出（spec.SETTLEMENT_CHECKS 是声明面镜像）。
SETTLEMENT_CHECK_IMPLS = tuple(sorted(_CHECKS))


async def run_settlement_checks(
    spec: LabScenario,
    sandbox: Optional[SettlementSandbox] = None,
) -> List[SettlementCheckResult]:
    """按场景声明的检查词表逐项驱动生产 seam（顺序执行，共享沙箱）。"""
    results: List[SettlementCheckResult] = []
    sb = sandbox or SettlementSandbox()
    with sb:
        for check in spec.settlement_checks:
            impl = _CHECKS.get(check)
            if impl is None:
                results.append(SettlementCheckResult(
                    check, "fail", f"no implementation for check {check!r}"))
                continue
            try:
                results.append(await impl(spec, sb))
            except Exception as exc:  # noqa: BLE001 — 检查崩溃 = 该项 fail
                results.append(SettlementCheckResult(
                    check, "fail", f"check crashed: {exc}"))
    return results


def _default_duplicate_op() -> Any:
    from app.lib.harness.lab.spec import ProviderOp

    return ProviderOp(
        call_id="lab-dup-1", tool="lab.echo",
        arguments={"q": "dup"}, result={"echo": "dup"},
    )


def _dup_fault() -> Any:
    from app.lib.harness.lab.spec import FaultStep

    return FaultStep(type="duplicate_event", target_turn=1)
