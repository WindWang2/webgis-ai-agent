"""Swarm 总控编排器（ADR-0187 §2/§4/§5）。

三件事：
1. ``HeuristicSpatialDecomposer``：确定性启发式任务分解（关键词 → 数据/
   计算/选址/制图/审计相位管道），LLM 分解器经 ``SwarmTaskDecomposer``
   Protocol 可插拔；
2. ``SwarmConcurrencyGovernor``：进程级单例并发熔断（全局在飞 ≤3），
   任何编排器实例派发前必须经其信号量 —— 队列背压 + 流水线唤醒；
3. ``SwarmOrchestrator``：分解 → 图校验 → 调度（复用 Execution Graph
   ``machine`` 纯函数做就绪集/闭包/转移裁决）→ 失败隔离与重试 → 聚合。

调度红线：状态词表/转移合法性一律来自 ``workflow_runtime``（ADR-0187
D2 不新造图 IR）；launcher 体内全量兜底，单点失败绝不击穿集群。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional, Protocol

from app.services.agent_swarm.delegation_contracts import (
    MAX_GOAL_CHARS,
    MAX_RETRIES_PER_TASK,
    MAX_SWARM_CONCURRENCY,
    MAX_SWARM_TASKS,
    SpecialistAssignment,
    SubagentReceipt,
    SwarmContractError,
    SwarmErrorCode,
    SwarmExecutionStatus,
    SwarmReceiptStatus,
    SwarmRunState,
    SwarmSpecialistRole,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.agent_swarm.dispatcher import SpecialistDispatcher
from app.services.workflow_runtime import machine
from app.services.workflow_runtime.contracts import NodeState

logger = logging.getLogger(__name__)

#: 调度主循环防御性迭代上限（DAG 有界 ⇒ 理论远小于此）。
_MAX_DRIVE_ITERS = MAX_SWARM_TASKS * 4 + 16


# ─────────────────────────── 分解契约与启发式实现 ───────────────────────────


class SwarmTaskDecomposer(Protocol):
    """任务分解协议：实现方须产出可通过 ``validate_swarm_graph`` 的图。"""

    def decompose(
        self, root_goal: str, projection: WorldStateProjection
    ) -> list[SwarmTaskDescriptor]: ...


_DATA_WORDS = (
    "数据", "获取", "边界", "dem", "地形", "影像", "底图",
    "data", "fetch", "download",
)
_THEME_WORDS = ("水文", "降雨", "洪水", "防汛", "flood", "rainfall", "hydro")
_COMPUTE_WORDS = (
    "分析", "计算", "评估", "叠加", "统计", "风险",
    "analysis", "compute", "overlay", "risk",
)
_SITING_WORDS = ("选址", "适宜性", "suitability", "siting")
_CARTO_WORDS = (
    "制图", "地图", "专题图", "出图", "排版",
    "map", "chart", "cartography",
)


class HeuristicSpatialDecomposer:
    """确定性关键词启发式：复合空间任务 → 专业相位管道（无 LLM）。

    零命中时退化为通用四相位（数据→计算→制图→审计）；审计任务恒为
    optional（审计缺位降级披露，不阻断成果交付）。
    """

    def decompose(
        self, root_goal: str, projection: WorldStateProjection
    ) -> list[SwarmTaskDescriptor]:
        goal = (root_goal or "").lower()
        data_hit = any(w in goal for w in _DATA_WORDS)
        theme_hit = any(w in goal for w in _THEME_WORDS)
        compute_hit = any(w in goal for w in _COMPUTE_WORDS)
        siting_hit = any(w in goal for w in _SITING_WORDS)
        carto_hit = any(w in goal for w in _CARTO_WORDS)
        generic = not (data_hit or theme_hit or compute_hit or siting_hit or carto_hit)

        tasks: list[SwarmTaskDescriptor] = [
            SwarmTaskDescriptor(
                task_id="swarm.data.base_geo",
                goal="获取基础地理底图、行政区划边界与高程数据",
                role=SwarmSpecialistRole.DATA_HUNTER,
                capability="swarm.data.base_geo",
                expected_outputs=("base_geo",),
            )
        ]
        if theme_hit:
            tasks.append(
                SwarmTaskDescriptor(
                    task_id="swarm.data.theme",
                    goal="获取专题数据（水文/降雨/洪水等）",
                    role=SwarmSpecialistRole.DATA_HUNTER,
                    capability="swarm.data.theme",
                    expected_outputs=("theme_data",),
                )
            )
        data_ids = tuple(t.task_id for t in tasks)
        tasks.append(
            SwarmTaskDescriptor(
                task_id="swarm.compute.overlay",
                goal="对多源数据执行空间叠加与风险/统计分析",
                role=SwarmSpecialistRole.COMPUTE_SPECIALIST,
                capability="swarm.compute.overlay",
                depends_on=data_ids,
                expected_outputs=("overlay_result",),
            )
        )
        last_compute = "swarm.compute.overlay"
        if siting_hit:
            tasks.append(
                SwarmTaskDescriptor(
                    task_id="swarm.compute.siting",
                    goal="开展选址适宜性评估与分区打分",
                    role=SwarmSpecialistRole.COMPUTE_SPECIALIST,
                    capability="swarm.compute.siting",
                    depends_on=("swarm.compute.overlay",),
                    expected_outputs=("siting_score",),
                )
            )
            last_compute = "swarm.compute.siting"
        want_carto = carto_hit or generic
        if want_carto:
            tasks.append(
                SwarmTaskDescriptor(
                    task_id="swarm.cartography.compose",
                    goal="按制图规范完成专题图排版与出图",
                    role=SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
                    capability="swarm.cartography.compose",
                    depends_on=(last_compute,),
                    expected_outputs=("thematic_map",),
                )
            )
        audit_deps = (
            ("swarm.cartography.compose", last_compute)
            if want_carto
            else (last_compute,)
        )
        tasks.append(
            SwarmTaskDescriptor(
                task_id="swarm.audit.judge",
                goal="对产物与过程证据执行质量审计与合规校核",
                role=SwarmSpecialistRole.AUDIT_JUDGE,
                capability="swarm.audit.judge",
                depends_on=audit_deps,
                optional=True,
                expected_outputs=("audit_report",),
            )
        )
        return tasks


# ─────────────────────────── 图校验（复用 machine 数学） ───────────────────────────


def validate_swarm_graph(
    tasks: list[SwarmTaskDescriptor],
) -> dict:
    """校验分解结果并给出 machine 兼容 dag dict。违反即 ``SwarmContractError``。

    校验面：数量上限 / task_id 唯一 / 依赖存在 / 无环（Kahn）。
    词表与依赖数上限已由 contracts validator 把守，此处复检结构事实。
    """
    if not tasks:
        raise SwarmContractError("分解结果为空")
    if len(tasks) > MAX_SWARM_TASKS:
        raise SwarmContractError(
            f"集群任务数 {len(tasks)} 超过上限 {MAX_SWARM_TASKS}"
        )
    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise SwarmContractError(f"task_id 重复: {dupes}")
    id_set = set(ids)
    for t in tasks:
        for dep in t.depends_on:
            if dep not in id_set:
                raise SwarmContractError(
                    f"{t.task_id} 依赖了未声明的 task_id: {dep!r}"
                )
    dag = {
        "nodes": [
            {"node_id": t.task_id, "depends_on": list(t.depends_on)} for t in tasks
        ],
        "edges": [],
    }
    adjacency = machine.build_adjacency(dag)
    indegree = {nid: 0 for nid in id_set}
    for src, dsts in adjacency.items():
        for dst in dsts:
            indegree[dst] += 1
    queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in sorted(adjacency.get(cur, ())):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if seen != len(id_set):
        raise SwarmContractError("分解图存在环（依赖不可满足）")
    return dag


class WorkflowMachineGraphPort:
    """Execution Graph 端口缺省适配器：全部委托 ``workflow_runtime.machine``。

    Swarm 层不实现任何图数学 —— 就绪集/闭包/转移裁决与 V5 同源
    （ADR-0187 D2）。测试/未来 DB 贯通可整体替换本端口。
    """

    def build_dag(self, tasks: dict[str, SwarmTaskDescriptor]) -> dict:
        return {
            "nodes": [
                {"node_id": t.task_id, "depends_on": list(t.depends_on)}
                for t in tasks.values()
            ],
            "edges": [],
        }

    def ready_set(self, dag: dict, states: dict[str, str]) -> list[str]:
        return machine.ready_set(dag, states)

    def downstream_closure(self, dag: dict, seeds: set[str]) -> set[str]:
        return machine.downstream_closure(dag, seeds)

    def check_transition(self, from_state: str, to_state: str) -> None:
        reason = machine.check_transition(from_state, to_state)
        if reason:
            raise SwarmContractError(
                f"非法状态转移 {from_state}->{to_state}: {reason}"
            )


# ─────────────────────────── 集群并发熔断 ───────────────────────────


class SwarmConcurrencyGovernor:
    """进程级单例并发熔断器：全局在飞子任务 ≤ ``max_concurrency``。

    每 event-loop 一把 ``asyncio.Semaphore``（loop 更换自动重建并清陈旧
    台账 —— 单测的 function-scoped loop 隔离需要）；派发前 ``acquire``、
    ``finally release``。多编排器/多会话叠加时上限仍是集群级硬界。
    """

    def __init__(self, max_concurrency: int = MAX_SWARM_CONCURRENCY) -> None:
        self._max = max(1, int(max_concurrency))
        self._sem: Optional[asyncio.Semaphore] = None
        self._sem_loop_key: Optional[int] = None
        self._active: dict[str, str] = {}  # assignment_id -> task_id

    def _semaphore(self) -> asyncio.Semaphore:
        loop_key = id(asyncio.get_running_loop())
        if self._sem is None or self._sem_loop_key != loop_key:
            self._sem = asyncio.Semaphore(self._max)
            self._sem_loop_key = loop_key
            self._active.clear()  # 旧 loop 的陈旧台账（其 finally 已不可达）
        return self._sem

    async def acquire(self, assignment_id: str, task_id: str) -> None:
        sem = self._semaphore()
        await sem.acquire()
        self._active[assignment_id] = task_id

    def release(self, assignment_id: str) -> None:
        self._active.pop(assignment_id, None)
        if self._sem is not None:
            self._sem.release()

    def snapshot(self) -> dict:
        return {
            "max_concurrency": self._max,
            "active_count": len(self._active),
            "active_task_ids": sorted(self._active.values()),
        }


_governor: Optional[SwarmConcurrencyGovernor] = None


def get_swarm_concurrency_governor() -> SwarmConcurrencyGovernor:
    global _governor
    if _governor is None:
        _governor = SwarmConcurrencyGovernor()
    return _governor


def reset_swarm_concurrency_governor_for_tests() -> None:
    global _governor
    _governor = None


# ─────────────────────────── 总控编排器 ───────────────────────────


class SwarmOrchestrator:
    """Master→Specialist 集群总控：分解 → 调度 → 派发 → 聚合。

    单实例服务一次 ``run_swarm``（集群运行态是实例内有界对象，见 ADR-0187
    D8 非目标）；跨实例并发安全由全局 Governor 保证。
    """

    def __init__(
        self,
        session_id: str,
        *,
        decomposer: Optional[SwarmTaskDecomposer] = None,
        dispatcher: Optional[SpecialistDispatcher] = None,
        aggregator: Any = None,
        graph_port: Optional[WorkflowMachineGraphPort] = None,
        governor: Optional[SwarmConcurrencyGovernor] = None,
    ) -> None:
        from app.services.agent_swarm.aggregator import SwarmAggregator

        self._session_id = session_id
        self._decomposer = decomposer or HeuristicSpatialDecomposer()
        self._dispatcher = dispatcher or SpecialistDispatcher()
        self._aggregator = aggregator or SwarmAggregator()
        self._graph = graph_port or WorkflowMachineGraphPort()
        self._governor = governor or get_swarm_concurrency_governor()
        # 运行态
        self._tasks: dict[str, SwarmTaskDescriptor] = {}
        self._states: dict[str, str] = {}
        self._receipts: dict[str, SubagentReceipt] = {}
        self._launchers: dict[str, "asyncio.Task[None]"] = {}
        self._propagated: set[str] = set()
        self._projection: Optional[WorldStateProjection] = None
        self._cancelled = False
        self._status: Optional[SwarmExecutionStatus] = None

    # ── 主入口 ──
    async def run_swarm(
        self,
        root_goal: str,
        *,
        projection: Optional[WorldStateProjection] = None,
        run_id: Optional[str] = None,
    ) -> SwarmExecutionStatus:
        started = time.time()
        goal = (root_goal or "").strip()
        if not goal:
            raise SwarmContractError("root_goal 不能为空")
        run_id = run_id or f"swarm-{uuid.uuid4().hex[:12]}"
        self._projection = projection or WorldStateProjection(
            session_id=self._session_id, goal_summary=goal[:MAX_GOAL_CHARS]
        )
        descriptors = self._decomposer.decompose(goal, self._projection)
        dag = validate_swarm_graph(descriptors)
        self._tasks = {t.task_id: t for t in descriptors}
        self._states = {tid: NodeState.PENDING for tid in self._tasks}
        self._receipts = {}
        self._launchers = {}
        self._propagated = set()
        self._cancelled = False
        self._status = SwarmExecutionStatus(
            run_id=run_id,
            session_id=self._session_id,
            root_goal=goal[:MAX_GOAL_CHARS],
            state=SwarmRunState.RUNNING,
            started_at=started,
        )
        try:
            await self._drive(dag)
        finally:
            for fut in self._launchers.values():
                if not fut.done():
                    fut.cancel()
            if self._launchers:
                await asyncio.gather(
                    *self._launchers.values(), return_exceptions=True
                )
            self._launchers = {}
        manifest = await self._aggregator.collect(
            run_id=run_id,
            session_id=self._session_id,
            receipts=self._receipts,
            tasks=self._tasks,
        )
        manifest_ref = await self._aggregator.merge(manifest)  # 内部 fail-open
        status = self._status
        status.state = self._adjudicate()
        status.manifest = manifest
        status.manifest_ref = manifest_ref
        status.counts = self._counts()
        status.active_task_ids = []
        status.finished_at = time.time()
        return status

    # ── 调度主循环 ──
    async def _drive(self, dag: dict) -> None:
        for _ in range(_MAX_DRIVE_ITERS):
            if self._all_terminal():
                return
            ready = [
                tid
                for tid in self._graph.ready_set(dag, self._states)
                if tid not in self._launchers
                and self._states.get(tid) == NodeState.PENDING
            ]
            for tid in sorted(ready, key=lambda t: (-self._tasks[t].priority, t)):
                self._transition(tid, NodeState.READY)
                self._launchers[tid] = asyncio.ensure_future(self._launcher(tid))
            if not self._launchers:
                break  # 无在飞且无可派发 → 剩余为不可达（收尾清扫）
            done, _pending = await asyncio.wait(
                set(self._launchers.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # 已结算 launcher 必须移出等待集：否则 asyncio.wait 每轮立即
            # 携带同一批 done 返回，主循环空转直至迭代上限（背压死锁面）。
            settled = [tid for tid, fut in self._launchers.items() if fut in done]
            for tid in settled:
                fut = self._launchers.pop(tid)
                try:
                    fut.result()
                except Exception:  # noqa: BLE001 — launcher 自兜底，此为最后防线
                    logger.exception("[Swarm] launcher escaped exception")
            self._propagate_failures(dag)
            self._refresh_status()
        # 收尾清扫：不可达残留（PENDING）诚实 SKIPPED
        for tid, state in list(self._states.items()):
            if state == NodeState.PENDING:
                self._transition(tid, NodeState.SKIPPED)
        self._refresh_status()

    async def _launcher(self, task_id: str) -> None:
        task = self._tasks[task_id]
        assignment = SpecialistAssignment(
            assignment_id=f"asg-{task_id}-{uuid.uuid4().hex[:8]}",
            task=task,
            projection=self._projection or WorldStateProjection(
                session_id=self._session_id
            ),
            upstream_receipts=[
                self._receipts[d] for d in task.depends_on if d in self._receipts
            ],
            issued_at=time.time(),
        )
        acquired = False
        try:
            try:
                await self._governor.acquire(assignment.assignment_id, task_id)
                acquired = True
            except asyncio.CancelledError:
                self._transition(task_id, NodeState.CANCELLED)
                self._receipts[task_id] = _cancelled_receipt(assignment)
                return
            try:
                if self._cancelled:
                    self._transition(task_id, NodeState.CANCELLED)
                    self._receipts[task_id] = _cancelled_receipt(assignment)
                    return
                self._transition(task_id, NodeState.RUNNING)
                receipt = await self._execute_with_retries(task, assignment)
                self._settle(task, receipt)
            except asyncio.CancelledError:
                self._transition(task_id, NodeState.CANCELLED)
                self._receipts[task_id] = _cancelled_receipt(assignment)
            except Exception as exc:  # noqa: BLE001 — 失败隔离最后防线
                logger.exception("[Swarm] launcher crash task=%s", task_id)
                self._transition(task_id, NodeState.FAILED)
                self._receipts[task_id] = SubagentReceipt(
                    assignment_id=assignment.assignment_id,
                    task_id=task_id,
                    role=task.role,
                    status=SwarmReceiptStatus.FAILED,
                    error=f"launcher crash: {exc}"[:300],
                    error_code=SwarmErrorCode.NON_RETRYABLE,
                    finished_at=time.time(),
                )
        finally:
            if acquired:  # 未获取到槽位不得释放（信号量超发面）
                self._governor.release(assignment.assignment_id)

    async def _execute_with_retries(
        self, task: SwarmTaskDescriptor, assignment: SpecialistAssignment
    ) -> SubagentReceipt:
        max_attempts = 1 + min(task.max_retries, MAX_RETRIES_PER_TASK)
        receipt: Optional[SubagentReceipt] = None
        for attempt in range(1, max_attempts + 1):
            task.attempts = attempt
            started = time.time()
            try:
                receipt = await asyncio.wait_for(
                    self._dispatcher.dispatch(
                        assignment,
                        on_heartbeat=lambda note: logger.debug(
                            "[Swarm] heartbeat %s: %s", task.task_id, note
                        ),
                    ),
                    timeout=task.timeout_s,
                )
            except (asyncio.TimeoutError, TimeoutError):
                receipt = SubagentReceipt(
                    assignment_id=assignment.assignment_id,
                    task_id=task.task_id,
                    role=task.role,
                    status=SwarmReceiptStatus.FAILED,
                    error=f"timeout after {task.timeout_s:.3f}s",
                    error_code=SwarmErrorCode.TIMEOUT,
                    attempts=attempt,
                    wall_time_s=time.time() - started,
                    finished_at=time.time(),
                )
            if receipt.status != SwarmReceiptStatus.FAILED:
                return receipt
            if task.side_effect == "destructive":
                return receipt  # ADR-0184 D6：破坏性 at-most-once，禁自动重驱
            if attempt >= max_attempts:
                return receipt
            if receipt.error_code not in (
                SwarmErrorCode.RETRYABLE,
                SwarmErrorCode.TIMEOUT,
            ):
                return receipt  # non_retryable/cancelled → 局部终局
        assert receipt is not None
        return receipt

    # ── 状态结算与传播 ──
    def _settle(self, task: SwarmTaskDescriptor, receipt: SubagentReceipt) -> None:
        receipt.finished_at = receipt.finished_at or time.time()
        if receipt.status == SwarmReceiptStatus.FAILED and task.optional:
            # 降级路线（ADR-0187 D5）：optional 任务重试穷尽 → 诚实降级券，
            # 节点 SKIPPED 结算 —— 下游视为已结算继续流水，集群 partial。
            receipt = receipt.model_copy(
                update={
                    "status": SwarmReceiptStatus.DEGRADED,
                    "degraded": True,
                    "error_code": receipt.error_code or SwarmErrorCode.NON_RETRYABLE,
                }
            )
        self._receipts[task.task_id] = receipt
        if receipt.status == SwarmReceiptStatus.SUCCEEDED:
            self._transition(task.task_id, NodeState.SUCCEEDED)
            return
        if receipt.status == SwarmReceiptStatus.DEGRADED:
            task.degraded = True
            # 降级 = FAILED→SKIPPED 两步合法转移（下游视为已结算继续流水）
            self._transition(task.task_id, NodeState.FAILED)
            self._transition(task.task_id, NodeState.SKIPPED)
            return
        self._transition(task.task_id, NodeState.FAILED)

    def _propagate_failures(self, dag: dict) -> None:
        failed = [
            tid
            for tid, st in self._states.items()
            if st == NodeState.FAILED and tid not in self._propagated
        ]
        for tid in failed:
            self._propagated.add(tid)
            closure = self._graph.downstream_closure(dag, {tid})
            for downstream in closure:
                if self._states.get(downstream) == NodeState.PENDING:
                    self._transition(downstream, NodeState.SKIPPED)

    def _transition(self, task_id: str, to_state: str) -> None:
        from_state = self._states.get(task_id, NodeState.PENDING)
        if from_state == to_state:
            return
        self._graph.check_transition(from_state, to_state)
        self._states[task_id] = to_state

    def _all_terminal(self) -> bool:
        terminal = {
            NodeState.SUCCEEDED,
            NodeState.FAILED,
            NodeState.SKIPPED,
            NodeState.CANCELLED,
        }
        return all(st in terminal for st in self._states.values())

    def _adjudicate(self) -> str:
        if any(st == NodeState.CANCELLED for st in self._states.values()):
            return SwarmRunState.CANCELLED
        hard_failed = any(
            st == NodeState.FAILED and not self._tasks[tid].optional
            for tid, st in self._states.items()
        )
        if hard_failed:
            return SwarmRunState.FAILED
        degraded = any(r.degraded for r in self._receipts.values())
        skipped = any(st == NodeState.SKIPPED for st in self._states.values())
        if degraded or skipped:
            return SwarmRunState.PARTIAL
        return SwarmRunState.SUCCEEDED

    def _counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for st in self._states.values():
            counts[st] = counts.get(st, 0) + 1
        return counts

    def _refresh_status(self) -> None:
        if self._status is None:
            return
        self._status.counts = self._counts()
        self._status.active_task_ids = [
            tid for tid, fut in self._launchers.items() if not fut.done()
        ]

    # ── 观测与取消 ──
    def status_snapshot(self) -> SwarmExecutionStatus:
        self._refresh_status()
        assert self._status is not None, "run_swarm 尚未启动"
        return self._status

    async def cancel(self) -> None:
        """协作式取消：向全部在飞 launcher 发取消并等待结算收敛。"""
        self._cancelled = True
        for fut in self._launchers.values():
            if not fut.done():
                fut.cancel()
        if self._launchers:
            await asyncio.gather(*self._launchers.values(), return_exceptions=True)
        self._refresh_status()


def _cancelled_receipt(assignment: SpecialistAssignment) -> SubagentReceipt:
    return SubagentReceipt(
        assignment_id=assignment.assignment_id,
        task_id=assignment.task.task_id,
        role=assignment.task.role,
        status=SwarmReceiptStatus.FAILED,
        error="cancelled by orchestrator",
        error_code=SwarmErrorCode.CANCELLED,
        finished_at=time.time(),
    )
