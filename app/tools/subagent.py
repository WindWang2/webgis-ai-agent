"""spawn_subagent — LLM-facing 入口，调起子代理执行隔离子任务。"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.subagent import SubagentDispatcher
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_subagent_tools(registry: ToolRegistry):
    """注册 spawn_subagent 工具。

    设为 tier=2 domains=["meta"] —— 不是默认 catalog 工具，需要用户或 plan
    显式提及"批量"、"子任务"、"委派" 等关键词才载入。这是有意为之：subagent
    本身有 LLM 调用成本，应当只在主任务真的需要时才暴露。
    """

    @registry.tool(
        name="spawn_subagent",
        tier=2,
        domains=["meta", "what_if"],
        description=(
            "委派一个隔离的子代理执行子任务。子代理拥有自己的工具子集和短轮次预算，"
            "完成后只把简洁摘要 + 新增 ref 列表回传，**主上下文保持精简**。\n"
            "何时用：(1) 主任务包含一段可独立完成的子工作（『先把这 50 个 POI 一个个找最近地铁站』）"
            "(2) 同一类批处理需要重复推理（不同区县做同一分析）"
            "(3) 长任务（≥10 轮）容易把主上下文塞满。\n"
            "何时不用：(1) 一两步就能完成 → 直接调工具；"
            "(2) 子任务依赖大量主任务上下文 → 上下文割裂反而错失关键信息；"
            "(3) 已经在 propose_plan 流程里 → 不要再嵌套。\n"
            "重要：subagent 看不到 propose_plan/spawn_subagent/execute_plan 这几个元工具，"
            "防止递归与计划嵌套。"
        ),
        param_descriptions={
            "task": "用一段自然语言准确描述子任务的目标、输入、期望输出格式。子代理只看这一段，要写充分。",
            "domains": "可选：要给子代理开放的 tier-2 工具域列表，如 ['network','chinese']。默认空表示只给 tier-1 基础工具。",
            "extra_tools": "可选：强制纳入工具白名单（按名字），无视 tier/domain。用于强制带上某个具体工具。",
            "max_rounds": "可选：子代理最大对话轮次，默认 10（比主 30 短）。复杂子任务可以调高。",
        },
    )
    async def spawn_subagent(
        task: str,
        domains: Optional[list[str]] = None,
        extra_tools: Optional[list[str]] = None,
        max_rounds: int = 10,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "spawn_subagent 必须在会话上下文中调用 (session_id 缺失)",
            }
        if not task or not task.strip():
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "task 不能为空",
            }
        if max_rounds < 1 or max_rounds > 30:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": "max_rounds 必须在 1-30 之间",
            }

        dispatcher = SubagentDispatcher(registry, parent_session_id=session_id)
        result = await dispatcher.run(
            task=task,
            domains=domains,
            extra_tools=extra_tools,
            max_rounds=max_rounds,
        )
        return result.to_dict()
