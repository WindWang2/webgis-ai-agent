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
import time
from collections import deque
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.services.session_data import session_data_manager
from app.services.planning.deps import MissingRefError, resolve_arg_refs
from app.services.jobs.cancellation import OperationCancelled
from app.services.distributed_lock import session_lock_registry
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# First-terminal-wins status transition rules.
#   * ``completed`` / ``cancelled`` are immutable (except a ``superseded``
#     annotation, which merges into a fresh payload read).
#   * ``failed`` is RESUMABLE but not freely overwritable: the executor may
#     legitimately converge it (failed -> running -> completed/partial), yet a
#     concurrent ``cancelled`` write must NOT flip it (chaos P2) — a cancel
#     that races the failure write loses, same as before.
#   * ``partially_completed`` / running / pending have no restriction.
# Previously ``partially_completed`` and ``failed`` were both fully terminal,
# which silently dropped the resume convergence write: a resumed-and-finished
# plan kept its old status in storage forever while the caller received the
# new terminal.
_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})
_FAILED_RESUME_TARGETS = frozenset(
    {"failed", "running", "completed", "partially_completed", "superseded"}
)


async def _cancel_wave_tasks(wave_tasks: set[asyncio.Task]) -> None:
    """取消并回收当前波次的 pending 任务（F6 收敛前的最后一步）。

    镜像 execution_engine 的 finally 清理模式：外层被取消 / 内部异常时，
    GIS 工具不能在 turn 结束后继续产生副作用，pending 任务必须被取消并
    await 回收。两个 except 子句共用。
    """
    for t in wave_tasks:
        if not t.done():
            t.cancel()
    if wave_tasks:
        await asyncio.gather(*wave_tasks, return_exceptions=True)


# ─────────────────── per-plan 执行锁（P1-C + 跨进程 claim） ───────────────────
# 把 execute_plan 的「status 检查 → running 写入 → 执行」整段按 plan 串行化，
# 堵住 check-then-act TOCTOU 双派发（两个并发 execute_plan 同时通过 running
# 检查后各自 dispatch 一遍）。两层锁：
#   1. 进程内 per-plan asyncio.Lock（本模块，有界、按 (session, plan) 键控）；
#   2. 分布式锁 session_lock_registry.lock(f"plan:{plan_id}")（P3 延后项 #2）——
#      Redis 在时跨进程/跨 pod 原子 claim，Redis 不可用时透明降级为进程内锁
#      （distributed_lock 契约：永不抛未处理异常）。plan_id 全局唯一，无需
#      session 前缀。
_PLAN_EXEC_LOCKS: dict[str, asyncio.Lock] = {}
_PLAN_LOCKS_MAX = 1024

# running 状态超过该秒数视为 crashed（worker 崩溃/被杀），允许 resume。
_RUNNING_STALE_SECONDS = 300


def _get_plan_lock(session_id: str, plan_id: str) -> asyncio.Lock:
    """取该 (session, plan) 的执行锁（进程内、有界）。"""
    key = f"{session_id}\0{plan_id}"
    lock = _PLAN_EXEC_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PLAN_EXEC_LOCKS[key] = lock
        if len(_PLAN_EXEC_LOCKS) > _PLAN_LOCKS_MAX:
            # 淘汰空闲锁（无持有者无等待者），防止长跑进程无限增长。
            idle = [k for k, lock in _PLAN_EXEC_LOCKS.items() if not lock.locked()][: _PLAN_LOCKS_MAX // 4]
            for k in idle:
                _PLAN_EXEC_LOCKS.pop(k, None)
    return lock


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
    # 状态词表：pending | running | partially_completed | completed | failed
    #           | cancelled | superseded
    # 说明：fail-fast 路径失败时仍写 ``failed``（向后兼容 get_plan_status /
    # execute_plan 的既有调用方）；``partially_completed`` 作为可读/可恢复状态
    # 被 execute_plan 接受（与 failed 同样触发 resume 语义）。
    payload["__status__"] = "pending"
    payload["__updated_at__"] = time.time()  # P1-C：stale-running 判定依赖它
    return await session_data_manager.store(session_id, payload, prefix="plan")


async def supersede_active_plans(session_id: str) -> None:
    """design-v3 §4：新计划提出时，把该 session 其他 pending/running 的
    plan-mode 计划标记为 superseded（绝不静默覆盖/并发双活）。

    只扫描 ref:plan-* 条目；scan + get 都是 session_data 的轻量读。
    """
    try:
        refs = await session_data_manager.list_refs(session_id)
    except Exception:  # noqa: BLE001
        return
    for ref in refs:
        if not str(ref).startswith("ref:plan-"):
            continue
        data = await session_data_manager.get(session_id, ref)
        if not isinstance(data, dict):
            continue
        if data.get("__status__") in ("pending", "running"):
            await update_plan_status(session_id, ref, __status__="superseded")


def validate_static_refs(plan: PlanProposal) -> list[str]:
    """Propose-time 静态引用校验（design-v3 §deps）。

    委托 app/services/planning/deps.validate_static_refs（CanonicalStep 形态）。
    与 validate_plan 的重叠检查（未知 id / 前向引用 / 自引用 / 环）在 validate_plan
    中已全部拦截，此调用是补充性第二道闸——返回完整 issue 列表，空列表即通过。
    """
    from app.services.planning.deps import validate_static_refs as _v
    from app.services.planning.models import CanonicalStep
    steps = [
        CanonicalStep(
            id=s.id,
            n=i + 1,
            goal=s.purpose or s.tool,
            tool_family=None,
            tool=s.tool,
            args=s.args,
            depends_on=list(s.depends_on),
        )
        for i, s in enumerate(plan.steps)
    ]
    return _v(steps)


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

    P0-1（数据丢失防御）：overwrite() 返回 False 说明原始 ref 已被逐出（LRU
    容量淘汰 / Redis DATA_TTL 过期）或后端降级——此时**重新 mint** 一个新 ref
    并把旧 plan_id 注册为别名，持有 plan_id 的调用方（load_plan /
    get_plan_status）仍能经别名寻址到最新 payload（镜像 PlanStore.save 的
    resilience，见 app/services/planning/store.py:123-141）。绝不静默丢写。

    每次写回都会自动刷新 ``__updated_at__``（stale-running 判定用），除非
    调用方显式传了该字段。

    首达终态获胜 (P2)：一旦 completed/failed/cancelled，后到的状态写入（包括
    running「复活」）全部忽略。
    """
    plan_data = await load_plan(session_id, plan_id)
    if plan_data is None:
        logger.warning(f"update_plan_status: plan {plan_id} 不存在")
        return
    current_status = plan_data.get("__status__")
    new_status = updates.get("__status__")
    # First-terminal-wins / resume convergence (see _TERMINAL_STATUSES above):
    # completed/cancelled are immutable (a ``superseded`` annotation is the one
    # exemption — it merges into a fresh payload read, so it cannot clobber
    # __step_results__); ``failed`` only accepts executor-resume targets.
    if (
        new_status is not None
        and new_status != current_status
        and (
            (current_status in _TERMINAL_STATUSES and new_status != "superseded")
            or (current_status == "failed" and new_status not in _FAILED_RESUME_TARGETS)
        )
    ):
        logger.warning(
            f"update_plan_status: plan {plan_id} 已处于终态 {current_status}，"
            f"忽略后到的 {new_status} 覆盖（首达终态获胜）",
        )
        return
    updates.setdefault("__updated_at__", time.time())
    plan_data.update(updates)
    overwrite = getattr(session_data_manager, "overwrite", None)
    if overwrite is not None and await overwrite(session_id, plan_id, plan_data):
        return
    # overwrite 失败（ref 被逐出 / 后端降级）→ 重新 mint + 把旧 plan_id 注册为
    # 别名，保证既有调用方仍读到更新后的 payload。set_alias 缺失的后端（测试
    # stub）退化为仅 re-mint（尽力而为）。
    new_ref = await session_data_manager.store(session_id, plan_data, prefix="plan")
    set_alias = getattr(session_data_manager, "set_alias", None)
    if set_alias is not None:
        await set_alias(session_id, new_ref, plan_id)


# ─────────────────── __step_results__ slim 持久化（P3 延后项 #1） ───────────────────
# plan payload 里不再内嵌完整工具结果（大 GeoJSON / 栅格摘要可达 MB 级，Redis
# payload 有界性问题）。每个已完成步骤的完整结果存入独立的 session ref
# （ref:planresult-*，deterministic 别名 + 原地 overwrite——不随执行次数 mint
# 新 ref），plan payload 只保留 slim 摘要 {__slim__, ref, keys}。resume 时按
# ref 水合回完整结果供 ${stepId[.path]} 解析——payload 有界、语义不变。
_PLANRESULT_ALIAS_PREFIX = "planresult:"


def _step_result_alias(plan_id: str, step_id: str) -> str:
    """一个 (plan, step) 一个 deterministic 结果 ref 别名（供原地 overwrite）。"""
    return f"{_PLANRESULT_ALIAS_PREFIX}{plan_id}:{step_id}"


def _is_slim_result(value: Any) -> bool:
    """True 当 value 是 slim 摘要（ref 指向别处存储的完整结果）。"""
    return isinstance(value, dict) and value.get("__slim__") is True


def _build_slim_result(ref_id: str, result: Any) -> dict:
    """slim 摘要：ref + 顶层 keys（有界，与完整结果大小无关）。"""
    keys = sorted(result) if isinstance(result, dict) else []
    return {"__slim__": True, "ref": ref_id, "keys": keys}


async def _persist_step_results(
    session_id: str, plan_id: str, step_results: dict[str, Any]
) -> dict[str, Any]:
    """把（内存中的）完整步骤结果落库到 ref:planresult-*，返回 slim 摘要 dict。

    每个 (plan, step) 对应一个 deterministic 别名：首次 store() + set_alias，
    之后 overwrite() 原地更新（镜像 PlanStore.save 的 resilience——overwrite
    失败即重新 mint + 别名，绝不静默丢数据）。返回的摘要 dict 写入 plan payload。
    """
    slim: dict[str, Any] = {}
    for sid, result in step_results.items():
        if _is_slim_result(result):
            slim[sid] = result  # 已是摘要（防御：水合前理论上不应出现）
            continue
        alias = _step_result_alias(plan_id, sid)
        ref_id = await session_data_manager.resolve_alias(session_id, alias)
        if ref_id != alias and await session_data_manager.overwrite(session_id, ref_id, result):
            slim[sid] = _build_slim_result(ref_id, result)
            continue
        new_ref = await session_data_manager.store(session_id, result, prefix="planresult")
        await session_data_manager.set_alias(session_id, new_ref, alias)
        slim[sid] = _build_slim_result(new_ref, result)
    return slim


async def _hydrate_step_results(
    session_id: str, step_results: dict[str, Any]
) -> dict[str, Any]:
    """resume 时把 slim 摘要还原为完整结果（按 ref 从 session store 读取）。

    ref 已被逐出/过期 → 保留摘要原值（后续 ${} 解析会以 missing_ref 响亮失败，
    绝不静默得到 None/""，与 deps.py 的失败哲学一致）。
    """
    hydrated: dict[str, Any] = {}
    for sid, value in step_results.items():
        if _is_slim_result(value):
            ref = value.get("ref")
            try:
                full = await session_data_manager.get(session_id, ref)
            except Exception as e:  # noqa: BLE001 读失败按失效处理
                logger.warning(f"[PlanMode] 水合步骤结果失败 sid={sid} ref={ref}: {e}")
                full = None
            if full is not None:
                hydrated[sid] = full
            else:
                logger.warning(
                    f"[PlanMode] 步骤 {sid} 的结果 ref {ref} 已失效，resume 引用将失败"
                )
                hydrated[sid] = value
        else:
            hydrated[sid] = value
    return hydrated


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
    波次，已派发的任务允许收尾（siblings-finish 契约，见下方失败处理）。

    design-v3 §4 收敛：
    - **Resume**：对 ``__status__`` ∈ {failed, partially_completed} 的计划，
      ``__step_results__`` 里已记录成功结果的步骤**不重新 dispatch**，其
      结果直接用于 ``${stepId}`` 解析；只有 pending/failed 步骤执行。
      completed / superseded 的计划直接返回已存结果，不重放（运行中 guard
      "已在执行中" 保持）。无新参数，resume 语义自动生效。
    - **Typed refs**：``${stepId[.path]}`` 解析失败（坏路径 / 无结果）→ 该
      步骤立即失败，``failure_class=missing_ref`` + 修正提示（列出坏路径与
      可用 keys），不再静默得到 None/""。
    - **失败持久化**：失败时同样写回 ``__step_results__``，供后续 resume 复用。

    P1-C（并发与崩溃恢复）：
    - 整段执行持有**两层 per-plan 锁**：进程内 asyncio.Lock（status 检查 →
      running 写入 → 执行串行化）之上再套一层分布式锁
      ``session_lock_registry.lock(f"plan:{plan_id}")``——Redis 在时跨 worker/
      跨 pod 原子 claim（P3 延后项 #2 落地），Redis 不可用时降级为进程内锁
      （单 worker / 测试语义不变）。绝无双派发。
    - ``running`` 但 ``__updated_at__`` 陈旧（>300s）→ 视为 crashed，允许 resume。
    - 终态写入（completed/failed/partially_completed）前重读：绝不覆盖已被
      superseded / cancelled 的计划。
    - 工具抛 ``OperationCancelled`` → 写终态 ``cancelled``（terminal），
      resume 拒绝 cancelled 计划。

    P2-1（失败语义）：
    - 失败路径有已存结果时写 ``partially_completed``，否则 ``failed``。
    - 确定性失败（首个未完成步骤 == 上次失败步骤，failure_class 非
      transient_network）→ 直接返回存储的失败（livelock guard），不重跑。
    - superseded / legacy 计划且**无已存结果** → success=False + 明确消息。

    P3 延后项 #1（payload 有界）：
    - ``__step_results__`` 持久化采用 slim 形状（完整结果存入独立的
      ref:planresult-* 引用，payload 只留 {__slim__, ref, keys} 摘要）；
      resume 时按 ref 水合回完整结果供 ``${stepId[.path]}`` 解析，语义不变。

    返回汇总 {plan_id, status, executed, results, failed_step, error}。
    """
    async with session_lock_registry.lock(f"plan:{plan_id}"):
        async with _get_plan_lock(session_id, plan_id):
            return await _execute_plan_locked(session_id, plan_id, registry)


async def _execute_plan_locked(
    session_id: str,
    plan_id: str,
    registry: ToolRegistry,
) -> dict:
    """execute_plan_async 的锁内实现（P1-C / P2-1 语义见 execute_plan_async）。"""
    plan_data = await load_plan(session_id, plan_id)
    if plan_data is None:
        return {"success": False, "error": f"找不到 plan_id={plan_id}"}
    status = plan_data.get("__status__", "pending")
    if status == "running":
        # P1-C：running 但 updated_at 陈旧（>300s，或字段缺失）→ 视为 crashed，
        # 允许 resume。旧 payload 无 __updated_at__ → 一律视为 crashed 孤儿。
        updated_at = plan_data.get("__updated_at__")
        try:
            stale = updated_at is None or (time.time() - float(updated_at)) > _RUNNING_STALE_SECONDS
        except (TypeError, ValueError):
            stale = True
        if stale:
            logger.info(
                f"[PlanMode] plan {plan_id} 上次运行状态已陈旧（crashed 判定），按可恢复计划继续"
            )
        else:
            return {"success": False, "error": f"plan {plan_id} 已在执行中"}
    elif status == "cancelled":
        # P1-C：resume 必须拒绝已取消的计划。
        return {
            "success": False,
            "plan_id": plan_id,
            "status": "cancelled",
            "error": f"plan {plan_id} 已取消，拒绝执行",
            "executed": [],
            "results": {},
        }

    # 还原 Pydantic 模型用于拓扑排序
    plan = PlanProposal.model_validate({k: v for k, v in plan_data.items() if not k.startswith("__")})

    # design-v3 §4：用已存结果播种 step_results（resume 起点）。
    # P3 #1：已存结果是 slim 摘要 → 先按 ref 水合回完整结果，保证 ${} 解析
    # 拿到真实值（ref 失效时保留摘要，解析会响亮失败而非静默 None）。
    stored_results = plan_data.get("__step_results__")
    step_results: dict[str, Any] = dict(stored_results) if isinstance(stored_results, dict) else {}
    if step_results:
        step_results = await _hydrate_step_results(session_id, step_results)

    # 先定义辅助闭包（终态写入等），供下方环检查复用。
    async def _write_terminal(**updates: Any) -> None:
        """终态写入（P1-C）：写入前重读，绝不覆盖 superseded / cancelled。

        执行中途被 supersede_active_plans 取代 / 被用户取消的计划，其终态是
        真相；本执行体的 completed / failed 不得把它覆盖回活跃语义。
        """
        fresh = await load_plan(session_id, plan_id)
        cur = (fresh or {}).get("__status__")
        if cur in ("superseded", "cancelled"):
            logger.info(
                f"[PlanMode] plan {plan_id} 已处于 {cur}，跳过终态覆盖 {updates.get('__status__')}"
            )
            return
        await update_plan_status(session_id, plan_id, **updates)

    order = _topological_order(plan)
    if order is None:
        await _write_terminal(__status__="failed", __error__="cycle")
        return {"success": False, "error": "依赖图含环"}

    topo_idx = {sid: i for i, sid in enumerate(order)}
    step_by_id = {s.id: s for s in plan.steps}

    def _ordered_executed() -> list[str]:
        """已执行步骤按拓扑序输出（确定性，不依赖并发完成顺序）。"""
        return sorted(step_results.keys(), key=topo_idx.__getitem__)

    # design-v3 §4：superseded 计划优先于 completed 判定（存储状态即真相）——
    # 有已存结果则返回（不重放），无结果则明确失败（P2-3）。
    if status == "superseded":
        if not step_results:
            return {
                "success": False,
                "plan_id": plan_id,
                "status": "superseded",
                "error": f"plan {plan_id} 已被新计划取代且未执行任何步骤",
                "executed": [],
                "results": {},
            }
        return {
            "success": True,
            "plan_id": plan_id,
            "status": "superseded",
            "executed": _ordered_executed(),
            "results": step_results,
        }
    # completed 或全部步骤已有结果 → 直接返回已存结果，不重放。
    if status == "completed" or all(s.id in step_results for s in plan.steps):
        return {
            "success": True,
            "plan_id": plan_id,
            "status": "completed",
            "executed": _ordered_executed(),
            "results": step_results,
        }

    # P2-2 livelock guard：确定性失败（首个未完成步骤 == 上次失败步骤，
    # 且 failure_class 非 transient_network）→ 直接返回存储的失败，不重跑。
    if status in ("failed", "partially_completed"):
        failed_step = plan_data.get("__failed_step__")
        fc = plan_data.get("__failure_class__")
        unfinished = [sid for sid in order if sid not in step_results]
        if (
            failed_step
            and fc
            and fc != "transient_network"
            and unfinished
            and unfinished[0] == failed_step
        ):
            err = plan_data.get("__error__") or f"步骤 {failed_step!r} 确定性失败"
            ra = _recovery_action_for_class(fc)
            logger.info(
                f"[PlanMode] plan {plan_id} 步骤 {failed_step} 为确定性失败（{fc}），"
                f"拒绝盲目重跑（livelock guard）"
            )
            ret: dict = {
                "success": False,
                "plan_id": plan_id,
                "status": status,
                "failed_step": failed_step,
                "tool": step_by_id[failed_step].tool,
                "error": err,
                "failure_class": fc,
                "executed": _ordered_executed(),
                "results": step_results,
            }
            if ra is not None:
                ret["recovery_action"] = ra
            return ret

    def _failure_status() -> str:
        """P2-1：有已存结果 → partially_completed（可读/可恢复）；否则 failed。"""
        return "partially_completed" if step_results else "failed"

    def _fail(
        sid: str,
        error: str,
        last_result: Optional[Any] = None,
        failure_class: Optional[str] = None,
        recovery_action: Optional[str] = None,
        correction_hint: Optional[str] = None,
    ) -> dict:
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
        if failure_class is not None:
            ret["failure_class"] = failure_class
        if recovery_action is not None:
            ret["recovery_action"] = recovery_action
        if correction_hint is not None:
            ret["correction_hint"] = correction_hint
        if last_result is not None:
            ret["last_result"] = last_result
        return ret

    await update_plan_status(session_id, plan_id, __status__="running")

    # RESUME：已有结果的步骤跳过（上次运行存下的结果继续供 ${} 引用），
    # 只对剩余步骤构建波次 DAG；已完成依赖不阻塞剩余步骤。
    unfinished = [s for s in plan.steps if s.id not in step_results]
    in_degree: dict[str, int] = {s.id: 0 for s in unfinished}
    edges: dict[str, list[str]] = {s.id: [] for s in unfinished}
    for step in unfinished:
        deps = (set(step.depends_on) | _extract_refs(step.args)) - {step.id}
        for dep in deps:
            if dep in step_results:
                continue  # 依赖已有结果（本次或上次运行）→ 不阻塞
            if dep in in_degree:
                edges[dep].append(step.id)
                in_degree[step.id] += 1

    ready: list[str] = [sid for sid, d in in_degree.items() if d == 0]
    wave_tasks: set[asyncio.Task] = set()

    try:
        while ready:
            # 同一波次：入度均为 0 的独立步骤，按拓扑序确定排布
            wave = sorted(ready, key=topo_idx.__getitem__)
            ready = []

            # 波次内 args 解析：只依赖已完成步骤，解析失败即中止本波次
            resolved_args: dict[str, dict] = {}
            for sid in wave:
                step = step_by_id[sid]
                try:
                    r = resolve_arg_refs(step.args, step_results)
                except MissingRefError as e:
                    hint = str(e)
                    await _write_terminal(
                        __status__=_failure_status(),
                        __failed_step__=sid,
                        __error__=hint,
                        __failure_class__="missing_ref",
                        __step_results__=await _persist_step_results(session_id, plan_id, step_results),
                    )
                    return _fail(
                        sid,
                        f"步骤 {sid!r} 引用解析失败: {hint}",
                        failure_class="missing_ref",
                        recovery_action="reuse_ref",
                        correction_hint=hint,
                    )
                except Exception as e:  # noqa: BLE001
                    await _write_terminal(
                        __status__=_failure_status(),
                        __failed_step__=sid,
                        __error__=f"args 解析异常: {e}",
                        __step_results__=await _persist_step_results(session_id, plan_id, step_results),
                    )
                    return _fail(
                        sid,
                        f"步骤 {sid!r} args 解析异常: {e}",
                        failure_class="internal",
                        recovery_action="replan_remaining",
                    )
                if not isinstance(r, dict):
                    await _write_terminal(
                        __status__=_failure_status(),
                        __failed_step__=sid,
                        __error__=f"args 解析后不是 dict: {type(r).__name__}",
                        __step_results__=await _persist_step_results(session_id, plan_id, step_results),
                    )
                    return _fail(
                        sid,
                        f"步骤 {sid!r} args 解析后不是 dict",
                        failure_class="validation",
                        recovery_action="correct_args",
                    )

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
            wave_tasks = set(tasks.values())
            task_to_sid = {t: sid for sid, t in tasks.items()}

            wave_successes: dict[str, Any] = {}
            # (sid, error, last_result, failure_class, recovery_action)
            failure: Optional[tuple[str, str, Optional[Any], Optional[str], Optional[str]]] = None
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
                    except OperationCancelled as e:
                        # P1-C：用户取消不是工具故障 —— 写终态 cancelled（terminal），
                        # 用 logger.info 记录（不是 exception）。走 _write_terminal：
                        # 若计划已在途中被 superseded，保留 superseded 不被覆盖。
                        logger.info(f"[PlanMode] plan {plan_id} 执行被取消: {e}")
                        await _write_terminal(
                            __status__="cancelled",
                            __error__=str(e) or "用户取消",
                            __step_results__=await _persist_step_results(session_id, plan_id, step_results),
                        )
                        return {
                            "success": False,
                            "plan_id": plan_id,
                            "status": "cancelled",
                            "error": f"plan {plan_id} 已取消",
                            "executed": _ordered_executed(),
                            "results": step_results,
                            "failure_class": "cancelled",
                        }
                    except Exception as e:
                        logger.exception(f"[PlanMode] step {sid} raised")
                        fc, ra = _classify_failure(exception=e)
                        failure = (sid, str(e), None, fc, ra)
                        break
                    # 工具返回 success=False（V3.x Exception As Thought 包装）也视为失败
                    if isinstance(result, dict) and result.get("success") is False:
                        err = result.get("message") or result.get("error", "tool failed")
                        fc, ra = _classify_failure(result=result)
                        failure = (sid, err, result, fc, ra)
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
                sid, err, last_result, fc, ra = failure
                updates: dict[str, Any] = {
                    "__status__": _failure_status(),
                    "__failed_step__": sid,
                    "__error__": err,
                    # design-v3 §4：失败也保留已完成结果，供下次 execute_plan resume。
                    # P3 #1：持久化 slim 摘要（完整结果在 ref:planresult-*）。
                    "__step_results__": await _persist_step_results(session_id, plan_id, step_results),
                }
                if fc is not None:
                    updates["__failure_class__"] = fc
                await _write_terminal(**updates)
                return _fail(sid, err, last_result, failure_class=fc, recovery_action=ra)

            # 波次完成 → 推进依赖图，解锁下一波次
            for sid in wave:
                for nxt in edges[sid]:
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        ready.append(nxt)

        await _write_terminal(
            __status__="completed",
            __step_results__=await _persist_step_results(session_id, plan_id, step_results),
        )
        return {
            "success": True,
            "plan_id": plan_id,
            "status": "completed",
            "executed": _ordered_executed(),
            "results": step_results,
        }
    except asyncio.CancelledError:
        # 外层被取消（客户端断连等）：取消并回收本波次 pending 任务，
        # 状态收敛到 cancelled 后原样上抛（不吞取消）。
        await _cancel_wave_tasks(wave_tasks)
        try:
            await _write_terminal(
                __status__="cancelled",
                __error__="cancelled",
                __step_results__=await _persist_step_results(session_id, plan_id, step_results),
            )
        except Exception as e:  # noqa: BLE001 —— 收敛写失败不能替换 CancelledError
            logger.warning(f"[PlanMode] 取消后计划状态收敛写失败: {e}")
        raise
    except Exception as e:  # noqa: BLE001
        # 非预期的内部异常：同样回收波次任务并把状态收敛到终态，
        # 避免计划永久卡在 running。
        await _cancel_wave_tasks(wave_tasks)
        logger.exception(f"[PlanMode] execute_plan_async aborted: {e}")
        try:
            await _write_terminal(
                __status__=_failure_status(),
                __error__=f"执行异常: {e}",
                # R2-4: record the failure class + failed step so the resume
                # livelock guard can engage — without these, every resume of an
                # internally-failed plan re-executed its tools from scratch
                # (guard needs both fields and a non-transient class).
                __failure_class__="internal",
                __failed_step__=next(
                    (s["id"] for s in plan.get("steps", []) if s["id"] not in step_results),
                    None,
                ),
                __step_results__=await _persist_step_results(session_id, plan_id, step_results),
            )
        except Exception as e2:  # noqa: BLE001 —— 收敛写失败不能替换原始异常
            logger.warning(f"[PlanMode] 失败后计划状态收敛写失败: {e2}")
        raise



def _recovery_action_for_class(failure_class: str) -> Optional[str]:
    """从 failure_class 反查 recovery_action；分类失败返回 None。"""
    try:
        from app.services.planning.models import FailureClass as _FC
        from app.services.planning.recovery import recovery_action_for
        return recovery_action_for(_FC(failure_class)).value
    except Exception:  # noqa: BLE001 分类失败不拖垮执行
        return None


def _classify_failure(
    *,
    result: Optional[dict] = None,
    exception: Optional[Exception] = None,
) -> tuple[Optional[str], Optional[str]]:
    """design-v3 §recovery：把 plan-mode 工具失败分类成 failure_class +
    recovery_action（additive 返回字段，不改变任何重试行为）。"""
    from app.services.planning.recovery import classify_error, recovery_action_for
    try:
        if exception is not None:
            fc = classify_error(exception=exception)
        else:
            fc = classify_error(
                code=(result or {}).get("code"),
                error_type=(result or {}).get("error_type"),
                message=(result or {}).get("message") or (result or {}).get("error"),
            )
        return fc.value, recovery_action_for(fc).value
    except Exception:  # noqa: BLE001 分类失败不拖垮执行
        return None, None
