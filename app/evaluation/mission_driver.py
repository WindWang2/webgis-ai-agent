"""Hermetic Mission Scenario Driver（V2；GOAL 里程碑 5/10）。

以**真实** ``MissionRuntimeService``（#1320）执行 mission_corpus 场景：

- sqlite 内存引擎（``StaticPool``）+ 可注入 factory —— 官方 hermetic 缝；
- 冻结时钟：monkeypatch ``store._utcnow`` 为滞后可控假钟（lease TTL、
  owner-dead 判定全确定；``finally`` 恢复）；
- 热路径 bind 走 ``mission_bind`` 生产缝（受控 env 窗口）；
- 故障为签名式注入（无 sleep）；frontier/预算经生产 CAS 缝推进。

红线：不重建任务宿主；不改 mission_runtime/*（#1335 拥有）；任何未预期
异常 = 案例失败（fail-closed 诚实化），不吞错。
"""
from __future__ import annotations

import os
import time as _time
from contextlib import contextmanager
from typing import Any, Dict, List

from app.evaluation.mission_corpus import (
    AdvanceClockStep,
    BindStep,
    CancelStep,
    CompleteStep,
    FaultStep,
    MissionScenario,
    ResumeStep,
    ReviseGoalStep,
    SuspendStep,
    SwarmStep,
    TurnStep,
)
from app.evaluation.runner import CaseResult

#: 假钟相对真实时间的滞后起点：所有 lease 写入（假钟 + TTL）必然早于
#: recovery.inspect 使用的真实 ``time.time()`` → owner-dead 判定确定成立，
#: 且场景内假钟推进（小时级）不会越过真实墙钟。
_CLOCK_LAG_S = 2 * 3600.0

_WORKER_A = "worker-a"


class _FakeClock:
    """可控假钟（确定性时间面）。"""

    def __init__(self, start_ts: float) -> None:
        self._ts = start_ts

    def now(self):
        import datetime as _dt

        return _dt.datetime.fromtimestamp(self._ts, tz=_dt.timezone.utc).replace(
            tzinfo=None)

    def advance(self, seconds: float) -> None:
        self._ts += float(seconds)

    @property
    def ts(self) -> float:
        return self._ts


@contextmanager
def hermetic_mission_runtime():
    """hermetic MissionRuntimeService（sqlite 内存 + 冻结 ``_utcnow``）。"""
    import app.models.mission  # noqa: F401 — 注册 mission 表
    from app.core.database import Base
    from app.services.mission_runtime import store as store_mod
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.mission_runtime.store import MissionStore

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = MissionStore(factory=factory)
    runtime = MissionRuntimeService(store=store)

    clock = _FakeClock(start_ts=_time.time() - _CLOCK_LAG_S)
    saved_utcnow = store_mod._utcnow
    store_mod._utcnow = clock.now  # type: ignore[assignment]
    try:
        yield runtime, clock
    finally:
        store_mod._utcnow = saved_utcnow  # type: ignore[assignment]
        engine.dispose()


def run_mission_scenario(scenario: MissionScenario) -> CaseResult:
    """确定性执行一个 mission 场景（离线；异常 = 失败，不吞错）。"""
    result = CaseResult(
        case_id=scenario.scenario_id, group="benchmark-mission",
        name=scenario.name,
    )
    evidence: Dict[str, Any] = {"steps": []}
    failures: List[str] = []
    metrics: Dict[str, Any] = {
        "mission_state_correct": None,
        "mission_goal_revision_ok": None,
        "mission_recovery_ok": None,
        "mission_resource_disclosed": None,
        "mission_cancel_terminal_ok": None,
        "mission_unresolved_ok": None,
        "mission_frontier_correct": None,
    }
    try:
        _execute_scenario(scenario, result, evidence, failures, metrics)
    except Exception as exc:  # noqa: BLE001 — fail-closed：场景崩溃即失败
        failures.append(f"driver error: {type(exc).__name__}: {exc}")
    result.metrics = metrics
    result.plan_evidence = evidence
    # 指标诚实化：未声明契约 → None（不虚构 pass）
    if not scenario.expected_final_state:
        metrics["mission_state_correct"] = None
    if not scenario.expected_goal_revision:
        metrics["mission_goal_revision_ok"] = None
    if not scenario.min_recovery_attempts:
        metrics["mission_recovery_ok"] = None
    if not scenario.expected_exhausted_dims:
        metrics["mission_resource_disclosed"] = None
    if not scenario.expect_cancel_terminal:
        metrics["mission_cancel_terminal_ok"] = None
    if not scenario.expected_unresolved_tasks:
        metrics["mission_unresolved_ok"] = None
    if not (scenario.expected_completed or scenario.expected_pending):
        metrics["mission_frontier_correct"] = None
    result.failures = failures
    result.passed = not failures
    result.status = "pass" if result.passed else "fail"
    return result


def _execute_scenario(
    scenario: MissionScenario,
    result: CaseResult,
    evidence: Dict[str, Any],
    failures: List[str],
    metrics: Dict[str, Any],
) -> None:
    from app.services.gis_harness.hotpath_convergence.mission_bind import (
        maybe_bind_mission_for_turn,
    )
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.mission_runtime import contracts as C

    mission_hotpath_env = "GIS_MISSION_HOTPATH"
    session_id = f"bench-mission-{scenario.scenario_id.lower()}"
    org_id = "bench-org"
    worker = _WORKER_A
    mission_id = ""
    swarm_ids: Dict[str, str] = {}

    with hermetic_mission_runtime() as (runtime, clock):
        for idx, step in enumerate(scenario.steps):
            step_note: Dict[str, Any] = {"i": idx, "kind": step.kind}
            if isinstance(step, BindStep):
                saved = os.environ.get(mission_hotpath_env)
                try:
                    os.environ[mission_hotpath_env] = "1"
                    bound = maybe_bind_mission_for_turn(
                        session_id=session_id, org_id=org_id,
                        user_id="bench-user", root_goal=scenario.root_goal,
                        runtime=runtime,
                    )
                finally:
                    if saved is None:
                        os.environ.pop(mission_hotpath_env, None)
                    else:
                        os.environ[mission_hotpath_env] = saved
                if not bound.mission_id:
                    failures.append(
                        f"step {idx} bind: no mission id "
                        f"({bound.skipped_reason!r})"
                    )
                    return
                mission_id = bound.mission_id
                step_note.update(bound.to_bounded_dict())
                runtime.start(mission_id, worker_id=worker, org_id=org_id)
                if scenario.quota:
                    # mission_bind 不透传 quota —— 场景配额经生产 CAS 缝补写
                    epoch = runtime._require_lease(mission_id, worker, org_id=org_id)
                    rec = runtime.store.get_mission(mission_id, org_id=org_id)
                    assert rec is not None
                    budget = rec.resource_budget.model_dump()
                    budget["quota"] = dict(scenario.quota)
                    runtime.store.patch_mission(
                        mission_id, lease_epoch=epoch, owner=worker,
                        resource_budget=budget,
                    )

            elif isinstance(step, TurnStep):
                intent = resolve_map_request_intent(step.query)
                if step.expected_task and intent.task != step.expected_task:
                    failures.append(
                        f"step {idx} turn {step.task_id}: expected task "
                        f"{step.expected_task}, got {intent.task}"
                    )
                epoch = runtime._require_lease(
                    mission_id, worker, org_id=org_id)
                rec = runtime.store.get_mission(mission_id, org_id=org_id)
                assert rec is not None
                frontier = rec.frontier.model_dump()
                if step.task_id not in frontier["completed"]:
                    frontier["completed"] = sorted(
                        frontier["completed"] + [step.task_id])
                # 完成即离开 pending/running/failed/blocked（frontier 单一事实）
                for bucket in ("pending", "running", "failed", "blocked"):
                    if step.task_id in frontier.get(bucket) or []:
                        frontier[bucket] = [
                            t for t in frontier.get(bucket) or []
                            if t != step.task_id
                        ]
                runtime.store.patch_mission(
                    mission_id, lease_epoch=epoch, owner=worker,
                    frontier=frontier,
                )
                step_note["task"] = intent.task

            elif isinstance(step, FaultStep):
                _execute_fault(
                    runtime, clock, scenario, step, mission_id, worker,
                    org_id, idx, failures,
                )
                step_note["fault"] = step.fault

            elif isinstance(step, SuspendStep):
                rec = runtime.suspend(mission_id, worker_id=worker, org_id=org_id)
                step_note["state"] = rec.state.value

            elif isinstance(step, ResumeStep):
                worker = step.worker_id
                resume = runtime.resume(mission_id, worker_id=worker, org_id=org_id)
                step_note["resume_ok"] = bool(resume.get("ok"))
                if not resume.get("ok"):
                    failures.append(
                        f"step {idx} resume: {resume.get('reason')!r}"
                    )

            elif isinstance(step, CancelStep):
                rec = runtime.cancel(mission_id, worker_id=worker, org_id=org_id)
                step_note["state"] = rec.state.value

            elif isinstance(step, ReviseGoalStep):
                runtime.revise_goal(
                    mission_id, worker_id=worker, new_goal=step.new_goal,
                    invalidate_task_ids=list(step.invalidate_task_ids),
                    org_id=org_id,
                )
                step_note["goal"] = step.new_goal[:40]

            elif isinstance(step, AdvanceClockStep):
                clock.advance(step.seconds)
                # 不变量：假钟不得越过真实墙钟（recovery/lease 面混用真实
                # time.time()）—— 越线 = owner-dead 判定失去确定性，立即败。
                if clock.ts >= _time.time():
                    failures.append(
                        f"step {idx}: fake clock crossed real wall clock "
                        f"({clock.ts:.0f}); owner-dead determinism lost — "
                        f"shrink AdvanceClockStep"
                    )
                step_note["to"] = clock.ts

            elif isinstance(step, SwarmStep):
                payload = {
                    tid: {"task_id": tid, **td}
                    for tid, td in step.tasks.items()
                }
                run = runtime.store.create_swarm_run(
                    mission_id, org_id=org_id, goal_slice=step.run_label,
                    tasks=payload,
                )
                swarm_ids[step.run_label] = run.swarm_run_id
                step_note["swarm_run_id"] = run.swarm_run_id

            elif isinstance(step, CompleteStep):
                rec = runtime.complete(mission_id, worker_id=worker, org_id=org_id)
                step_note["state"] = rec.state.value

            else:  # pragma: no cover — 语料守卫已闭合词表
                failures.append(f"step {idx}: unknown kind {step.kind}")
            evidence["steps"].append(step_note)
            if failures:
                return

        # ── 终局记分（结构化证据；不存 CoT）──────────────────────────────
        rec = runtime.store.get_mission(mission_id, org_id=org_id)
        if rec is None:
            failures.append("scoring: mission missing")
            return
        evidence["final_state"] = rec.state.value
        evidence["goal_revision"] = rec.goal_revision
        diagnostics = runtime.diagnostics(mission_id, org_id=org_id)
        evidence["recovery_count"] = diagnostics.recovery_count
        evidence["frontier"] = rec.frontier.model_dump()
        evidence["exhausted"] = rec.resource_budget.exhausted()

        if scenario.expected_final_state:
            metrics["mission_state_correct"] = (
                rec.state.value == scenario.expected_final_state
            )
            if rec.state.value != scenario.expected_final_state:
                failures.append(
                    f"final state: expected {scenario.expected_final_state}, "
                    f"got {rec.state.value}"
                )
        if scenario.expected_goal_revision:
            metrics["mission_goal_revision_ok"] = (
                rec.goal_revision == scenario.expected_goal_revision
            )
            if rec.goal_revision != scenario.expected_goal_revision:
                failures.append(
                    f"goal revision: expected {scenario.expected_goal_revision}, "
                    f"got {rec.goal_revision}"
                )
        if scenario.min_recovery_attempts:
            metrics["mission_recovery_ok"] = (
                diagnostics.recovery_count >= scenario.min_recovery_attempts
            )
            if diagnostics.recovery_count < scenario.min_recovery_attempts:
                failures.append(
                    f"recovery attempts: expected >= {scenario.min_recovery_attempts}, "
                    f"got {diagnostics.recovery_count}"
                )
        if scenario.expected_exhausted_dims:
            got = set(rec.resource_budget.exhausted())
            want = set(scenario.expected_exhausted_dims)
            metrics["mission_resource_disclosed"] = want <= got
            if not want <= got:
                failures.append(
                    f"budget: exhausted dims {sorted(got)} do not disclose "
                    f"{sorted(want)}"
                )
        if scenario.expect_cancel_terminal:
            ok = rec.state is C.MissionState.CANCELLED and not rec.lease_owner
            if ok:
                # 终态幂等探针：终态后再 acquire/transition 必须被拒
                try:
                    runtime.complete(mission_id, worker_id=worker, org_id=org_id)
                    ok = False
                    failures.append("cancel terminal: post-terminal complete accepted")
                except Exception:  # noqa: BLE001 — 拒绝 = 预期（fail-closed）
                    ok = ok and True
            metrics["mission_cancel_terminal_ok"] = ok
            if rec.lease_owner:
                failures.append(
                    f"cancel terminal: lease not cleared ({rec.lease_owner!r})"
                )
        if scenario.expected_unresolved_tasks:
            got_unresolved: List[str] = []
            for run in runtime.store.list_swarm_runs_for_mission(mission_id):
                for tid, receipt in run.tasks.items():
                    if receipt.state == C.SwarmTaskDurableState.UNRESOLVED:
                        got_unresolved.append(tid)
            want = set(scenario.expected_unresolved_tasks)
            metrics["mission_unresolved_ok"] = want <= set(got_unresolved)
            if not want <= set(got_unresolved):
                failures.append(
                    f"unresolved tasks: expected {sorted(want)}, "
                    f"got {sorted(set(got_unresolved))}"
                )
        if scenario.expected_completed or scenario.expected_pending:
            got_completed = sorted(rec.frontier.completed)
            got_pending = sorted(rec.frontier.pending)
            frontier_ok = (
                got_completed == sorted(scenario.expected_completed)
                and got_pending == sorted(scenario.expected_pending)
            )
            metrics["mission_frontier_correct"] = frontier_ok
            if not frontier_ok:
                failures.append(
                    f"frontier: expected completed="
                    f"{sorted(scenario.expected_completed)} pending="
                    f"{sorted(scenario.expected_pending)}, got completed="
                    f"{got_completed} pending={got_pending}"
                )


def _execute_fault(
    runtime, clock, scenario: MissionScenario, step: FaultStep,
    mission_id: str, worker: str, org_id: str, idx: int,
    failures: List[str],
) -> None:
    """签名式故障注入（无 sleep；走真实分类/CAS 面）。"""
    from app.services.gis_harness.failure_taxonomy import classify_harness_failure
    from app.services.mission_runtime.store import FencingError

    if step.fault == "timeout":
        result = classify_harness_failure(
            exception=TimeoutError("provider timeout (signature)"))
        if result is None or result.value == "unknown":
            failures.append(f"step {idx} fault timeout: classifier gave {result!r}")
        return
    if step.fault == "stale_writer":
        epoch = runtime._require_lease(mission_id, worker, org_id=org_id)
        ghost_epoch = max(0, epoch - 1)
        try:
            runtime.store.patch_mission(
                mission_id, lease_epoch=ghost_epoch, owner="worker-ghost",
                root_goal="ghost write (must be rejected)",
            )
            failures.append(
                f"step {idx} fault stale_writer: ghost write accepted (fail-open!)"
            )
        except FencingError:
            return  # 预期：陈旧写者 fail-closed
        return
    if step.fault == "budget_exhaustion":
        epoch = runtime._require_lease(mission_id, worker, org_id=org_id)
        rec = runtime.store.get_mission(mission_id, org_id=org_id)
        assert rec is not None
        rec.resource_budget.charge(step.dim, step.amount)
        runtime.store.patch_mission(
            mission_id, lease_epoch=epoch, owner=worker,
            resource_budget=rec.resource_budget.model_dump(),
        )
        return
    failures.append(f"step {idx}: unknown fault {step.fault!r}")
