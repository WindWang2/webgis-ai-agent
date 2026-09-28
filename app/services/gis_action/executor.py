"""ActionPlan 执行器：编译产物 → 有序受控执行（H10 / ADR-0217）。

职责边界：executor 只消费**已编译**的 ActionPlanCompilation；contract
validation 已在 compiler 完成，这里做的是执行面语义 —— 逐步 precondition
校验、逐 action 失败策略（fail_closed / best_effort / compensate）、补偿
回滚（逆序、只补已执行的 mutating 步）、step 级 receipt。

纪律：

- dry-run = 零 dispatch（``would_run`` 逐条标记；编译本身即 dry-run 视图）；
- registry 是唯一执行通道（不绕过 ToolRegistry/Dispatch 拥有者）；
- receipt 只携带 digest/状态/id —— 结果本体不入 receipt（有界）；
- 补偿不可合成时**如实记 UNCOMPENSATED**，绝不虚构恢复动作；
- 不持久化任何 runtime state（权威仍在 MapSpec/SessionPlan）。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.lib.gis.action_ir import digest_of
from app.services.gis_action.compiler import ActionPlanCompilation, ResolvedAction

logger = logging.getLogger(__name__)

__all__ = ["StepReceipt", "PlanRunReceipt", "ActionPlanExecutor"]

DetailFn = Callable[[str], str]
StateView = Callable[[], Awaitable[Dict[str, Any]]]
RefAlive = Callable[[str], Awaitable[bool]]
ArgsProvider = Callable[[ResolvedAction], Dict[str, Any]]
CompensationArgsProvider = Callable[[ResolvedAction, Any], Dict[str, Any]]

_STEP_STATUS_MAX = 64
_ERR_MAX = 200

#: 补偿 kind → registry 工具（确定性映射；不在表内 = 不可自动补偿）。
_COMPENSATION_TOOL: Dict[str, str] = {
    "remove_layer": "webgis_layer_remove",
    "remove_component": "webgis_remove_component",
}


class StepReceipt(BaseModel):
    """一步执行的 typed 回执（digest 级；结果本体不入 receipt）。"""

    model_config = ConfigDict(frozen=True)

    step: int = Field(ge=1)
    action_id: str = Field(max_length=128)
    client_action_id: str = Field(max_length=160)
    tool: str = Field(max_length=96)
    status: str = Field(max_length=_STEP_STATUS_MAX)  # applied|failed|skipped|would_run|precondition_failed|uncompensated
    code: str = Field(default="", max_length=64)
    detail: str = Field(default="", max_length=_ERR_MAX)
    result_digest: str = Field(default="", max_length=24)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class PlanRunReceipt(BaseModel):
    """一次执行的聚合回执（对接 ToolDispatch 面 / decision 记录）。"""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(max_length=128)
    compile_id: str = Field(default="", max_length=48)
    compile_digest: str = Field(default="", max_length=80)
    status: str = Field(default="ran", max_length=32)  # ran|blocked|aborted|completed_with_failures
    step_receipts: List[StepReceipt] = Field(default_factory=list)
    compensation_receipts: List[StepReceipt] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list, max_length=12)

    @property
    def applied_count(self) -> int:
        return sum(1 for r in self.step_receipts if r.status == "applied")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def _default_args_provider(step: ResolvedAction) -> Dict[str, Any]:
    """确定性 args 重建：params token + 具名 input refs（无 IO）。"""
    args: Dict[str, Any] = dict(step.params)
    for descriptor in step.inputs:
        if descriptor.ref:
            args.setdefault(descriptor.name, descriptor.ref)
    return args


def _default_compensation_args(step: ResolvedAction, compensation: Any) -> Dict[str, Any]:
    return {"layer_id": compensation.target} if compensation.target else {}


class ActionPlanExecutor:
    """计划执行器（registry 注入；state/ref 视图可注入 → 测试零 mock 库）。"""

    def __init__(
        self,
        *,
        registry: Any,
        session_id: str = "",
        state_view: Optional[StateView] = None,
        ref_alive: Optional[RefAlive] = None,
        args_provider: Optional[ArgsProvider] = None,
        compensation_args_provider: Optional[CompensationArgsProvider] = None,
    ) -> None:
        self._registry = registry
        self._session_id = session_id
        self._state_view = state_view
        self._ref_alive = ref_alive
        self._args_provider = args_provider or _default_args_provider
        self._comp_args = compensation_args_provider or _default_compensation_args

    # ── precondition 校验 ───────────────────────────────────────────
    async def _check_preconditions(
        self, step: ResolvedAction,
    ) -> Tuple[bool, str, str]:
        """逐步前提校验（不可静态判定 → 放行；权威在 runtime 闸）。

        视图故障（state_view/ref_alive 抛异常）= typed 失败，绝不向
        ``run()`` 逃逸 —— executor 的输出契约是 receipt，不是异常。
        """
        for pre in step.preconditions:
            try:
                ok, code, detail = await self._check_one_precondition(pre)
            except Exception as exc:  # noqa: BLE001 — 视图异常 fail-closed
                logger.warning("[action-executor] precondition view error: %s",
                               exc)
                return False, "VIEW_ERROR", str(exc)[:_ERR_MAX]
            if not ok:
                return False, code, detail
        return True, "", ""

    async def _check_one_precondition(
        self, pre: Any,
    ) -> Tuple[bool, str, str]:
        if pre.kind == "data_ref_alive":
            if self._ref_alive is None:
                return True, "", ""  # 无视图 → runtime 闸兜底
            if not await self._ref_alive(pre.target):
                return False, "REF_DEAD", f"ref '{pre.target}' unavailable"
        elif pre.kind in ("layer_present", "layer_absent"):
            if self._state_view is None:
                return True, "", ""
            doc = await self._state_view()
            layer_ids = {
                str(l.get("id")) for l in (doc.get("layers") or [])
                if isinstance(l, dict) and l.get("id")
            }
            present = pre.target in layer_ids
            if pre.kind == "layer_present" and not present:
                return False, "LAYER_ABSENT", f"layer '{pre.target}' absent"
            if pre.kind == "layer_absent" and present:
                return False, "LAYER_PRESENT", f"layer '{pre.target}' already present"
        elif pre.kind == "revision_match":
            if self._state_view is None:
                return True, "", ""
            doc = await self._state_view()
            try:
                current_rev = int(
                    doc.get("_cartographic_mutation_revision", 0) or 0)
            except (TypeError, ValueError):
                current_rev = 0
            try:
                expected = int(pre.expected)
            except (TypeError, ValueError):
                return False, "REVISION_MALFORMED", "revision expected malformed"
            if current_rev != expected:
                return False, "REVISION_MISMATCH", (
                    f"expected rev {expected}, current {current_rev}")
        # capability_eligible：权威在 dispatch capability bind，此处不裁决。
        return True, "", ""

    async def _dispatch_step(
        self, step: ResolvedAction, *, dry_run: bool,
    ) -> StepReceipt:
        base = dict(step=step.step, action_id=step.action_id,
                    client_action_id=step.client_action_id, tool=step.tool)
        if dry_run:
            return StepReceipt(**base, status="would_run")
        ok, code, detail = await self._check_preconditions(step)
        if not ok:
            return StepReceipt(**base, status="precondition_failed",
                               code=code, detail=detail[:_ERR_MAX])
        try:
            args = self._args_provider(step)
        except Exception as exc:  # noqa: BLE001 — args 重建故障 = typed 失败
            logger.warning("[action-executor] args provider error: %s", exc)
            return StepReceipt(**base, status="failed",
                               code="ARGS_ERROR", detail=str(exc)[:_ERR_MAX])
        try:
            result = await self._registry.dispatch(
                step.tool, args, session_id=self._session_id)
        except Exception as exc:  # noqa: BLE001 — 工具异常 = failed（与 dispatch 语义一致）
            logger.warning("[action-executor] step %s dispatch failed: %s",
                           step.client_action_id, exc)
            return StepReceipt(**base, status="failed",
                               code="DISPATCH_ERROR", detail=str(exc)[:_ERR_MAX])
        success = bool(result.get("success", True)) if isinstance(result, dict) else True
        return StepReceipt(
            **base,
            status="applied" if success else "failed",
            code="" if success else str(
                (result or {}).get("code") or "TOOL_FAILED")[:64],
            detail="" if success else str(
                (result or {}).get("message") or (result or {}).get("error") or "")[:_ERR_MAX],
            result_digest=digest_of(result)[:24] if isinstance(result, dict) else "",
        )

    async def _compensate(
        self, executed: List[StepReceipt],
        steps_by_id: Dict[str, ResolvedAction],
    ) -> List[StepReceipt]:
        """逆序补偿已执行的 mutating 步（合成不出 → 如实 UNCOMPENSATED）。"""
        receipts: List[StepReceipt] = []
        for receipt in reversed(executed):
            step = steps_by_id.get(receipt.action_id)
            if step is None or step.side_effect != "session_state":
                continue
            comp = step.compensation
            if comp.kind == "none":
                receipts.append(StepReceipt(
                    step=step.step, action_id=step.action_id,
                    client_action_id=step.client_action_id, tool=step.tool,
                    status="uncompensated", code="NO_COMPENSATION_DECLARED"))
                continue
            tool = _COMPENSATION_TOOL.get(comp.kind)
            if tool is None or self._registry is None:
                receipts.append(StepReceipt(
                    step=step.step, action_id=step.action_id,
                    client_action_id=step.client_action_id, tool=step.tool,
                    status="uncompensated", code="COMPENSATION_NOT_SYNTHESIZABLE"))
                continue
            args = self._comp_args(step, comp)
            try:
                result = await self._registry.dispatch(
                    tool, args, session_id=self._session_id)
                ok = bool(result.get("success", True)) if isinstance(result, dict) else True
            except Exception as exc:  # noqa: BLE001 — 补偿失败如实记录
                ok = False
                result = {"success": False, "message": str(exc)}
            receipts.append(StepReceipt(
                step=step.step, action_id=step.action_id,
                client_action_id=step.client_action_id, tool=tool,
                status="compensated" if ok else "uncompensated",
                code="" if ok else "COMPENSATION_FAILED",
                result_digest=digest_of(result)[:24],
            ))
        return receipts

    # ── 入口 ────────────────────────────────────────────────────────
    async def run(
        self, compilation: ActionPlanCompilation, *, dry_run: bool = False,
    ) -> PlanRunReceipt:
        """按编译序执行（blocked 计划零执行；逐步失败策略推进）。"""
        base = dict(plan_id=compilation.plan_id,
                    compile_id=compilation.compile_id,
                    compile_digest=compilation.compile_digest)
        if compilation.blocked:
            return PlanRunReceipt(
                **base, status="blocked",
                reason_codes=(["ACTION_PLAN_BLOCKED"]
                              + [f.code for f in
                                 compilation.blocking_findings()[:6]])[:12])
        steps_by_id = {s.action_id: s for s in compilation.steps}
        receipts: List[StepReceipt] = []
        executed: List[StepReceipt] = []
        aborted = False
        need_compensation = False
        for step in compilation.steps:
            if aborted:
                receipts.append(StepReceipt(
                    step=step.step, action_id=step.action_id,
                    client_action_id=step.client_action_id, tool=step.tool,
                    status="skipped", code="ABORTED_UPSTREAM"))
                continue
            receipt = await self._dispatch_step(step, dry_run=dry_run)
            receipts.append(receipt)
            if receipt.status in ("applied", "would_run"):
                if receipt.status == "applied":
                    executed.append(receipt)
                continue
            if receipt.status == "precondition_failed":
                # 前提失败 = 计划与现场失配：一律 fail-closed 中止后续
                # （继续执行会在失配状态上叠加更多变更）。
                aborted = True
                need_compensation = step.failure == "compensate"
                continue
            # failed：按该 action 声明的失败策略。
            if step.failure in ("fail_closed", "compensate") and not dry_run:
                aborted = True
                need_compensation = step.failure == "compensate"
        compensations: List[StepReceipt] = []
        if need_compensation and not dry_run and executed:
            compensations = await self._compensate(executed, steps_by_id)
        failed = [r for r in receipts if r.status in ("failed", "precondition_failed")]
        if aborted:
            status = "aborted"
        elif failed:
            status = "completed_with_failures"
        else:
            status = "ran"
        reason_codes = ["ACTION_PLAN_RAN"]
        if aborted:
            reason_codes = (["ACTION_PLAN_ABORTED", "COMPENSATION_ISSUED"]
                            if compensations else ["ACTION_PLAN_ABORTED"])
        elif failed:
            reason_codes = ["ACTION_PLAN_PARTIAL"]
        return PlanRunReceipt(
            **base, status=status,
            step_receipts=receipts,
            compensation_receipts=compensations,
            reason_codes=reason_codes,
        )
