"""Swarm 集群契约层（ADR-0187）—— 叶子模块。

命名纪要（ADR-0189 合并调和）：本文件原为 ``contracts.py``，因 ADR-0188
专家输出契约同名落地，于 agent/05 合并时改名为 ``delegation_contracts``；
内容零改动，仅模块路径变化。

只依赖 stdlib + pydantic + ``workflow_runtime.contracts``（NodeState 词表）。
本模块定义 Master→Specialist 委派链路的全部类型化契约：任务描述符、
派发单、提货券、聚合清单与运行状态。所有上下文面强制
「Zero Big Data in Context」：进出的只有 ref: 提货券与有界摘要，
payload 永不过境（validator fail-closed）。
"""
from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.workflow_runtime.contracts import NodeState

# ─────────────────────────── 有界常量 ───────────────────────────

#: 集群级全局并发子任务硬上限（SwarmConcurrencyGovernor 熔断）。
MAX_SWARM_CONCURRENCY = 3
#: 单次集群任务数上限（与 V5 MAX_INSTANCE_NODES 的有界纪律同型）。
MAX_SWARM_TASKS = 12
#: 单任务直接依赖上限。
MAX_DEPENDS_PER_TASK = 6
#: 单任务自动重试上限（attempts ≤ 3，与 V5 MAX_NODE_ATTEMPTS 对齐）。
MAX_RETRIES_PER_TASK = 2
#: 单任务硬墙钟缺省。
DEFAULT_TASK_TIMEOUT_S = 120.0
#: 根目标 / 任务切片 goal 字符上限（超限截断；网关层超限拒绝）。
MAX_GOAL_CHARS = 2000
MAX_TASK_GOAL_CHARS = 800
#: 提货券摘要 / 错误字段上限（超限截断）。
MAX_SUMMARY_CHARS = 400
MAX_ERROR_CHARS = 300
#: 单张提货券的产物 ref 数上限。
MAX_REFS_PER_RECEIPT = 12
#: 世界态投影事实行数 / 单行字符上限。
MAX_FACTS = 24
MAX_FACT_CHARS = 240
#: 投影相关 ref / 约束行上限。
MAX_REF_LIST = 24
MAX_CONSTRAINTS = 12
#: 聚合清单条目上限。
MAX_MANIFEST_ENTRIES = 32
#: 派发单内上游提货券摘要上限。
MAX_UPSTREAM_RECEIPTS = 6


# ─────────────────────────── 词表 ───────────────────────────


class SwarmSpecialistRole(str, enum.Enum):
    """四类专业子代理角色（策略标签，映射 subagent_roles 角色档）。"""

    DATA_HUNTER = "data_hunter"  # 数据猎手
    COMPUTE_SPECIALIST = "compute_specialist"  # 计算专家
    CARTOGRAPHY_SPECIALIST = "cartography_specialist"  # 制图专家
    AUDIT_JUDGE = "audit_judge"  # 审计裁判


class SwarmReceiptStatus(str, enum.Enum):
    """提货券终态（degraded 落节点态为 SKIPPED —— 见 ADR-0187 D2）。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEGRADED = "degraded"


class SwarmRunState:
    """集群运行终态词表。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SwarmErrorCode:
    """失败分类（仅 retryable/timeout 参与自动重试；destructive 一票否决）。"""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


#: 任务副作用词表（沿用 ADR-0184 D6 纪律；destructive at-most-once）。
SWARM_SIDE_EFFECTS = ("pure", "derived_external", "destructive")


class SwarmContractError(ValueError):
    """契约/图校验失败（分解图非法、非法状态转移等）。fail-closed。"""


# ─────────────────────────── 模型 ───────────────────────────


class WorldStateProjection(BaseModel):
    """子 Agent 可见的有界世界态切片（输入切片②）。

    红线：零 payload。事实行是短句，``relevant_refs`` 只收 ``ref:`` 前缀
    提货券 —— 任何 GeoJSON/栅格/大文本出现在此即契约违约。
    """

    session_id: str
    goal_summary: str = Field(default="", max_length=MAX_GOAL_CHARS)
    facts: list[str] = Field(default_factory=list, max_length=MAX_FACTS)
    relevant_refs: list[str] = Field(default_factory=list, max_length=MAX_REF_LIST)
    constraints: list[str] = Field(default_factory=list, max_length=MAX_CONSTRAINTS)
    world_revision: str = ""

    @field_validator("relevant_refs")
    @classmethod
    def _refs_only(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.startswith("ref:"):
                raise SwarmContractError(
                    "WorldStateProjection.relevant_refs 只收 ref: 提货券，"
                    f"收到非 ref 值（Zero Big Data in Context）: {item[:48]!r}"
                )
        return v

    @field_validator("facts", "constraints")
    @classmethod
    def _clip_lines(cls, v: list[str]) -> list[str]:
        return [str(line)[:MAX_FACT_CHARS] for line in v]


class SubagentReceipt(BaseModel):
    """子代理回传的唯一合法产物（输出提货券）。

    ``produced_refs`` 是 session store 的 ``ref:`` 提货券，payload 永不
    内联；summary/error 有界。下游任务经 ``SpecialistAssignment.
    upstream_receipts`` 只看到券，不看到数据本体。
    """

    assignment_id: str
    task_id: str
    role: SwarmSpecialistRole
    status: SwarmReceiptStatus
    produced_refs: list[str] = Field(
        default_factory=list, max_length=MAX_REFS_PER_RECEIPT
    )
    summary: str = ""
    error: str = ""
    error_code: str = ""
    degraded: bool = False
    attempts: int = 1
    heartbeats: int = 0
    wall_time_s: float = 0.0
    finished_at: float = 0.0

    @field_validator("produced_refs")
    @classmethod
    def _refs_only(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.startswith("ref:"):
                raise SwarmContractError(
                    "SubagentReceipt.produced_refs 只收 ref: 提货券，"
                    f"收到非 ref 值: {item[:48]!r}"
                )
        return v

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, v: str) -> str:
        return v[:MAX_SUMMARY_CHARS]

    @field_validator("error")
    @classmethod
    def _clip_error(cls, v: str) -> str:
        return v[:MAX_ERROR_CHARS]


class SwarmTaskDescriptor(BaseModel):
    """单条子任务描述符（调度事实，运行期字段由编排器回填）。

    任务状态复用 Execution Graph ``NodeState`` 词表（ADR-0187 D2，
    零新词表）；``side_effect`` 沿用 ADR-0184 D6 副作用词表。
    """

    task_id: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    goal: str = ""
    role: SwarmSpecialistRole
    capability: str = ""
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    expected_outputs: tuple[str, ...] = Field(default_factory=tuple)
    optional: bool = False
    side_effect: str = "pure"
    priority: int = Field(default=5, ge=-10, le=10)
    timeout_s: float = Field(default=DEFAULT_TASK_TIMEOUT_S, gt=0)
    max_retries: int = Field(default=1, ge=0, le=MAX_RETRIES_PER_TASK)
    # 运行期回填
    state: str = NodeState.PENDING
    attempts: int = 0
    degraded: bool = False

    @field_validator("goal")
    @classmethod
    def _clip_goal(cls, v: str) -> str:
        return v[:MAX_TASK_GOAL_CHARS]

    @field_validator("depends_on")
    @classmethod
    def _bound_deps(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(v) > MAX_DEPENDS_PER_TASK:
            raise SwarmContractError(
                f"depends_on 超过上限 {MAX_DEPENDS_PER_TASK}: {len(v)}"
            )
        return v

    @field_validator("side_effect")
    @classmethod
    def _known_side_effect(cls, v: str) -> str:
        if v not in SWARM_SIDE_EFFECTS:
            raise SwarmContractError(
                f"未知副作用词表值: {v!r}（合法：{SWARM_SIDE_EFFECTS}）"
            )
        return v


class SpecialistAssignment(BaseModel):
    """派发单：子代理的唯一合法输入（委派契约）。

    三段输入切片：①任务描述符（本任务 goal，非父目标全文）；
    ②世界态投影；③直接上游提货券摘要。严禁附带全量对话历史。
    """

    assignment_id: str
    task: SwarmTaskDescriptor
    projection: WorldStateProjection
    upstream_receipts: list[SubagentReceipt] = Field(
        default_factory=list, max_length=MAX_UPSTREAM_RECEIPTS
    )
    issued_at: float = 0.0


class SwarmAssetEntry(BaseModel):
    """聚合清单条目（一条提货券的披露面）。"""

    ref_id: str
    capability: str = ""
    role: SwarmSpecialistRole = SwarmSpecialistRole.DATA_HUNTER
    task_id: str = ""
    summary: str = ""

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, v: str) -> str:
        return v[:MAX_SUMMARY_CHARS]


class SwarmAssetManifest(BaseModel):
    """凭证总线提货单：一次集群运行的全部产物 ref 与受损披露。"""

    run_id: str
    session_id: str
    entries: list[SwarmAssetEntry] = Field(
        default_factory=list, max_length=MAX_MANIFEST_ENTRIES
    )
    failed_capabilities: list[str] = Field(default_factory=list)
    dropped_refs: list[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "entries": [e.model_dump() for e in self.entries],
            "failed_capabilities": self.failed_capabilities[:MAX_SWARM_TASKS],
            "dropped_refs": self.dropped_refs[:MAX_MANIFEST_ENTRIES],
        }


class SwarmExecutionStatus(BaseModel):
    """集群运行状态（有界投影；``counts`` 键为 NodeState 词表）。"""

    run_id: str
    session_id: str
    root_goal: str = ""
    state: str = SwarmRunState.RUNNING
    counts: dict[str, int] = Field(default_factory=dict)
    active_task_ids: list[str] = Field(default_factory=list)
    # Per-task outcomes for durable Mission mirror (status + side_effect + refs).
    # Keys are task_id; values are bounded dicts — never payloads (#1323).
    tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    manifest: Optional[SwarmAssetManifest] = None
    manifest_ref: Optional[str] = None
    started_at: float = 0.0
    finished_at: float = 0.0

    @field_validator("manifest_ref")
    @classmethod
    def _manifest_ref_is_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("ref:"):
            raise SwarmContractError(f"manifest_ref 必须是 ref: 提货券: {v[:48]!r}")
        return v
