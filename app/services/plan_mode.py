"""Plan Mode：多步分析计划的提交、校验、执行。

设计要点（对应 Claude Code 的 ExitPlanMode 模式）：
- LLM 通过 propose_plan 工具提交一份结构化 DAG（步骤 + 工具 + 依赖 + 参数占位符）；
  结构化输出强制 LLM 一次性把全局规划想清楚，避免逐步贪心选择导致的死胡同。
- 计划落到 session_data_manager（以 prefix='plan' 存为 ref:plan-xxx），由 plan_id 复用。
- execute_plan 按拓扑顺序逐步 dispatch；步骤间用 `${stepId}` / `${stepId.path.to.field}`
  占位符引用前一步结果，由本模块的解析器替换为实际对象。
- 任一步失败立刻中止，返回累计已执行步骤结果 + 失败步骤信息，让上层 LLM 自愈。

破坏性工具（tier 3：create_new_skill / what_if_simulate / spatial_reasoning）
出现在计划里时会被标记 destructive=True，工具描述里要求 LLM 必须先得到用户
对计划的明确确认才能调 execute_plan。这是审计 P1-A8 (prompt injection)
的额外保护层。
"""
from __future__ import annotations

import logging
import re
import uuid
from collections import deque
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ────────────────────────────── 数据模型 ──────────────────────────────


class PlanStep(BaseModel):
    """计划中的单个步骤。"""

    id: str = Field(
        ...,
        description="步骤短 ID（如 s1, s2, get_boundary），用于其他步骤的占位符引用",
        min_length=1,
        max_length=32,
    )
    tool: str = Field(..., description="要调用的已注册工具名")
    args: dict = Field(
        default_factory=dict,
        description=(
            "传给该工具的参数字典。值里可以含 ${stepId} 或 ${stepId.path.to.field} "
            "占位符引用前置步骤的输出"
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="该步依赖的前置步骤 ID 列表（不写则从 args 的 ${} 占位符自动推断）",
    )
    purpose: str = Field("", description="该步的自然语言意图，用于在审核界面解释为什么需要这步")


class PlanProposal(BaseModel):
    """完整的计划提案。"""

    title: str = Field(..., min_length=1, max_length=200, description="计划标题")
    summary: str = Field("", description="计划总体摘要")
    steps: list[PlanStep] = Field(..., min_length=1, max_length=20)


# ───────────────────────── 校验：DAG + 工具名 ─────────────────────────


_REF_PATTERN = re.compile(r"\$\{([a-zA-Z_][\w]*?)(?:\.([\w\.]+))?\}")


def _extract_refs(value: Any) -> set[str]:
    """递归从 args 值里抓出所有 ${stepId...} 引用的 stepId 集合。"""
    refs: set[str] = set()
    if isinstance(value, str):
        for m in _REF_PATTERN.finditer(value):
            refs.add(m.group(1))
    elif isinstance(value, dict):
        for v in value.values():
            refs.update(_extract_refs(v))
    elif isinstance(value, list):
        for v in value:
            refs.update(_extract_refs(v))
    return refs


def validate_plan(plan: PlanProposal, known_tools: set[str]) -> Optional[str]:
    """返回错误信息字符串；通过校验返回 None。"""
    seen: dict[str, PlanStep] = {}
    for step in plan.steps:
        if step.id in seen:
            return f"步骤 ID 重复: {step.id}"
        if step.tool not in known_tools:
            return f"步骤 {step.id!r} 引用了未知工具: {step.tool!r}"
        # 自动从 args 推断依赖（如果用户没显式写）
        inferred = _extract_refs(step.args)
        if step.id in inferred:
            return f"步骤 {step.id!r} 不能自我引用"
        for ref in inferred:
            if ref not in seen and ref != step.id:
                return (
                    f"步骤 {step.id!r} 的 args 引用了 {ref!r}，"
                    f"但 {ref!r} 在计划中不存在或顺序在后"
                )
        for dep in step.depends_on:
            if dep not in seen:
                return (
                    f"步骤 {step.id!r} 显式依赖 {dep!r}，但 {dep!r} 不存在或顺序在后"
                )
        seen[step.id] = step

    # 第二轮：拓扑排序校验无环（虽然单次扫描已保证，但显式跑一次更稳）
    order = _topological_order(plan)
    if order is None:
        return "依赖图含环，无法拓扑排序"

    return None


def _topological_order(plan: PlanProposal) -> Optional[list[str]]:
    """返回拓扑序的 step id 列表；存在环时返回 None。"""
    in_degree: dict[str, int] = {s.id: 0 for s in plan.steps}
    edges: dict[str, list[str]] = {s.id: [] for s in plan.steps}
    for step in plan.steps:
        deps = set(step.depends_on) | _extract_refs(step.args)
        deps.discard(step.id)
        for dep in deps:
            if dep in in_degree:
                edges[dep].append(step.id)
                in_degree[step.id] += 1

    queue: deque[str] = deque([sid for sid, d in in_degree.items() if d == 0])
    order: list[str] = []
    while queue:
        sid = queue.popleft()
        order.append(sid)
        for nxt in edges[sid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(plan.steps) else None


# ──────────────────────── 引用解析：${...} 替换 ────────────────────────


def _resolve_path(obj: Any, path: str) -> Any:
    """obj.a.b.c 风格路径解析。中途任意一步不存在则返回 None。"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def resolve_refs(value: Any, step_results: dict[str, Any]) -> Any:
    """递归把 ${stepId} 或 ${stepId.path} 占位符替换为实际值。

    单一占位符 -> 直接返回引用的对象（保留 dict/list 结构）；
    嵌在字符串里 -> 字符串拼接（按 str(value)）。
    """
    if isinstance(value, str):
        m_full = _REF_PATTERN.fullmatch(value)
        if m_full:
            sid = m_full.group(1)
            path = m_full.group(2)
            base = step_results.get(sid)
            return _resolve_path(base, path) if path else base

        # 嵌入式：替换为字符串
        def _sub(m: re.Match[str]) -> str:
            sid = m.group(1)
            path = m.group(2)
            base = step_results.get(sid)
            resolved = _resolve_path(base, path) if path else base
            return "" if resolved is None else str(resolved)

        return _REF_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_refs(v, step_results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, step_results) for v in value]
    return value


# ─────────────────────── 计划存储：session_data 后端 ───────────────────────


async def store_plan(session_id: str, plan: PlanProposal) -> str:
    """把计划落进 session_data_manager，返回 plan_id (即 ref:plan-xxxxxx)。"""
    payload = plan.model_dump()
    payload["__kind__"] = "plan_proposal"
    payload["__status__"] = "pending"  # pending | running | completed | failed | cancelled
    return await session_data_manager.store(session_id, payload, prefix="plan")


async def load_plan(session_id: str, plan_id: str) -> Optional[dict]:
    """根据 plan_id 取出（含执行状态字段）。"""
    return await session_data_manager.get(session_id, plan_id)


async def update_plan_status(session_id: str, plan_id: str, **updates: Any) -> None:
    """更新计划的状态字段并写回存储。

    Redis 后端 get() 返回反序列化副本，原地 update 不会持久化，
    因此必须显式 store 写回。
    """
    plan_data = await load_plan(session_id, plan_id)
    if plan_data is None:
        logger.warning(f"update_plan_status: plan {plan_id} 不存在")
        return
    plan_data.update(updates)
    await session_data_manager.store(session_id, plan_data, prefix="plan")


# ─────────────────────────────── 执行引擎 ───────────────────────────────


async def execute_plan_async(
    session_id: str,
    plan_id: str,
    registry: ToolRegistry,
) -> dict:
    """按拓扑顺序执行计划；任一步失败立即中止。

    返回汇总 {plan_id, status, executed, results, failed_step, error}。
    """
    plan_data = await load_plan(session_id, plan_id)
    if plan_data is None:
        return {"success": False, "error": f"找不到 plan_id={plan_id}"}
    if plan_data.get("__status__") == "running":
        return {"success": False, "error": f"plan {plan_id} 已在执行中"}

    # 还原 Pydantic 模型用于拓扑排序
    plan = PlanProposal.model_validate({k: v for k, v in plan_data.items() if not k.startswith("__")})

    order = _topological_order(plan)
    if order is None:
        await update_plan_status(session_id, plan_id, __status__="failed", __error__="cycle")
        return {"success": False, "error": "依赖图含环"}

    step_by_id = {s.id: s for s in plan.steps}
    step_results: dict[str, Any] = {}
    await update_plan_status(session_id, plan_id, __status__="running")

    for sid in order:
        step = step_by_id[sid]
        try:
            resolved_args = resolve_refs(step.args, step_results)
            if not isinstance(resolved_args, dict):
                await update_plan_status(
                    session_id, plan_id,
                    __status__="failed",
                    __failed_step__=sid,
                    __error__=f"args 解析后不是 dict: {type(resolved_args).__name__}",
                )
                return {
                    "success": False,
                    "plan_id": plan_id,
                    "failed_step": sid,
                    "error": f"步骤 {sid!r} args 解析后不是 dict",
                    "executed": list(step_results.keys()),
                    "results": step_results,
                }

            logger.info(f"[PlanMode] running step {sid} -> {step.tool}")
            result = await registry.dispatch(step.tool, resolved_args, session_id=session_id)

            # 工具返回 success=False（V3.x Exception As Thought 包装）也视为失败
            if isinstance(result, dict) and result.get("success") is False:
                await update_plan_status(
                    session_id, plan_id,
                    __status__="failed",
                    __failed_step__=sid,
                    __error__=result.get("message") or result.get("error", "tool failed"),
                )
                return {
                    "success": False,
                    "plan_id": plan_id,
                    "failed_step": sid,
                    "tool": step.tool,
                    "error": result.get("message") or result.get("error"),
                    "executed": list(step_results.keys()),
                    "results": step_results,
                    "last_result": result,
                }

            step_results[sid] = result
        except Exception as e:
            logger.exception(f"[PlanMode] step {sid} raised")
            await update_plan_status(
                session_id, plan_id,
                __status__="failed",
                __failed_step__=sid,
                __error__=str(e),
            )
            return {
                "success": False,
                "plan_id": plan_id,
                "failed_step": sid,
                "tool": step.tool,
                "error": str(e),
                "executed": list(step_results.keys()),
                "results": step_results,
            }

    await update_plan_status(
        session_id, plan_id,
        __status__="completed",
        __step_results__=step_results,
    )
    return {
        "success": True,
        "plan_id": plan_id,
        "status": "completed",
        "executed": list(step_results.keys()),
        "results": step_results,
    }
