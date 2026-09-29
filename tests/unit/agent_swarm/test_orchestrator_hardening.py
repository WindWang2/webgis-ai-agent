"""SwarmOrchestrator 异常路径与结算口径硬化（ADR-0187）。

与 tests/unit/test_swarm_orchestrator.py 互补：那套件覆盖 happy path 与
主要隔离语义；本文件专盯异常/边界路径 —— 入口校验、重试穷尽、launcher
崩溃兜底、协作式取消收敛、不可达任务清扫、裁决口径与观测面。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Optional

import pytest

from app.services.agent_swarm.aggregator import NullSink, SwarmAggregator
from app.services.agent_swarm.delegation_contracts import (
    MAX_GOAL_CHARS,
    MAX_SWARM_TASKS,
    SwarmContractError,
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SubagentReceipt,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.agent_swarm.dispatcher import SpecialistDispatcher
from app.services.agent_swarm.orchestrator import (
    HeuristicSpatialDecomposer,
    SwarmConcurrencyGovernor,
    SwarmOrchestrator,
    WorkflowMachineGraphPort,
)
from app.services.workflow_runtime.contracts import NodeState


# ─────────────────────────── 假件与工厂 ───────────────────────────


class RecordingRuntime:
    """记录调用的 SpecialistRuntime 假件（behavior 可脚本化）。

    behavior 为 dict 时原样返回（经 dispatcher normalize_receipt 归一化）；
    为 SubagentReceipt 时直接返回；为 BaseException 时抛出。
    """

    def __init__(self, behaviors: Optional[dict[str, Any]] = None) -> None:
        self.behaviors = behaviors or {}
        self.calls: list[str] = []

    async def execute(
        self,
        assignment: Any,
        *,
        on_heartbeat: Optional[Callable[[str], None]] = None,
    ) -> Any:
        task_id = assignment.task.task_id
        self.calls.append(task_id)
        behavior = self.behaviors.get(task_id)
        if isinstance(behavior, BaseException):
            raise behavior
        if isinstance(behavior, SubagentReceipt):
            return behavior
        if isinstance(behavior, dict):
            return behavior
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id=task_id,
            role=assignment.task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=[f"ref:out-{task_id}"],
            summary=f"done {task_id}",
        )


class GatedRuntime(RecordingRuntime):
    """asyncio.Event 门控假件（取消时序测试用）。"""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def execute(
        self,
        assignment: Any,
        *,
        on_heartbeat: Optional[Callable[[str], None]] = None,
    ) -> SubagentReceipt:
        self.calls.append(assignment.task.task_id)
        self.started.set()
        await self.gate.wait()
        return SubagentReceipt(
            assignment_id=assignment.assignment_id,
            task_id=assignment.task.task_id,
            role=assignment.task.role,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=[f"ref:out-{assignment.task.task_id}"],
        )


class ExplodingGovernor:
    """acquire 抛非取消异常的熔断器假件（launcher 崩溃兜底面）。"""

    def __init__(self) -> None:
        self.released: list[str] = []

    async def acquire(self, assignment_id: str, task_id: str) -> None:
        raise RuntimeError("governor semaphore broken")

    def release(self, assignment_id: str) -> None:
        self.released.append(assignment_id)

    def snapshot(self) -> dict:
        return {"max_concurrency": 1, "active_count": 0, "active_task_ids": []}


class _FixedDecomposer:
    def __init__(self, tasks: list[SwarmTaskDescriptor]) -> None:
        self._tasks = tasks

    def decompose(
        self, root_goal: str, projection: WorldStateProjection
    ) -> list[SwarmTaskDescriptor]:
        return self._tasks


def _task(task_id: str, **kw: Any) -> SwarmTaskDescriptor:
    kw.setdefault("role", SwarmSpecialistRole.DATA_HUNTER)
    kw.setdefault("goal", f"task {task_id}")
    return SwarmTaskDescriptor(task_id=task_id, **kw)


def _orch(
    runtime: Any,
    tasks: list[SwarmTaskDescriptor],
    *,
    governor: Any = None,
    graph_port: Any = None,
    sink: Any = None,
) -> SwarmOrchestrator:
    return SwarmOrchestrator(
        f"s-{uuid.uuid4().hex[:8]}",
        decomposer=_FixedDecomposer(tasks),
        dispatcher=SpecialistDispatcher(runtime),
        aggregator=SwarmAggregator(sink=sink if sink is not None else NullSink()),
        governor=governor,
        graph_port=graph_port,
    )


def _failed(error_code: str) -> SubagentReceipt:
    return SubagentReceipt(
        assignment_id="asg-x",
        task_id="t1",
        role=SwarmSpecialistRole.DATA_HUNTER,
        status=SwarmReceiptStatus.FAILED,
        error=f"{error_code} failure",
        error_code=error_code,
    )


@pytest.fixture(autouse=True)
def _fresh_governor():
    from app.services.agent_swarm import orchestrator as orch_mod

    orch_mod.reset_swarm_concurrency_governor_for_tests()
    yield
    orch_mod.reset_swarm_concurrency_governor_for_tests()


# ─────────────────────────── 入口校验 ───────────────────────────


class TestRunSwarmEntryGuards:
    async def test_empty_root_goal_rejected(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        with pytest.raises(SwarmContractError, match="root_goal 不能为空"):
            await orch.run_swarm("")

    async def test_whitespace_root_goal_rejected(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        with pytest.raises(SwarmContractError, match="root_goal 不能为空"):
            await orch.run_swarm("   \n  ")

    async def test_empty_decomposition_rejected(self):
        orch = _orch(RecordingRuntime(), [])
        with pytest.raises(SwarmContractError, match="分解结果为空"):
            await orch.run_swarm("有目标但无任务")

    async def test_over_cap_decomposition_rejected(self):
        tasks = [_task(f"t{i}") for i in range(MAX_SWARM_TASKS + 1)]
        orch = _orch(RecordingRuntime(), tasks)
        with pytest.raises(SwarmContractError, match="超过上限"):
            await orch.run_swarm("任务超限")

    async def test_duplicate_task_ids_rejected(self):
        orch = _orch(RecordingRuntime(), [_task("dup"), _task("dup")])
        with pytest.raises(SwarmContractError, match="task_id 重复"):
            await orch.run_swarm("重复 id")

    async def test_dangling_dependency_rejected(self):
        orch = _orch(RecordingRuntime(), [_task("t1", depends_on=("ghost",))])
        with pytest.raises(SwarmContractError, match="未声明的 task_id"):
            await orch.run_swarm("悬空依赖")

    async def test_cyclic_dependency_rejected(self):
        tasks = [_task("a", depends_on=("b",)), _task("b", depends_on=("a",))]
        orch = _orch(RecordingRuntime(), tasks)
        with pytest.raises(SwarmContractError, match="存在环"):
            await orch.run_swarm("成环")


# ─────────────────────────── 重试与失败隔离 ───────────────────────────


class TestRetrySemantics:
    async def test_retryable_failure_exhausts_max_attempts_then_fails(self):
        rt = RecordingRuntime({"t1": _failed("retryable")})
        orch = _orch(rt, [_task("t1", max_retries=2)])
        status = await orch.run_swarm("重试穷尽")
        assert rt.calls == ["t1", "t1", "t1"]  # 1 + min(2, MAX_RETRIES)
        assert status.state == "failed"
        assert status.counts.get(NodeState.FAILED) == 1
        # t1 未声明 capability：失败披露面无 capability 可列
        assert status.manifest is not None
        assert status.manifest.failed_capabilities == []

    async def test_non_retryable_failure_settles_after_single_attempt(self):
        rt = RecordingRuntime({"t1": _failed("non_retryable")})
        orch = _orch(rt, [_task("t1", max_retries=2)])
        status = await orch.run_swarm("禁重试")
        assert rt.calls == ["t1"]
        assert status.state == "failed"

    async def test_timeout_error_code_is_retryable(self):
        rt = RecordingRuntime({"t1": _failed("timeout")})
        orch = _orch(rt, [_task("t1", max_retries=1)])
        status = await orch.run_swarm("超时重试")
        assert rt.calls == ["t1", "t1"]
        assert status.state == "failed"

    async def test_zero_retries_single_attempt(self):
        rt = RecordingRuntime({"t1": _failed("retryable")})
        orch = _orch(rt, [_task("t1", max_retries=0)])
        status = await orch.run_swarm("零重试")
        assert rt.calls == ["t1"]
        assert status.state == "failed"

    async def test_required_failure_makes_cluster_failed(self):
        rt = RecordingRuntime({"t1": _failed("non_retryable")})
        orch = _orch(rt, [_task("t1"), _task("t2")])
        status = await orch.run_swarm("硬失败")
        assert status.state == "failed"
        assert status.counts.get(NodeState.SUCCEEDED) == 1

    async def test_downstream_of_failed_task_is_skipped_and_disclosed(self):
        rt = RecordingRuntime({"t1": _failed("non_retryable")})
        tasks = [
            _task("t1", capability="cap.up"),
            _task("t2", capability="cap.down", depends_on=("t1",)),
        ]
        orch = _orch(rt, tasks)
        status = await orch.run_swarm("下游隔离")
        assert status.tasks["t2"]["status"] == "skipped"
        assert status.tasks["t1"]["status"] == "failed"
        assert status.manifest is not None
        assert status.manifest.failed_capabilities == ["cap.up"]


# ─────────────────────────── launcher 崩溃兜底 ───────────────────────────


class TestLauncherCrashFallback:
    """_launcher 的最后防线：execute 之后的异常折算诚实 FAILED 崩溃券。

    崩溃缝选择：runtime 返回 ``wall_time_s="not-a-number"`` 的裸 dict，
    ``normalize_receipt`` 的 float() 转换抛 ValueError（dispatcher 的
    try 只包 runtime 调用，归一化异常直通编排器）—— 任务已处于 RUNNING，
    崩溃兜底的 FAILED 转移合法。
    """

    CRASH_PAYLOAD = {"status": "succeeded", "wall_time_s": "not-a-number"}

    async def test_normalize_crash_settles_as_failed_receipt(self):
        rt = RecordingRuntime({"t1": self.CRASH_PAYLOAD})
        orch = _orch(rt, [_task("t1")])
        status = await orch.run_swarm("归一化崩溃")
        assert status.state == "failed"
        assert status.tasks["t1"]["status"] == "failed"
        crash = orch._receipts["t1"]
        assert crash.error.startswith("launcher crash:")
        assert crash.error_code == "non_retryable"
        assert crash.produced_refs == []

    async def test_crash_path_does_not_retry(self):
        rt = RecordingRuntime({"t1": self.CRASH_PAYLOAD})
        orch = _orch(rt, [_task("t1", max_retries=2)])
        status = await orch.run_swarm("崩溃不重试")
        assert rt.calls == ["t1"]
        assert status.state == "failed"

    async def test_governor_acquire_failure_settles_failed_receipt(self):
        """acquire 相位兜底（H07/ADR-0216 清偿 PR #1529 登记的缺口）：
        governor.acquire 抛非取消异常 → 诚实 FAILED 崩溃券（真实失败原因
        入 receipt），集群裁决 failed —— 不再卡 READY / 静默判成功。"""
        governor = ExplodingGovernor()
        orch = _orch(RecordingRuntime(), [_task("t1")], governor=governor)
        status = await orch.run_swarm("熔断器故障")
        assert status.tasks["t1"]["status"] == "failed"
        crash = orch._receipts["t1"]
        assert crash.error.startswith("acquire crash:")
        assert crash.error_code == "non_retryable"
        assert status.state == "failed"
        # 未获取到槽位不得释放（信号量超发面）
        assert governor.released == []

    async def test_assignment_build_crash_settles_failed_receipt(self, monkeypatch):
        """assignment 构造相位（原 try 之外）崩溃 → 同样诚实 FAILED。"""
        from app.services.agent_swarm import orchestrator as orch_mod

        def _explode(*a, **kw):
            raise ValueError("bad descriptor")

        monkeypatch.setattr(orch_mod, "SpecialistAssignment", _explode)
        orch = _orch(RecordingRuntime(), [_task("t1")])
        status = await orch.run_swarm("派发单装配故障")
        assert status.tasks["t1"]["status"] == "failed"
        crash = orch._receipts["t1"]
        assert crash.error.startswith("assignment build crash:")
        assert crash.assignment_id.startswith("asg-unissued-")
        assert status.state == "failed"

    async def test_acquire_queue_deadline_settles_failed_not_stuck_ready(self):
        """acquire 排队受任务 deadline 约束：槽位永不释放时诚实 FAILED
        （timeout 券），不再无限等待 READY。"""
        governor = SwarmConcurrencyGovernor(max_concurrency=1)
        rt = GatedRuntime()
        tasks = [
            _task("t1", timeout_s=0.2),
            _task("t2", timeout_s=0.2),
        ]
        orch = _orch(rt, tasks, governor=governor)
        status = await orch.run_swarm("背压死线")
        # 槽位被 t1 长期占用（gate 未放行 → t1 自身超时释放），t2 排队
        # 超过自身 deadline → FAILED(timeout) 而非卡 READY
        assert status.tasks["t1"]["status"] == "failed"
        assert status.tasks["t2"]["status"] == "failed"
        assert status.tasks["t2"]["error_code"] == "timeout"
        assert status.state == "failed"
        assert governor.snapshot()["active_count"] == 0


# ─────────────────────────── 取消收敛 ───────────────────────────


class TestCancellation:
    async def test_cancel_before_run_is_noop(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        await orch.cancel()  # 未启动：无在飞 launcher，静默返回
        assert orch._cancelled is True

    async def test_cancel_during_run_settles_cancelled(self):
        rt = GatedRuntime()
        orch = _orch(rt, [_task("t1")])
        run = asyncio.ensure_future(orch.run_swarm("可取消任务"))
        await asyncio.wait_for(rt.started.wait(), timeout=2.0)
        await orch.cancel()
        rt.gate.set()
        status = await asyncio.wait_for(run, timeout=5.0)
        assert status.state == "cancelled"
        assert status.tasks["t1"]["status"] == "cancelled"
        assert status.tasks["t1"]["error_code"] == "cancelled"

    async def test_cancelled_flag_blocks_subsequent_dispatch(self):
        rt = GatedRuntime()
        tasks = [_task("t1"), _task("t2", depends_on=("t1",))]
        orch = _orch(rt, tasks)
        run = asyncio.ensure_future(orch.run_swarm("取消后续"))
        await asyncio.wait_for(rt.started.wait(), timeout=2.0)
        await orch.cancel()
        rt.gate.set()
        status = await asyncio.wait_for(run, timeout=5.0)
        # 上游 t1 结算为 CANCELLED —— ready_set 只认 SUCCEEDED/SKIPPED 为
        # 已完成上游，t2 就绪条件永不满足，落入不可达清扫结算 SKIPPED
        assert rt.calls == ["t1"]
        assert status.tasks["t1"]["status"] == "cancelled"
        assert status.tasks["t2"]["status"] == "skipped"
        assert status.state == "cancelled"

    async def test_cancel_during_governor_backpressure_settles_cancelled(self):
        """取消竞赛窗口：acquire 等信号量（背压）期间被取消 → CANCELLED 券。

        max_concurrency=1 + 两就绪任务：t1 门控在 execute 内占槽，t2 阻塞
        在 governor.acquire；此时 cancel → t2 走 launcher 的 acquire 取消
        分支（不等 execute），诚实结算 CANCELLED 而非崩溃。"""
        governor = SwarmConcurrencyGovernor(max_concurrency=1)
        rt = GatedRuntime()
        orch = _orch(rt, [_task("t1"), _task("t2")], governor=governor)
        run = asyncio.ensure_future(orch.run_swarm("背压取消"))
        await asyncio.wait_for(rt.started.wait(), timeout=2.0)
        await asyncio.sleep(0.05)  # 让 t2 进入 acquire 等待
        await orch.cancel()
        rt.gate.set()
        status = await asyncio.wait_for(run, timeout=5.0)
        assert rt.calls == ["t1"]  # t2 未进入 execute
        assert status.tasks["t2"]["status"] == "cancelled"
        assert status.tasks["t2"]["error_code"] == "cancelled"
        assert status.state == "cancelled"
        assert governor.snapshot()["active_count"] == 0

    async def test_active_ids_visible_during_run(self):
        """运行中快照有在飞任务（收尾后清空 —— 后者由 status 字段保证）。"""
        rt = GatedRuntime()
        orch = _orch(rt, [_task("t1")])
        run = asyncio.ensure_future(orch.run_swarm("在飞观测"))
        await asyncio.wait_for(rt.started.wait(), timeout=2.0)
        assert orch.status_snapshot().active_task_ids == ["t1"]
        await orch.cancel()
        rt.gate.set()
        status = await asyncio.wait_for(run, timeout=5.0)
        assert status.active_task_ids == []


# ─────────────────────────── 不可达清扫与裁决口径 ───────────────────────────


class StuckGraphPort(WorkflowMachineGraphPort):
    """ready_set 恒空的图端口假件（模拟不可达残留）。"""

    def ready_set(self, dag: dict, states: dict[str, str]) -> list[str]:
        return []


class TestUnreachableCleanupAndAdjudication:
    async def test_unreachable_pending_tasks_settled_skipped(self):
        orch = _orch(
            RecordingRuntime(), [_task("t1"), _task("t2")], graph_port=StuckGraphPort()
        )
        status = await orch.run_swarm("不可达")
        assert status.tasks["t1"]["status"] == "skipped"
        assert status.tasks["t2"]["status"] == "skipped"
        assert status.state == "partial"
        # 不可达任务从未派发：运行态里无任何 receipt（assignment_id 空）
        assert all(row["assignment_id"] == "" for row in status.tasks.values())

    async def test_all_succeeded_cluster_is_succeeded(self):
        orch = _orch(RecordingRuntime(), [_task("t1"), _task("t2")])
        status = await orch.run_swarm("全成功")
        assert status.state == "succeeded"
        assert status.counts == {NodeState.SUCCEEDED: 2}

    async def test_optional_failure_degrades_to_partial(self):
        rt = RecordingRuntime({"t2": _failed("non_retryable")})
        tasks = [_task("t1"), _task("t2", optional=True)]
        orch = _orch(rt, tasks)
        status = await orch.run_swarm("可选降级")
        assert status.state == "partial"
        assert status.tasks["t2"]["status"] == "skipped"
        assert status.manifest is not None
        assert status.manifest.failed_capabilities == []

    async def test_degraded_receipt_marks_task_degraded(self):
        degraded = SubagentReceipt(
            assignment_id="asg-x",
            task_id="t2",
            role=SwarmSpecialistRole.DATA_HUNTER,
            status=SwarmReceiptStatus.DEGRADED,
            degraded=True,
            produced_refs=["ref:out-t2"],
            error_code="non_compliant",
        )
        rt = RecordingRuntime({"t2": degraded})
        tasks = [_task("t1"), _task("t2", optional=True, capability="cap.opt")]
        orch = _orch(rt, tasks)
        status = await orch.run_swarm("降级券")
        assert status.state == "partial"
        assert status.tasks["t2"]["status"] == "skipped"
        # 降级产物仍入清单并披露 capability；t1 正常产物同列
        assert status.manifest is not None
        assert [e.ref_id for e in status.manifest.entries] == ["ref:out-t1", "ref:out-t2"]
        degraded_entry = next(
            e for e in status.manifest.entries if e.ref_id == "ref:out-t2"
        )
        assert degraded_entry.summary.startswith("[degraded]")
        assert status.manifest.failed_capabilities == ["cap.opt"]


class TestGraphPortFailClosed:
    """图端口：非法状态转移 fail-closed（Swarm 层不私放图数学）。"""

    def test_illegal_transition_raises_contract_error(self):
        port = WorkflowMachineGraphPort()
        with pytest.raises(SwarmContractError, match="非法状态转移"):
            port.check_transition(NodeState.READY, NodeState.FAILED)  # READY→FAILED 非法

    def test_legal_transition_passes_silently(self):
        port = WorkflowMachineGraphPort()
        assert port.check_transition(NodeState.PENDING, NodeState.READY) is None
        assert port.check_transition(NodeState.RUNNING, NodeState.FAILED) is None
        assert port.check_transition(NodeState.FAILED, NodeState.SKIPPED) is None

    def test_ready_set_and_closure_delegate_to_machine(self):
        port = WorkflowMachineGraphPort()
        dag = {
            "nodes": [
                {"node_id": "a", "depends_on": []},
                {"node_id": "b", "depends_on": ["a"]},
                {"node_id": "c", "depends_on": ["b"]},
            ],
            "edges": [],
        }
        states = {"a": NodeState.SUCCEEDED, "b": NodeState.PENDING, "c": NodeState.PENDING}
        assert port.ready_set(dag, states) == ["b"]
        assert port.downstream_closure(dag, {"a"}) == {"b", "c"}

    def test_build_dag_from_task_map(self):
        port = WorkflowMachineGraphPort()
        tasks = {
            "t1": _task("t1"),
            "t2": _task("t2", depends_on=("t1",)),
        }
        dag = port.build_dag(tasks)
        assert dag["nodes"] == [
            {"node_id": "t1", "depends_on": []},
            {"node_id": "t2", "depends_on": ["t1"]},
        ]


# ─────────────────────────── 观测面 ───────────────────────────


class TestStatusObservability:
    async def test_snapshot_before_run_raises(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        with pytest.raises(AssertionError, match="run_swarm 尚未启动"):
            orch.status_snapshot()

    async def test_snapshot_shape_after_run(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        status = await orch.run_swarm("观测面")
        snapshot = orch.status_snapshot()
        assert snapshot.run_id == status.run_id
        assert snapshot.active_task_ids == []
        assert snapshot.finished_at >= snapshot.started_at
        row = snapshot.tasks["t1"]
        assert set(row) == {
            "status", "side_effect", "assignment_id", "produced_refs",
            "error_code", "summary", "attempt",
        }
        assert row["status"] == "succeeded"
        assert row["side_effect"] == "pure"
        assert row["produced_refs"] == ["ref:out-t1"]
        assert row["attempt"] == 1  # receipt 未显式回填时的缺省 attempts

    async def test_explicit_run_id_is_honored(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        status = await orch.run_swarm("固定 run", run_id="run-fixed-001")
        assert status.run_id == "run-fixed-001"

    async def test_oversized_goal_truncated_in_status(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        status = await orch.run_swarm("g" * 5000)
        assert len(status.root_goal) == MAX_GOAL_CHARS

    async def test_counts_project_node_state_vocabulary(self):
        rt = RecordingRuntime({"t2": _failed("non_retryable")})
        tasks = [_task("t1"), _task("t2"), _task("t3", depends_on=("t2",))]
        orch = _orch(rt, tasks)
        status = await orch.run_swarm("计数口径")
        assert status.counts.get(NodeState.SUCCEEDED) == 1
        assert status.counts.get(NodeState.FAILED) == 1
        assert status.counts.get(NodeState.SKIPPED) == 1

    async def test_governor_slots_fully_released_after_run(self):
        governor = SwarmConcurrencyGovernor(max_concurrency=2)
        orch = _orch(RecordingRuntime(), [_task("t1"), _task("t2")], governor=governor)
        await orch.run_swarm("槽位释放")
        assert governor.snapshot()["active_count"] == 0

    async def test_null_sink_leaves_manifest_ref_none(self):
        orch = _orch(RecordingRuntime(), [_task("t1")])
        status = await orch.run_swarm("无回写")
        assert status.manifest_ref is None
        assert status.manifest is not None

    async def test_projection_defaults_to_goal_summary(self):
        rt = RecordingRuntime()
        orch = _orch(rt, [_task("t1")])
        status = await orch.run_swarm("投影缺省")
        assert status.session_id == orch._session_id
        assert status.root_goal == "投影缺省"
        # 未显式传 projection 时，世界态缺省 = 目标摘要（有界）
        assert orch._projection.goal_summary == "投影缺省"
        assert orch._projection.session_id == orch._session_id
        assert orch._projection.facts == []


# ─────────────────────────── 分解器（启发式相位） ───────────────────────────


class TestHeuristicDecomposer:
    def _decompose(self, goal: str):
        decomposer = HeuristicSpatialDecomposer()
        return decomposer.decompose(goal, WorldStateProjection(session_id="s1"))

    def test_composite_goal_produces_auditable_phases(self):
        tasks = self._decompose("获取洪水降雨数据并做风险分析后出专题图")
        ids = [t.task_id for t in tasks]
        assert "swarm.data.base_geo" in ids
        assert "swarm.data.theme" in ids  # theme 命中
        assert "swarm.compute.overlay" in ids
        assert "swarm.cartography.compose" in ids
        audit = next(t for t in tasks if t.task_id == "swarm.audit.judge")
        assert audit.optional is True
        assert audit.role == SwarmSpecialistRole.AUDIT_JUDGE

    def test_generic_goal_falls_back_to_four_phase(self):
        tasks = self._decompose("do something unspecified")
        ids = [t.task_id for t in tasks]
        assert ids == [
            "swarm.data.base_geo",
            "swarm.compute.overlay",
            "swarm.cartography.compose",
            "swarm.audit.judge",
        ]

    def test_siting_goal_adds_siting_phase_between_compute_and_carto(self):
        tasks = self._decompose("开展选址适宜性评估并制图")
        ids = [t.task_id for t in tasks]
        assert "swarm.compute.siting" in ids
        siting = next(t for t in tasks if t.task_id == "swarm.compute.siting")
        assert siting.depends_on == ("swarm.compute.overlay",)
        carto = next(t for t in tasks if t.task_id == "swarm.cartography.compose")
        assert carto.depends_on == ("swarm.compute.siting",)

    def test_compute_only_goal_skips_cartography(self):
        tasks = self._decompose("对数据做叠加统计分析")
        ids = [t.task_id for t in tasks]
        assert "swarm.cartography.compose" not in ids
        audit = next(t for t in tasks if t.task_id == "swarm.audit.judge")
        assert audit.depends_on == ("swarm.compute.overlay",)

    def test_empty_goal_still_produces_base_pipeline(self):
        tasks = self._decompose("")
        assert [t.task_id for t in tasks][0] == "swarm.data.base_geo"
