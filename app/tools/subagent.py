"""spawn_subagent — LLM-facing 入口，调起子代理执行隔离子任务。"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.subagent import SubagentDispatcher
from app.services.subagent_roles import SUBAGENT_ROLES, get_subagent_role
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _role_budget_lines() -> str:
    """从注册表生成有界的角色预算表（工具描述用；逐角色一行）。"""
    lines = []
    for name in sorted(SUBAGENT_ROLES):
        r = SUBAGENT_ROLES[name]
        lines.append(
            f"- {name}: 轮次≤{r.max_rounds}，工具≤{r.max_tool_calls}"
            f"（重≤{r.max_heavy_tool_calls}），墙钟≤{r.max_wall_time_s:.0f}s，"
            f"{'可写会话' if r.allow_mutation else '只读'}"
        )
    return "\n".join(lines)


def register_subagent_tools(registry: ToolRegistry):
    """注册 spawn_subagent 工具。

    设为 tier=2 domains=["meta", "what_if"] —— 不是默认 catalog 工具，需要用户或 plan
    显式提及"批量"、"子任务"、"委派" 等关键词才载入。这是有意为之：subagent
    本身有 LLM 调用成本，应当只在主任务真的需要时才暴露。

    ADR-0104 决策 7：新增可选 role 参数（专家团队角色）。未知/非法角色
    fail-closed（诚实 VALIDATION_ERROR，不回退默认行为）；不传 role 时
    行为与本工具历史版本一致（无角色的 adhoc 子代理）。
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
            "role（可选）：专家角色。角色约束只会收紧调用方参数（域取交集、预算取更严者），"
            "不会放宽。未知角色会被拒绝。\n"
            "可用 role 及预算：\n"
            + _role_budget_lines() + "\n"
            "重要：subagent 看不到 propose_plan/spawn_subagent/execute_plan 这几个元工具，"
            "防止递归与计划嵌套。"
        ),
        param_descriptions={
            "task": "用一段自然语言准确描述子任务的目标、输入、期望输出格式。子代理只看这一段，要写充分。",
            "role": "可选：专家角色名（如 spatial_scientist/algorithm_reviewer/map_observer/"
                    "result_verifier/doc_crosschecker/planner/corpus_worker）。不传 = 无角色的通用子代理。",
            "domains": "可选：要给子代理开放的 tier-2 工具域列表，如 ['network','chinese']。默认空表示只给 tier-1 基础工具。",
            "extra_tools": "可选：强制纳入工具白名单（按名字），无视 tier/domain。用于强制带上某个具体工具。",
            "max_rounds": "可选：子代理最大对话轮次，默认 10（比主 30 短）。复杂子任务可以调高。",
        },
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=["子代理", "委派", "批量", "subagent", "子任务", "隔离子任务"],
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=["timeout", "invalid_args"],
    )
    async def spawn_subagent(
        task: str,
        role: Optional[str] = None,
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
        # ADR-0104 决策 7：role 校验 fail-closed —— 未知/非法角色诚实报错
        # （不静默回退 adhoc，避免「请求了专家却拿到无约束代理」的假成功）。
        if role is not None:
            if not isinstance(role, str) or not role.strip():
                return {
                    "success": False,
                    "code": "VALIDATION_ERROR",
                    "message": "role 必须是非空字符串",
                }
            try:
                get_subagent_role(role)
            except ValueError as e:
                return {
                    "success": False,
                    "code": "VALIDATION_ERROR",
                    "message": str(e),
                }

        dispatcher = SubagentDispatcher(registry, parent_session_id=session_id)
        result = await dispatcher.run(
            task=task,
            domains=domains,
            extra_tools=extra_tools,
            max_rounds=max_rounds,
            role=role,
        )
        return result.to_dict()
