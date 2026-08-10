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

import asyncio
import logging
import re
from collections import deque
from typing import Any, Optional

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

    必须写回到 SAME plan_id（即原始 ref_id），否则持有 plan_id 的调用方
    （get_plan_status / load_plan）仍会读到旧 payload。

    Redis 后端 get() 返回反序列化副本，原地 update 不会持久化；且 store() 会
    生成新的 ref_id 而非原地覆盖。所以这里用 overwrite() 把更新后的 dict 写回
    原始 key。内存后端虽然返回的是同一个对象（mutation 会“偶然”可见），但同样
    走 overwrite 以保持两个后端行为一致。
    """
    plan_data = await load_plan(session_id, plan_id)
    if plan_data is None:
        logger.warning(f"update_plan_status: plan {plan_id} 不存在")
        return
    plan_data.update(updates)
    overwrite = getattr(session_data_manager, "overwrite", None)
    if overwrite is not None:
        await overwrite(session_id, plan_id, plan_data)
    else:
        # Backends without overwrite(): fall back to store (in-memory only path).
        await session_data_manager.store(session_id, plan_data, prefix="plan")


# ─────────────────────────────── 执行引擎 ───────────────────────────────


async def execute_plan_async(
    session_id: str,
    plan_id: str,
    registry: ToolRegistry,
) -> dict:
    """按拓扑顺序执行计划；任一步失败立即中止。

    性能 (Phase 8)：同一波次内无依赖关系的步骤并发 dispatch
    (asyncio.as_completed)——例如 3 个独立分析工具一轮内并行跑，
    总耗时从串行之和降为最慢者。波次间保持严格依赖顺序；失败立即
    cancel 同波次剩余任务。

    语义差异（与纯串行相比）：同一波次中与失败步骤无依赖关系的兄弟
    步骤可能已完成（结果计入 executed）——"立即中止" 指不再启动新的
    波次，已派发的任务允许收尾。

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

    # 构建邻接表 / 入度，用于波次调度（与 _topological_order 同一依赖口径）
    topo_idx = {sid: i for i, sid in enumerate(order)}
    in_degree: dict[str, int] = {s.id: 0 for s in plan.steps}
    edges: dict[str, list[str]] = {s.id: [] for s in plan.steps}
    for step in plan.steps:
        deps = set(step.depends_on) | _extract_refs(step.args)
        deps.discard(step.id)
        for dep in deps:
            if dep in in_degree:
                edges[dep].append(step.id)
                in_degree[step.id] += 1

    step_by_id = {s.id: s for s in plan.steps}
    step_results: dict[str, Any] = {}
    await update_plan_status(session_id, plan_id, __status__="running")

    def _ordered_executed() -> list[str]:
        """已执行步骤按拓扑序输出（确定性，不依赖并发完成顺序）。"""
        return sorted(step_results.keys(), key=topo_idx.__getitem__)

    def _fail(sid: str, error: str, last_result: Optional[Any] = None) -> dict:
        # 失败中止：不再启动新波次；已完成的兄弟步骤保留在 executed 中。
        ret: dict = {
            "success": False,
            "plan_id": plan_id,
            "failed_step": sid,
            "tool": step_by_id[sid].tool,
            "error": error,
            "executed": _ordered_executed(),
            "results": step_results,
        }
        if last_result is not None:
            ret["last_result"] = last_result
        return ret

    ready: list[str] = [sid for sid, d in in_degree.items() if d == 0]

    while ready:
        # 同一波次：入度均为 0 的独立步骤，按拓扑序确定排布
        wave = sorted(ready, key=topo_idx.__getitem__)
        ready = []

        # 波次内 args 解析：只依赖已完成步骤，解析失败即中止本波次
        resolved_args: dict[str, dict] = {}
        for sid in wave:
            step = step_by_id[sid]
            try:
                r = resolve_refs(step.args, step_results)
            except Exception as e:  # noqa: BLE001
                await update_plan_status(
                    session_id, plan_id,
                    __status__="failed",
                    __failed_step__=sid,
                    __error__=f"args 解析异常: {e}",
                )
                return _fail(sid, f"步骤 {sid!r} args 解析异常: {e}")
            if not isinstance(r, dict):
                await update_plan_status(
                    session_id, plan_id,
                    __status__="failed",
                    __failed_step__=sid,
                    __error__=f"args 解析后不是 dict: {type(r).__name__}",
                )
                return _fail(sid, f"步骤 {sid!r} args 解析后不是 dict")

            resolved_args[sid] = r

        # 并发 dispatch 整个波次。注意：Python 3.12 的 asyncio.as_completed
        # yield 的是内部 _wait_for_one 协程而非原始 Task，无法映射回 sid；
        # 因此改用 asyncio.wait(FIRST_COMPLETED)，它返回原始 Task 对象。
        tasks = {
            sid: asyncio.create_task(
                registry.dispatch(
                    step_by_id[sid].tool, resolved_args[sid], session_id=session_id
                )
            )
            for sid in wave
        }
        task_to_sid = {t: sid for sid, t in tasks.items()}

        wave_successes: dict[str, Any] = {}
        failure: Optional[tuple[str, str, Optional[Any]]] = None  # (sid, error, last_result)
        pending: set[asyncio.Task] = set(tasks.values())
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                sid = task_to_sid[t]
                try:
                    result = t.result()
                except asyncio.CancelledError:
                    continue  # 外部取消（如引擎关闭），忽略该任务
                except Exception as e:
                    logger.exception(f"[PlanMode] step {sid} raised")
                    failure = (sid, str(e), None)
                    break
                # 工具返回 success=False（V3.x Exception As Thought 包装）也视为失败
                if isinstance(result, dict) and result.get("success") is False:
                    failure = (sid, result.get("message") or result.get("error", "tool failed"), result)
                    break
                wave_successes[sid] = result
            if failure is not None:
                break

        # 失败处理：按本函数契约（见 docstring / line 252），同波次已 dispatch 的
        # 兄弟步骤应跑到完成、其成功结果计入 executed；"立即中止"指不再启动 *新波次*。
        # 此前这里 cancel 了 pending 兄弟，在较慢的 runner 上 s2 快速失败会 race 掉
        # 即将完成的 s1，导致 flaky executed=[]（master CI 间歇性失败）。
        if failure is not None and pending:
            done_siblings, _ = await asyncio.wait(pending)  # 不 cancel，让兄弟完成
            for t in done_siblings:
                sid_sib = task_to_sid.get(t)
                if sid_sib is None:
                    continue
                try:
                    r_sib = t.result()
                except Exception:
                    continue  # 兄弟也失败；已记录首个 failure，忽略
                if isinstance(r_sib, dict) and r_sib.get("success") is not False:
                    wave_successes[sid_sib] = r_sib

        # 按拓扑序提交成功结果（确定性）
        for sid in wave:
            if sid in wave_successes:
                step_results[sid] = wave_successes[sid]

        if failure is not None:
            sid, err, last_result = failure
            await update_plan_status(
                session_id, plan_id,
                __status__="failed",
                __failed_step__=sid,
                __error__=err,
            )
            return _fail(sid, err, last_result)

        # 波次完成 → 推进依赖图，解锁下一波次
        for sid in wave:
            for nxt in edges[sid]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    ready.append(nxt)

    await update_plan_status(
        session_id, plan_id,
        __status__="completed",
        __step_results__=step_results,
    )
    return {
        "success": True,
        "plan_id": plan_id,
        "status": "completed",
        "executed": _ordered_executed(),
        "results": step_results,
    }
