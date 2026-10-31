"""AnalysisWorkflow 运行态持久（goal-02 M3/M7）。

会话 store 单键 ``analysis_workflow.v1:<workflow_id>``（alias）——
单写者协议：所有 load→modify→save 都必须经 ``workflow_lock`` 持锁执行
（进程内 asyncio + Redis 分布式锁降级语义复用 session_lock_registry，
与 plan_mode 同纪律）。

有界性：run_log ≤24 条（环形截断）、receipts/envelopes 随步骤数
（≤24）封顶；无时间戳字段依赖（updated_at 用于 crash 判定，属运行
事实非确定性投影）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisArtifactEnvelope,
    AnalysisWorkflowContract,
)

#: 状态块 schema 版本（持久形状 additive 演进）。
STATE_SCHEMA_VERSION = 1

#: 单工作流 run_log 上限（环形截断——老事件丢弃，保最近）。
MAX_RUN_LOG = 24

#: running 状态陈旧判定（秒）——超时视为 crashed，允许 resume（对齐
#: plan_mode._RUNNING_STALE_SECONDS 哲学）。
RUNNING_STALE_SECONDS = 300.0


def state_alias(workflow_id: str) -> str:
    return f"analysis_workflow.v1:{workflow_id}"


class StepStateEntry(BaseModel):
    """步骤运行态（与 contracts.STEP_OUTCOME_STATES 词表一致）。"""

    step_id: str
    state: str = "pending"
    reason_code: str = ""
    message: str = ""
    fingerprint: str = ""
    envelope_ref_hint: str = ""      # 封套内 ref 的快照（诊断用；权威在 envelopes）
    attempts: int = 0
    updated_at: float = 0.0

    def touch(self) -> None:
        self.updated_at = time.time()


class WorkflowRunState(BaseModel):
    """一个工作流实例的持久运行态。"""

    schema_version: int = STATE_SCHEMA_VERSION
    workflow_id: str
    family: str
    contract_fingerprint: str = ""
    steps: Dict[str, StepStateEntry] = Field(default_factory=dict)
    envelopes: Dict[str, AnalysisArtifactEnvelope] = Field(default_factory=dict)
    receipts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    run_log: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "pending"          # pending|running|satisfied|partial|blocked|failed|cancelled
    updated_at: float = 0.0
    contract_snapshot: Dict[str, Any] = Field(default_factory=dict)

    def log(self, event: str, **facts: Any) -> None:
        """有界运行日志（环形；确定性字段，不含绝对时间戳）。"""
        self.run_log.append({"event": event, **facts})
        if len(self.run_log) > MAX_RUN_LOG:
            self.run_log = self.run_log[-MAX_RUN_LOG:]

    def touch(self) -> None:
        self.updated_at = time.time()

    def running_stale(self) -> bool:
        if self.status != "running":
            return False
        return (time.time() - self.updated_at) > RUNNING_STALE_SECONDS


def new_state(contract: AnalysisWorkflowContract, *,
              contract_fingerprint: str) -> WorkflowRunState:
    """从契约初始化运行态（步骤全 pending；blocked/skipped 裁决由
    ``apply_plan_verdicts`` 从 plan 结果灌入——plan 与 state 单向流动）。"""
    return WorkflowRunState(
        workflow_id=contract.workflow_id,
        family=contract.family,
        contract_fingerprint=contract_fingerprint,
        steps={
            s.step_id: StepStateEntry(step_id=s.step_id)
            for s in contract.steps
        },
        contract_snapshot=contract.model_dump(),
    )


def apply_plan_verdicts(state: WorkflowRunState,
                        step_verdicts: list) -> WorkflowRunState:
    """把 plan 期 typed 裁决（blocked/skipped）写入运行态。

    只写入非 ready 裁决；ready 保持现状（可能已有 satisfied 历史——
    幂等复用优先）。参数类型为 orchestrator.StepPlanVerdict 列表
    （避免循环 import，按鸭子类型消费）。
    """
    for verdict in step_verdicts:
        entry = state.steps.get(verdict.step_id)
        if entry is None:
            continue
        if verdict.verdict == "blocked":
            entry.state = "blocked"
            entry.reason_code = verdict.reason_code
            entry.message = verdict.message
        elif verdict.verdict == "skipped":
            entry.state = "skipped"
            entry.reason_code = verdict.reason_code
            entry.message = verdict.message
    state.status = verdict_of(state)
    return state


def state_to_payload(state: WorkflowRunState) -> Dict[str, Any]:
    """持久化载荷（envelopes 摘要化：ref 在即可，stats 截断防膨胀）。"""
    return state.model_dump()


def state_from_payload(payload: Dict[str, Any]) -> Optional[WorkflowRunState]:
    """反序列化（未知键忽略——旧读者/新读者 additive 兼容）。"""
    if not isinstance(payload, dict):
        return None
    try:
        return WorkflowRunState.model_validate(payload)
    except Exception:  # noqa: BLE001 — 损坏状态按不可恢复处理（诚实失败）
        return None


def verdict_of(state: WorkflowRunState) -> str:
    """整体判据：全部 satisfied（optional skipped 计 satisfied）才算
    satisfied；任何 failed/cancelled → 绝不 satisfied（诚实语义）。"""
    if not state.steps:
        return "blocked"
    if any(s.state == "failed" for s in state.steps.values()):
        return "failed"
    if any(s.state == "cancelled" for s in state.steps.values()):
        return "cancelled"
    if any(s.state == "blocked" for s in state.steps.values()):
        return "blocked"
    if any(s.state in ("pending", "running", "stale") for s in state.steps.values()):
        return "partial" if any(
            s.state == "satisfied" for s in state.steps.values()) else "pending"
    return "satisfied"
