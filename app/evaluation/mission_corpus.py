"""Mission-level Benchmark Corpus（V2；GOAL 里程碑 5/10）。

任务级场景：多轮 mission 的中断 / 恢复 / 目标修订 / 取消 / 资源耗尽 /
fencing / 破坏性任务不可盲重跑。场景由**真实** ``MissionRuntimeService``
（#1320）hermetic 驱动（sqlite 内存 + 冻结时钟，见 mission_driver.py）——
不建第二套任务宿主；#1335 的 fail-closed 修复会以语料 diff 浮出。

步骤词表（闭合）：
- ``bind`` / ``turn`` / ``fault`` / ``suspend`` / ``resume`` / ``cancel``
- ``revise_goal`` / ``advance_clock`` / ``swarm`` / ``complete``

全部离线、确定、零 LLM；记分 = 状态机终态 / frontier / 恢复分类 /
预算披露 / 终态幂等，均为结构化证据（不存 CoT）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── 步骤（dataclass，确定性声明）──────────────────────────────────────────

@dataclass(frozen=True)
class BindStep:
    """经 mission_bind 热路径缝创建/复用 Mission。"""

    kind: str = "bind"


@dataclass(frozen=True)
class TurnStep:
    """一个任务轮：计划 + frontier 推进（task_id 记完成）。"""

    task_id: str
    query: str
    expected_task: str = ""
    kind: str = "turn"


@dataclass(frozen=True)
class FaultStep:
    """确定性故障注入（签名式，非 sleep）。"""

    fault: str  # timeout | stale_ref | budget_exhaustion | provider_open
    dim: str = "tool_calls"  # budget_exhaustion 的配额维度
    amount: float = 0.0
    kind: str = "fault"


@dataclass(frozen=True)
class SuspendStep:
    kind: str = "suspend"


@dataclass(frozen=True)
class ResumeStep:
    worker_id: str = "worker-b"
    kind: str = "resume"


@dataclass(frozen=True)
class CancelStep:
    kind: str = "cancel"


@dataclass(frozen=True)
class ReviseGoalStep:
    new_goal: str
    invalidate_task_ids: Tuple[str, ...] = ()
    kind: str = "revise_goal"


@dataclass(frozen=True)
class AdvanceClockStep:
    seconds: float
    kind: str = "advance_clock"


@dataclass(frozen=True)
class SwarmStep:
    """创建 durable swarm run（任务表：id → receipt 字段）。"""

    run_label: str
    tasks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    kind: str = "swarm"


@dataclass(frozen=True)
class CompleteStep:
    kind: str = "complete"


# ── 场景 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MissionScenario:
    """一个 mission 级评测场景（驱动器消费；全部期望手工审定）。"""

    scenario_id: str
    name: str
    root_goal: str
    steps: Tuple[Any, ...]
    quota: Optional[Dict[str, float]] = None
    #: 期望终态（MissionState 词表；"" = 不检查）
    expected_final_state: str = ""
    #: 期望 goal_revision（0 = 不检查）
    expected_goal_revision: int = 0
    #: 期望恢复（diagnostics.recovery_count >= 值；0 = 不检查）
    min_recovery_attempts: int = 0
    #: 期望资源披露（exhausted() 报告的维度；空 = 不检查）
    expected_exhausted_dims: Tuple[str, ...] = ()
    #: 取消后必须终态 + 幂等（再 transition 拒绝）
    expect_cancel_terminal: bool = False
    #: 期望被标记 UNRESOLVED 的 swarm 任务（破坏性不可盲重跑红线）
    expected_unresolved_tasks: Tuple[str, ...] = ()
    #: 期望 frontier.completed（排序后）
    expected_completed: Tuple[str, ...] = ()
    #: 期望 frontier.pending（排序后）
    expected_pending: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    description: str = ""


def build_mission_corpus() -> List[MissionScenario]:
    """Mission 级语料（按 scenario_id 排序 + 构建期守卫）。"""
    scenarios = [
        MissionScenario(
            scenario_id="MSN-artifact-reuse-retained",
            name="目标修订后有效 artifact 引用保留",
            root_goal="成都学校专题图（先全市后重点区）",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布",
                                     expected_task="distribution_overview"),
                ReviseGoalStep(
                    new_goal="成都重点区学校专题图",
                    invalidate_task_ids=("t2",),
                ),
                CompleteStep(),
            ),
            expected_final_state="complete",
            expected_goal_revision=2,
            tags=("mission", "artifact-reuse", "goal-revision"),
            description="goal revision 只失效受影响 frontier；未受影响完成项"
                        "不重跑（#1320 语义）",
        ),
        MissionScenario(
            scenario_id="MSN-budget-exhaustion-disclosed",
            name="预算耗尽必须披露维度",
            root_goal="配额受限的批量任务",
            quota={"tool_calls": 3.0},
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                FaultStep(fault="budget_exhaustion", dim="tool_calls", amount=3.0),
                SuspendStep(),
            ),
            expected_final_state="suspended",
            expected_exhausted_dims=("tool_calls",),
            tags=("mission", "resource", "budget"),
            description="quota 耗尽后 exhausted() 必须披露维度（禁止静默超支）",
        ),
        MissionScenario(
            scenario_id="MSN-cancel-terminal-idempotent",
            name="取消后终态幂等",
            root_goal="可中途取消的导出任务",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                CancelStep(),
            ),
            expected_final_state="cancelled",
            expect_cancel_terminal=True,
            tags=("mission", "cancel", "terminal"),
            description="CANCELLED 为终态：终态后 transition 一律拒绝"
                        "（状态机红线），lease 清空",
        ),
        MissionScenario(
            scenario_id="MSN-goal-revision-requeue",
            name="目标修订只重排受影响任务",
            root_goal="成都学校专题图 v1",
            steps=(
                BindStep(),
                TurnStep(task_id="t1", query="成都各区小学分布",
                         expected_task="distribution_overview"),
                TurnStep(task_id="t2", query="成都哪个区小学密度最高",
                         expected_task="concentration_analysis"),
                ReviseGoalStep(new_goal="成都学校专题图 v2（含医院）",
                               invalidate_task_ids=("t2",)),
                TurnStep(task_id="t2", query="成都哪个区医院密度最高",
                         expected_task="concentration_analysis"),
                CompleteStep(),
            ),
            expected_final_state="complete",
            expected_goal_revision=2,
            expected_completed=("t1", "t2"),
            tags=("mission", "goal-revision"),
            description="goal_revision=2；t1 不重排、t2 失效后重排再完成",
        ),
        MissionScenario(
            scenario_id="MSN-fencing-stale-writer-rejected",
            name="陈旧 lease 写者被拒绝（fail-closed）",
            root_goal="fencing 语义锚",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                FaultStep(fault="stale_writer"),
                TurnStep(task_id="t2", query="成都哪个区小学密度最高"),
            ),
            expected_final_state="running",
            tags=("mission", "concurrency", "fencing", "security"),
            description="epoch 过期的写者 patch/transition 必须抛 FencingError"
                        "（静默成功 = P0）",
        ),
        MissionScenario(
            scenario_id="MSN-recovery-owner-dead",
            name="owner 死亡后恢复（lease 过期）",
            root_goal="长时间运行的任务（worker 崩溃恢复）",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                AdvanceClockStep(seconds=3600.0),
                ResumeStep(worker_id="worker-b"),
                TurnStep(task_id="t2", query="成都哪个区小学密度最高",
                         expected_task="concentration_analysis"),
                CompleteStep(),
            ),
            expected_final_state="complete",
            min_recovery_attempts=1,
            tags=("mission", "recovery", "lease"),
            description="TTL 过期 → worker-b resume：recovery_count≥1，"
                        "frontier 保留，任务继续到 complete",
        ),
        MissionScenario(
            scenario_id="MSN-suspend-resume-continuity",
            name="挂起恢复保持 frontier 连续性",
            root_goal="分阶段任务",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                SuspendStep(),
                AdvanceClockStep(seconds=60.0),
                ResumeStep(worker_id="worker-b"),
                TurnStep(task_id="t2", query="成都哪个区小学密度最高",
                         expected_task="concentration_analysis"),
                CompleteStep(),
            ),
            expected_final_state="complete",
            expected_completed=("t1", "t2"),
            min_recovery_attempts=1,
            tags=("mission", "suspend", "resume", "recovery"),
            description="suspend 释放 lease；resume 进入 recovering→running，"
                        "已完成任务不重跑",
        ),
        MissionScenario(
            scenario_id="MSN-swarm-destructive-unresolved",
            name="破坏性任务 owner 死亡 → UNRESOLVED 不可盲重跑",
            root_goal="含破坏性步骤的任务",
            steps=(
                BindStep(),
                SwarmStep(
                    run_label="sw1",
                    tasks={
                        "safe-1": {"state": "SUCCEEDED",
                                   "operation_class": "pure"},
                        "destr-1": {"state": "RUNNING",
                                    "operation_class": "destructive_at_most_once"},
                    },
                ),
                AdvanceClockStep(seconds=3600.0),
                ResumeStep(worker_id="worker-b"),
            ),
            expected_final_state="partially_complete",
            expected_unresolved_tasks=("destr-1",),
            min_recovery_attempts=1,
            tags=("mission", "recovery", "destructive", "safety"),
            description="破坏性 running 任务 owner 死亡 → UNRESOLVED"
                        "（永不盲重跑红线）；safe-1 保留为 completed",
        ),
        MissionScenario(
            scenario_id="MSN-timeout-fault-then-recover",
            name="provider 超时故障后恢复",
            root_goal="带外部依赖的任务",
            steps=(
                BindStep(), TurnStep(task_id="t1", query="成都各区小学分布"),
                FaultStep(fault="timeout"),
                AdvanceClockStep(seconds=120.0),
                ResumeStep(worker_id="worker-b"),
                CompleteStep(),
            ),
            expected_final_state="complete",
            min_recovery_attempts=1,
            tags=("mission", "fault-injection", "timeout", "recovery"),
            description="超时签名注入不中断任务账本；恢复后任务可完成"
                        "（故障不得造成静默状态漂移）",
        ),
    ]
    scenarios.sort(key=lambda s: s.scenario_id)
    # 构建期守卫：id 唯一 / 必有 bind 起步 / 步骤词表闭合
    ids = [s.scenario_id for s in scenarios]
    assert len(ids) == len(set(ids)), f"duplicate mission scenario ids: {ids}"
    _KNOWN = {"bind", "turn", "fault", "suspend", "resume", "cancel",
              "revise_goal", "advance_clock", "swarm", "complete"}
    for s in scenarios:
        assert s.steps and s.steps[0].kind == "bind", (
            f"{s.scenario_id} must start with bind"
        )
        for step in s.steps:
            assert step.kind in _KNOWN, f"{s.scenario_id}: unknown step {step.kind}"
    return scenarios
