"""规划阶段 Adapter - 委托给 deep AgentPlanOrchestrator.

向后兼容 Adapter，保留所有原有的导出的函数签名与符号。
"""
from __future__ import annotations

from typing import Optional

from app.services.chat.llm_client import LLMConfig, call_llm
from app.services.chat.plan_orchestrator import (
    plan_orchestrator,
    Plan,
    PlanStep,
    parse_plan,
    should_plan,
    VALID_DOMAINS,
    PLANNER_PROMPT,
)


def get_plan(session_id: str) -> Optional[Plan]:
    return plan_orchestrator.get_plan(session_id)


def set_plan(session_id: str, plan: Plan) -> None:
    plan_orchestrator.set_plan(session_id, plan)


def clear_plan(session_id: str) -> None:
    plan_orchestrator.clear_plan(session_id)


def mark_step_done(session_id: str, tool_name: str, registry, tool_catalog=None) -> Optional[int]:
    return plan_orchestrator.advance_step(session_id, tool_name, registry, tool_catalog)


async def make_plan(
    cfg: LLMConfig,
    session_id: str,
    user_message: str,
    env_summary: str,
) -> Optional[Plan]:
    return await plan_orchestrator.make_plan(cfg, session_id, user_message, env_summary)


__all__ = [
    "Plan",
    "PlanStep",
    "parse_plan",
    "should_plan",
    "get_plan",
    "set_plan",
    "clear_plan",
    "mark_step_done",
    "make_plan",
    "call_llm",
    "VALID_DOMAINS",
    "PLANNER_PROMPT",
]
