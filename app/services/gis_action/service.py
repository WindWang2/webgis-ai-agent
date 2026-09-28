"""GISAction 服务门面 —— 投影 / 编译 / diff / replan / 执行的唯一入口
（H10 / ADR-0217）。

生产接线面：

- ``bind_action_ir``：ToolDispatchService 调度前的 IR 投影闸（kill switch
  ``GIS_ACTION_IR_BIND``，默认 ON；fail-open —— 投影/编译故障绝不阻断调度，
  与 capability bind 同纪律）。默认模式下 blocking findings 只降级为证据
  与 telemetry；``GIS_ACTION_IR_STRICT=1`` 时 blocking → typed 拒绝。
- ``RegistryToolResolver``：registry.metadata 只读投影（tool 解析权威）；
  capability 候选经 ExecutionCatalog 缓存快照（描述性视图）。
- executor 的 production 构造（registry 注入，state/ref 视图可注入）。

本门面不持久化 runtime state；receipt 由调用方对接 ToolDispatch /
decision 记录面。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.lib.gis.action_ir import (
    GISAction,
    GISActionPlan,
    ACTION_IR_VERSION,
    compute_plan_id,
    describe_plan,
)
from app.services.gis_action.compiler import (
    ActionPlanCompilation,
    ToolResolution,
    ToolResolver,
    compile_actions,
)
from app.services.gis_action.diff import ActionPlanDiff, diff_plans
from app.services.gis_action.executor import (
    ActionPlanExecutor,
    PlanRunReceipt,
)
from app.services.gis_action.legacy_adapter import (
    project_tool_call_to_plan,
    record_usage,
    usage_snapshot,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GISActionService", "gis_action_service",
    "RegistryToolResolver",
    "ActionIRBind", "bind_action_ir",
    "action_ir_bind_enabled", "action_ir_strict",
]


def action_ir_bind_enabled() -> bool:
    return os.getenv("GIS_ACTION_IR_BIND", "1") != "0"


def action_ir_strict() -> bool:
    return os.getenv("GIS_ACTION_IR_STRICT", "0") == "1"


class RegistryToolResolver:
    """registry.metadata 只读投影（tool 事实权威）+ catalog 候选视图。"""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def _metadata(self, tool_name: str) -> Optional[Dict[str, Any]]:
        meta_fn = getattr(self._registry, "metadata", None)
        if not callable(meta_fn):
            return None
        try:
            meta = meta_fn(tool_name)
        except Exception:  # noqa: BLE001 — 元数据缺失 = 不可解析
            return None
        return meta if isinstance(meta, dict) and meta else None

    def resolve_tool(self, tool_name: str) -> Optional[ToolResolution]:
        meta = self._metadata(tool_name)
        if meta is None:
            return None
        resource = {
            dim: str(meta.get(f"{dim}_class") or "")
            for dim in ("latency", "memory", "scale")
            if meta.get(f"{dim}_class")
        }
        output_type = str(meta.get("output_semantic_type") or "")
        # registry 弃用惯例：deprecation_of/fallback_tool 指向继任者。
        successor = str(meta.get("superseded_by")
                        or meta.get("fallback_tool")
                        or meta.get("deprecation_of") or "")
        return ToolResolution(
            tool=tool_name[:96],
            side_effect=str(meta.get("side_effect") or "unclassified"),
            deterministic=meta.get("deterministic"),
            deprecated=str(meta.get("status") or "") == "deprecated",
            superseded_by=successor,
            output_semantic_types=(output_type,) if output_type else (),
            resource_class=resource,
        )

    def capability_candidates(self, capability_id: str) -> Sequence[str]:
        try:
            from app.lib.gis.execution_catalog import get_execution_catalog
            return tuple(get_execution_catalog()
                         .tool_candidates_for_capability(capability_id))
        except Exception:  # noqa: BLE001 — 候选视图失败 = 不可解析
            return ()


@dataclass(frozen=True)
class ActionIRBind:
    """一次调度前投影的结果（evidence 有界；不携带参数）。"""

    plan_id: str
    compile_id: str
    kind: str
    status: str
    evidence: Dict[str, Any] = dc_field(default_factory=dict)
    compilation: Optional[ActionPlanCompilation] = None
    denied: bool = False
    denial_text: str = ""


def bind_action_ir(
    tool_name: str,
    args: Any,
    *,
    registry: Any,
    args_projected: bool = True,
) -> Optional[ActionIRBind]:
    """调度前 IR 投影闸（fail-open；strict 模式 blocking → 拒绝）。

    ``args_projected=False``：caller 未能解析原始参数（非 dict/JSON 坏形）
    —— 投影基于空参数，evidence 如实披露（plan_id 与真实调用无参关）。
    """
    if not action_ir_bind_enabled():
        return None
    if not isinstance(args, dict):
        args = {}
    try:
        meta: Dict[str, Any] = {}
        meta_fn = getattr(registry, "metadata", None)
        if callable(meta_fn):
            resolved_meta = meta_fn(tool_name)
            if isinstance(resolved_meta, dict):
                meta = resolved_meta
        plan = project_tool_call_to_plan(tool_name, args, meta)
        compilation = compile_actions(
            plan, resolver=RegistryToolResolver(registry))
    except Exception:  # noqa: BLE001 — 投影/编译故障绝不阻断调度面
        logger.debug("[action-ir] bind skipped for %s", tool_name, exc_info=True)
        return None
    kind = plan.actions[0].kind if plan.actions else "inspect"
    blocking = compilation.blocking_findings()
    record_usage(
        "compile_blocked" if blocking else "direct_routed", kind)
    evidence = {
        "policy_version": "gis_action_ir_bind.v1",
        "plan_id": plan.plan_id[:64],
        "compile_id": compilation.compile_id,
        "kind": kind,
        "status": compilation.status,
        "finding_codes": [f.code for f in blocking[:4]],
        "args_projected": bool(args_projected),
    }
    denied = bool(blocking) and action_ir_strict()
    denial_text = ""
    if denied:
        record_usage("compile_hint", kind)
        hints = "；".join(
            f.detail for f in blocking[:3]) if blocking else ""
        denial_text = (
            f"[Action IR 拦截] 工具 {tool_name} 的调用未通过编译期 contract "
            f"validation（{evidence['finding_codes']}）。{hints} "
            "请改用建议的替代工具或修正声明后重试。")
    return ActionIRBind(
        plan_id=plan.plan_id,
        compile_id=compilation.compile_id,
        kind=kind,
        status=compilation.status,
        evidence=evidence,
        compilation=compilation,
        denied=denied,
        denial_text=denial_text,
    )


class GISActionService:
    """无状态门面：project / compile / diff / replan / execute。"""

    def compile(
        self, plan: GISActionPlan, *, resolver: ToolResolver,
    ) -> ActionPlanCompilation:
        return compile_actions(plan, resolver=resolver)

    def diff(self, before: GISActionPlan, after: GISActionPlan) -> ActionPlanDiff:
        return diff_plans(before, after)

    def describe(self, plan: GISActionPlan) -> str:
        return describe_plan(plan)

    def replan(
        self,
        base: GISActionPlan,
        actions: Sequence[GISAction],
    ) -> GISActionPlan:
        """基于上一代计划的修订（revision+1 + supersedes；内容寻址新 id）。"""
        body = {
            "plan_version": ACTION_IR_VERSION,
            "revision": base.revision + 1,
            "origin": base.origin,
            "upstream_fingerprint": base.upstream_fingerprint,
            "actions": [a.model_dump() for a in actions],
        }
        return GISActionPlan(
            plan_id=compute_plan_id(body),
            revision=base.revision + 1,
            supersedes=base.plan_id,
            origin=base.origin,
            actions=list(actions),
            upstream_fingerprint=base.upstream_fingerprint,
        )

    def executor(
        self,
        *,
        registry: Any,
        session_id: str = "",
        state_view: Optional[Callable[[], Any]] = None,
        ref_alive: Optional[Callable[[str], Any]] = None,
        args_provider: Optional[Callable[[Any], Dict[str, Any]]] = None,
        compensation_args_provider: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> ActionPlanExecutor:
        return ActionPlanExecutor(
            registry=registry, session_id=session_id,
            state_view=state_view, ref_alive=ref_alive,
            args_provider=args_provider,
            compensation_args_provider=compensation_args_provider,
        )

    async def execute(
        self,
        compilation: ActionPlanCompilation,
        *,
        registry: Any,
        session_id: str = "",
        dry_run: bool = False,
    ) -> PlanRunReceipt:
        executor = self.executor(registry=registry, session_id=session_id)
        return await executor.run(compilation, dry_run=dry_run)

    def usage(self) -> Dict[str, Dict[str, int]]:
        """legacy vs plan 路由收敛指标（进程内有界快照）。"""
        return usage_snapshot()


gis_action_service = GISActionService()
