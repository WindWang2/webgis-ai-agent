"""实验运行态持久（method-experiment WP3）—— 会话 store 单键别名。

与 AWF state 同纪律：

- 单写者协议：所有 load→modify→save 必须经实验级分布式锁（工具层持有，
  与 awf:<id> 同锁族语义 ``mexp:<experiment_id>``）；
- 诚实语义：failed/cancelled/not_evaluated 绝不冒充 completed；
- 有界：run_log ≤32 条环形截断；trial 记录只持有界指标与诊断（原始
  工具结果不进状态——大载荷在 ref/封套里）；
- 无时间戳字段依赖（updated_at 仅 crash 判定的运行事实）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: 状态块 schema 版本（持久形状 additive 演进）。
STATE_SCHEMA_VERSION = 1

#: run_log 上限（环形截断）。
MAX_RUN_LOG = 32

#: running 陈旧判定（秒）——超时允许 resume（与 AWF 同哲学）。
RUNNING_STALE_SECONDS = 300.0

#: 单试验诊断/错误消息截断（有界）。
_MAX_MESSAGE = 240


def state_alias(experiment_id: str) -> str:
    return f"method_experiment.v1:{experiment_id}"


#: 试验结局词表（诚实：not_evaluated ≠ failed ≠ completed）。
TRIAL_STATES = (
    "pending", "running", "completed", "not_evaluated",
    "skipped", "failed", "cancelled", "reused",
)


class TrialRecord(BaseModel):
    """一个试验的运行记录（指标只存工具产出的事实，缺=未评估）。"""

    trial_id: str
    candidate_id: str
    variant_index: int = 0
    params: Dict[str, Any] = Field(default_factory=dict)
    seed: int = 0
    eval_tool: str = ""
    state: str = "pending"                       # ⊆ TRIAL_STATES
    content_fingerprint: str = ""                # 输入+方法+参数+seed+协议 hash
    metrics: Dict[str, float] = Field(default_factory=dict)   # metric_id → 值
    metric_basis: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # ^ metric_id → {sample_count, folds, scheme, note, …}（计算依据，有界）
    diagnostics: Dict[str, Any] = Field(default_factory=dict)  # ≤8 键（族适配投影）
    reason_code: str = ""                        # 失败/跳过/未评估的 typed 码
    message: str = ""
    attempts: int = 0
    wall_ms: int = 0                             # 真实执行耗时（成本记账）
    updated_at: float = 0.0

    def touch(self) -> None:
        self.updated_at = time.time()


class AdoptionReceipt(BaseModel):
    """用户采纳回执（decision receipt——记录选择，不自动改地图）。"""

    candidate_id: str
    trial_id: str = ""
    metric_snapshot: Dict[str, float] = Field(default_factory=dict)
    reason: str = ""                             # 用户理由（有界）
    received_at: float = 0.0
    plan_fingerprint: str = ""


class ExperimentRunState(BaseModel):
    """一个实验的持久运行态。"""

    schema_version: int = STATE_SCHEMA_VERSION
    experiment_id: str
    family_id: str = ""
    plan_fingerprint: str = ""
    plan_snapshot: Dict[str, Any] = Field(default_factory=dict)  # 计划快照（恢复用）
    trials: Dict[str, TrialRecord] = Field(default_factory=dict)
    run_log: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "pending"   # pending|running|completed|partial|cancelled|failed
    updated_at: float = 0.0
    adoption: Optional[AdoptionReceipt] = None

    def log(self, event: str, **detail: Any) -> None:
        """有界运行日志（环形截断，老事件丢弃）。"""
        entry: Dict[str, Any] = {"event": str(event)[:64], "at": time.time()}
        if detail:
            entry.update({k: v for k, v in list(detail.items())[:6]})
        self.run_log.append(entry)
        if len(self.run_log) > MAX_RUN_LOG:
            self.run_log = self.run_log[-MAX_RUN_LOG:]

    def touch(self) -> None:
        self.updated_at = time.time()

    def running_stale(self) -> bool:
        """running 且长时间无心跳 → 允许 resume（crash 判定）。"""
        return (self.status == "running"
                and time.time() - self.updated_at > RUNNING_STALE_SECONDS)

    def counts(self) -> Dict[str, int]:
        """按状态的试验计数（进度 = 实际完成数，绝不虚构 ETA）。"""
        out: Dict[str, int] = {}
        for t in self.trials.values():
            out[t.state] = out.get(t.state, 0) + 1
        return out

    def total_wall_ms(self) -> int:
        return sum(t.wall_ms for t in self.trials.values())



def new_state_from_plan(plan) -> ExperimentRunState:
    """从计划模型构建初始运行态（快照 = 完整 model dump，可回读恢复）。"""
    dump = plan.model_dump(mode="json")
    trials = {
        t.trial_id: TrialRecord(
            trial_id=t.trial_id,
            candidate_id=t.candidate_id,
            variant_index=t.variant_index,
            params=dict(t.params),
            seed=t.seed,
            eval_tool=t.eval_tool,
        )
        for t in plan.trials
    }
    return ExperimentRunState(
        experiment_id=plan.experiment_id,
        family_id=plan.family_id,
        plan_fingerprint=plan.fingerprint()[:16],
        plan_snapshot=dump,
        trials=trials,
        status="pending",
    )


def state_to_payload(state: ExperimentRunState) -> Dict[str, Any]:
    return state.model_dump(mode="json")


def state_from_payload(payload: Dict[str, Any]) -> ExperimentRunState:
    if not payload:
        return ExperimentRunState(experiment_id="")
    try:
        return ExperimentRunState.model_validate(payload)
    except Exception:  # noqa: BLE001 — 快照损坏 = 诚实空态（调用方按不存在处理）
        return ExperimentRunState(experiment_id="")
