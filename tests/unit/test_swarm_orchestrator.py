"""Swarm Orchestrator 单测套件（ADR-0187 / agent-03）。

确定性纪律：假 runtime（asyncio.Event 门控 + 行为脚本）、无 LLM、无网络、
无真 DB（session store 注入假件）。覆盖：任务分解 DAG 映射、集群并发熔断
（≤3）与队列背压、失败隔离与局部重试/降级、destructive at-most-once、
提货券（ref-only）纪律、聚合回写、SwarmBridge 网关守卫。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Optional

import pytest
from pydantic import ValidationError

from app.services.agent_swarm.aggregator import (
    NullSink,
    SessionPlanSwarmSink,
    SwarmAggregator,
)
from app.services.agent_swarm.delegation_contracts import (
    MAX_SWARM_CONCURRENCY,
    MAX_SWARM_TASKS,
    SwarmContractError,
    SwarmExecutionStatus,
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SubagentReceipt,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.agent_swarm.dispatcher import (
    SpecialistDispatcher,
    normalize_receipt,
)
from app.services.agent_swarm.orchestrator import (
    HeuristicSpatialDecomposer,
    SwarmOrchestrator,
    get_swarm_concurrency_governor,
    validate_swarm_graph,
)
from app.services.workflow_runtime.contracts import NodeState

# ─────────────────────────── 假件与工厂 ───────────────────────────


class _Counter:
    """跨 runtime 共享的在飞计数器（并发断言用）。"""

    def __init__(self) -> None:
        self.inflight = 0
        self.max_inflight = 0

    def enter(self) -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)

    def exit(self) -> None:
        self.inflight -= 1


class ScriptedRuntime:
    """按 task_id 脚本化的 SpecialistRuntime 假件。

    behavior 值：
      - asyncio.Event        → 等待事件置位后成功；
      - Callable(assignment) → await 协程返回值；普通返回值作为 receipt；
        抛出的异常经 dispatcher 包装为 retryable FAILED 券；
      - 其它（含 None）       → 立即成功。
    """

    def __init__(
        self,
        behaviors: Optional[dict[str, Any]] = None,
        *,
        counter: Optional[_Counter] = None,
        refs_prefix: str = "out",
    ) -> None:
        self.behaviors = behaviors or {}
        self.counter = counter or _Counter()
        self.calls: list[str] = []
        self.start_order: list[str] = []
        self.end_order: list[str] = []
        self._refs_prefix = refs_prefix

    def _receipt(self, assignment: Any, **kw: Any) -> SubagentReceipt:
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id=assignment.task.task_id,
            role=assignment.task.role,
            status=kw.pop("status", SwarmReceiptStatus.SUCCEEDED),
            produced_refs=kw.pop(
                "produced_refs", [f"ref:{self._refs_prefix}-{assignment.task.task_id}"]
            ),
            summary=kw.pop("summary", f"done {assignment.task.task_id}"),
            **kw,
        )

    async def execute(
        self,
        assignment: Any,
        *,
        on_heartbeat: Optional[Callable[[str], None]] = None,
    ) -> SubagentReceipt:
        task_id = assignment.task.task_id
        self.calls.append(task_id)
        self.start_order.append(task_id)
        self.counter.enter()
        try:
            behavior = self.behaviors.get(task_id)
            if isinstance(behavior, asyncio.Event):
                await behavior.wait()
            elif callable(behavior):
                maybe = behavior(assignment)
                if asyncio.isfuture(maybe) or asyncio.iscoroutine(maybe):
                    return await maybe
                if maybe is not None:
                    return maybe
            elif behavior is not None:
                raise behavior
            if on_heartbeat is not None:
                on_heartbeat(f"progress {task_id}")
            return self._receipt(assignment)
        finally:
            self.counter.exit()
            self.end_order.append(task_id)


def _sid() -> str:
    return f"s-{uuid.uuid4().hex[:10]}"


def _proj(session_id: str, **kw: Any) -> WorldStateProjection:
    kw.setdefault("session_id", session_id)
    kw.setdefault("goal_summary", "综合空间任务")
    return WorldStateProjection(**kw)


def _task(task_id: str, role: SwarmSpecialistRole, **kw: Any) -> SwarmTaskDescriptor:
    kw.setdefault("goal", f"task {task_id}")
    return SwarmTaskDescriptor(task_id=task_id, role=role, **kw)


def _linear_tasks(n: int, *, role: SwarmSpecialistRole) -> list[SwarmTaskDescriptor]:
    tasks = []
    for i in range(n):
        dep = (f"t{i - 1}",) if i else ()
        tasks.append(_task(f"t{i}", role, depends_on=dep))
    return tasks


class _FixedDecomposer:
    """测试桩：固定返回给定 descriptors。"""

    def __init__(self, tasks: list[SwarmTaskDescriptor]) -> None:
        self._tasks = tasks

    def decompose(
        self, root_goal: str, projection: WorldStateProjection
    ) -> list[SwarmTaskDescriptor]:
        return self._tasks


def _orch(
    session_id: str,
    runtime: ScriptedRuntime,
    *,
    decomposer: Any = None,
    sink: Any = None,
    tasks_override: Optional[list[SwarmTaskDescriptor]] = None,
) -> SwarmOrchestrator:
    if tasks_override is not None:
        decomposer = _FixedDecomposer(tasks_override)
    return SwarmOrchestrator(
        session_id,
        decomposer=decomposer or HeuristicSpatialDecomposer(),
        dispatcher=SpecialistDispatcher(runtime),
        aggregator=SwarmAggregator(sink=sink if sink is not None else NullSink()),
    )


def _failed_receipt(assignment: Any, *, error_code: str, error: str) -> SubagentReceipt:
    return SubagentReceipt(
        assignment_id=assignment.assignment_id,
        task_id=assignment.task.task_id,
        role=assignment.task.role,
        status=SwarmReceiptStatus.FAILED,
        produced_refs=[],
        error=error,
        error_code=error_code,
    )


async def _wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached within timeout")


@pytest.fixture(autouse=True)
def _fresh_governor():
    """每个用例独享熔断器（loop 绑定信号量不可跨用例复用）。"""
    from app.services.agent_swarm import orchestrator as orch_mod

    orch_mod.reset_swarm_concurrency_governor_for_tests()
    yield
    orch_mod.reset_swarm_concurrency_governor_for_tests()


# ─────────────────────────── 契约边界（Zero Big Data） ───────────────────────────


@pytest.mark.asyncio
async def test_projection_rejects_non_ref_strings():
    """投影 relevant_refs 必须是 ref: 提货券 —— 非 ref 字串 fail-closed 拒绝。"""
    with pytest.raises(ValidationError):
        _proj("s1", relevant_refs=["{\"geojson\": \"<3MB payload>\"}"])


@pytest.mark.asyncio
async def test_projection_truncates_oversized_facts():
    big = "x" * 5000
    proj = _proj("s1", facts=[big])
    assert len(proj.facts[0]) <= 240


@pytest.mark.asyncio
async def test_receipt_rejects_non_ref_outputs_and_truncates_summary():
    with pytest.raises(ValidationError):
        SubagentReceipt(
            assignment_id="asg-1",
            task_id="t1",
            role=SwarmSpecialistRole.DATA_HUNTER,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=["raw-grid-binary-blob"],
        )
    r = SubagentReceipt(
        assignment_id="asg-1",
        task_id="t1",
        role=SwarmSpecialistRole.AUDIT_JUDGE,
        status=SwarmReceiptStatus.FAILED,
        produced_refs=["ref:ok"],
        summary="y" * 9999,
        error="e" * 9999,
    )
    assert len(r.summary) <= 400 and len(r.error) <= 300


@pytest.mark.asyncio
async def test_descriptor_rejects_over_dependencies():
    with pytest.raises(ValidationError):
        _task(
            "t",
            SwarmSpecialistRole.COMPUTE_SPECIALIST,
            depends_on=tuple(f"d{i}" for i in range(7)),
        )


# ─────────────────────────── 任务分解与 DAG 映射 ───────────────────────────


@pytest.mark.asyncio
async def test_flood_siting_decomposition_dag():
    """防汛选址综合任务 → 带依赖关系的子任务图（角色/依赖/环检全过）。"""
    goal = "在岷江流域开展防汛风险分析，完成应急避难所选址适宜性评估并输出防汛专题制图"
    proj = _proj("s1", goal_summary=goal)
    tasks = HeuristicSpatialDecomposer().decompose(goal, proj)
    dag = validate_swarm_graph(tasks)  # 唯一 id / 依赖存在 / 无环 / 上限

    by_id = {t.task_id: t for t in tasks}
    assert "swarm.data.base_geo" in by_id
    assert "swarm.compute.overlay" in by_id
    assert "swarm.compute.siting" in by_id
    assert "swarm.cartography.compose" in by_id
    assert "swarm.audit.judge" in by_id
    assert by_id["swarm.data.base_geo"].role == SwarmSpecialistRole.DATA_HUNTER
    assert by_id["swarm.compute.overlay"].role == SwarmSpecialistRole.COMPUTE_SPECIALIST
    assert by_id["swarm.cartography.compose"].role == (
        SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST
    )
    assert by_id["swarm.audit.judge"].role == SwarmSpecialistRole.AUDIT_JUDGE
    # 水文/降雨类专题词 → 专题数据任务并入 overlay 依赖
    theme = by_id.get("swarm.data.theme")
    assert theme is not None
    assert set(by_id["swarm.compute.overlay"].depends_on) >= {
        "swarm.data.base_geo",
        "swarm.data.theme",
    }
    assert by_id["swarm.compute.siting"].depends_on == ("swarm.compute.overlay",)
    assert by_id["swarm.cartography.compose"].depends_on == ("swarm.compute.siting",)
    assert by_id["swarm.audit.judge"].optional is True
    assert dag["nodes"]  # machine 兼容形态


@pytest.mark.asyncio
async def test_generic_goal_falls_back_to_four_phase_pipeline():
    goal = "帮我做一个跨部门的综合空间任务"
    tasks = HeuristicSpatialDecomposer().decompose(goal, _proj("s1", goal_summary=goal))
    roles = [t.role for t in tasks]
    assert roles == [
        SwarmSpecialistRole.DATA_HUNTER,
        SwarmSpecialistRole.COMPUTE_SPECIALIST,
        SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
        SwarmSpecialistRole.AUDIT_JUDGE,
    ]
    validate_swarm_graph(tasks)


@pytest.mark.asyncio
async def test_cyclic_and_dangling_decompositions_rejected():
    cyc = [
        _task("a", SwarmSpecialistRole.DATA_HUNTER, depends_on=("b",)),
        _task("b", SwarmSpecialistRole.COMPUTE_SPECIALIST, depends_on=("a",)),
    ]
    with pytest.raises(SwarmContractError):
        validate_swarm_graph(cyc)
    dangling = [_task("a", SwarmSpecialistRole.DATA_HUNTER, depends_on=("ghost",))]
    with pytest.raises(SwarmContractError):
        validate_swarm_graph(dangling)
    too_many = [
        _task(f"t{i}", SwarmSpecialistRole.DATA_HUNTER)
        for i in range(MAX_SWARM_TASKS + 1)
    ]
    with pytest.raises(SwarmContractError):
        validate_swarm_graph(too_many)
    orch = _orch("s1", ScriptedRuntime(), tasks_override=cyc)
    with pytest.raises(SwarmContractError):
        await orch.run_swarm("任意目标")


# ─────────────────────────── 并发调度与队列背压 ───────────────────────────


@pytest.mark.asyncio
async def test_concurrency_capped_at_three_with_pipeline_wake():
    """5 个就绪子任务：严格 ≤3 在飞，其余排队，槽位释放即流水线唤醒。"""
    counter = _Counter()
    gates = {f"t{i}": asyncio.Event() for i in range(5)}
    runtime = ScriptedRuntime(
        {tid: gate for tid, gate in gates.items()}, counter=counter
    )
    orch = _orch(
        "s1",
        runtime,
        tasks_override=[
            _task(f"t{i}", SwarmSpecialistRole.DATA_HUNTER) for i in range(5)
        ],
    )
    run = asyncio.ensure_future(orch.run_swarm("并发背压"))
    try:
        await _wait_until(
            lambda: counter.inflight == MAX_SWARM_CONCURRENCY
            and get_swarm_concurrency_governor().snapshot()["active_count"] == 3
        )
        assert counter.max_inflight == MAX_SWARM_CONCURRENCY
        snap = orch.status_snapshot()
        assert snap.counts.get(NodeState.RUNNING) == 3
        assert snap.counts.get(NodeState.READY) == 2  # 背压排队中

        gates["t0"].set()  # 释放 1 个槽位 → 队首唤醒
        await _wait_until(lambda: len(runtime.start_order) == 4)
        assert counter.inflight == 3
        gates["t1"].set()
        gates["t2"].set()
        await _wait_until(lambda: len(runtime.start_order) == 5)
    finally:
        for gate in gates.values():
            gate.set()
    status = await run
    assert status.state == "succeeded"
    assert counter.max_inflight <= MAX_SWARM_CONCURRENCY
    assert len(status.manifest.entries) == 5


@pytest.mark.asyncio
async def test_cluster_governor_is_global_across_orchestrators():
    """双编排器并发派发：集群全局在飞仍 ≤3（熔断是进程级而非 run 级）。"""
    counter = _Counter()
    gates = {f"o{k}-t{i}": asyncio.Event() for k in range(2) for i in range(2)}
    runs = []
    for k in range(2):
        rt = ScriptedRuntime(
            {tid: g for tid, g in gates.items() if tid.startswith(f"o{k}-")},
            counter=counter,
        )
        tasks = [
            _task(f"o{k}-t{i}", SwarmSpecialistRole.DATA_HUNTER) for i in range(2)
        ]
        orch = _orch(f"s{k}", rt, tasks_override=tasks)
        runs.append(asyncio.ensure_future(orch.run_swarm(f"目标{k}")))
    try:
        await _wait_until(lambda: counter.inflight == 3)
        assert counter.max_inflight <= MAX_SWARM_CONCURRENCY
        assert get_swarm_concurrency_governor().snapshot()["active_count"] == 3
    finally:
        for gate in gates.values():
            gate.set()
    statuses = await asyncio.gather(*runs)
    assert all(s.state == "succeeded" for s in statuses)


@pytest.mark.asyncio
async def test_dependency_order_and_upstream_receipt_delivery():
    """下游必须等全部直接上游结算；上游提货券经 assignment 注入下游。"""
    seen_upstream: dict[str, list[str]] = {}
    order: list[str] = []

    def behavior_for(tid: str):
        async def _go(assignment: Any) -> SubagentReceipt:
            seen_upstream[tid] = [r.task_id for r in assignment.upstream_receipts]
            order.append(f"start:{tid}")
            await asyncio.sleep(0.01)
            order.append(f"end:{tid}")
            return SubagentReceipt(
                assignment_id=assignment.assignment_id,
                task_id=tid,
                role=assignment.task.role,
                status=SwarmReceiptStatus.SUCCEEDED,
                produced_refs=[f"ref:out-{tid}"],
            )

        return _go

    tasks = _linear_tasks(3, role=SwarmSpecialistRole.COMPUTE_SPECIALIST)
    runtime = ScriptedRuntime({t.task_id: behavior_for(t.task_id) for t in tasks})
    orch = _orch("s1", runtime, tasks_override=tasks)
    status = await orch.run_swarm("链式依赖")
    assert status.state == "succeeded"
    assert order.index("end:t0") < order.index("start:t1")
    assert order.index("end:t1") < order.index("start:t2")
    assert seen_upstream["t1"] == ["t0"]
    assert seen_upstream["t2"] == ["t1"]


@pytest.mark.asyncio
async def test_priority_orders_dispatch_under_backpressure():
    """容量不足时按 priority 降序派发（高优先级先占槽）。"""
    gates = {tid: asyncio.Event() for tid in ("low", "mid", "high", "higher")}
    counter = _Counter()
    calls: list[str] = []

    async def _gated(assignment: Any) -> SubagentReceipt:
        calls.append(assignment.task.task_id)
        await gates[assignment.task.task_id].wait()
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id=assignment.task.task_id,
            role=assignment.task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=[f"ref:out-{assignment.task.task_id}"],
        )

    rt = ScriptedRuntime({tid: _gated for tid in gates}, counter=counter)
    prios = {"low": 1, "mid": 5, "high": 8, "higher": 10}
    tasks = [
        _task(tid, SwarmSpecialistRole.DATA_HUNTER, priority=p)
        for tid, p in prios.items()
    ]
    orch = _orch("s1", rt, tasks_override=tasks)
    run = asyncio.ensure_future(orch.run_swarm("优先级"))
    try:
        await _wait_until(lambda: counter.inflight == 3)
        assert calls[:3] == ["higher", "high", "mid"]  # low 被背压
    finally:
        for gate in gates.values():
            gate.set()
    status = await run
    assert status.state == "succeeded"


# ─────────────────────────── 失败隔离 / 重试 / 降级 ───────────────────────────


@pytest.mark.asyncio
async def test_failure_skips_only_downstream_closure():
    """菱形图 t1→(t2,t4), t2→t3；t1 失败 → 仅闭包 {t2,t3,t4} SKIPPED，旁支成功。"""
    hits = {"n": 0}

    def _non_retryable_boom(assignment: Any) -> SubagentReceipt:
        hits["n"] += 1
        return _failed_receipt(
            assignment, error_code="non_retryable", error="data source exploded"
        )

    tasks = [
        _task("t1", SwarmSpecialistRole.DATA_HUNTER),
        _task("t2", SwarmSpecialistRole.COMPUTE_SPECIALIST, depends_on=("t1",)),
        _task("t3", SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST, depends_on=("t2",)),
        _task("t4", SwarmSpecialistRole.AUDIT_JUDGE, depends_on=("t1",)),
        _task("sib", SwarmSpecialistRole.DATA_HUNTER),
    ]
    rt = ScriptedRuntime({"t1": _non_retryable_boom})
    orch = _orch("s1", rt, tasks_override=tasks)
    status = await orch.run_swarm("失败隔离")
    assert status.state == "failed"  # t1 非 optional
    counts = status.counts
    assert counts.get(NodeState.FAILED) == 1
    assert counts.get(NodeState.SKIPPED) == 3
    assert counts.get(NodeState.SUCCEEDED) == 1
    assert "sib" in rt.calls  # 旁支不受牵连
    assert hits["n"] == 1  # non_retryable 券不重试


@pytest.mark.asyncio
async def test_transient_failure_retries_then_succeeds():
    attempts = {"n": 0}

    def _flaky(assignment: Any) -> SubagentReceipt:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("transient timeout")
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id="t1",
            role=assignment.task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=["ref:recovered"],
        )

    rt = ScriptedRuntime({"t1": _flaky})
    orch = _orch(
        "s1",
        rt,
        tasks_override=[
            _task("t1", SwarmSpecialistRole.DATA_HUNTER, max_retries=2)
        ],
    )
    status = await orch.run_swarm("瞬时故障")
    assert status.state == "succeeded"
    assert attempts["n"] == 2
    assert status.manifest.entries[0].ref_id == "ref:recovered"


@pytest.mark.asyncio
async def test_optional_task_degrades_and_downstream_proceeds():
    """optional 任务重试穷尽 → 降级（SKIPPED 结算），下游照常交付，集群 partial。"""
    attempts = {"n": 0}

    def _always_fail(assignment: Any) -> SubagentReceipt:
        attempts["n"] += 1
        raise RuntimeError("audit service down")

    tasks = [
        _task("root", SwarmSpecialistRole.DATA_HUNTER),
        _task(
            "audit",
            SwarmSpecialistRole.AUDIT_JUDGE,
            depends_on=("root",),
            optional=True,
            max_retries=1,
            capability="swarm.audit.judge",
        ),
        _task(
            "leaf", SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST, depends_on=("root",)
        ),
    ]
    rt = ScriptedRuntime({"audit": _always_fail})
    orch = _orch("s1", rt, tasks_override=tasks)
    status = await orch.run_swarm("审计降级")
    assert attempts["n"] == 2  # 1 + max_retries
    assert status.state == "partial"
    assert "swarm.audit.judge" in status.manifest.failed_capabilities
    assert "leaf" in rt.calls  # 下游未被击穿


@pytest.mark.asyncio
async def test_destructive_task_never_auto_retried():
    attempts = {"n": 0}

    def _boom(assignment: Any) -> SubagentReceipt:
        attempts["n"] += 1
        raise TimeoutError("destructive step timeout")

    rt = ScriptedRuntime({"t1": _boom})
    orch = _orch(
        "s1",
        rt,
        tasks_override=[
            _task(
                "t1",
                SwarmSpecialistRole.COMPUTE_SPECIALIST,
                side_effect="destructive",
                max_retries=2,  # 契约上限；即便预算充裕 destructive 也不自动重驱
            )
        ],
    )
    status = await orch.run_swarm("破坏性纪律")
    assert attempts["n"] == 1  # at-most-once：即便可重试错误也不自动重驱
    assert status.state == "failed"


@pytest.mark.asyncio
async def test_timeout_hard_deadline_isolates_and_settles():
    gate = asyncio.Event()

    async def _hang(assignment: Any) -> SubagentReceipt:
        await gate.wait()
        raise AssertionError("should have been cancelled")

    tasks = [
        _task("slow", SwarmSpecialistRole.DATA_HUNTER, timeout_s=0.05),
        _task("down", SwarmSpecialistRole.COMPUTE_SPECIALIST, depends_on=("slow",)),
        _task("other", SwarmSpecialistRole.DATA_HUNTER),
    ]
    rt = ScriptedRuntime({"slow": _hang})
    orch = _orch("s1", rt, tasks_override=tasks)
    started = time.monotonic()
    status = await orch.run_swarm("超时熔断")
    elapsed = time.monotonic() - started
    gate.set()  # 清理（若泄漏会挂到轮询超时之外）
    assert elapsed < 5.0
    assert status.state == "failed"
    assert status.counts.get(NodeState.SKIPPED) == 1  # down
    assert status.counts.get(NodeState.SUCCEEDED) == 1  # other


@pytest.mark.asyncio
async def test_cancel_settles_cluster_and_drains_slots():
    gates = {tid: asyncio.Event() for tid in ("a", "b")}
    counter = _Counter()
    rt = ScriptedRuntime(gates, counter=counter)
    orch = _orch(
        "s1",
        rt,
        tasks_override=[
            _task("a", SwarmSpecialistRole.DATA_HUNTER),
            _task("b", SwarmSpecialistRole.COMPUTE_SPECIALIST),
        ],
    )
    run = asyncio.ensure_future(orch.run_swarm("取消"))
    await _wait_until(lambda: counter.inflight == 2)
    await orch.cancel()
    status = await run
    assert status.state == "cancelled"
    assert counter.inflight == 0
    assert get_swarm_concurrency_governor().snapshot()["active_count"] == 0
    for gate in gates.values():
        gate.set()


@pytest.mark.asyncio
async def test_heartbeat_notes_do_not_break_dispatch():
    async def _beating(assignment: Any) -> SubagentReceipt:
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id="t1",
            role=assignment.task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=["ref:beat"],
            heartbeats=2,
        )

    rt = ScriptedRuntime({"t1": _beating})
    orch = _orch(
        "s1", rt, tasks_override=[_task("t1", SwarmSpecialistRole.DATA_HUNTER)]
    )
    status = await orch.run_swarm("心跳")
    assert status.state == "succeeded"


# ─────────────────────────── 提货券纪律与归一化 ───────────────────────────


def _build_assignment() -> Any:
    from app.services.agent_swarm.delegation_contracts import SpecialistAssignment

    return SpecialistAssignment(
        assignment_id="asg-t1-x",
        task=_task("t1", SwarmSpecialistRole.DATA_HUNTER, expected_outputs=("out",)),
        projection=_proj("s1"),
        upstream_receipts=[],
        issued_at=time.time(),
    )


@pytest.mark.asyncio
async def test_normalize_receipt_strips_payload_and_enforces_ref_discipline():
    """runtime 试图夹带非 ref 载荷 / 超长摘要 → 归一化剔除截断。"""
    assignment = _build_assignment()
    raw = {
        "assignment_id": assignment.assignment_id,
        "task_id": "t1",
        "role": SwarmSpecialistRole.DATA_HUNTER,
        "status": "succeeded",
        "produced_refs": ["ref:good", "RAW_GEOJSON_PAYLOAD_NOT_A_REF"],
        "summary": "z" * 5000,
    }
    receipt = normalize_receipt(assignment, raw)
    assert receipt.produced_refs == ["ref:good"]
    assert len(receipt.summary) <= 400
    assert receipt.status == SwarmReceiptStatus.SUCCEEDED

    # 声明成功但期望产出无任何有效券 → 诚实降级
    empty = normalize_receipt(
        assignment, {**raw, "produced_refs": ["NOT_A_REF_EITHER"]}
    )
    assert empty.status == SwarmReceiptStatus.DEGRADED
    assert empty.degraded is True


@pytest.mark.asyncio
async def test_dispatcher_wraps_runtime_exception_as_failed_receipt():
    class _ExplodingRuntime:
        async def execute(self, assignment, *, on_heartbeat=None):
            raise RuntimeError("subprocess crashed")

    assignment = _build_assignment()
    receipt = await SpecialistDispatcher(_ExplodingRuntime()).dispatch(assignment)
    assert receipt.status == SwarmReceiptStatus.FAILED
    assert receipt.error_code == "retryable"


# ─────────────────────────── 聚合总线与计划回写 ───────────────────────────


class FakeStore:
    """最小 SessionStoreProtocol 假件（alias/get/overwrite/store/set_alias/ref_exists）。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.aliases: dict[str, str] = {}
        self._seq = 0

    async def resolve_alias(self, session_id: str, alias: str) -> str:
        return self.aliases.get(f"{session_id}:{alias}", alias)

    async def get(self, session_id: str, ref_id: str) -> Any:
        return self.data.get(f"{session_id}:{ref_id}")

    async def overwrite(self, session_id: str, ref_id: str, payload: Any) -> bool:
        key = f"{session_id}:{ref_id}"
        if key in self.data:
            self.data[key] = payload
            return True
        return False

    async def store(self, session_id: str, payload: Any, prefix: str = "data") -> str:
        self._seq += 1
        ref = f"ref:{prefix}-{self._seq}"
        self.data[f"{session_id}:{ref}"] = payload
        return ref

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        self.aliases[f"{session_id}:{alias}"] = ref_id

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        return f"{session_id}:{ref_id}" in self.data


@pytest.mark.asyncio
async def test_aggregation_manifest_and_session_plan_merge():
    """终态聚合：manifest 提货单 + 会话锁内 CapabilityProgress 回写 + manifest_ref。"""
    store = FakeStore()
    tasks = _linear_tasks(2, role=SwarmSpecialistRole.COMPUTE_SPECIALIST)
    for i, t in enumerate(tasks):
        t.capability = f"cap.{i}"
    rt = ScriptedRuntime()
    sink = SessionPlanSwarmSink(store=store)
    orch = _orch("s1", rt, tasks_override=tasks, sink=sink)
    status = await orch.run_swarm("聚合回写")
    assert status.state == "succeeded"
    assert [e.ref_id for e in status.manifest.entries] == [
        "ref:out-t0",
        "ref:out-t1",
    ]
    assert status.manifest_ref and status.manifest_ref.startswith("ref:swarm-")
    plan_ref = await store.resolve_alias("s1", "session-plan")
    plan_payload = await store.get("s1", plan_ref)
    assert plan_payload is not None
    progress = {row["capability"]: row for row in plan_payload["progress"]}
    assert progress["cap.0"]["status"] == "complete"
    assert progress["cap.0"]["bound_ref"] == "ref:out-t0"


@pytest.mark.asyncio
async def test_aggregator_fail_open_on_sink_error():
    class _BrokenSink:
        async def merge(self, manifest: Any) -> None:
            raise RuntimeError("plan store down")

    tasks = [_task("t1", SwarmSpecialistRole.DATA_HUNTER, capability="cap.x")]
    rt = ScriptedRuntime()
    orch = _orch("s1", rt, tasks_override=tasks, sink=_BrokenSink())
    status = await orch.run_swarm("回写降级")
    assert status.state == "succeeded"  # fail-open：回写失败不影响 settle
    assert status.manifest is not None
    assert status.manifest_ref is None


@pytest.mark.asyncio
async def test_aggregator_prunes_missing_refs_via_store_probe():
    store = FakeStore()
    tasks = [_task("t1", SwarmSpecialistRole.DATA_HUNTER, capability="cap.x")]
    rt = ScriptedRuntime()  # 产出 ref:out-t1，但 store 中不存在
    agg = SwarmAggregator(sink=NullSink(), store=store)
    orch = SwarmOrchestrator(
        "s1",
        decomposer=_FixedDecomposer(tasks),
        dispatcher=SpecialistDispatcher(rt),
        aggregator=agg,
    )
    status = await orch.run_swarm("验券")
    assert status.state == "succeeded"
    assert status.manifest.entries == []
    assert status.manifest.dropped_refs == ["ref:out-t1"]


# ─────────────────────────── SwarmBridge 网关守卫 ───────────────────────────


def _bridge_orch_factory(runtime: ScriptedRuntime):
    def _factory(session_id: str) -> SwarmOrchestrator:
        return _orch(session_id, runtime)

    return _factory


@pytest.mark.asyncio
async def test_bridge_disabled_by_default_refuses(monkeypatch):
    from app.agent_pi_bridge import SwarmBridge

    monkeypatch.delenv("GIS_SWARM_ORCHESTRATOR", raising=False)
    bridge = SwarmBridge(
        "s1", orchestrator_factory=_bridge_orch_factory(ScriptedRuntime())
    )
    out = await bridge.delegate_compound_task("防汛选址综合任务")
    assert out == {"delegated": False, "reason": "swarm_disabled"}


@pytest.mark.asyncio
async def test_bridge_refuses_when_master_turn_active(monkeypatch):
    from app.agent_pi_bridge import SwarmBridge, _active_turns, _ActiveTurnEntry

    monkeypatch.setenv("GIS_SWARM_ORCHESTRATOR", "1")
    sid = _sid()
    _active_turns[sid] = _ActiveTurnEntry(sid, "turn-1", None)
    try:
        bridge = SwarmBridge(
            sid, orchestrator_factory=_bridge_orch_factory(ScriptedRuntime())
        )
        out = await bridge.delegate_compound_task("目标")
        assert out["delegated"] is False
        assert out["reason"] == "master_turn_active"
    finally:
        _active_turns.pop(sid, None)


@pytest.mark.asyncio
async def test_bridge_happy_path_returns_bounded_summary(monkeypatch):
    from app.agent_pi_bridge import SwarmBridge

    monkeypatch.setenv("GIS_SWARM_ORCHESTRATOR", "1")
    rt = ScriptedRuntime()
    bridge = SwarmBridge("s1", orchestrator_factory=_bridge_orch_factory(rt))
    out = await bridge.delegate_compound_task("开展防汛风险分析、选址评估与专题制图")
    assert out["delegated"] is True
    assert out["state"] == "succeeded"
    assert out["run_id"]
    assert all(ref.startswith("ref:") for ref in out["refs"])
    assert "swarm.audit.judge" in rt.calls


@pytest.mark.asyncio
async def test_bridge_rejects_oversized_goal(monkeypatch):
    from app.agent_pi_bridge import SwarmBridge

    monkeypatch.setenv("GIS_SWARM_ORCHESTRATOR", "1")
    bridge = SwarmBridge(
        "s1", orchestrator_factory=_bridge_orch_factory(ScriptedRuntime())
    )
    out = await bridge.delegate_compound_task("g" * 2001)
    assert out == {"delegated": False, "reason": "invalid_goal"}


# ─────────────────────────── 状态快照 ───────────────────────────


@pytest.mark.asyncio
async def test_status_snapshot_shape_and_run_id_stability():
    gate = asyncio.Event()
    rt = ScriptedRuntime({"t1": gate})
    orch = _orch(
        "s9", rt, tasks_override=[_task("t1", SwarmSpecialistRole.DATA_HUNTER)]
    )
    run = asyncio.ensure_future(orch.run_swarm("快照", run_id="swarm-fixed"))
    await _wait_until(lambda: bool(rt.calls))
    snap = orch.status_snapshot()
    assert isinstance(snap, SwarmExecutionStatus)
    assert snap.run_id == "swarm-fixed"
    assert snap.state == "running"
    assert snap.counts.get(NodeState.RUNNING) == 1
    gate.set()
    status = await run
    assert status.run_id == "swarm-fixed"
    assert status.state == "succeeded"
    assert status.finished_at >= status.started_at
