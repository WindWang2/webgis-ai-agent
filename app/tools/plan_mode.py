"""Plan Mode 工具入口（薄包装层）。

把 app/services/plan_mode 的 propose / execute / inspect 能力暴露给 LLM。
真正的存储、校验、执行逻辑放在 service 层便于独立单测。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services import plan_mode as plan_svc
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_plan_mode_tools(registry: ToolRegistry):
    """注册 Plan Mode 工具：propose_plan / execute_plan / get_plan_status。"""

    @registry.tool(
        name="propose_plan",
        tier=1,
        description=(
            "Plan Mode：提交一个结构化多步分析计划，**不立即执行**，仅返回 plan_id 与可读摘要。\n"
            "适合场景：(1) 用户请求需要 ≥3 个工具串联（缓冲→裁剪→热点→着色）；"
            "(2) 计划里包含破坏性工具（create_new_skill / what_if_simulate / spatial_reasoning）；"
            "(3) 用户明确说『先告诉我步骤』『先列计划』；"
            "(4) 涉及高成本调用（大范围 RS、跨省 OSM 抓取）— 在花费前让用户预览。\n"
            "**用法规约**：调用本工具后必须把计划摘要呈现给用户、等用户确认后才能调 execute_plan。\n"
            "**步骤间引用**：args 里可以写 `${stepId}` 或 `${stepId.data.bbox}` 引用前置步骤输出。"
            "依赖关系会从占位符自动推断，也可以显式 depends_on 增强可读性。\n"
            "**约束**：步骤数 1~20；工具名必须是已注册工具；不能有环；不允许自我引用。"
        ),
        args_model=plan_svc.PlanProposal,
    )
    async def propose_plan(
        title: str,
        steps: list[dict],
        summary: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "propose_plan 必须在会话上下文中调用 (session_id 缺失)",
            }

        try:
            plan = plan_svc.PlanProposal(title=title, summary=summary, steps=steps)
        except Exception as e:
            return {"success": False, "code": "VALIDATION_ERROR", "message": str(e)}

        # 校验：工具名 + DAG
        err = plan_svc.validate_plan(plan, known_tools=set(registry.list_tools()))
        if err:
            return {"success": False, "code": "VALIDATION_ERROR", "message": err}

        plan_id = await plan_svc.store_plan(session_id, plan)

        # 标记包含破坏性工具的步骤，便于 UI / LLM 提示用户
        meta_all = registry.all_metadata()
        destructive_steps = [
            s.id for s in plan.steps if meta_all.get(s.tool, {}).get("tier") == 3
        ]

        return {
            "success": True,
            "plan_id": plan_id,
            "title": plan.title,
            "summary": plan.summary,
            "step_count": len(plan.steps),
            "destructive_steps": destructive_steps,
            "steps_preview": [
                {
                    "id": s.id,
                    "tool": s.tool,
                    "purpose": s.purpose,
                    "destructive": meta_all.get(s.tool, {}).get("tier", 1) == 3,
                }
                for s in plan.steps
            ],
            "next_action": (
                "请把以上计划摘要展示给用户，**等待用户明确确认**后再调用 "
                f"execute_plan(plan_id='{plan_id}')。"
            ),
        }

    @registry.tool(
        name="execute_plan",
        tier=1,
        description=(
            "执行由 propose_plan 创建的、**用户已明确确认**的计划。\n"
            "按拓扑顺序逐步运行，自动解析 ${stepId} 占位符。\n"
            "任一步失败立即中止并返回累计已执行步骤的结果。\n"
            "**调用纪律**：只有当用户在最近一轮明确回复『好/可以/执行吧』类肯定后才调用。"
            "如果计划里 destructive_steps 非空，必须额外确认。"
        ),
        param_descriptions={
            "plan_id": "由 propose_plan 返回的 plan_id（形如 ref:plan-xxxxxxxxxxxxxxxx）",
        },
    )
    async def execute_plan(plan_id: str, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "execute_plan 必须在会话上下文中调用 (session_id 缺失)",
            }
        return await plan_svc.execute_plan_async(session_id, plan_id, registry)

    @registry.tool(
        name="get_plan_status",
        tier=1,
        description=(
            "查询一个已提交计划的当前状态：pending(待审) / running / completed / failed / cancelled。"
            "用于在长时计划执行后回看哪一步失败 / 检查计划是否已经跑过避免重复执行。"
        ),
        param_descriptions={"plan_id": "propose_plan 返回的 plan_id"},
    )
    async def get_plan_status(plan_id: str, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "get_plan_status 必须在会话上下文中调用",
            }
        plan_data = await plan_svc.load_plan(session_id, plan_id)
        if plan_data is None:
            return {"success": False, "code": "NOT_FOUND", "message": f"plan {plan_id} 不存在或已过期"}
        return {
            "success": True,
            "plan_id": plan_id,
            "title": plan_data.get("title"),
            "summary": plan_data.get("summary"),
            "status": plan_data.get("__status__", "unknown"),
            "step_count": len(plan_data.get("steps", [])),
            "failed_step": plan_data.get("__failed_step__"),
            "error": plan_data.get("__error__"),
        }
