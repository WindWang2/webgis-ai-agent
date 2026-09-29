"""GISAction 工具面（H10 / ADR-0217，只读）。

``webgis_action_plan``：把 LLM 声明的 typed 动作序列先编译为
GISActionPlan compilation 并做 contract validation —— **dry-run 面**，
零执行、零状态变更。LLM 不再"盲发工具调用等报错"，而是先看编译器
裁决（缺工具/副作用不一致/依赖环/弃用）再行动；执行仍走既有调度面
（ToolRegistry/ToolDispatch 唯一拥有者，本工具绝不自调工具）。

``operation="usage"``：legacy vs plan 路由收敛指标快照（进程内有界）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool, ToolExecutionPolicy

logger = logging.getLogger(__name__)

_MAX_DECLARED_ACTIONS = 64


class ActionPlanArgs(BaseModel):
    session_id: Optional[str] = Field(None, max_length=128,
                                      description="目标会话；缺省用当前会话上下文")
    operation: str = Field("dry_run", max_length=16,
                           description="dry_run（编译校验，零执行）| usage（路由收敛指标）")
    actions: Optional[List[Dict[str, Any]]] = Field(
        None, max_length=_MAX_DECLARED_ACTIONS,
        description=(
            "typed 动作声明：[{kind:'analyze', tool:'hotspot_analysis', "
            "params:{...}, depends_on:['act-…']}]；kind ∈ data_acquire/inspect/"
            "transform/analyze/cartograph/mutate_presentation/export/observe；"
            "tool 与 capability 至少给一个"))


def register_gis_action_tools(registry: ToolRegistry):
    """注册 Action IR dry-run 工具（additive；只读面）。"""

    @tool(
        registry,
        name="webgis_action_plan",
        tier=2,
        domains=["cartography"],
        description=(
            "把声明的 GIS 动作序列编译为执行计划并校验（dry-run，不执行）。"
            "\n何时用：(1) 一批动作相互依赖（先分析→再制图→再导出）想先验证可行性；"
            "(2) 收到弃用/副作用告警想看替代工具；"
            "(3) 查看 legacy/plan 路由收敛指标。"
            "\n何时不用：直接执行单个工具 —— 直接调用即可，本工具不执行任何动作。"
            "\n关键约束：动作只收小参数 token，数据一律用 ref 引用。"
        ),
        args_model=ActionPlanArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="pure",
        deterministic=True,
        idempotent=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("执行计划", "Action IR", "dry-run", "校验", "plan"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args",),
    )
    async def webgis_action_plan(
        session_id: Optional[str] = None,
        operation: str = "dry_run",
        actions: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        from app.lib.gis.action_ir import GISAction, GISActionPlan, compute_plan_id
        from app.services.gis_action.legacy_adapter import usage_snapshot
        from app.services.gis_action.service import (
            RegistryToolResolver,
            gis_action_service,
        )

        if operation == "usage":
            return {
                "success": True,
                "operation": "usage",
                "usage": usage_snapshot(),
                "summary": "GISAction 路由收敛指标（direct vs plan，进程内有界）",
            }
        if operation != "dry_run":
            return {
                "success": False,
                "code": "INVALID_OPERATION",
                "message": f"未知 operation '{operation}'；可用：dry_run | usage",
                "correction_hint": "dry_run 编译校验（零执行）；usage 查看收敛指标。",
            }
        if not actions:
            return {
                "success": False,
                "code": "INVALID_ARGS",
                "message": "dry_run 需要 actions 声明（typed 动作列表）",
                "correction_hint": (
                    "示例：[{kind:'analyze', tool:'hotspot_analysis', "
                    "params:{...}}]；tool/capability 至少一个。"),
            }
        parsed: List[GISAction] = []
        for i, raw in enumerate(actions[:_MAX_DECLARED_ACTIONS]):
            if not isinstance(raw, dict):
                return {
                    "success": False, "code": "INVALID_ARGS",
                    "message": f"actions[{i}] 不是对象",
                }
            raw = dict(raw)
            raw.setdefault("action_id", f"act-decl-{i + 1:02d}")
            try:
                parsed.append(GISAction(**{
                    k: v for k, v in raw.items()
                    if k in GISAction.model_fields
                }))
            except Exception as exc:
                return {
                    "success": False, "code": "INVALID_ACTION",
                    "message": f"actions[{i}] 校验失败：{exc}",
                    "correction_hint": (
                        "kind 必须在封闭词表内；tool/capability 至少一个；"
                        "params 只收小参数 token（≤16 键）。"),
                }
        body = {
            "plan_version": "1.0.0", "revision": 1,
            "origin": "tool_call",
            "actions": [a.model_dump() for a in parsed],
        }
        plan = GISActionPlan(
            plan_id=compute_plan_id(body), origin="tool_call", actions=parsed)
        compilation = gis_action_service.compile(
            plan, resolver=RegistryToolResolver(registry))
        return {
            "success": not compilation.blocked,
            "operation": "dry_run",
            "plan_id": plan.plan_id,
            "compile_id": compilation.compile_id,
            "status": compilation.status,
            "plan_preview": gis_action_service.describe(plan),
            "steps": [
                {"step": s.step, "kind": s.kind, "tool": s.tool,
                 "client_action_id": s.client_action_id,
                 "side_effect": s.side_effect, "failure": s.failure,
                 "wave": s.wave}
                for s in compilation.steps[:16]
            ],
            "findings": [f.model_dump() for f in compilation.findings[:8]],
            "resource_summary": compilation.resource_summary.model_dump(),
            "reason_codes": list(compilation.reason_codes)[:8],
            "summary": (
                f"计划编译{'通过' if not compilation.blocked else '被阻塞'}："
                f"{len(compilation.steps)} 步可执行；"
                f"{len(compilation.findings)} 条 findings。"),
        }
